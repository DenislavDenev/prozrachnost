// Икономика: the interactive map (AGENTS.md 7). One SVG on one plane (EPSG:3035, Eurostat GISCO), so Bulgaria and
// Europe are two views of the same map and the page flies between them. Data from /api/karta.json; the outlines from
// /static/europe.json (tools/build_europe_map.py).
//   zoom: the buttons, Ctrl + wheel, double click, two fingers; move: drag (the wheel and one finger scroll the page)
//   hover: the region is outlined and its value shown; click (on the map or the list): the region is selected, its
//   borders drawn clearly and its row marked in the list, which scrolls inside itself; the map does not move
(async () => {
  const NS = 'http://www.w3.org/2000/svg';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const svg = $('k-svg'), world = svg.querySelector('.world'), layers = svg.querySelector('.layers');
  const borders = svg.querySelector('.borders'), top = svg.querySelector('.top'), tip = $('k-tip'), box = $('k-box');
  const tbody = $('k-list').tBodies[0];
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ver = new URL(document.currentScript?.src || location.href).searchParams.get('v') || '';
  const G = await (await fetch('/static/europe.json?v=' + ver)).json();
  let d = JSON.parse($('k-data').textContent), sel = null, view, byCode = new Map();

  // ---------- the views: Europe is the whole frame, Bulgaria its outline with a margin ----------
  const ASPECT = 1012 / (G.h + 12);
  svg.style.aspectRatio = `${ASPECT}`;
  const fit = (b) => {   // a box grown to the map's proportions, around the same centre
    const w = Math.max(b.w, b.h * ASPECT), h = w / ASPECT;
    return { x: b.x + b.w / 2 - w / 2, y: b.y + b.h / 2 - h / 2, w, h };
  };
  function bbox(path) {
    const n = path.match(/-?\d+(\.\d+)?/g).map(Number);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < n.length; i += 2) { x0 = Math.min(x0, n[i]); x1 = Math.max(x1, n[i]); y0 = Math.min(y0, n[i + 1]); y1 = Math.max(y1, n[i + 1]); }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  const bgBox = bbox(G.bg['0'].BG);
  const HOME = { eu: fit({ x: -6, y: -6, w: 1012, h: G.h + 12 }),
    bg: fit({ x: bgBox.x - bgBox.w * 0.08, y: bgBox.y - bgBox.h * 0.1, w: bgBox.w * 1.16, h: bgBox.h * 1.2 }) };
  const setView = (v) => { view = v; svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`); };
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  function fly(to, ms, each) {   // the camera moves from the current view to `to`; each(t) runs on every frame
    const from = view, t0 = performance.now();
    return new Promise((done) => {
      const step = (now) => {
        const t = still ? 1 : Math.min(1, (now - t0) / ms), k = ease(t);
        // zoom on a log scale, so flying in feels even rather than rushing at the end
        const w = from.w * (to.w / from.w) ** k, h = w / ASPECT;
        const cx = from.x + from.w / 2 + (to.x + to.w / 2 - from.x - from.w / 2) * k;
        const cy = from.y + from.h / 2 + (to.y + to.h / 2 - from.y - from.h / 2) * k;
        setView({ x: cx - w / 2, y: cy - h / 2, w, h });
        each && each(k);
        t < 1 ? requestAnimationFrame(step) : done();
      };
      requestAnimationFrame(step);
    });
  }

  // ---------- drawing ----------
  const pathEl = (dd, attrs) => { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', dd); for (const k in attrs) p.setAttribute(k, attrs[k]); return p; };
  // the ground: every country in the frame; the European ones without NUTS regions (Belarus, Moldova …) answer on hover
  for (const [c, dd] of Object.entries(G.world)) world.append(pathEl(dd, { 'data-code': c, class: d.countries[c] ? 'ctx' : 'far' }));

  function shapesOf(o, nuts) { return o === 'bg' ? G.bg[String(nuts)] : G[String(nuts)]; }
  function colours(data) {   // one continuous gradient of the accent, log scale: the capital does not wash out the rest
    const vals = data.items.map((it) => it.v).filter((v) => v != null && v > 0).sort((a, b) => a - b);
    const lo = vals[0] || 1, hi = vals[vals.length - 1] || 1, L = Math.log(hi / lo) || 1;
    return (v) => (v == null || v <= 0 ? null : `color-mix(in srgb, var(--accent) ${Math.round(8 + (Math.log(v / lo) / L) * 92)}%, #fff)`);
  }
  function layer(data) {
    const g = document.createElementNS(NS, 'g'), shapes = shapesOf(data.o, data.nuts), fill = colours(data);
    for (const it of data.items) {
      if (!shapes[it.code]) continue;
      const p = pathEl(shapes[it.code], { 'data-code': it.code, class: 'area' + (data.o === 'eu' && it.code.startsWith('BG') ? ' bg' : '') });
      p.style.fill = fill(it.v) || 'var(--line-2)';
      g.append(p);
    }
    g.classList.toggle('few', g.childElementCount < 400);   // colour changes fade only on small layers: a thousand would lag
    return g;
  }
  function recolour(data) {
    const fill = colours(data);
    for (const it of data.items) { const p = layers.querySelector(`[data-code="${it.code}"]`); if (p) p.style.fill = fill(it.v) || 'var(--line-2)'; }
  }
  function drawBorders(data) {   // the countries over the regions of Europe; Bulgaria's outline over its own map
    borders.replaceChildren();
    if (data.o === 'bg') borders.append(pathEl(G.bg['0'].BG, { class: 'bgline' }));
    else if (data.nuts > 0) for (const dd of Object.values(G['0'])) borders.append(pathEl(dd, {}));
    world.classList.toggle('inbg', data.o === 'bg');
  }

  // ---------- the text, the controls and the list ----------
  const date = (s) => (s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(0, 4)}` : '');
  function texts(data) {
    $('k-title').textContent = `${data.title} по ${data.plural.toLowerCase()}${data.o === 'eu' ? ' в Европа' : ''}`;
    $('k-lede').innerHTML = data.y ? `През ${data.y} г.${data.bg != null ? ` за България: <b>${num(data.bg, data.digits)} ${esc(data.unit)}</b>.` : ''}
      ${data.updated ? `Регионалните сметки излизат около 14 месеца след края на годината: данните за ${data.y} г. са публикувани от Eurostat на ${date(data.updated)}.` : ''}` : 'Още няма данни.';
    $('k-h2').textContent = data.title + (data.y ? `, ${data.y}` : '');
    $('k-unit').textContent = data.unit;
    $('k-plural').textContent = data.plural;
    $('k-h-name').textContent = data.single;
    $('k-h-y').textContent = data.y || '';
    $('k-h-base').textContent = `Промяна от ${data.base || ''}, %`;
    $('k-ds').textContent = data.dataset; $('k-ds').href = data.url; $('k-csv').href = data.csv;
    $('k-year').innerHTML = data.years.map((y) => `<option${y === data.y ? ' selected' : ''}>${y}</option>`).join('');
    $('k-level').innerHTML = data.levels.map(([k, n]) => `<a href="/karta?m=${data.m}&o=${data.o}&l=${k}" data-l="${k}"${k === data.l ? ' aria-current="page"' : ''}>${n}</a>`).join('');
    for (const [id, key] of [['k-scope', 'o'], ['k-measure', 'm']]) $(id).querySelectorAll('a').forEach((a) => {
      a.toggleAttribute('aria-current', a.dataset[key] === data[key]); if (a.dataset[key] === data[key]) a.setAttribute('aria-current', 'page'); });
    const q = new URLSearchParams({ m: data.m, o: data.o, l: data.l }); if (data.y) q.set('y', data.y);
    history.replaceState(null, '', '/karta?' + q);
  }
  function list(data) {
    byCode = new Map(data.items.map((it) => [it.code, it]));
    tbody.innerHTML = data.items.map((it) => `<tr data-code="${it.code}"${data.o === 'eu' && it.code.startsWith('BG') ? ' class="hl"' : ''}>
      <td>${esc(it.name)}</td><td class="n" data-v="${it.v ?? ''}">${num(it.v, data.digits)}</td>
      <td class="n" data-v="${it.change ?? ''}">${num(it.change)}</td><td class="n">${it.rank ?? ''}</td></tr>`).join('');
    $('k-list').querySelectorAll('th').forEach((th) => th.hasAttribute('aria-sort') && th.setAttribute('aria-sort', 'none'));
  }

  // ---------- hover, tooltip, selection ----------
  const nameOf = (code) => byCode.get(code)?.name || d.countries[code] || code;
  function showTip(e, code) {
    const it = byCode.get(code), r = svg.parentElement.getBoundingClientRect();
    // a neighbour on the map of Bulgaria is only ground: its name, no value
    const what = !it && d.o === 'bg' ? '' : `<br>${it && it.v != null ? `${num(it.v, d.digits)} ${esc(d.unit)}${it.rank ? ` · ${it.rank}-о място` : ''}` : 'няма данни'}`;
    tip.innerHTML = `<b>${esc(nameOf(code))}</b>${what}`;
    tip.hidden = false;
    const x = e.clientX - r.left, y = e.clientY - r.top;
    tip.style.left = `${Math.min(x + 14, r.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${y + 16 + tip.offsetHeight > r.height ? y - tip.offsetHeight - 10 : y + 16}px`;
  }
  function select(code) {
    sel = sel === code ? null : code;
    top.replaceChildren();
    tbody.querySelectorAll('tr.sel').forEach((tr) => tr.classList.remove('sel'));
    if (!sel) return;
    const src = layers.querySelector(`[data-code="${sel}"]`) || world.querySelector(`[data-code="${sel}"]`);
    // the borders of the selection on top of everything: a white halo and a dark line
    if (src) for (const cls of ['halo', 'line']) top.append(pathEl(src.getAttribute('d'), { class: cls }));
    const tr = tbody.querySelector(`tr[data-code="${sel}"]`);
    if (tr) {   // the list scrolls inside itself; the page and the map stay where they are
      tr.classList.add('sel');
      const off = tr.getBoundingClientRect().top - box.getBoundingClientRect().top;
      box.scrollTo({ top: box.scrollTop + off - box.clientHeight / 2 + tr.offsetHeight / 2, behavior: still ? 'auto' : 'smooth' });
    }
  }
  let moved = false;
  svg.addEventListener('pointermove', (e) => { const p = e.target.closest('[data-code]'); p && !p.classList.contains('far') ? showTip(e, p.dataset.code) : (tip.hidden = true); });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; });
  svg.addEventListener('click', (e) => {
    if (moved) return;
    const p = e.target.closest('.area, .ctx');
    if (p && (p.classList.contains('area') || byCode.has(p.dataset.code))) select(p.dataset.code);
  });
  tbody.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-code]'); if (tr) select(tr.dataset.code); });
  tbody.addEventListener('mouseover', (e) => {
    layers.querySelectorAll('.hov').forEach((p) => p.classList.remove('hov'));
    const tr = e.target.closest('tr[data-code]'), p = tr && layers.querySelector(`[data-code="${tr.dataset.code}"]`);
    if (p) { p.classList.add('hov'); p.parentNode.append(p); }   // last drawn, so its whole outline shows
  });
  tbody.addEventListener('mouseleave', () => layers.querySelectorAll('.hov').forEach((p) => p.classList.remove('hov')));

  // ---------- zoom and move ----------
  const home = () => HOME[d.o];
  function clamp(v) {
    const h0 = home(), w = Math.min(h0.w, Math.max(h0.w / 40, v.w)), h = w / ASPECT;
    const x = Math.min(h0.x + h0.w - w / 2, Math.max(h0.x - w / 2, v.x)), y = Math.min(h0.y + h0.h - h / 2, Math.max(h0.y - h / 2, v.y));
    return { x, y, w, h };
  }
  const toMap = (cx, cy) => { const r = svg.getBoundingClientRect(); return [view.x + ((cx - r.left) / r.width) * view.w, view.y + ((cy - r.top) / r.height) * view.h]; };
  function zoomAt(f, mx, my) {   // f > 1 zooms in, around the map point (mx, my)
    const w = view.w / f, h = w / ASPECT;
    setView(clamp({ x: mx - ((mx - view.x) / view.w) * w, y: my - ((my - view.y) / view.h) * h, w, h }));
  }
  const centre = () => [view.x + view.w / 2, view.y + view.h / 2];
  svg.parentElement.querySelector('.zoom').addEventListener('click', (e) => {
    const z = e.target.closest('button')?.dataset.z;
    if (z === 'home') return fly(home(), 500);
    if (!z) return;
    const [cx, cy] = centre(), w = view.w / (z === 'in' ? 2 : 0.5), h = w / ASPECT;
    fly(clamp({ x: cx - w / 2, y: cy - h / 2, w, h }), 350);
  });
  svg.addEventListener('wheel', (e) => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); zoomAt(Math.exp(-e.deltaY * 0.0025), ...toMap(e.clientX, e.clientY)); }, { passive: false });
  svg.addEventListener('dblclick', (e) => { e.preventDefault(); const [mx, my] = toMap(e.clientX, e.clientY), w = view.w / 2, h = w / ASPECT;
    fly(clamp({ x: mx - ((mx - view.x) / view.w) * w, y: my - ((my - view.y) / view.h) * h, w, h }), 300); });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => { if (e.pointerType === 'touch' || e.button !== 0) return; drag = { x: e.clientX, y: e.clientY, v: view }; moved = false; });
  addEventListener('pointermove', (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!moved && Math.hypot(dx, dy) < 4) return;
    moved = true; svg.classList.add('drag'); tip.hidden = true;
    const r = svg.getBoundingClientRect();
    setView(clamp({ ...drag.v, x: drag.v.x - (dx / r.width) * drag.v.w, y: drag.v.y - (dy / r.height) * drag.v.h }));
  });
  addEventListener('pointerup', () => { drag = null; svg.classList.remove('drag'); setTimeout(() => { moved = false; }, 0); });
  // two fingers zoom and move; one finger scrolls the page (touch-action in the CSS)
  let pinch = null;
  const two = (t) => ({ d: Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY), x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 });
  svg.addEventListener('touchstart', (e) => { if (e.touches.length === 2) { e.preventDefault(); pinch = { ...two(e.touches), v: view }; } }, { passive: false });
  svg.addEventListener('touchmove', (e) => {
    if (!pinch || e.touches.length !== 2) return;
    e.preventDefault();
    const n = two(e.touches), r = svg.getBoundingClientRect(), f = n.d / pinch.d, v = pinch.v;
    const mx = v.x + ((pinch.x - r.left) / r.width) * v.w, my = v.y + ((pinch.y - r.top) / r.height) * v.h;
    const w = v.w / f, h = w / ASPECT;
    setView(clamp({ x: mx - ((pinch.x - r.left) / r.width) * w - ((n.x - pinch.x) / r.width) * w, y: my - ((pinch.y - r.top) / r.height) * h - ((n.y - pinch.y) / r.height) * h, w, h }));
  }, { passive: false });
  svg.addEventListener('touchend', (e) => { if (e.touches.length < 2) pinch = null; });

  // ---------- changes: a measure or year recolours, a level crossfades, Bulgaria <-> Europe flies ----------
  async function go(params, kind) {
    const q = new URLSearchParams({ m: d.m, o: d.o, l: d.l, ...params });
    if (!params.y) q.delete('y');
    const res = await fetch('/api/karta.json?' + q);
    if (!res.ok) return;
    const next = await res.json(), old = layers.firstElementChild;
    const fresh = kind === 'colour' ? null : layer(next);
    d = next; texts(next); list(next); sel = null; top.replaceChildren(); tip.hidden = true;
    if (kind === 'colour') { recolour(next); return; }
    fresh.style.opacity = 0; layers.append(fresh);
    if (kind === 'level') {
      drawBorders(next);
      fresh.style.transition = 'opacity .35s'; requestAnimationFrame(() => { fresh.style.opacity = 1; });
      setTimeout(() => old && old.remove(), 380);
      return;
    }
    // the flight: out of Bulgaria the rest of Europe fades in around it; into Bulgaria the rest fades to ground
    borders.replaceChildren();
    svg.classList.add('flying');
    await fly(HOME[next.o], 1300, (k) => { fresh.style.opacity = Math.min(1, Math.max(0, (k - 0.25) / 0.6)); if (old) old.style.opacity = 1 - Math.min(1, k / 0.6); });
    old && old.remove(); fresh.style.opacity = 1;
    drawBorders(next); svg.classList.remove('flying');
  }
  const pick = (id, key, kind) => $(id).addEventListener('click', (e) => {
    const a = e.target.closest('a'); if (!a) return; e.preventDefault();
    if (a.dataset[key] === d[key]) return;
    const p = { [key]: a.dataset[key] };
    // away from Bulgaria the map opens on the countries (light, and the first thing to see); back home on the oblasts
    if (key === 'o') p.l = p.o === 'eu' ? 'darzhavi' : 'oblasti';
    go(p, kind);
  });
  pick('k-scope', 'o', 'fly'); pick('k-level', 'l', 'level'); pick('k-measure', 'm', 'colour');
  $('k-year').addEventListener('change', (e) => go({ y: e.target.value }, 'colour'));

  // ---------- start ----------
  setView(HOME[d.o]);
  layers.append(layer(d)); drawBorders(d); texts(d); list(d);
})();

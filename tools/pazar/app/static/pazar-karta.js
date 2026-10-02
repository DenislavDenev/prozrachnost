// Пазар: the map of the municipalities (AGENTS.md 7). One SVG from /static/bg-municipalities.json (Eurostat GISCO, EPSG:3035).
// zoom: buttons, Ctrl + wheel, double click, two fingers; move: drag. Hover shows the name and value, a click (map or list)
// selects the municipality with a white halo and dark line and marks its row. A request that is overtaken is cancelled;
// the contours are drawn once and only repainted.
(async () => {
  const NS = 'http://www.w3.org/2000/svg', $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const svg = $('k-svg'), layers = svg.querySelector('.layers'), borders = svg.querySelector('.borders'), top = svg.querySelector('.top');
  const tip = $('k-tip'), box = $('k-box'), tbody = $('k-list').tBodies[0];
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ver = new URL(document.currentScript?.src || location.href).searchParams.get('v') || '';
  const M = await (await fetch('/static/bg-municipalities.json?v=' + ver)).json();
  const num = (v, d = 1) => v == null ? 'няма данни' : v.toLocaleString('bg-BG', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');
  let d = JSON.parse($('k-data').textContent), by = new Map(), sel = null, view, flight = 0, ctrl = null;
  const [vx, vy, vw, vh] = M.view, ASPECT = vw / vh, HOME = { x: vx, y: vy, w: vw, h: vh };
  svg.style.aspectRatio = `${ASPECT}`;
  const setView = (v) => { view = v; svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`); };
  const path = (dd, cls) => { const p = document.createElementNS(NS, 'path'); p.setAttribute('d', dd); if (cls) p.setAttribute('class', cls); return p; };
  const paths = new Map();
  for (const [id, s] of Object.entries(M.shapes)) { const p = path(s.d, 'area'); p.dataset.code = id; paths.set(id, p); layers.append(p); }
  for (const o of Object.values(M.oblasts)) borders.append(path(o.d, 'bgline'));
  function colours(items) {   // one continuous gradient of the accent, linear (a share): 8% for the lowest value, 100% for the highest
    const v = items.map((i) => i.v).filter((x) => x != null), lo = Math.min(...v), hi = Math.max(...v), L = hi - lo || 1;
    return (x) => x == null ? null : `color-mix(in srgb, var(--accent) ${Math.round(8 + (x - lo) / L * 92)}%, #fff)`;
  }
  function paint() {
    const fill = colours(d.items); by = new Map(d.items.map((i) => [i.code, i]));
    for (const [id, p] of paths) { const it = by.get(id); p.style.fill = (it && fill(it.v)) || 'var(--line-2)'; }
    tbody.innerHTML = d.items.slice().sort((a, b) => (b.v ?? -1e9) - (a.v ?? -1e9)).map((it) =>
      `<tr data-code="${it.code}"><td><button type="button" class="map-pick">${esc(it.name)}</button></td><td class="n" data-v="${it.v ?? ''}">${it.v == null ? '<span class="mut">недостатъчно данни</span>' : num(it.v)}</td><td class="n">${it.stores}</td></tr>`).join('');
    $('k-lede').innerHTML = `За кошницата „${esc(d.basket)}“ на <b>${d.y.slice(8)}.${d.y.slice(5, 7)}.${d.y.slice(0, 4)}</b>: ${d.shown} от ${d.total} общини имат достатъчно данни.`;
    $('k-csv').href = d.csv;
    sel = null; top.replaceChildren(); tip.hidden = true;
    const s = document.querySelector('[data-filter="k-list"]'); s && s.dispatchEvent(new Event('input'));
  }
  const select = (code) => {
    sel = sel === code ? null : code; top.replaceChildren(); tbody.querySelectorAll('tr.sel').forEach((t) => t.classList.remove('sel'));
    if (!sel) return;
    const src = paths.get(sel); if (src) for (const c of ['halo', 'line']) top.append(path(src.getAttribute('d'), c));
    const tr = tbody.querySelector(`tr[data-code="${sel}"]`);
    if (tr) { tr.classList.add('sel'); const off = tr.getBoundingClientRect().top - box.getBoundingClientRect().top;
      box.scrollTo({ top: box.scrollTop + off - box.clientHeight / 2 + tr.offsetHeight / 2, behavior: still ? 'auto' : 'smooth' }); }
  };
  function showTip(e, code) {
    const it = by.get(code), r = svg.parentElement.getBoundingClientRect(), s = M.shapes[code];
    tip.innerHTML = `<b>${esc(s ? s.n : code)}</b><br>${it && it.v != null ? num(it.v) + '% спрямо страната · ' + it.stores + ' обекта, ' + it.chains + ' вериги' : esc(it && it.why || 'няма данни')}`;
    tip.hidden = false; const x = e.clientX - r.left, y = e.clientY - r.top;
    tip.style.left = `${Math.max(4, Math.min(x + 14, r.width - tip.offsetWidth - 4))}px`; tip.style.top = `${y + 16 + tip.offsetHeight > r.height ? y - tip.offsetHeight - 10 : y + 16}px`;
  }
  let moved = false;
  svg.addEventListener('pointermove', (e) => { const p = e.target.closest('[data-code]'); p ? showTip(e, p.dataset.code) : (tip.hidden = true); });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; });
  svg.addEventListener('click', (e) => { if (moved) return; const p = e.target.closest('.area'); if (p) select(p.dataset.code); });
  tbody.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-code]'); if (tr) select(tr.dataset.code); });
  tbody.addEventListener('mouseover', (e) => { layers.querySelectorAll('.hov').forEach((p) => p.classList.remove('hov')); const tr = e.target.closest('tr[data-code]'), p = tr && paths.get(tr.dataset.code); if (p) { p.classList.add('hov'); p.parentNode.append(p); } });
  tbody.addEventListener('mouseleave', () => layers.querySelectorAll('.hov').forEach((p) => p.classList.remove('hov')));
  // zoom and move
  const clamp = (v) => { const w = Math.min(HOME.w, Math.max(HOME.w / 40, v.w)), h = w / ASPECT;
    return { x: Math.min(HOME.x + HOME.w - w / 2, Math.max(HOME.x - w / 2, v.x)), y: Math.min(HOME.y + HOME.h - h / 2, Math.max(HOME.y - h / 2, v.y)), w, h }; };
  const toMap = (cx, cy) => { const r = svg.getBoundingClientRect(); return [view.x + ((cx - r.left) / r.width) * view.w, view.y + ((cy - r.top) / r.height) * view.h]; };
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
  function fly(to, ms) { const token = ++flight, from = view, t0 = performance.now();
    return new Promise((done) => { const step = (now) => { if (token !== flight) return done();
      const t = still ? 1 : Math.min(1, (now - t0) / ms), k = ease(t), w = from.w * (to.w / from.w) ** k, h = w / ASPECT;
      setView({ x: from.x + from.w / 2 + (to.x + to.w / 2 - from.x - from.w / 2) * k - w / 2, y: from.y + from.h / 2 + (to.y + to.h / 2 - from.y - from.h / 2) * k - h / 2, w, h });
      t < 1 ? requestAnimationFrame(step) : done(); }; requestAnimationFrame(step); }); }
  const zoomAt = (f, mx, my) => { const w = view.w / f, h = w / ASPECT; setView(clamp({ x: mx - ((mx - view.x) / view.w) * w, y: my - ((my - view.y) / view.h) * h, w, h })); };
  svg.parentElement.querySelector('.zoom').addEventListener('click', (e) => { const z = e.target.closest('button')?.dataset.z; if (!z) return;
    if (z === 'home') return fly(HOME, 500); const cx = view.x + view.w / 2, cy = view.y + view.h / 2, w = view.w / (z === 'in' ? 2 : 0.5), h = w / ASPECT; fly(clamp({ x: cx - w / 2, y: cy - h / 2, w, h }), 350); });
  svg.addEventListener('wheel', (e) => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); ++flight; zoomAt(Math.exp(-e.deltaY * 0.0025), ...toMap(e.clientX, e.clientY)); }, { passive: false });
  svg.addEventListener('dblclick', (e) => { e.preventDefault(); const [mx, my] = toMap(e.clientX, e.clientY), w = view.w / 2, h = w / ASPECT; fly(clamp({ x: mx - ((mx - view.x) / view.w) * w, y: my - ((my - view.y) / view.h) * h, w, h }), 300); });
  let drag = null;
  svg.addEventListener('pointerdown', (e) => { if (e.pointerType === 'touch' || e.button !== 0) return; ++flight; drag = { x: e.clientX, y: e.clientY, v: view }; moved = false; });
  addEventListener('pointermove', (e) => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (!moved && Math.hypot(dx, dy) < 4) return;
    moved = true; svg.classList.add('drag'); tip.hidden = true; const r = svg.getBoundingClientRect(); setView(clamp({ ...drag.v, x: drag.v.x - (dx / r.width) * drag.v.w, y: drag.v.y - (dy / r.height) * drag.v.h })); });
  addEventListener('pointerup', () => { drag = null; svg.classList.remove('drag'); setTimeout(() => { moved = false; }, 0); });
  let pinch = null; const two = (t) => ({ d: Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY), x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 });
  svg.addEventListener('touchstart', (e) => { if (e.touches.length === 2) { e.preventDefault(); ++flight; pinch = { ...two(e.touches), v: view }; } }, { passive: false });
  svg.addEventListener('touchmove', (e) => { if (!pinch || e.touches.length !== 2) return; e.preventDefault();
    const n = two(e.touches), r = svg.getBoundingClientRect(), f = n.d / pinch.d, v = pinch.v, mx = v.x + ((pinch.x - r.left) / r.width) * v.w, my = v.y + ((pinch.y - r.top) / r.height) * v.h, w = v.w / f, h = w / ASPECT;
    setView(clamp({ x: mx - ((pinch.x - r.left) / r.width) * w - ((n.x - pinch.x) / r.width) * w, y: my - ((pinch.y - r.top) / r.height) * h - ((n.y - pinch.y) / r.height) * h, w, h })); }, { passive: false });
  svg.addEventListener('touchend', (e) => { if (e.touches.length < 2) pinch = null; });
  // a change of basket or day: the page's own form; the data are fetched without a reload and an overtaken request is cancelled
  const form = $('filters');
  form && form.addEventListener('submit', async (e) => {
    e.preventDefault(); ctrl?.abort(); ctrl = new AbortController();
    const q = new URLSearchParams(new FormData(form)); q.set('y', q.get('d')); svg.setAttribute('aria-busy', 'true');
    try { const r = await fetch('/api/karta.json?' + q, { signal: ctrl.signal }); if (!r.ok) throw Error(); d = await r.json(); paint(); history.replaceState(null, '', '/karta?' + new URLSearchParams(new FormData(form))); }
    catch (err) { if (err.name !== 'AbortError') $('k-lede').textContent = 'Не успяхме да прочетем данните. Показани са последните заредени стойности.'; }
    svg.removeAttribute('aria-busy');
  });
  setView(HOME); paint();
})().catch(() => { document.getElementById('k-lede').textContent = 'Не успяхме да заредим картата. Данните са в CSV.'; });

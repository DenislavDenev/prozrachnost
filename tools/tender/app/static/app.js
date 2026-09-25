// Тендер: formatting, charts, timeline and the network view. Pages pass data in <script type="application/json" id="data">.
const ROLE = { manager: 'управител', sole_owner: 'едноличен собственик', partner: 'съдружник', representative: 'представител',
  board_of_directors: 'член на СД', management_board: 'член на УС', supervisory_board: 'член на НС', procurator: 'прокурист',
  liquidator: 'ликвидатор', trader: 'едноличен търговец', chair: 'председател', governing_body: 'член на орган',
  controlling_board: 'член на КС', branch_manager: 'управител на клон', trustee: 'синдик', board_of_trustees: 'член на настоятелство',
  verification_commission: 'член на проверителна комисия' };
const OWN = new Set(['partner', 'sole_owner', 'trader']);
const nf = new Intl.NumberFormat('bg-BG', { maximumFractionDigits: 0 });
const eur = (v) => v == null ? '—' : nf.format(v) + ' €';
const unit = (v, d, u) => (v / d).toLocaleString('bg-BG', { maximumFractionDigits: 1 }) + u;
const big = (v) => v == null ? '—' : v >= 1e9 ? unit(v, 1e9, ' млрд. €') : v >= 1e6 ? unit(v, 1e6, ' млн. €') : eur(v);
const short = (v) => v >= 1e9 ? unit(v, 1e9, ' млрд.') : v >= 1e6 ? unit(v, 1e6, ' млн.') : v >= 1e3 ? Math.round(v / 1e3) + ' хил.' : nf.format(v);
const yr = (s) => s ? +String(s).slice(0, 4) : null;
const span = (r) => r.valid_to ? `${yr(r.valid_from)}–${yr(r.valid_to)}` : r.uncertain_after ? `${yr(r.valid_from)}–?` : `от ${yr(r.valid_from)}`;
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
const tc = (s) => (s || '').toLowerCase().replace(/(^|[\s\-"„(])(\p{L})/gu, (m, a, b) => a + b.toUpperCase())
  .replace(/(^|\s)(Ад|Оод|Еоод|Еад|Ет|Дп|Дззд|Кд)(?=$|\s)/gu, (m) => m.toUpperCase());
const $ = (id) => document.getElementById(id);
const DATA = (() => { const el = $('data'); return el ? JSON.parse(el.textContent) : {}; })();
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function bars(el, rows, key, label, W = 720, H = 250) {
  if (!rows.length) { el.outerHTML = '<p class="mut">Няма договори с дата.</p>'; return; }
  const pad = { t: 28, b: 30 }, max = Math.max(...rows.map((r) => +r[key] || 0)) || 1, bw = W / rows.length, rx = cssVar('--bar-r');
  let o = '';
  for (let k = 1; k <= 3; k++) { const y = pad.t + ((H - pad.t - pad.b) * k) / 3; o += `<line class="gl" x1="0" x2="${W}" y1="${y}" y2="${y}"/>`; }
  rows.forEach((r, i) => {
    const h = ((H - pad.t - pad.b) * (+r[key] || 0)) / max, w = Math.min(bw * .6, 90), x = i * bw + (bw - w) / 2, t = H - pad.b - h;
    o += `<rect class="bar" x="${x}" y="${t}" width="${w}" height="${Math.max(h, 2)}" rx="${rx}"><title>${r.y}: ${nf.format(r.n)} договора, ${eur(r.eur)}</title></rect>`;
    o += `<text class="v" x="${x + w / 2}" y="${t - 8}" text-anchor="middle">${label(r)}</text><text x="${x + w / 2}" y="${H - 8}" text-anchor="middle">${r.y}</text>`;
  });
  el.innerHTML = o;
}
function barTabs(tabs, el, rows) {
  const draw = (k) => bars(el, rows, k, (r) => k === 'eur' ? short(+r.eur || 0) : nf.format(r.n));
  draw('eur');
  tabs.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; tabs.querySelectorAll('button').forEach((x) => x.setAttribute('aria-pressed', x === b)); draw(b.dataset.k); };
}
function line(el, rows, key, ref, W = 400, H = 250) {
  const pad = { t: 20, b: 30, l: 34 };
  const X = (i) => pad.l + ((W - pad.l - 10) * (i + .5)) / rows.length, Y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / 100);
  let o = '';
  for (const v of [0, 25, 50, 75, 100]) o += `<line class="gl" x1="${pad.l}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}"/><text x="0" y="${Y(v) + 4}">${v}%</text>`;
  if (ref != null) o += `<line class="ref" x1="${pad.l}" x2="${W}" y1="${Y(ref)}" y2="${Y(ref)}"/>`;
  const pts = rows.filter((r) => r[key] != null);
  o += `<path class="ln" d="${rows.map((r, i) => r[key] == null ? '' : (i && rows[i - 1][key] != null ? 'L' : 'M') + X(i) + ',' + Y(+r[key])).join(' ')}"/>`;
  rows.forEach((r, i) => { if (r[key] == null) return; o += `<circle class="dotp" cx="${X(i)}" cy="${Y(+r[key])}" r="4.5"/><text class="v" x="${X(i)}" y="${Y(+r[key]) - 12}" text-anchor="middle">${(+r[key]).toLocaleString('bg-BG')}%</text>`; });
  rows.forEach((r, i) => { o += `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${r.y}</text>`; });
  el.innerHTML = pts.length ? o : '';
}
function hbars(el, rows, { name, val, right, href, marker }) {
  const max = Math.max(...rows.map((r) => +val(r) || 0)) || 1;
  el.innerHTML = rows.map((r) => `<div class="r"><a class="nm u" href="${href ? href(r) : '#'}">${esc(name(r))}</a><span class="val">${right(r)}</span>
    <div class="tr"><i style="width:${((+val(r) || 0) / max) * 100}%"></i>${marker && marker(r) != null ? `<s style="left:${marker(r)}%" title="${marker(r)}%"></s>` : ''}</div></div>`).join('');
}
function offersStack(stack, key, offers) {
  const off = Object.fromEntries(offers.map((o) => [o.k, o.n]));
  const OF = [['1', '1 оферта', 'var(--amber)'], ['2', '2 оферти', '#9cc4b5'], ['3', '3 оферти', '#55a78b'], ['4+', '4 и повече', 'var(--accent)'], ['unknown', 'Неизвестно', 'var(--line)']];
  stack.innerHTML = OF.map(([k, , c]) => `<i style="flex:${off[k] || 0};background:${c}" title="${k}"></i>`).join('');
  key.innerHTML = OF.map(([k, l, c]) => `<div><em style="background:${c}"></em>${l}<b>${nf.format(off[k] || 0)}</b></div>`).join('');
}
function timeline(el, groups, Y0 = 2008, Y1 = 2027) {
  if (!groups.length) { el.innerHTML = '<p class="mut" style="grid-column:1/-1">Няма прочетени роли в Търговския регистър.</p>'; return; }
  Y0 = Math.min(Y0, ...groups.flatMap((g) => g.roles.map((r) => yr(r.valid_from))));
  const X = (d) => ((yr(d) + (+String(d).slice(5, 7) - 1) / 12 - Y0) / (Y1 - Y0)) * 100;
  const ticks = []; for (let y = Y0; y < Y1; y++) if (y % 2 === 0) ticks.push(y);
  let o = `<div></div><div class="ax">${ticks.filter((y) => y % 4 === 0).map((y) => `<span style="left:${((y - Y0) / (Y1 - Y0)) * 100}%">${y}</span>`).join('')}</div>`;
  const now = new Date().getFullYear() + new Date().getMonth() / 12;
  for (const g of groups) {
    o += `<div class="who"><a class="u" href="${g.href || '#'}">${esc(g.label)}</a><small>${g.sub || ''}</small></div><div class="lane" style="height:${14 + g.roles.length * 28}px">
      ${ticks.map((y) => `<u style="left:${((y - Y0) / (Y1 - Y0)) * 100}%"></u>`).join('')}
      ${g.roles.map((r, i) => { const a = X(r.valid_from), b = r.valid_to ? X(r.valid_to) : ((now - Y0) / (Y1 - Y0)) * 100;
        return `<div class="b ${OWN.has(r.role) ? 'own' : 'mg'} ${r.valid_to ? 'end' : ''}" style="left:${Math.min(a, 88)}%;width:${Math.max(b - a, 12)}%;top:${8 + i * 28}px" title="${ROLE[r.role] || r.role} ${span(r)}">${esc(g.text ? g.text(r) : ROLE[r.role] || r.role)}</div>`; }).join('')}</div>`;
  }
  el.innerHTML = o;
}

// ---------- network ----------
function radial(nodes, edges, focus, W, H) {
  const adj = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) { adj.get(e.a)?.push(e.b); adj.get(e.b)?.push(e.a); }
  const pos = new Map(), cx = W / 2, cy = H / 2;
  if (!adj.has(focus)) return pos;
  pos.set(focus, { x: cx, y: cy });
  const ring1 = [...new Set(adj.get(focus))], seen = new Set([focus, ...ring1]), kids = new Map();
  for (const n of ring1) { kids.set(n, []); for (const m of adj.get(n)) if (!seen.has(m)) { seen.add(m); kids.get(n).push(m); } }
  const wgt = (n) => Math.max(1.4, kids.get(n).length), tot = ring1.reduce((a, n) => a + wgt(n), 0) || 1;
  let a0 = -Math.PI / 2 - (ring1.length ? Math.PI * wgt(ring1[0]) / tot : 0);
  for (const n of ring1) {
    const sec = (2 * Math.PI * wgt(n)) / tot, mid = a0 + sec / 2;
    pos.set(n, { x: cx + W * .21 * Math.cos(mid), y: cy + H * .25 * Math.sin(mid) });
    kids.get(n).forEach((m, j, arr) => { const a = a0 + sec * (j + .5) / arr.length, r = arr.length > 3 && j % 2 ? .41 : .36;
      pos.set(m, { x: cx + W * r * Math.cos(a), y: cy + H * (r + .06) * Math.sin(a) }); });
    a0 += sec;
  }
  // anything further away than two steps goes on an outer ring
  const rest = nodes.filter((n) => !pos.has(n.id));
  rest.forEach((n, i) => { const a = (2 * Math.PI * i) / rest.length; pos.set(n.id, { x: cx + W * .46 * Math.cos(a), y: cy + H * .46 * Math.sin(a) }); });
  return pos;
}
function network(host, focus) {
  const svg = host.querySelector('.canvas svg'), g = svg.querySelector('g'), det = host.querySelector('.det'), note = host.querySelector('.cap');
  // phones get a portrait canvas so node labels stay readable instead of shrinking a landscape one
  const narrow = svg.parentElement.clientWidth < 700, W = narrow ? 560 : 980, H = narrow ? 900 : 600;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const st = { view: 'all', at: null, sel: null, data: null };
  async function load() {
    const q = new URLSearchParams({ focus, view: st.view }); if (st.at) q.set('at', st.at + '-07-01');
    const r = await fetch('/network.json?' + q); st.data = await r.json(); draw();
  }
  function draw() {
    const d = st.data; if (!d) return;
    const nodes = new Map(), edges = new Map();
    for (const e of d.edges) {
      if (!nodes.has(e.holder)) nodes.set(e.holder, { id: e.holder, kind: e.holder_kind === 'person' ? 'p' : 'c', name: tc(e.holder_name), ref: e.holder_ref, n: e.holder_contracts });
      if (!nodes.has(e.company)) nodes.set(e.company, { id: e.company, kind: 'c', name: tc(e.company_name || 'ЕИК ' + e.company.slice(2)), ref: e.company_ref, n: e.company_contracts });
      const k = e.holder + '|' + e.company; if (!edges.has(k)) edges.set(k, { a: e.holder, b: e.company, roles: [] }); edges.get(k).roles.push(e);
    }
    const N = [...nodes.values()], E = [...edges.values()];
    if (!N.length) { g.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" class="mt">Няма регистърни връзки за избрания изглед и година.</text>`; det.classList.remove('on'); return; }
    const pos = radial(N, E, focus, W, H);
    const dims = (n) => { const nm = n.name.length > 24 ? n.name.slice(0, 23) + '…' : n.name; return { nm, w: Math.max(116, nm.length * 7.6 + 38), h: n.kind === 'c' ? 44 : 32 }; };
    const box = new Map(N.filter((n) => pos.has(n.id)).map((n) => [n.id, dims(n)]));
    const M = 14, clamp = () => { for (const [id, b] of box) { const p = pos.get(id);
      p.x = Math.min(W - b.w / 2 - M, Math.max(b.w / 2 + M, p.x)); p.y = Math.min(H - b.h / 2 - M, Math.max(b.h / 2 + M, p.y)); } };
    clamp();
    const ids = [...box.keys()];
    for (let it = 0; it < 80; it++) {
      let moved = false;
      for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
        const a = pos.get(ids[i]), b = pos.get(ids[j]), A = box.get(ids[i]), B = box.get(ids[j]);
        const ox = (A.w + B.w) / 2 + 10 - Math.abs(a.x - b.x), oy = (A.h + B.h) / 2 + 8 - Math.abs(a.y - b.y);
        if (ox > 0 && oy > 0) { moved = true; const fa = ids[i] === focus ? 0 : ids[j] === focus ? 1 : .5, fb = 1 - fa;
          if (oy < ox) { const dd = (a.y <= b.y ? -1 : 1) * oy; a.y += dd * fa; b.y -= dd * fb; } else { const dd = (a.x <= b.x ? -1 : 1) * ox; a.x += dd * fa; b.x -= dd * fb; } }
      }
      clamp(); if (!moved) break;
    }
    const hot = new Set(st.sel ? [st.sel] : []);
    let es = '', ls = '', ns = '';
    for (const e of E) {
      const a = pos.get(e.a), b = pos.get(e.b); if (!a || !b) continue;
      const own = e.roles.some((r) => OWN.has(r.role)), open = e.roles.some((r) => !r.valid_to && !r.uncertain_after);
      const on = st.sel && (e.a === st.sel || e.b === st.sel); if (on) { hot.add(e.a); hot.add(e.b); }
      const mx = (a.x + b.x) / 2 - (b.y - a.y) * .1, my = (a.y + b.y) / 2 + (b.x - a.x) * .1;
      es += `<path class="ge ${own ? 'own' : ''} ${open ? '' : 'end'} ${on ? 'hot' : ''}" d="M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}"/>`;
      if (on) { const t = e.roles.map((r) => (ROLE[r.role] || r.role) + ' ' + span(r)).join(', '), w = Math.min(t.length * 6 + 18, 270), lx = (a.x + 2 * mx + b.x) / 4, ly = (a.y + 2 * my + b.y) / 4;
        ls += `<g class="gl-l"><rect x="${lx - w / 2}" y="${ly - 11}" width="${w}" height="22" rx="4"/><text x="${lx}" y="${ly + 4}" text-anchor="middle">${esc(t.length > 46 ? t.slice(0, 44) + '…' : t)}</text></g>`; }
    }
    for (const n of N) {
      const p = pos.get(n.id); if (!p) continue; const { nm, w, h } = box.get(n.id);
      const rx = n.kind === 'p' ? h / 2 : cssVar('--radius');
      ns += `<g class="gn ${n.id === focus ? 'focus' : ''} ${st.sel === n.id ? 'sel' : ''} ${hot.has(n.id) ? 'hot' : ''}" data-id="${n.id}" tabindex="0" role="button" aria-label="${esc(n.name)}">
        <rect class="cd" x="${p.x - w / 2}" y="${p.y - h / 2}" width="${w}" height="${h}" rx="${rx}"/>
        ${n.kind === 'c' ? `<rect class="mk-c ${n.n ? '' : 'no'}" x="${p.x - w / 2 + 12}" y="${p.y - 5}" width="10" height="10" rx="2"/>
          <text class="nm" x="${p.x - w / 2 + 30}" y="${p.y - 1}">${esc(nm)}</text><text class="mt" x="${p.x - w / 2 + 30}" y="${p.y + 14}">${n.n ? nf.format(n.n) + ' договора' : 'ЕИК ' + n.id.slice(2)}</text>`
        : `<circle class="mk-p" cx="${p.x - w / 2 + 16}" cy="${p.y}" r="5"/><text class="nm" x="${p.x - w / 2 + 29}" y="${p.y + 4.5}">${esc(nm)}</text>`}</g>`;
    }
    g.innerHTML = es + ls + ns; svg.classList.toggle('dim', !!st.sel);
    if (note) note.textContent = d.capped ? `Показани са първите ${d.nodes} възела; мрежата е по-голяма.` : d.beyond ? 'Има още връзки на повече от 2 стъпки.' : '';
    const n = st.sel && nodes.get(st.sel);
    if (n) {
      const rel = E.filter((e) => e.a === st.sel || e.b === st.sel);
      const href = n.ref ? (n.kind === 'p' ? '/persons/' + n.ref : '/companies/' + n.ref) : null;
      det.innerHTML = `<h3>${esc(n.name)}</h3><div class="mut">${n.kind === 'p' ? 'Лице' : 'ЕИК ' + n.id.slice(2) + (n.n ? ' · ' + nf.format(n.n) + ' договора' : '')}</div>
        <ul>${rel.flatMap((e) => e.roles.map((r) => `<li><span>${esc((nodes.get(e.a === st.sel ? e.b : e.a) || {}).name)} · ${ROLE[r.role] || r.role}</span><span>${span(r)}</span></li>`)).join('')}</ul>
        ${href ? `<a class="btn" href="${href}" style="width:100%;justify-content:center;height:34px">Отвори профила</a>` : ''}`;
      det.classList.add('on');
    } else det.classList.remove('on');
  }
  g.addEventListener('click', (e) => { const n = e.target.closest('.gn'); st.sel = n && st.sel !== n.dataset.id ? n.dataset.id : null; draw(); });
  g.addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.closest('.gn')?.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  host.querySelectorAll('[data-view]').forEach((b) => b.onclick = () => { host.querySelectorAll('[data-view]').forEach((x) => x.setAttribute('aria-pressed', x === b)); st.view = b.dataset.view; st.sel = null; load(); });
  const at = host.querySelector('.yr input'), on = host.querySelector('.chk input'), out = host.querySelector('.yr output');
  const set = () => { at.disabled = !on.checked; st.at = on.checked ? +at.value : null; out.textContent = st.at ?? '—'; load(); };
  at.onchange = set; at.oninput = () => { out.textContent = at.value; }; on.onchange = set;
  load();
}

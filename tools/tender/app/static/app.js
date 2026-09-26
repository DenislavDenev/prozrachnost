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
const pct = (v) => v == null ? '—' : (+v).toLocaleString('bg-BG') + '%';
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
// share(r): percent of this row's bar (e.g. single-bid share), drawn inside the bar
function hbars(el, rows, { name, val, right, href, share }) {
  const max = Math.max(...rows.map((r) => +val(r) || 0)) || 1;
  el.innerHTML = rows.map((r) => `<div class="r"><a class="nm u" href="${href ? href(r) : '#'}">${esc(name(r))}</a><span class="val">${right(r)}</span>
    <div class="tr"><i style="width:${((+val(r) || 0) / max) * 100}%">${share && share(r) != null ? `<b style="width:${share(r)}%"></b>` : ''}</i></div></div>`).join('');
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

// list filters fold away on phones unless a filter is set
if (matchMedia('(max-width: 720px)').matches && !/[?&](?!sort=|dir=|offset=)[^=&]+=[^&]/.test(location.search))
  document.querySelectorAll('details.filt').forEach((d) => { d.open = false; });

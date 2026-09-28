// Парламент: the hall (a seat per MP, the groups in the Assembly's order, each a wedge under an arc in its party's
// colour) and the MP's strip of votes. In a vote each seat is the party's colour with a badge in the vote's colour
// (+ − =), faded without a badge when the MP did not vote; the seats come in from left to right while the counts run
// up, "намалено движение" shows it at once.
const VOTE = { '+': '#0b7a5e', '-': '#b0413e', '=': '#6b7a8c', '0': '#d5d9de' };
const VNAME = { '+': 'за', '-': 'против', '=': 'въздържал се', '0': 'не гласувал' };
const ORDER = ['+', '=', '-', '0'];
// a group's line is a side: + for, - not for (against or abstained; ingest/stats.py); a vote against it is on the other side
const LINE = { '+': 'за', '-': 'не подкрепя' };
const against = (r) => r[4] && '+-='.includes(r[3]) && (r[3] === '+') !== (r[4] === '+');
const EMPTY = '#eceef1';
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt = (v) => v.toLocaleString('bg-BG');
const NS = 'http://www.w3.org/2000/svg';

// rows of seats on half circles, each row with seats in proportion to its length; read left to right by angle,
// the seats of one group make a wedge
function layout(n) {
  const rows = n <= 40 ? 2 : n <= 90 ? 4 : n <= 160 ? 6 : 8;
  const r0 = rows === 2 ? 0.55 : 0.36;
  const radii = [...Array(rows)].map((_, k) => r0 + (1 - r0) * k / Math.max(1, rows - 1));
  const sum = radii.reduce((a, b) => a + b, 0);
  const per = radii.map((r) => Math.max(1, Math.round(n * r / sum)));
  per[rows - 1] += n - per.reduce((a, b) => a + b, 0);
  const seats = [];
  let gap = (1 - r0) / Math.max(1, rows - 1);
  radii.forEach((r, k) => {
    const m = per[k];
    if (m > 1) gap = Math.min(gap, Math.PI * r / (m - 1));
    for (let j = 0; j < m; j++) {
      const a = Math.PI * (m === 1 ? 0.5 : 1 - j / (m - 1));
      seats.push({ a, r, x: r * Math.cos(a), y: r * Math.sin(a) });
    }
  });
  seats.sort((p, q) => q.a - p.a || p.r - q.r);
  return { seats, size: gap * 0.42 };
}

const make = (tag, attrs, parent) => {
  const e = document.createElementNS(NS, tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  if (parent) parent.append(e);
  return e;
};

// wedges: [[first seat, last seat, code, colour]]: an arc in the group's colour over its seats and its code beyond;
// draw(g, i, x, y, size): what seat i is, in a <g class="seat">; without it a seat is a circle
function svgHall(el, n, wedges = [], draw = null) {
  const { seats, size } = layout(n);
  const pad = size * 1.3, top = wedges.length ? 0.2 : pad, side = wedges.length ? 0.46 : pad;   // room for "ВЪЗРАЖДАНЕ" at the side
  const svg = make('svg', { viewBox: `${-1 - side} ${-1 - top} ${2 + 2 * side} ${1 + top + pad}` });
  const circles = seats.map((s, i) => {
    if (!draw) return make('circle', { cx: s.x.toFixed(4), cy: (-s.y).toFixed(4), r: size.toFixed(4), fill: EMPTY }, svg);
    const g = make('g', { class: 'seat' }, svg);
    draw(g, i, s.x, -s.y, size);
    return g;
  });
  const R = 1 + pad + 0.035, step = Math.PI / Math.max(1, n) * 0.9;
  const pt = (t, r) => `${(r * Math.cos(t)).toFixed(4)} ${(-r * Math.sin(t)).toFixed(4)}`;
  wedges.forEach(([a, b, code, color]) => {
    const a0 = Math.min(Math.PI, seats[a].a + step), a1 = Math.max(0, seats[b].a - step);
    make('path', { d: `M ${pt(a0, R)} A ${R} ${R} 0 0 1 ${pt(a1, R)}`, stroke: color, 'stroke-width': '0.035', fill: 'none' }, svg);
    const mid = (a0 + a1) / 2, r = R + 0.06;
    const t = make('text', { x: (r * Math.cos(mid)).toFixed(3), y: (-(r * Math.sin(mid))).toFixed(3), 'font-size': '0.05', class: 'glabel',
      'text-anchor': Math.cos(mid) > 0.3 ? 'start' : Math.cos(mid) < -0.3 ? 'end' : 'middle', fill: color }, svg);
    t.textContent = code;
  });
  const num = make('text', { class: 'center', 'text-anchor': 'middle', y: '-0.1', 'font-size': '0.15' }, svg);
  const what = make('text', { class: 'center-t', 'text-anchor': 'middle', y: '-0.02', 'font-size': '0.052' }, svg);
  el.prepend(svg);
  return { circles, center: (a, b) => { num.textContent = a; what.textContent = b; } };
}

function tip(el) {
  let t = el.querySelector('.tip');
  if (!t) { t = document.createElement('div'); t.className = 'tip'; t.hidden = true; el.append(t); }
  return {
    show(ev, html) {
      t.innerHTML = html; t.hidden = false;
      const b = el.getBoundingClientRect(), w = t.offsetWidth;
      t.style.left = `${Math.min(b.width - w, Math.max(0, ev.clientX - b.left + 12))}px`;
      t.style.top = `${ev.clientY - b.top + 14}px`;
    },
    hide() { t.hidden = true; },
  };
}

// the fill runs left to right in about 1.2 s (without colours the seats come in); the counts run with it
function play(circles, colors, counts) {
  const n = circles.length;
  const show = colors ? (c, i) => c.setAttribute('fill', colors[i]) : (c) => { c.style.opacity = ''; };
  circles.forEach((c) => { c.style.transition = 'none'; if (colors) c.setAttribute('fill', EMPTY); else c.style.opacity = 0; });
  if (still) { circles.forEach(show); counts.forEach(([e, v]) => { e.textContent = fmt(v); }); return; }
  void circles[0]?.getBoundingClientRect();
  circles.forEach((c, i) => { c.style.transition = ''; c.style.transitionDelay = `${(i / n) * 1.2}s`; show(c, i); });
  const t0 = performance.now(), D = 1500;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / D), e = 1 - (1 - k) ** 3;
    counts.forEach(([el, v]) => { el.textContent = fmt(Math.round(v * e)); });
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function whenSeen(el, fn) {
  if (!('IntersectionObserver' in window)) { fn(); return; }
  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); fn(); } }, { threshold: 0.12 });
  io.observe(el);
}

// the seats of each group in a row: [[first, last, code, colour]]
function wedgesOf(list, groups) {
  const by = new Map(groups.map((g) => [g.grp, g]));
  const out = [];
  list.forEach((s, i) => {
    if (!i || list[i - 1].grp !== s.grp) out.push([i, i, s.grp, by.get(s.grp)?.color || '#9aa1aa']);
    else out[out.length - 1][1] = i;
  });
  return out;
}

// data-vote="/api/glasuvane/<sitting>/<no>.json": the vote in the hall
async function voteHall(el) {
  const j = await (await fetch(el.dataset.vote)).json();
  const rank = new Map(j.groups.map((g, i) => [g.grp, i]));
  const names = new Map(j.groups.map((g) => [g.grp, g.name]));
  const seats = [...j.seats].sort((a, b) => (rank.get(a.grp) - rank.get(b.grp)) || (ORDER.indexOf(a.code) - ORDER.indexOf(b.code)) || a.name.localeCompare(b.name, 'bg'));
  const color = new Map(j.groups.map((g) => [g.grp, g.color]));
  const { circles, center } = svgHall(el, seats.length, wedgesOf(seats, j.groups),
    (g, i, x, y, size) => seat(g, color.get(seats[i].grp) || '#9aa1aa', seats[i].code, x, y, size));
  center(fmt(j.totals.voted), 'гласували');
  const tp = tip(el);
  circles.forEach((c, i) => {
    const s = seats[i];
    c.setAttribute('aria-label', `${s.name}, ${s.grp}: ${VNAME[s.code]}`);
    c.addEventListener('mousemove', (ev) => tp.show(ev, `<b>${s.name}</b><br>${names.get(s.grp) || s.grp}<br>${VNAME[s.code]}`));
    c.addEventListener('mouseleave', () => tp.hide());
    c.addEventListener('click', () => { location.href = `/deputati/${j.assembly}/${s.mp}`; });
  });
  const box = el.parentElement;
  const counts = [['yes', j.totals.yes], ['no', j.totals.no], ['abs', j.totals.abstain], ['none', seats.length - j.totals.voted]]
    .map(([k, v]) => [box.querySelector(`.counts .${k} .n`), v]).filter(([e]) => e);
  whenSeen(el, () => play(circles, null, counts));
  box.querySelector('.replay')?.addEventListener('click', () => play(circles, null, counts));
  // a count pressed keeps only its MPs in sight; pressed again, all
  const bar = box.querySelector('.counts');
  bar?.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-c]');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    bar.querySelectorAll('button[data-c]').forEach((x) => x.setAttribute('aria-pressed', on && x === b));
    bar.classList.toggle('on', on);
    circles.forEach((c, i) => { c.style.transitionDelay = '0s'; c.classList.toggle('dim', on && seats[i].code !== b.dataset.c); });
  });
}

// a seat of a vote: a circle in the party's colour and a badge in the vote's colour with its sign; not voted, the
// circle faded and no badge
function seat(g, color, code, x, y, s) {
  make('circle', { cx: x, cy: y, r: s * 0.95, fill: color, 'fill-opacity': code === '0' ? 0.25 : 1 }, g);
  if (code === '0') return;
  const bx = x + s * 0.6, by = y + s * 0.6, br = s * 0.5;
  make('circle', { cx: bx, cy: by, r: br, fill: VOTE[code], stroke: '#fff', 'stroke-width': s * 0.12 }, g);
  if (code === '=') { make('rect', { x: bx - br * 0.45, y: by - br * 0.1, width: br * 0.9, height: br * 0.2, fill: '#fff' }, g); return; }
  make('text', { x: bx, y: by + br * 0.4, 'font-size': br * 1.1, 'text-anchor': 'middle', fill: '#fff', 'font-weight': 700 }, g)
    .textContent = code === '+' ? '+' : '−';
}

// data-hall="/api/zala/<assembly>.json": the groups of the assembly, each in its party's colour
async function groupHall(el) {
  const j = await (await fetch(el.dataset.hall)).json();
  const n = j.groups.reduce((a, g) => a + g.n, 0);
  if (!n) return;
  const list = j.groups.flatMap((g) => Array(g.n).fill(g));
  const { circles, center } = svgHall(el, n, wedgesOf(list, j.groups));
  center(fmt(n), 'депутати');
  const tp = tip(el);
  circles.forEach((c, i) => {
    const g = list[i];
    c.style.cursor = 'default';
    c.addEventListener('mousemove', (ev) => tp.show(ev, `<b>${g.name}</b><br>${fmt(g.n)} депутати`));
    c.addEventListener('mouseleave', () => tp.hide());
  });
  const leg = el.parentElement.querySelector('.glegend');
  if (leg) leg.innerHTML = j.groups.map((g) => `<span><i style="background:${g.color}"></i>${g.name} · <b>${g.n}</b></span>`).join('')
    + (j.date ? `<span class="mut">към ${j.date.split('-').reverse().join('.')}</span>` : '');
  whenSeen(el, () => play(circles, list.map((g) => g.color), []));
}

// data-strip="/api/deputat/<ns>/<mp>.json": every vote of the MP in time, a thin column each; a mark above the
// votes against the group's line
async function strip(el) {
  const j = await (await fetch(el.dataset.strip)).json();
  const v = j.votes;
  if (!v.length) return;
  const cv = el.querySelector('canvas'), tp = tip(el);
  const draw = () => {
    const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const x = cv.getContext('2d'); x.scale(dpr, dpr); x.clearRect(0, 0, W, H);
    const w = W / v.length;
    v.forEach((r, i) => {
      x.fillStyle = VOTE[r[3]] || EMPTY; x.fillRect(i * w, 14, Math.max(w, 0.6), H - 14);
      if (against(r)) { x.fillStyle = '#121417'; x.fillRect(i * w, 2, Math.max(w, 1.5), 8); }
    });
  };
  draw();
  addEventListener('resize', draw);
  const at = (ev) => Math.min(v.length - 1, Math.max(0, Math.floor((ev.clientX - cv.getBoundingClientRect().left) / cv.clientWidth * v.length)));
  cv.addEventListener('mousemove', (ev) => {
    const r = v[at(ev)], d = r[2].split('-').reverse().join('.');
    const other = against(r) ? `<br><span class="k-no">групата: ${LINE[r[4]]}</span>` : '';
    tp.show(ev, `${d}<br><b>${r[5]}</b><br>${VNAME[r[3]]}${other}`);
  });
  cv.addEventListener('mouseleave', () => tp.hide());
  cv.addEventListener('click', (ev) => { const r = v[at(ev)]; location.href = `/glasuvane/${r[0]}/${r[1]}`; });
  const ax = el.querySelector('.axis');
  if (ax) ax.innerHTML = `<span>${v[0][2].split('-').reverse().join('.')}</span><span>${v[v.length - 1][2].split('-').reverse().join('.')}</span>`;
  // the share of votes the MP took part in, by month
  const m = el.parentElement.querySelector('.chart[data-months]');
  if (m && window.echarts) {
    const by = new Map();
    v.forEach((r) => { const k = r[2].slice(0, 7), c = by.get(k) || [0, 0]; c[1] += 1; if ('+-='.includes(r[3])) c[0] += 1; by.set(k, c); });
    const months = [...by.keys()];
    const ch = echarts.init(m, null, { renderer: 'svg' });
    ch.setOption({
      animation: !still, textStyle: { fontFamily: 'Sofia Sans, sans-serif' },
      grid: { left: 8, right: 12, top: 12, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', confine: true, formatter: (it) => { const c = by.get(it[0].axisValue);
        return `<b>${it[0].axisValue.split('-').reverse().join('.')}</b><br>участвал в ${c[0]} от ${c[1]} гласувания (${Math.round(100 * c[0] / c[1])}%)`; } },
      xAxis: { type: 'category', data: months, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e1e4e8' } },
        axisLabel: { color: '#69707a', formatter: (t) => t.split('-').reverse().join('.') } },
      yAxis: { type: 'value', max: 100, splitLine: { lineStyle: { color: '#eceef1' } }, axisLabel: { color: '#69707a', formatter: '{value}%' } },
      series: [{ type: 'bar', data: months.map((k) => Math.round(1000 * by.get(k)[0] / by.get(k)[1]) / 10), itemStyle: { color: m.dataset.color || '#0b7a5e', borderRadius: [2, 2, 0, 0] }, barMaxWidth: 18 }],
    });
    addEventListener('resize', () => ch.resize());
  }
}

document.querySelectorAll('.hall[data-vote]').forEach(voteHall);
document.querySelectorAll('.hall[data-hall]').forEach(groupHall);
document.querySelectorAll('.strip[data-strip]').forEach(strip);

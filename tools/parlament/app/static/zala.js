// Парламент: the hall (a seat per MP, groups in the Assembly's order, each a wedge) and the MP's strip of votes.
// A vote fills the hall seat by seat from left to right while the counts run up; "намалено движение" shows it at once.
const VOTE = { '+': '#0b7a5e', '-': '#b0413e', '=': '#6b7a8c', '0': '#d5d9de' };
const VNAME = { '+': 'за', '-': 'против', '=': 'въздържал се', '0': 'не гласувал' };
const ORDER = ['+', '=', '-', '0'];
// the groups in quiet tones, never a party's colour: the hall shows who sits where, the vote shows how they voted
const TONES = ['#2f3b4a', '#9aa4b0', '#56637a', '#c5cbd3', '#434e5e', '#7d8896', '#b0b8c2', '#687587'];
const EMPTY = '#eceef1';
const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt = (v) => v.toLocaleString('bg-BG');

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

function svgHall(el, n) {
  const { seats, size } = layout(n);
  const pad = size * 1.3, W = 2 + 2 * pad, H = 1 + 2 * pad;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `${-1 - pad} ${-1 - pad} ${W} ${H}`);
  const circles = seats.map((s) => {
    const c = document.createElementNS(ns, 'circle');
    c.setAttribute('cx', s.x.toFixed(4)); c.setAttribute('cy', (-s.y).toFixed(4)); c.setAttribute('r', size.toFixed(4));
    c.setAttribute('fill', EMPTY); c.setAttribute('vector-effect', 'non-scaling-stroke');
    svg.append(c);
    return c;
  });
  const center = document.createElementNS(ns, 'text');
  center.setAttribute('class', 'center'); center.setAttribute('text-anchor', 'middle'); center.setAttribute('y', '-0.04');
  center.setAttribute('font-size', '0.2');
  svg.append(center);
  el.prepend(svg);
  return { svg, circles, center };
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

// the fill runs left to right in about 1.2 s; the counts run with it
function play(circles, colors, counts) {
  const n = circles.length;
  circles.forEach((c) => { c.style.transition = 'none'; c.setAttribute('fill', EMPTY); });
  if (still) { circles.forEach((c, i) => c.setAttribute('fill', colors[i])); counts.forEach(([e, v]) => { e.textContent = fmt(v); }); return; }
  void circles[0]?.getBoundingClientRect();
  circles.forEach((c, i) => { c.style.transition = ''; c.style.transitionDelay = `${(i / n) * 1.2}s`; c.setAttribute('fill', colors[i]); });
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
  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { io.disconnect(); fn(); } }, { threshold: 0.3 });
  io.observe(el);
}

// data-vote="/api/glasuvane/<sitting>/<no>.json": the vote in the hall
async function voteHall(el) {
  const j = await (await fetch(el.dataset.vote)).json();
  const rank = new Map(j.groups.map((g, i) => [g, i]));
  const seats = [...j.seats].sort((a, b) => (rank.get(a.grp) - rank.get(b.grp)) || (ORDER.indexOf(a.code) - ORDER.indexOf(b.code)) || a.name.localeCompare(b.name, 'bg'));
  const { circles, center } = svgHall(el, seats.length);
  center.textContent = `${fmt(j.totals.voted)} гласували`;
  const tp = tip(el);
  circles.forEach((c, i) => {
    const s = seats[i];
    c.setAttribute('aria-label', `${s.name}, ${s.grp}: ${VNAME[s.code]}`);
    c.addEventListener('mousemove', (ev) => tp.show(ev, `<b>${s.name}</b><br>${s.grp} · ${VNAME[s.code]}`));
    c.addEventListener('mouseleave', () => tp.hide());
    c.addEventListener('click', () => { location.href = `/deputati/${j.assembly}/${s.mp}`; });
  });
  const box = el.parentElement;
  const counts = [['yes', j.totals.yes], ['no', j.totals.no], ['abs', j.totals.abstain], ['none', seats.length - j.totals.voted]]
    .map(([k, v]) => [box.querySelector(`.counts .${k} .n`), v]).filter(([e]) => e);
  const colors = seats.map((s) => VOTE[s.code]);
  whenSeen(el, () => play(circles, colors, counts));
  box.querySelector('.replay')?.addEventListener('click', () => play(circles, colors, counts));
}

// data-hall="/api/zala/<assembly>.json": the groups of the assembly
async function groupHall(el) {
  const j = await (await fetch(el.dataset.hall)).json();
  const names = JSON.parse(el.dataset.names || '{}');
  const n = j.groups.reduce((a, g) => a + g.n, 0);
  if (!n) return;
  const { circles, center } = svgHall(el, n);
  center.textContent = `${fmt(n)} депутати`;
  const tp = tip(el), colors = [], who = [];
  j.groups.forEach((g, gi) => { for (let k = 0; k < g.n; k++) { colors.push(TONES[gi % TONES.length]); who.push(g); } });
  circles.forEach((c, i) => {
    const g = who[i];
    c.style.cursor = 'default';
    c.addEventListener('mousemove', (ev) => tp.show(ev, `<b>${names[g.grp] || g.grp}</b><br>${fmt(g.n)} депутати`));
    c.addEventListener('mouseleave', () => tp.hide());
  });
  const leg = el.parentElement.querySelector('.glegend');
  if (leg) leg.innerHTML = j.groups.map((g, gi) => `<span><i style="background:${TONES[gi % TONES.length]}"></i>${names[g.grp] || g.grp} · <b>${g.n}</b></span>`).join('');
  whenSeen(el, () => play(circles, colors, []));
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
      if (r[4] && '+-='.includes(r[3]) && r[3] !== r[4]) { x.fillStyle = '#121417'; x.fillRect(i * w, 2, Math.max(w, 1.5), 8); }
    });
  };
  draw();
  addEventListener('resize', draw);
  const at = (ev) => Math.min(v.length - 1, Math.max(0, Math.floor((ev.clientX - cv.getBoundingClientRect().left) / cv.clientWidth * v.length)));
  cv.addEventListener('mousemove', (ev) => {
    const r = v[at(ev)], d = r[2].split('-').reverse().join('.');
    const against = r[4] && '+-='.includes(r[3]) && r[3] !== r[4] ? `<br><span class="k-no">групата: ${VNAME[r[4]]}</span>` : '';
    tp.show(ev, `${d}<br><b>${r[5]}</b><br>${VNAME[r[3]]}${against}`);
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
      series: [{ type: 'bar', data: months.map((k) => Math.round(1000 * by.get(k)[0] / by.get(k)[1]) / 10), itemStyle: { color: '#0b7a5e', borderRadius: [2, 2, 0, 0] }, barMaxWidth: 18 }],
    });
    addEventListener('resize', () => ch.resize());
  }
}

document.querySelectorAll('.hall[data-vote]').forEach(voteHall);
document.querySelectorAll('.hall[data-hall]').forEach(groupHall);
document.querySelectorAll('.strip[data-strip]').forEach(strip);

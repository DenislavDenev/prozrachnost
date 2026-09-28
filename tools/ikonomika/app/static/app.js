// Икономика: line charts from /api (ECharts, local), sortable and filterable tables. Everything else is server HTML.
const MONTHS = ['януари', 'февруари', 'март', 'април', 'май', 'юни', 'юли', 'август', 'септември', 'октомври', 'ноември', 'декември'];
// Bulgaria green, the EU blue, the euro area yellow (AGENTS.md 7), on every chart; amber is for warnings only,
// and the other series use neither blue nor yellow, so the two keep their meaning
const EU = '#1f4fa8', EA = '#d9a400';
const GEO_COLOR = { BG: '#0b7a5e', EU27_2020: EU, EA, EA21: EA, EA20: EA };
const PALETTE = ['#121417', '#0b7a5e', '#8a5cb8', '#c2587a', '#7a8b2c', '#5b6b7f', '#2a9d8f', '#8d6e63', '#9aa1aa', '#b0413e'];
const FLAGS = { p: 'предварителни', e: 'оценка', b: 'прекъсване на реда', d: 'различно определение', u: 'ниска надеждност', s: 'оценка на Eurostat', r: 'ревизирани' };

const ZOOM_FROM = '2010';

const num = (v, d = 1) => v == null ? 'няма данни'
  : v.toLocaleString('bg-BG', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');
function period(t) {
  if (/^\d{4}-\d{2}$/.test(t)) return `${MONTHS[+t.slice(5) - 1]} ${t.slice(0, 4)}`;
  if (/-Q\d$/.test(t)) return `${['I', 'II', 'III', 'IV'][+t.slice(-1) - 1]} тримесечие ${t.slice(0, 4)}`;
  if (/-S\d$/.test(t)) return `${t.endsWith('1') ? 'първо' : 'второ'} полугодие ${t.slice(0, 4)}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return `${t.slice(8)}.${t.slice(5, 7)}.${t.slice(0, 4)}`;
  return t;
}

// one label per year on the first period of the year (every 2nd or 5th year when there are many)
function yearTicks(times) {
  const years = new Set(times.map((t) => t.slice(0, 4))).size;
  const step = years > 40 ? 5 : years > 16 ? 2 : 1;
  return (i, t) => (i === 0 || times[i - 1].slice(0, 4) !== t.slice(0, 4)) && +t.slice(0, 4) % step === 0;
}

async function lineChart(el) {
  const res = await fetch(el.dataset.src);
  if (!res.ok) { el.textContent = 'Няма данни.'; return; }
  const j = await res.json();
  const series = j.series || [{ name: j.unit, geo: 'BG', points: j.points }];
  if (!series.length) { el.textContent = 'Няма данни.'; return; }
  const d = +(el.dataset.digits ?? 1);
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))].sort();
  const byGeo = new Set(series.map((s) => s.geo)).size === series.length;   // one line per place: coloured by place
  const color = (s, i) => (byGeo && GEO_COLOR[s.geo]) || PALETTE[i % PALETTE.length];
  const byT = series.map((s) => new Map(s.points.map((p) => [p[0], p])));
  let leg = el.previousElementSibling;
  if (!leg || !leg.classList.contains('legend')) { leg = document.createElement('div'); leg.className = 'legend'; el.before(leg); }
  leg.innerHTML = series.map((s, i) => `<span><i style="background:${color(s, i)}"></i>${s.name}</span>`).join('');
  // every chart has the slider and opens on the same period: from ZOOM_FROM to the latest (AGENTS.md 7)
  const start = Math.max(0, times.indexOf(times.find((t) => t >= ZOOM_FROM))) / Math.max(1, times.length - 1) * 100;
  const chart = echarts.getInstanceByDom(el) || echarts.init(el, null, { renderer: 'svg' });
  chart.setOption({
    animation: false,
    textStyle: { fontFamily: 'Sofia Sans, sans-serif' },
    grid: { left: 8, right: 12, top: 12, bottom: 44, containLabel: true },
    tooltip: {
      trigger: 'axis', confine: true,
      formatter: (items) => `<b>${period(items[0].axisValue)}</b><br>` + items.map((it) => {
        const p = byT[it.seriesIndex].get(it.axisValue);
        const f = p && p[2] && FLAGS[p[2]] ? ` <span style="color:#b25b06">(${FLAGS[p[2]]})</span>` : '';
        return `${it.marker}${it.seriesName}: <b>${num(p ? p[1] : null, d)}</b>${f}`;
      }).join('<br>'),
    },
    xAxis: { type: 'category', data: times, boundaryGap: false, axisLine: { lineStyle: { color: '#e1e4e8' } }, axisTick: { show: false },
      axisLabel: { color: '#69707a', interval: yearTicks(times), formatter: (t) => t.slice(0, 4) } },
    yAxis: { type: 'value', scale: el.dataset.zero == null, splitLine: { lineStyle: { color: '#eceef1' } },
      axisLabel: { color: '#69707a', formatter: (v) => num(v, Math.min(4, (String(v).split('.')[1] || '').length)) } },   // as many decimals as the tick has
    dataZoom: [{ type: 'slider', start, bottom: 8, height: 22, borderColor: '#e1e4e8', fillerColor: 'rgba(11,122,94,.08)',
      handleStyle: { color: '#0b7a5e' }, labelFormatter: (i) => period(times[i] || '') }],   // only the slider: an "inside" zoom takes the wheel and the finger from the page
    series: series.map((s, i) => ({
      name: s.name, type: 'line', showSymbol: false, connectNulls: false, color: color(s, i),
      lineStyle: { width: s.geo === 'BG' || i === 0 ? 2.4 : 1.6 },
      data: times.map((t) => { const p = byT[i].get(t); return p ? p[1] : null; }),
    })),
  }, true);
  addEventListener('resize', () => chart.resize());
}
document.querySelectorAll('.chart[data-src]').forEach(lineChart);

// buttons that pick what one chart shows (green when on): data-for = the chart's id, data-param = the query parameter;
// the last one on stays on
document.querySelectorAll('.chips[data-for]').forEach((box) => {
  const el = document.getElementById(box.dataset.for);
  box.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    if (!on && box.querySelectorAll('[aria-pressed="true"]').length === 1) return;
    b.setAttribute('aria-pressed', on);
    const u = new URL(el.dataset.src, location.href);
    u.searchParams.set(box.dataset.param, [...box.querySelectorAll('[aria-pressed="true"]')].map((x) => x.value).join(','));
    el.dataset.src = u.pathname + u.search;
    lineChart(el);
  });
});

// complete tables (class sortable) sort in the browser; a cell's data-v, when present, is its sort value
function sortable(t) {
  const ths = [...t.tHead.rows[0].cells], NUM = /^[−-]?[\d\s .,]+$/;
  const key = (tr, i) => { const c = tr.cells[i], v = (c?.dataset.v ?? c?.textContent ?? '').trim();
    return NUM.test(v) && /\d/.test(v) ? parseFloat(v.replace(/[\s ]/g, '').replace('−', '-').replace(',', '.')) : v.toLowerCase(); };
  ths.forEach((th, i) => {
    if (th.dataset.nosort != null) return;
    const b = document.createElement('button'); b.type = 'button'; b.className = 'sort'; b.append(...th.childNodes); th.append(b);
    th.setAttribute('aria-sort', 'none');
    b.onclick = () => {
      const dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
      ths.forEach((x) => x.hasAttribute('aria-sort') && x.setAttribute('aria-sort', 'none')); th.setAttribute('aria-sort', dir);
      const rows = [...t.tBodies[0].rows];
      rows.sort((a, z) => { const x = key(a, i), y = key(z, i);
        // "няма данни" goes last either way
        if (typeof x !== typeof y) return typeof x === 'number' ? -1 : 1;
        const r = typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'bg');
        return dir === 'ascending' ? r : -r; });
      t.tBodies[0].append(...rows);
    };
  });
}
document.querySelectorAll('table.sortable').forEach(sortable);

// <input type=search data-filter="table id" [data-chips="buttons id"]>: shows the rows (and buttons) with the text or code
document.querySelectorAll('input[data-filter]').forEach((inp) => {
  const t = document.getElementById(inp.dataset.filter), chips = document.getElementById(inp.dataset.chips);
  inp.addEventListener('input', () => {
    const s = inp.value.trim().toLowerCase(), has = (x) => !s || x.toLowerCase().includes(s);
    [...t.tBodies[0].rows].forEach((r) => { r.hidden = !has(r.textContent); });
    if (chips) chips.querySelectorAll('button').forEach((b) => { b.hidden = !has(b.textContent + ' ' + b.title) && b.getAttribute('aria-pressed') !== 'true'; });
  });
});

// Закони и решения: the weekly chart (ECharts, local), sortable and filterable tables, the oblast -> municipality filter.
const num = (v, d = 0) => v == null ? 'няма данни'
  : v.toLocaleString('bg-BG', { minimumFractionDigits: d, maximumFractionDigits: d }).replace('-', '−');
const dmy = (s) => `${s.slice(8, 10)}.${s.slice(5, 7)}.${s.slice(0, 4)}`;

// acts per week, stacked; the slider under the chart opens on the whole period given (the wheel and the finger scroll the page)
(function weeks() {
  const el = document.getElementById('weeks-chart'), data = document.getElementById('weeks-data');
  if (!el || !data || typeof echarts === 'undefined') return;
  const rows = JSON.parse(data.textContent);
  if (!rows.length) { el.textContent = 'Няма данни за периода.'; return; }
  const chart = echarts.init(el, null, { renderer: 'svg' });
  const series = [['postanovleniya', 'постановления', '#0b7a5e'], ['resheniya', 'решения', '#121417'], ['razporezhdaniya', 'разпореждания', '#8a5cb8']];
  chart.setOption({
    animation: false, textStyle: { fontFamily: 'Sofia Sans, sans-serif' },
    grid: { left: 8, right: 12, top: 12, bottom: 44, containLabel: true },
    tooltip: { trigger: 'axis', confine: true, axisPointer: { type: 'shadow' },
      formatter: (items) => `<b>седмица от ${dmy(items[0].axisValue)}</b><br>` + items.map((i) => `${i.marker}${i.seriesName}: <b>${num(i.value)}</b>`).join('<br>') + `<br>общо: <b>${num(items.reduce((a, i) => a + i.value, 0))}</b>` },
    xAxis: { type: 'category', data: rows.map((r) => r.week), axisLine: { lineStyle: { color: '#e1e4e8' } }, axisTick: { show: false },
      axisLabel: { color: '#69707a', formatter: (t) => dmy(t).slice(0, 5) + '.' + t.slice(2, 4) } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eceef1' } }, axisLabel: { color: '#69707a' } },
    dataZoom: [{ type: 'slider', start: 0, end: 100, bottom: 8, height: 22, borderColor: '#e1e4e8', fillerColor: 'rgba(11,122,94,.08)', handleStyle: { color: '#0b7a5e' } }],
    series: series.map(([k, name, color]) => ({ name, type: 'bar', stack: 'a', color, data: rows.map((r) => r[k]) })),
  });
  addEventListener('resize', () => chart.resize());
})();

// complete tables (class sortable) sort in the browser; a cell's data-v, when present, is its sort value
function sortable(t) {
  const ths = [...t.tHead.rows[0].cells], NUM = /^[−-]?[\d\s .,]+$/;
  const key = (tr, i) => { const c = tr.cells[i], v = (c?.dataset.v ?? c?.textContent ?? '').trim();
    return NUM.test(v) && /\d/.test(v) ? parseFloat(v.replace(/[\s ]/g, '').replace('−', '-').replace(',', '.')) : v.toLowerCase(); };
  ths.forEach((th, i) => {
    if (th.dataset.nosort != null || th.hidden) return;
    const b = document.createElement('button'); b.type = 'button'; b.className = 'sort'; b.append(...th.childNodes); th.append(b);
    th.setAttribute('aria-sort', 'none');
    b.onclick = () => {
      const dir = th.getAttribute('aria-sort') === 'descending' ? 'ascending' : 'descending';
      ths.forEach((x) => x.hasAttribute('aria-sort') && x.setAttribute('aria-sort', 'none')); th.setAttribute('aria-sort', dir);
      const rows = [...t.tBodies[0].rows];
      rows.sort((a, z) => { const x = key(a, i), y = key(z, i);
        // "няма данни" and empty cells go last either way
        if ((x === '') !== (y === '')) return x === '' ? 1 : -1;
        if (typeof x !== typeof y) return typeof x === 'number' ? -1 : 1;
        const r = typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'bg');
        return dir === 'ascending' ? r : -r; });
      t.tBodies[0].append(...rows);
    };
  });
}
document.querySelectorAll('table.sortable').forEach(sortable);

// <input type=search data-filter="table id">: shows the rows with the text
document.querySelectorAll('input[data-filter]').forEach((inp) => {
  const t = document.getElementById(inp.dataset.filter);
  inp.addEventListener('input', () => {
    const s = inp.value.trim().toLowerCase();
    [...t.tBodies[0].rows].forEach((r) => { r.hidden = !!s && !r.textContent.toLowerCase().includes(s); });
  });
});

// choosing an oblast at once limits the municipalities, and a change of oblast clears the old municipality (AGENTS.md 7)
(function cascade() {
  const ob = document.getElementById('f-oblast'), mu = document.getElementById('f-municipality');
  if (!ob || !mu) return;
  const all = [...mu.options].slice(1);
  const apply = (clear) => {
    const o = ob.value;
    if (clear) mu.value = '';
    all.forEach((opt) => { opt.hidden = !!o && opt.dataset.oblast !== o; opt.disabled = opt.hidden; });
  };
  ob.addEventListener('change', () => apply(true));
  apply(false);
})();

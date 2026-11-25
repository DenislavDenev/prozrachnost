// Субсидии и помощи: the oblast chooses the municipalities of every filter form, the selects submit their form, and the
// bars of the dashboard. The same parameters feed the page, the chart, the CSV and the JSON.
(() => {
  document.querySelectorAll('form.ctl').forEach((form) => {
    const ob = form.elements.oblast, mun = form.elements.obshtina;
    if (ob && mun) {
      const all = [...mun.options].slice(1).map((o) => o.cloneNode(true));
      const limit = (reset) => {
        const value = reset ? '' : mun.value;
        mun.replaceChildren(new Option('Всички', ''), ...all.filter((o) => !ob.value || o.dataset.oblast === ob.value).map((o) => o.cloneNode(true)));
        mun.value = [...mun.options].some((o) => o.value === value) ? value : '';
      };
      limit(false);
      ob.addEventListener('change', () => { limit(true); form.requestSubmit(); });
    }
    form.querySelectorAll('select').forEach((el) => { if (el !== ob && !el.closest('fieldset')) el.addEventListener('change', () => form.requestSubmit()); });
  });
  const el = document.getElementById('year-chart'), data = document.getElementById('year-data');
  if (el && data && window.echarts) {
    const rows = JSON.parse(data.textContent), colors = { efgz: '#0b7a5e', ezfrs: '#121417', nb: '#8a5cb8' };
    const names = { efgz: 'ЕФГЗ', ezfrs: 'ЕЗФРСР', nb: 'Национален бюджет' };
    const num = (v) => v == null ? 'няма данни' : (Math.abs(v) >= 1e9 ? (v / 1e9).toLocaleString('bg-BG', { maximumFractionDigits: 2 }) + ' млрд. €' : (v / 1e6).toLocaleString('bg-BG', { maximumFractionDigits: 1 }) + ' млн. €');
    const chart = echarts.init(el, null, { renderer: 'svg' });
    chart.setOption({
      animation: false, textStyle: { fontFamily: 'Sofia Sans, sans-serif' }, grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
      legend: { top: 0, textStyle: { color: '#69707a' } },
      tooltip: { trigger: 'axis', confine: true, formatter: (items) => `<b>ФГ ${items[0].axisValue}</b><br>` + items.map((i) => `${i.marker}${i.seriesName}: <b>${num(i.value)}</b>`).join('<br>') },
      xAxis: { type: 'category', data: rows.map((r) => r.fy), axisTick: { show: false }, axisLine: { lineStyle: { color: '#e1e4e8' } }, axisLabel: { color: '#69707a' } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eceef1' } }, axisLabel: { color: '#69707a', formatter: (v) => num(v) } },
      series: Object.keys(names).map((k) => ({ name: names[k], type: 'bar', stack: 'fund', color: colors[k], data: rows.map((r) => r[k]) })),
    });
    addEventListener('resize', () => chart.resize());
  }
})();

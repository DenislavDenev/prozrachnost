// Small, lazy GISCO map. The server-rendered table remains the accessible fallback.
(() => {
  const svg = document.getElementById('municipality-svg');
  if (!svg) return;
  const rows = [...document.querySelectorAll('#municipality-list-body tr')];
  const byCode = new Map(rows.map(row => [row.dataset.code, row]));
  const paths = new Map();
  const detail = document.getElementById('municipality-detail');
  const loading = document.getElementById('municipality-loading');
  const search = document.getElementById('municipality-search');
  const buttons = [...document.querySelectorAll('.municipality-subject')];
  const fmt = new Intl.NumberFormat('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const count = new Intl.NumberFormat('bg-BG');
  const NS = 'http://www.w3.org/2000/svg';
  const palette = ['#eef7f3', '#d2eadf', '#a5d8c2', '#70bf9f', '#2d9874', '#0b7a5e'];
  let subject = 'bel', selected = null;

  const value = row => row.dataset[subject] === '' ? null : Number(row.dataset[subject]);
  const color = v => v == null ? '#eceff3' : palette[Math.min(5, Math.max(0, Math.floor(v / 20)))];
  const label = () => subject === 'bel' ? 'БЕЛ' : 'математика';

  function title(row) {
    const v = value(row);
    return `${row.dataset.name}, ${row.dataset.oblast}: ${v == null ? 'няма данни' : fmt.format(v) + ' точки'} по ${label()}`;
  }
  function refresh() {
    for (const row of rows) {
      const v = value(row);
      row.querySelector('.municipality-value').textContent = v == null ? 'няма данни' : fmt.format(v);
      const path = paths.get(row.dataset.code);
      if (path) {
        path.style.fill = color(v);
        path.setAttribute('aria-label', title(row));
        path.querySelector('title').textContent = title(row);
      }
    }
    document.getElementById('municipality-subtitle').textContent =
      (subject === 'bel' ? 'Български език и литература' : 'Математика') + ' · точки';
    if (selected) select(selected, false);
  }
  function select(code, scroll = true) {
    const row = byCode.get(code);
    if (!row) return;
    selected = code;
    for (const item of rows) item.classList.toggle('municipality-selected', item === row);
    for (const [id, path] of paths) path.classList.toggle('municipality-selected', id === code);
    const v = value(row), takers = Number(row.dataset[subject + 'Takers']);
    detail.textContent = `${row.dataset.name}, ${row.dataset.oblast} · ${label()}: ` +
      (v == null ? 'няма данни' : `${fmt.format(v)} точки · ${count.format(takers)} явили се`);
    if (scroll) row.scrollIntoView({ block: 'nearest', behavior:
      matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  buttons.forEach(button => button.addEventListener('click', () => {
    subject = button.dataset.subject;
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    refresh();
  }));
  search.addEventListener('input', () => {
    const q = search.value.trim().toLocaleLowerCase('bg-BG');
    for (const row of rows) row.hidden = !`${row.dataset.name} ${row.dataset.oblast}`.toLocaleLowerCase('bg-BG').includes(q);
  });
  rows.forEach(row => {
    row.addEventListener('click', event => { if (!event.target.closest('a')) select(row.dataset.code, false); });
    row.addEventListener('mouseenter', () => paths.get(row.dataset.code)?.classList.add('municipality-hover'));
    row.addEventListener('mouseleave', () => paths.get(row.dataset.code)?.classList.remove('municipality-hover'));
  });

  async function draw() {
    try {
      const response = await fetch('/static/bg-municipalities.json');
      if (!response.ok) throw new Error('Map geometry unavailable');
      const geo = await response.json();
      svg.setAttribute('viewBox', geo.view.join(' '));
      for (const [code, shape] of Object.entries(geo.shapes)) {
        const row = byCode.get(code);
        if (!row) continue;
        const path = document.createElementNS(NS, 'path');
        const nodeTitle = document.createElementNS(NS, 'title');
        path.setAttribute('d', shape.d);
        path.dataset.code = code;
        path.append(nodeTitle);
        path.addEventListener('click', () => select(code));
        svg.append(path);
        paths.set(code, path);
      }
      if (paths.size !== rows.length) throw new Error('Municipality geometry and rows differ');
      refresh();
      loading.remove();
    } catch (_) {
      loading.textContent = 'Картата не се зареди. Стойностите и връзките към училищата са в списъка.';
    }
  }

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); draw(); }
    }, { rootMargin: '250px' });
    observer.observe(svg);
  } else draw();
})();

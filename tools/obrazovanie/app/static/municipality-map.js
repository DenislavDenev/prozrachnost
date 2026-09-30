// The same reviewed GISCO contours and territorial levels used across Прозрачност.
(() => {
  const svg = document.getElementById('municipality-svg');
  if (!svg) return;
  const rows = [...document.querySelectorAll('#municipality-list-body tr')];
  const byCode = new Map(rows.map(row => [row.dataset.code, row]));
  const paths = new Map();
  const detail = document.getElementById('municipality-detail');
  const loading = document.getElementById('municipality-loading');
  const search = document.getElementById('municipality-search');
  const fmt = new Intl.NumberFormat('bg-BG', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const count = new Intl.NumberFormat('bg-BG');
  const NS = 'http://www.w3.org/2000/svg';
  const overlay = document.createElementNS(NS, 'g');
  const level = {darzhava: '0', makrorayoni: '1', rayoni: '2', oblasti: '3', obshtini: '4'}[svg.dataset.level];
  const values = rows.map(row => Number(row.dataset.score)).filter((_, i) => rows[i].dataset.score !== '');
  const lo = Math.min(...values), hi = Math.max(...values);
  let selected = null, home = null, view = null, dragging = null, moved = false;

  function setView(box) {
    view = box;
    svg.setAttribute('viewBox', [box.x, box.y, box.w, box.h].join(' '));
  }
  function zoom(factor, clientX, clientY) {
    if (!view || !home) return;
    const rect = svg.getBoundingClientRect();
    const rx = clientX == null ? .5 : (clientX - rect.left) / rect.width;
    const ry = clientY == null ? .5 : (clientY - rect.top) / rect.height;
    const width = Math.max(home.w / 8, Math.min(home.w * 2, view.w * factor));
    const height = width * home.h / home.w;
    setView({x: view.x + rx * (view.w - width), y: view.y + ry * (view.h - height), w: width, h: height});
  }
  document.querySelectorAll('.map-zoom button').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.zoom === 'home') setView({...home});
    else zoom(button.dataset.zoom === 'in' ? .7 : 1 / .7);
  }));
  svg.addEventListener('wheel', event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    zoom(event.deltaY < 0 ? .8 : 1.25, event.clientX, event.clientY);
  }, {passive: false});
  svg.addEventListener('dblclick', event => { event.preventDefault(); zoom(.7, event.clientX, event.clientY); });
  svg.addEventListener('pointerdown', event => {
    if (event.pointerType === 'touch') return;
    dragging = {x: event.clientX, y: event.clientY, view: {...view}};
    moved = false;
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove', event => {
    if (!dragging || !view) return;
    const rect = svg.getBoundingClientRect();
    const dx = event.clientX - dragging.x, dy = event.clientY - dragging.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    if (moved) setView({x: dragging.view.x - dx * dragging.view.w / rect.width,
      y: dragging.view.y - dy * dragging.view.h / rect.height, w: dragging.view.w, h: dragging.view.h});
  });
  svg.addEventListener('pointerup', () => { dragging = null; });

  const color = row => {
    if (row.dataset.score === '') return '#eceff3';
    const share = (Number(row.dataset.score) - lo) / (hi - lo || 1);
    return 'color-mix(in srgb, #0b7a5e ' + Math.round(8 + share * 92) + '%, white)';
  };
  const title = row => row.dataset.name + (row.dataset.oblast ? ', ' + row.dataset.oblast : '') + ': ' +
    (row.dataset.score === '' ? 'няма данни' : fmt.format(Number(row.dataset.score)) + ' ' + svg.dataset.scale);
  function select(code, scroll = true) {
    const row = byCode.get(code);
    if (!row) return;
    selected = code;
    for (const item of rows) item.classList.toggle('municipality-selected', item === row);
    for (const [id, path] of paths) path.classList.toggle('municipality-selected', id === code);
    overlay.replaceChildren();
    const selectedPath = paths.get(code);
    if (selectedPath) for (const name of ['halo', 'line']) {
      const outline = selectedPath.cloneNode(false);
      outline.removeAttribute('style');
      outline.setAttribute('class', 'map-selected-' + name);
      overlay.append(outline);
    }
    detail.textContent = title(row) + (row.dataset.takers ? ' · ' + count.format(Number(row.dataset.takers)) + ' явили се' : '');
    if (scroll) row.scrollIntoView({block: 'nearest', behavior:
      matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }
  search.addEventListener('input', () => {
    const q = search.value.trim().toLocaleLowerCase('bg-BG');
    for (const row of rows) row.hidden = !`${row.dataset.name} ${row.dataset.oblast}`.toLocaleLowerCase('bg-BG').includes(q);
  });
  rows.forEach(row => row.querySelector('.map-pick').addEventListener('click', () => select(row.dataset.code, false)));
  for (const id of ['map-exam', 'map-session', 'map-kind']) {
    const control = document.getElementById(id);
    control?.addEventListener('change', () => {
      if (id === 'map-session' && control.value === 'august') document.getElementById('map-kind').value = 'mandatory';
      document.getElementById('map-year').disabled = true;
      document.getElementById('map-subject').disabled = true;
      control.form.requestSubmit();
    });
  }

  async function draw() {
    try {
      const [response, neighboursResponse] = await Promise.all([
        fetch(level === '4' ? '/static/bg-municipalities.json' : '/static/bg-geography.json'),
        fetch('/static/bg-neighbors.json')]);
      if (!response.ok) throw new Error('Missing geography');
      const geo = await response.json();
      const neighbours = neighboursResponse.ok ? await neighboursResponse.json() : {};
      const [x, y, w, h] = geo.view;
      home = {x: x - w * .05, y: y - h * .05, w: w * 1.1, h: h * 1.1};
      setView({...home});
      for (const d of Object.values(neighbours)) {
        const path = document.createElementNS(NS, 'path');
        path.setAttribute('d', d);
        path.classList.add('map-neighbour');
        svg.append(path);
      }
      const shapes = level === '4' ? Object.fromEntries(Object.entries(geo.shapes).map(([code, value]) => [code, value.d])) : geo.shapes[level];
      for (const [code, d] of Object.entries(shapes)) {
        const row = byCode.get(code);
        if (!row) throw new Error('Geometry without a place row');
        const path = document.createElementNS(NS, 'path');
        path.setAttribute('d', d);
        path.dataset.code = code;
        path.style.fill = color(row);
        path.setAttribute('aria-label', title(row));
        path.addEventListener('click', () => { if (!moved) select(code); moved = false; });
        path.addEventListener('mouseenter', () => {
          path.classList.add('municipality-hover');
          detail.textContent = title(row);
        });
        path.addEventListener('mouseleave', () => {
          path.classList.remove('municipality-hover');
          detail.textContent = selected ? title(byCode.get(selected)) : 'Избери място на картата или в списъка.';
        });
        svg.append(path);
        paths.set(code, path);
      }
      if (paths.size !== rows.length) throw new Error('Missing territorial contour');
      svg.append(overlay);
      loading.remove();
    } catch (_) {
      loading.textContent = 'Картата не се зареди. Стойностите са в списъка.';
    }
  }
  draw();
})();

// Sort every row currently selected by the form, without changing the source data.
for (const table of document.querySelectorAll('table.sortable-client')) {
  for (const button of table.querySelectorAll('button.sort')) {
    button.addEventListener('click', () => {
      const index = Number(button.dataset.col);
      const header = button.closest('th');
      const ascending = header.getAttribute('aria-sort') !== 'ascending';
      for (const th of table.querySelectorAll('th')) th.removeAttribute('aria-sort');
      header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => {
        const left = a.cells[index].dataset.sort;
        const right = b.cells[index].dataset.sort;
        if (left === '') return right === '' ? 0 : 1;
        if (right === '') return -1;
        const order = header.classList.contains('n')
          ? Number(left) - Number(right)
          : left.localeCompare(right, 'bg');
        return (ascending ? 1 : -1) * order;
      });
      table.tBodies[0].append(...rows);
    });
  }
}

// A province limits the municipality choices before submitting any filter form.
for (const form of document.querySelectorAll('form.filters')) {
  const oblast = form.querySelector('select[name=oblast]');
  const municipality = form.querySelector('select[name=municipality]');
  if (!oblast || !municipality) continue;
  const update = () => {
    for (const option of municipality.options) {
      option.hidden = !!option.dataset.oblast && !!oblast.value && option.dataset.oblast !== oblast.value;
      option.disabled = option.hidden;
    }
    if (municipality.selectedOptions[0]?.hidden) municipality.value = '';
  };
  oblast.addEventListener('change', () => { municipality.value = ''; update(); });
  update();
}

const kindSearch = document.getElementById('context-kind-search');
if (kindSearch) kindSearch.addEventListener('input', () => {
  const q = kindSearch.value.trim().toLocaleLowerCase('bg-BG');
  for (const row of document.querySelectorAll('#context-type-table tbody tr')) {
    row.hidden = !row.cells[0].textContent.toLocaleLowerCase('bg-BG').includes(q);
  }
});

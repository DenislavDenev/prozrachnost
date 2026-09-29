// Sort every row currently selected by the form, without changing the source data.
const table = document.getElementById('school-table');
if (table) {
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
        const order = index > 1
          ? Number(left) - Number(right)
          : left.localeCompare(right, 'bg');
        return (ascending ? 1 : -1) * order;
      });
      table.tBodies[0].append(...rows);
    });
  }
}

const oblast = document.querySelector('select[name=oblast]');
if (oblast) oblast.addEventListener('change', () => {
  document.querySelector('select[name=municipality]').value = '';
});

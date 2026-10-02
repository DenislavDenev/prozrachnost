// Пазар: a change of a select in a filter form submits it (the same parameters feed the page, the chart, the CSV).
document.querySelectorAll('form.ctl select').forEach((el) => el.addEventListener('change', () => el.form.requestSubmit()));
// the visitor's own basket: tick boxes, "select the group" buttons
document.querySelectorAll('[data-group]').forEach((b) => b.addEventListener('click', () => {
  const boxes = [...document.querySelectorAll(`input[name=cat][data-g="${b.dataset.group}"]`)], on = boxes.some((x) => !x.checked);
  boxes.forEach((x) => { x.checked = on; });
}));
const own = document.getElementById('own');
if (own) own.addEventListener('submit', (e) => {
  e.preventDefault();
  const c = [...own.querySelectorAll('input[name=cat]:checked')].map((x) => x.value).join(',');
  if (!c) return;
  const u = new URLSearchParams({ c }); const d = own.elements.d && own.elements.d.value; if (d) u.set('d', d);
  location.href = '/koshnica?' + u;
});

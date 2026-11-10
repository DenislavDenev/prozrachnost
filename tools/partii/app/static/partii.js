// Партии и пари: a change of a select in a filter form submits it (the same parameters feed the page, the chart, the CSV).
document.querySelectorAll('form.ctl select').forEach((el) => el.addEventListener('change', () => el.form.requestSubmit()));
// a "?" in the heading of a sortable column stays a button of its own, beside the sort button, not inside it
document.querySelectorAll('th .sort .help-trigger').forEach((h) => h.closest('th').append(h));

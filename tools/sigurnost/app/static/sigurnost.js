// Престъпност и пожари: a change of a select in a filter form submits it (the same parameters feed the page, the CSV and the JSON).
document.querySelectorAll('form.ctl select').forEach((el) => el.addEventListener('change', () => el.form.requestSubmit()));

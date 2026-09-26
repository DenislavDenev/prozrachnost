// Тендер: one autocomplete for every search field (WAI-ARIA APG "editable combobox with list autocomplete").
// <input data-combo="buyer,company,person,tender" data-pick="go|filter|event" [data-net]>
//   go: open the profile; filter: put the pick into the form as buyer=/company=/person= and submit;
//   event: dispatch "combopick" on the input with the item (graph, Свързаности). data-net: only people and
//   companies that are in the registry network (item.node set). Source: /find.json. Needs esc() from app.js.
(() => {
  const KIND = { buyer: 'Възложител', company: 'Фирма', person: 'Лице', tender: 'Поръчка' };
  let seq = 0;
  function combo(input) {
    const id = 'cb' + ++seq, list = document.createElement('ul'), say = document.createElement('span');
    list.className = 'results combo'; list.id = id + '-l'; list.setAttribute('role', 'listbox'); list.hidden = true;
    say.className = 'vh'; say.setAttribute('aria-live', 'polite');
    const box = document.createElement('div'); box.className = 'combo-box';
    input.replaceWith(box); box.append(input, list, say);
    Object.entries({ role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': list.id, autocomplete: 'off' })
      .forEach(([k, v]) => input.setAttribute(k, v));
    let items = [], at = -1, ctl, tmr, last = '';
    const open = (on) => { list.hidden = !on; input.setAttribute('aria-expanded', on); if (!on) move(-1); };
    function move(i) {
      at = i;
      [...list.children].forEach((li, k) => li.setAttribute('aria-selected', k === i));
      if (i >= 0 && list.children[i]) { input.setAttribute('aria-activedescendant', list.children[i].id); list.children[i].scrollIntoView({ block: 'nearest' }); }
      else input.removeAttribute('aria-activedescendant');
    }
    async function fetchItems() {
      const q = input.value.trim();
      if (q === last) return; last = q;
      if (q.length < 2) { items = []; list.innerHTML = ''; open(false); return; }
      ctl?.abort(); ctl = new AbortController();
      const p = new URLSearchParams({ q, kinds: input.dataset.combo || 'buyer,company,person,tender' });
      if (input.dataset.net != null) p.set('net', '1');
      try { items = await (await fetch('/find.json?' + p, { signal: ctl.signal })).json(); } catch (e) { if (e.name === 'AbortError') return; items = []; }
      list.innerHTML = items.map((x, k) => `<li role="option" id="${id}-${k}" aria-selected="false"><span class="k">${KIND[x.kind] || ''}</span><span class="t">${esc(x.label)}</span><span class="s">${esc(x.sub || '')}</span></li>`).join('')
        || `<li class="empty" aria-disabled="true">Няма резултати за „${esc(q)}“</li>`;
      say.textContent = items.length ? `${items.length} резултата` : 'Няма резултати';
      open(document.activeElement === input); move(-1);
    }
    function pick(x) {
      open(false);
      const mode = input.dataset.pick || 'go', form = input.form;
      if (mode === 'event') { input.dispatchEvent(new CustomEvent('combopick', { detail: x })); input.value = ''; last = ''; return; }
      if (mode === 'filter' && form) {
        const name = x.kind === 'buyer' ? 'buyer' : x.kind === 'person' ? 'person' : 'company';
        let h = form.querySelector(`input[type=hidden][name=${name}]`);
        if (!h) { h = document.createElement('input'); h.type = 'hidden'; h.name = name; form.append(h); }
        h.value = x.ref; input.value = ''; form.submit(); return;
      }
      location.href = x.href;
    }
    input.addEventListener('input', () => { clearTimeout(tmr); tmr = setTimeout(fetchItems, 200); });
    input.addEventListener('keydown', (e) => {
      const n = items.length;
      if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden && n) open(true); if (!e.altKey && n) move((at + 1) % n); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); if (e.altKey) return open(false); if (n) { open(true); move(at <= 0 ? n - 1 : at - 1); } }
      else if (e.key === 'Enter' && !list.hidden && at >= 0) { e.preventDefault(); pick(items[at]); }
      else if (e.key === 'Escape') { if (!list.hidden) open(false); else { input.value = ''; last = ''; } }
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') move(-1);
    });
    list.addEventListener('mousedown', (e) => e.preventDefault());  // keep focus in the input
    list.addEventListener('click', (e) => { const li = e.target.closest('[role=option]'); if (li) pick(items[[...list.children].indexOf(li)]); });
    input.addEventListener('blur', () => open(false));
    input.addEventListener('focus', () => { if (items.length) open(true); });
  }
  document.querySelectorAll('input[data-combo]').forEach(combo);
})();

// Тендер: visual register of registry links (after rejstrik.penize.cz „Vizualizace vztahů“).
// Markup in templates/_vazby.html, styles in app.css (.vz-*). Uses Cytoscape.js 3.34.3 (MIT).
// opts.routes: the shortest paths of the Свързаности page ({ids, pairs, nodes, roles}), drawn on open
async function vazby(FOCUS, opts = {}) {
  const HUB = 100, YEAR_NOW = +$('year').max;
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  // ---------- look ----------
  const FORMS = [
    ['ltd', 'ООД / ЕООД', '#2c5f8e', ['OOD', 'EOOD']], ['jsc', 'АД / ЕАД', '#b3362b', ['AD', 'EAD', 'KDA']],
    ['sole', 'ЕТ', '#b8741a', ['ET']], ['ngo', 'Сдружение, фондация', '#6a4c93', ['ASSOC', 'FOUND', 'CC', 'KCHT']],
    ['other', 'Друга форма', '#7d858f', []]];
  const form = (f) => FORMS.find((x) => x[3].includes(f)) || FORMS[4];
  const LINKS = [['own', 'собственост', css('--accent'), ['partner', 'sole_owner', 'trader']],
    ['mgmt', 'управление', '#2c5f8e', ['manager', 'representative', 'procurator', 'branch_manager', 'liquidator', 'trustee']],
    ['board', 'член на орган', '#c07a12', []]];
  const linkOf = (role) => LINKS.find((x) => x[3].includes(role)) || LINKS[2];
  const uri = (s) => 'data:image/svg+xml;utf8,' + encodeURIComponent(s);
  const G_BUILD = '<path d="M-7 8V-9h9V8M2-4h6V8M-10 8h20" fill="none" stroke="#fff" stroke-width="1.7" stroke-linejoin="round"/><path d="M-4-6h3M-4-3h3M-4 0h3M-4 3h3" stroke="#fff" stroke-width="1.7" stroke-linecap="round"/>';
  const G_PERS = '<circle cx="0" cy="-4" r="4.3" fill="#fff"/><path d="M-8.5 9c0-5 3.8-7.6 8.5-7.6S8.5 4 8.5 9z" fill="#fff"/>';
  // one picture per look: circle, glyph and badge drawn together, so the glyph can never slip out of the circle
  const PIC = new Map();
  const pic = (kind, color, badge, ring) => {
    const k = [kind, color, badge, ring].join('|'); if (PIC.has(k)) return PIC.get(k);
    const b = badge === 'none' ? '' : `<g transform="translate(24 -24)"><circle r="9" fill="#121417" stroke="#fff" stroke-width="2"/><path d="M-4.5 0h9${badge === 'plus' ? 'M0-4.5v9' : ''}" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></g>`;
    const s = uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="-35 -35 70 70" width="140" height="140"><circle r="27" fill="${color}" ${ring ? `stroke="${ring}" stroke-width="4"` : ''}/><g transform="scale(1.25)">${kind === 'p' ? G_PERS : G_BUILD}</g>${b}</svg>`);
    PIC.set(k, s); return s; };
  $('legend').innerHTML = `<div class="grp"><span><span class="dot" style="background:#6b7a8c"><img src="${uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="-12 -12 24 24">${G_PERS}</svg>`)}" alt=""></span>лице</span>` +
    FORMS.map((f) => `<span><span class="dot" style="background:${f[2]}"><img src="${uri(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="-12 -12 24 24">${G_BUILD}</svg>`)}" alt=""></span>${f[1]}</span>`).join('') +
    `<span><span class="dot" style="box-sizing:border-box;border:3px solid ${css('--accent')}"></span>с договори</span></div><div class="grp">` +
    LINKS.map((l) => `<span><span class="ln" style="background:${l[2]}"></span>${l[1]}</span>`).join('') + `<span><svg width="24" height="4" aria-hidden="true"><line x1="0" y1="2" x2="24" y2="2" stroke="#7d858f" stroke-width="3" stroke-dasharray="5 4"/></svg>прекратена</span></div>`;

  // ---------- model ----------
  const N = new Map(), E = new Map();
  const node = (id, o) => { const n = N.get(id) || { id, kind: id.startsWith('p:') || id.startsWith('l:') ? 'p' : 'c' }; for (const [k, v] of Object.entries(o)) if (v != null && n[k] == null) n[k] = v; N.set(id, n); return n; };
  const ingest = (edges) => { for (const e of edges) {
    node(e.holder, { name: tc(e.holder_name), ref: e.holder_ref, n: e.holder_contracts, eur: e.holder_eur, links: e.holder_links, form: e.holder_form });
    node(e.company, { name: tc(e.company_name || 'ЕИК ' + e.company.slice(2)), ref: e.company_ref, n: e.company_contracts, eur: e.company_eur, links: e.company_links, form: e.company_form });
    E.set([e.holder, e.company, e.role, e.valid_from].join('|'), { holder: e.holder, company: e.company, role: e.role, share: e.share, from: e.valid_from, to: e.valid_to, unsure: !!e.uncertain_after }); } };
  const loaded = new Set();
  const load = async (id) => { if (loaded.has(id)) return; const d = await (await fetch('/network.json?' + new URLSearchParams({ focus: id, view: 'all', depth: 1 }))).json(); ingest(d.edges); loaded.add(id); };
  const S = { expanded: new Set([FOCUS]), hidden: new Set(), added: new Set(), path: new Set(), pathPairs: new Set(), live: false, year: null };
  const hist = [], snap = () => { hist.push(JSON.stringify({ ...S, expanded: [...S.expanded], hidden: [...S.hidden], added: [...S.added], path: [...S.path], pathPairs: [...S.pathPairs] })); $('undo').disabled = false; };
  const liveNow = (e) => !e.to && !e.unsure;
  const inYear = (e, y) => { const d = `${y}-07-01`; return (!e.from || String(e.from) <= d) && (!e.to || String(e.to) > d); };
  // „само действащи“ and the year never remove anything: they dim what was not in force
  const shownEdge = () => true;
  const litEdge = (e) => (!S.live || liveNow(e)) && (!S.year || inYear(e, S.year));
  const nb = (id) => { const m = new Map(); for (const e of E.values()) { if (!shownEdge(e)) continue; const o = e.holder === id ? e.company : e.company === id ? e.holder : null; if (o) { if (!m.has(o)) m.set(o, []); m.get(o).push(e); } } return m; };
  const isHub = (id) => (N.get(id)?.links || 0) > HUB && id !== FOCUS;
  function visible() {
    const v = new Set([FOCUS, ...S.added, ...S.path]);
    for (const x of S.expanded) if (v.has(x)) for (const y of nb(x).keys()) v.add(y);
    for (const h of S.hidden) v.delete(h);
    return v;
  }
  const roleTxt = (e) => (ROLE[e.role] || e.role) + (e.share ? ' ' + e.share : '');
  const when = (e) => span({ valid_from: e.from, valid_to: e.to, uncertain_after: e.unsure ? 1 : null });
  const href = (n) => n.ref ? (n.kind === 'p' ? '/persons/' + n.ref : '/companies/' + n.ref) : null;
  const colorOf = (n) => n.kind === 'p' ? '#6b7a8c' : form(n.form)[2];

  // ---------- Cytoscape ----------
  const cy = cytoscape({ container: $('cy'), wheelSensitivity: 0.25, minZoom: 0.1, maxZoom: 3, boxSelectionEnabled: false, style: [
    { selector: 'node', style: { shape: 'ellipse', width: 54, height: 54, 'background-opacity': 0, 'border-width': 0,
      'background-image': (el) => el.data('pic'), 'background-width': '130%', 'background-height': '130%', 'background-clip': 'none', 'bounds-expansion': 10,
      label: 'data(label)', 'text-valign': 'bottom', 'text-margin-y': 6, 'text-wrap': 'wrap', 'text-max-width': 150, 'font-size': 11.5,
      'font-family': 'Sofia Sans, sans-serif', 'font-weight': 600, color: css('--ink'), 'text-background-color': '#fff', 'text-background-opacity': .85, 'text-background-padding': '1px',
      'transition-property': 'opacity', 'transition-duration': '0.45s' } },
    { selector: 'node.focus', style: { width: 68, height: 68, 'font-size': 13 } },
    { selector: 'edge', style: { width: 2, 'line-color': 'data(color)', 'target-arrow-color': 'data(color)', 'target-arrow-shape': 'triangle', 'arrow-scale': .9, 'curve-style': 'bezier', opacity: .85,
      'transition-property': 'opacity', 'transition-duration': '0.45s' } },
    { selector: 'edge.end', style: { 'line-style': 'dashed', opacity: .45 } },
    { selector: 'edge.path', style: { width: 5, opacity: 1, 'z-index': 9 } },
    { selector: '.off', style: { opacity: .07 } },
    { selector: '.mute', style: { opacity: .06, label: '' } },
    { selector: 'node.route', style: { 'font-size': 12.5, 'z-index': 9 } },
    { selector: 'edge.path.gone', style: { opacity: .35, 'line-style': 'dashed' } },
    { selector: '.faded', style: { opacity: .15 } },
  ] });
  new ResizeObserver(() => cy.resize()).observe($('cy'));
  vazby.cy = cy;  // for tests and the console

  const P = new Map([[FOCUS, { x: 0, y: 0 }]]);
  function place(ids) {
    // new nodes go on an arc around the node that brought them, facing away from the centre (the penize.cz star);
    // in rounds, so a route lays out step by step outwards instead of every far node circling the centre
    for (let todo = [...ids]; todo.length; todo = todo.filter((id) => !P.has(id))) {
      const ready = todo.filter((id) => [...nb(id).keys()].some((x) => P.has(x)));
      arc(ready.length ? ready : todo);
    }
    separate([...P.keys()].filter((id) => id === FOCUS || cy.getElementById(id).length || ids.includes(id)));
  }
  function arc(ids) {
    const byParent = new Map();
    for (const id of ids) { const par = [...nb(id).keys()].find((x) => P.has(x) && S.expanded.has(x)) || [...nb(id).keys()].find((x) => P.has(x)) || FOCUS;
      if (!byParent.has(par)) byParent.set(par, []); byParent.get(par).push(id); }
    for (const [par, kids] of byParent) {
      const p = P.get(par) || { x: 0, y: 0 }, away = par === FOCUS ? 0 : Math.atan2(p.y, p.x);
      const sweep = par === FOCUS ? 2 * Math.PI : Math.min(Math.PI * 1.4, 0.5 + kids.length * 0.32), r = Math.max(170, kids.length * 64 / sweep);
      kids.forEach((id, i) => { const a = par === FOCUS ? -Math.PI / 2 + (2 * Math.PI * i) / kids.length : away - sweep / 2 + (sweep * (i + .5)) / kids.length;
        P.set(id, { x: p.x + r * Math.cos(a), y: p.y + r * Math.sin(a) }); });
    }
  }
  // a node takes its circle plus the label under it; boxes may not overlap (12 px air between them)
  function box(id) {
    const n = N.get(id) || { name: '' }, name = n.name || '', lines = Math.max(1, Math.ceil((name.length * 6.6) / 150)) + (n.kind === 'c' && n.n ? 1 : 0);
    const r = id === FOCUS ? 34 : 27, w = Math.max(2 * r, Math.min(156, name.length * 6.6 + 8));
    return { w, top: r, bottom: r + 8 + lines * 14 };
  }
  function separate(ids) {
    const B = new Map(ids.map((id) => [id, box(id)]));
    for (let it = 0; it < 120; it++) { let moved = false;
      for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
        const a = P.get(ids[i]), b = P.get(ids[j]); if (!a || !b) continue;
        const A = B.get(ids[i]), Bb = B.get(ids[j]);
        const acy = a.y + (A.bottom - A.top) / 2, bcy = b.y + (Bb.bottom - Bb.top) / 2;
        const ox = (A.w + Bb.w) / 2 + 12 - Math.abs(b.x - a.x), oy = (A.top + A.bottom + Bb.top + Bb.bottom) / 2 + 12 - Math.abs(bcy - acy);
        if (ox > 0 && oy > 0) { moved = true;
          const fa = ids[i] === FOCUS ? 0 : ids[j] === FOCUS ? 1 : .5, fb = 1 - fa;
          if (ox < oy) { const d = (b.x >= a.x ? 1 : -1) * ox; a.x -= d * fa; b.x += d * fb; }
          else { const d = (bcy >= acy ? 1 : -1) * oy; a.y -= d * fa; b.y += d * fb; } } }
      if (!moved) break; }
  }
  let pairs = new Map();
  function draw(fit) {
    const vis = visible(); place([...vis].filter((id) => !P.has(id)));
    const els = [];
    for (const id of vis) { const n = N.get(id); if (!n) continue;
      const shown = [...nb(id).keys()].filter((x) => vis.has(x)).length, more = (n.links || 0) > shown;
      const badge = id === FOCUS ? 'none' : S.expanded.has(id) ? 'minus' : more ? 'plus' : 'none';
      const ring = id === FOCUS || (n.kind === 'c' && n.n) ? css('--accent') : '';
      els.push({ group: 'nodes', data: { id, badge, pic: pic(n.kind, colorOf(n), badge, ring), label: n.name + (n.kind === 'c' && n.n ? `\n${nf.format(n.n)} дог. · ${big(+n.eur || 0)}` : '') },
        position: { ...P.get(id) }, classes: id === FOCUS ? 'focus' : '' }); }
    pairs = new Map();
    for (const e of E.values()) { if (!vis.has(e.holder) || !vis.has(e.company) || !shownEdge(e)) continue; const k = e.holder + '>' + e.company; if (!pairs.has(k)) pairs.set(k, []); pairs.get(k).push(e); }
    for (const [k, rs] of pairs) { const [h, c] = k.split('>'), kind = rs.map((r) => linkOf(r.role)).sort((a, b) => LINKS.indexOf(a) - LINKS.indexOf(b))[0];
      els.push({ group: 'edges', data: { id: 'e:' + k, source: h, target: c, color: kind[2], label: rs.map((r) => roleTxt(r) + ' ' + when(r)).join('\n') },
        classes: [rs.some(liveNow) ? '' : 'end', S.pathPairs.has([h, c].sort().join('|')) ? 'path' : ''].join(' ') }); }
    cy.batch(() => { cy.elements().remove(); cy.add(els); });
    light();
    if (fit) cy.fit(undefined, 50);
  }
  // the year only dims what was not in force then, so the picture stays put while the years play
  function light() {
    cy.batch(() => {
      const filt = !!S.year || S.live;
      cy.edges().forEach((ed) => { const rs = pairs.get(ed.source().id() + '>' + ed.target().id()) || []; ed.toggleClass('off', filt && !rs.some(litEdge)); });
      cy.nodes().forEach((nd) => { if (nd.id() === FOCUS || !filt) { nd.removeClass('off'); return; } nd.toggleClass('off', nd.connectedEdges().not('.off').length === 0); });
    });
    // a route is on: it stays drawn, everything else steps back; its links dim in years they did not exist
    if (S.path.size) cy.batch(() => {
      cy.nodes().forEach((nd) => { const on = S.path.has(nd.id()); nd.toggleClass('mute', !on).toggleClass('route', on); if (on) nd.removeClass('off'); });
      cy.edges().forEach((ed) => { const on = ed.hasClass('path'); ed.toggleClass('mute', !on); if (on) { ed.toggleClass('gone', ed.hasClass('off')); ed.removeClass('off'); } });
    });
    else cy.elements().removeClass('mute route gone');
    $('yout').textContent = S.year ?? 'няма избрана година';
    $('ybadge').textContent = S.year ? String(S.year) : S.live ? 'днес' : 'всички години';
    $('ybadge').classList.toggle('dim', !S.year);
    route();
  }
  cy.on('mouseover', 'node', (e) => { const h = e.target.closedNeighborhood(); cy.elements().not(h).addClass('faded'); });
  cy.on('mouseout', 'node', () => cy.elements().removeClass('faded'));
  cy.on('dragfree', 'node', (e) => P.set(e.target.id(), { ...e.target.position() }));
  const M = { from: null };
  const onBadge = (nd, pos) => {  // the badge sits at (24, -24) of the 70-unit picture, radius 9
    if (nd.data('badge') === 'none') return false;
    const u = (0.65 * nd.width()) / 35, c = nd.position();
    return Math.hypot(pos.x - (c.x + 24 * u), pos.y - (c.y - 24 * u)) <= 12 * u; };
  cy.on('tap', 'node', (e) => { const id = e.target.id(); if (M.from) return finishConnect(id);
    if (onBadge(e.target, e.position)) return toggle(id);
    card(id); });
  cy.on('dbltap', 'node', (e) => toggle(e.target.id()));
  cy.on('tap', (e) => { if (e.target !== cy) return;
    const hit = cy.nodes().filter((nd) => onBadge(nd, e.position));
    if (hit.length) return toggle(hit[0].id());
    $('card').classList.remove('on'); });

  async function toggle(id) {
    if (id === FOCUS) return;
    if (isHub(id) && !S.expanded.has(id)) { await load(id); return card(id, true); }
    snap();
    if (S.expanded.has(id)) S.expanded.delete(id); else { await load(id); S.expanded.add(id); }
    draw(); card(id);
  }
  // ---------- the card: what this is and what you can do with it ----------
  function card(id, hubList) {
    const n = N.get(id); if (!n) return;
    const m = nb(id), h = href(n), open = S.expanded.has(id), hub = isHub(id);
    const rows = [...m].map(([o, rs]) => `<li>${hubList ? `<button class="btn sm" data-add="${esc(o)}" style="height:24px;padding:0 8px;margin-right:6px">+</button>` : ''}<b>${esc(N.get(o)?.name || o)}</b><br><small>${rs.map((e) => `${esc(roleTxt(e))} ${when(e)}`).join(' · ')}</small></li>`);
    $('card').innerHTML = `<button class="btn sm x" id="cx" aria-label="Затвори">×</button>
      <div class="who"><img src="${pic(n.kind, colorOf(n), 'none', '')}" width="34" height="34" alt=""><div><h3>${esc(n.name)}</h3>
      <div class="mut">${n.kind === 'p' ? 'Лице' : form(n.form)[1]}${n.kind === 'c' ? ' · ' + (n.n ? `${nf.format(n.n)} договора, ${big(+n.eur || 0)}` : 'без договори') : ''}</div></div></div>
      <div class="acts2">
        ${id !== FOCUS ? `<button class="btn sm go" id="cexp">${open ? 'Скрий връзките му' : hub ? `Покажи връзките му (${nf.format(n.links)}) като списък` : 'Покажи връзките му'}</button>` : ''}
        <button class="btn sm" id="cconn">Как е свързан с…</button>
        ${h ? `<a class="btn sm" href="${h}">Отвори профила</a>` : ''}
        ${id !== FOCUS && location.pathname.startsWith('/lab/') ? `<a class="btn sm" href="?node=${encodeURIComponent(id)}">Сложи в центъра</a>` : ''}</div>
      ${m.size ? `<ul>${(hubList ? rows : rows.slice(0, 8)).join('')}${!hubList && rows.length > 8 ? `<li class="mut">и още ${rows.length - 8}</li>` : ''}</ul>` : ''}`;
    $('card').classList.add('on');
    $('cx').onclick = () => $('card').classList.remove('on');
    $('cexp')?.addEventListener('click', () => (hub && !open ? load(id).then(() => card(id, true)) : toggle(id)));
    $('cconn').onclick = () => startConnect(id);
    $('card').onclick = (ev) => { const b = ev.target.closest('[data-add]'); if (!b) return; snap(); S.added.add(b.dataset.add); b.disabled = true; draw(); };
  }
  // ---------- "how is X connected with…": pick X, then click or search the other one ----------
  function startConnect(id) {
    M.from = id; $('card').classList.remove('on');
    $('banner').innerHTML = `<span>Как е свързан <b>${esc(N.get(id)?.name)}</b> с…? Кликни друго кръгче или потърси фирма или лице горе вдясно.</span><span class="grow"></span><button class="btn sm" id="bcancel">Отказ</button>`;
    $('banner').classList.add('on'); $('bcancel').onclick = () => { M.from = null; $('banner').classList.remove('on'); };
  }
  async function finishConnect(to) {
    const from = M.from; M.from = null; if (!from || from === to) { $('banner').classList.remove('on'); return; }
    $('banner').innerHTML = `<span>Търся пътя между <b>${esc(N.get(from)?.name)}</b> и <b>${esc(N.get(to)?.name || to)}</b>…</span>`;
    const q = new URLSearchParams([['n', from], ['n', to]]); if (S.live) q.set('active', 1);
    const d = await (await fetch('/connect.json?' + q)).json();
    take(d); snap();
    const p = d.pairs[0];
    $('banner').classList.remove('on');
    if (!p.paths.length) { R.list = []; $('path').innerHTML = `<b>${esc(N.get(from)?.name)}</b> и <b>${esc(N.get(to)?.name)}</b> не са свързани в регистъра. <button class="btn sm" id="pclose">Затвори</button>`; $('path').classList.add('on'); $('pclose').onclick = () => $('path').classList.remove('on'); return; }
    R.list = p.paths; showRoutes();
  }
  // the nodes and roles of a /connect.json answer (or of the Свързаности page) into the model
  function take(d) {
    for (const [id, n] of Object.entries(d.nodes)) node(id, { name: tc(n.name || id), ref: n.ref, n: n.contracts, eur: n.eur, links: n.links });
    const edges = []; for (const rs of Object.values(d.roles)) for (const r of rs) edges.push({ ...r, holder_name: d.nodes[r.holder]?.name, company_name: d.nodes[r.company]?.name });
    ingest(edges);
  }
  // every shortest route between the two is drawn and described at once
  const R = { list: [] };
  function showRoutes() {
    S.path = new Set(R.list.flat()); S.pathPairs = new Set(R.list.flatMap((r) => r.slice(1).map((y, k) => [r[k], y].sort().join('|'))));
    $('card').classList.remove('on'); draw(false);
    // frame the route itself, clear of the description panel on the left
    const w = $('path').offsetWidth || 360;
    const bb = cy.nodes('.route').boundingBox({ includeLabels: true }), left = Math.min(w + 30, cy.width() / 2), pad = 30;
    const aw = cy.width() - left - pad, ah = cy.height() - 2 * pad, z = Math.min(1.3, aw / bb.w, ah / bb.h);
    cy.zoom(z); cy.pan({ x: left + aw / 2 - (bb.x1 + bb.w / 2) * z, y: pad + ah / 2 - (bb.y1 + bb.h / 2) * z });
  }
  function route() {
    if (!S.path.size || !R.list.length) { $('path').classList.remove('on'); return; }
    const rolesOf = (a, b) => [...E.values()].filter((e) => (e.holder === a && e.company === b) || (e.holder === b && e.company === a));
    const live = (a, b) => !S.year || rolesOf(a, b).some((e) => inYear(e, S.year));
    const whole = (r) => r.slice(1).every((y, k) => live(r[k], y));
    const lens = new Set(R.list.map((r) => r.length - 1)), steps = R.list[0].length - 1, ok = R.list.filter(whole).length;
    $('path').innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px">
        <b>${R.list.length === 1 ? 'Един път' : `${R.list.length} пътя`}${lens.size === 1 ? ` по ${steps} ${steps === 1 ? 'стъпка' : 'стъпки'}` : ''}</b><button class="btn sm" id="pclose">Изчисти</button></div>
      ${S.year ? `<div class="${ok ? '' : 'warn'}" style="margin-bottom:8px">През ${S.year}: ${ok ? `${ok} от ${R.list.length} ${R.list.length === 1 ? 'пътя е цял' : 'пътя са цели'}` : 'нито един път не е цял'}</div>` : ''}
      ${R.list.map((r, i) => `<div style="padding:6px 0;border-top:1px solid var(--line-2)"><div class="mut" style="margin-bottom:2px">Път ${i + 1}${S.year ? (whole(r) ? ' · цял' : ' · прекъснат') : ''}</div>
        ${r.map((id, k) => `${k ? `<div class="r" style="${live(r[k - 1], id) ? '' : 'opacity:.45;text-decoration:line-through'}">↓ ${esc(rolesOf(r[k - 1], id).map((e) => roleTxt(e) + ' ' + when(e)).join(', '))}</div>` : ''}<div><b>${esc(N.get(id)?.name || id)}</b></div>`).join('')}</div>`).join('')}`;
    $('path').classList.add('on');
    $('pclose').onclick = () => { snap(); S.path = new Set(); S.pathPairs = new Set(); R.list = []; draw(); };
  }
  // ---------- years: slider and a slideshow that plays through them ----------
  let timer = null;
  const firstYear = () => { const ys = [...E.values()].map((e) => +String(e.from || '').slice(0, 4)).filter((y) => y > 1990); return Math.max(+$('year').min, Math.min(...ys, YEAR_NOW)); };
  const setYear = (y) => { S.year = y; if (y) $('year').value = y; $('byyear').checked = !!y; light(); };
  $('byyear').onchange = () => setYear($('byyear').checked ? +$('year').value : null);
  $('year').oninput = () => { stop(); setYear(+$('year').value); };
  function stop() { clearInterval(timer); timer = null; $('play').textContent = '▶ Пусни годините'; }
  $('play').onclick = () => {
    if (timer) return stop();
    let y = S.year && S.year < YEAR_NOW ? S.year : firstYear(); setYear(y);
    $('play').textContent = '❚❚ Пауза';
    timer = setInterval(() => { y += 1; if (y > YEAR_NOW) { stop(); return; } setYear(y); }, 1100);
  };
  $('onlylive').onchange = () => { S.live = $('onlylive').checked; light(); };
  // full screen: legend, controls and graph together, so the year slideshow can be recorded as a timelapse
  $('full').hidden = !document.fullscreenEnabled;
  $('full').onclick = () => (document.fullscreenElement ? document.exitFullscreen() : $('vz').requestFullscreen());
  document.addEventListener('fullscreenchange', () => {
    const on = document.fullscreenElement === $('vz');
    $('full').textContent = on ? 'Изход от цял екран' : 'На цял екран'; $('full').setAttribute('aria-pressed', on);
    requestAnimationFrame(() => { cy.resize(); cy.fit(undefined, 50); });
  });
  $('fit').onclick = () => (S.path.size ? cy.fit(cy.nodes('.route'), 60) : cy.fit(undefined, 50));
  $('undo').onclick = () => { if (!hist.length) return; const o = JSON.parse(hist.pop()); Object.assign(S, o, { expanded: new Set(o.expanded), hidden: new Set(o.hidden), added: new Set(o.added), path: new Set(o.path), pathPairs: new Set(o.pathPairs) });
    $('undo').disabled = !hist.length; $('onlylive').checked = S.live; if (!S.path.size) $('path').classList.remove('on'); draw(); setYear(S.year); };
  $('addq').addEventListener('combopick', async (e) => { const id = e.detail.node;
    await load(id);
    if (M.from) return finishConnect(id);
    snap(); S.added.add(id); draw(); card(id); });

  await load(FOCUS);
  if (opts.routes) {  // Свързаности: every chosen node on the drawing, all shortest paths lit
    const D = opts.routes;
    for (const id of D.ids) if (id !== FOCUS) { await load(id); S.added.add(id); }
    take(D); R.list = D.pairs.flatMap((p) => p.paths);
    if (R.list.length) { draw(true); showRoutes(); return; }
  }
  draw(true);
  // ?with=<node> opens the route from the object to it, ?y=<year> sets the year: a view can be shared
  const U = new URLSearchParams(location.search);
  if (/^[pcfl]:[\w:-]+$/.test(U.get('with') || '')) { M.from = FOCUS; await load(U.get('with')); await finishConnect(U.get('with')); }
  if (+U.get('y') > 1990) setYear(+U.get('y'));
}

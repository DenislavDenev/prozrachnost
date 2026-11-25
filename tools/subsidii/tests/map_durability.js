// Durability run of the map (/karta): the same transitions many times over, in a real browser. Paste into the page's console;
// it runs in the background and leaves its result in window.__durability (window.__progress counts steps).
// For every transition: the time from the change to a settled map (no request in flight, no flight, one layer) and the
// longest gap between two frames while it ran (a frozen page shows as a long gap); at the end the number of elements of
// the page, to see that nothing piles up. AGENTS.md 7: a change without a flight under 0.1 s from the cache, a flight
// as long as its animation (1.3 to 1.6 s), no freeze over 250 ms, no growing count of elements from round to round.
(() => {
  const ROUNDS = 5;
  const level = (l) => ({ run: () => document.querySelector(`#k-level a[data-l="${l}"]`).click(), name: 'level ' + l, flight: true });
  const select = (id, v) => ({ run: () => { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event('change')); }, name: `${id}=${v}` });
  const STEPS = [level('oblasti'), level('rayoni'), level('makrorayoni'), level('obshtini'), select('k-fond', 'efgz'), select('k-per', 'resident'),
    select('k-fond', 'total'), select('k-per', 'all'), select('k-fy', '2024'), select('k-fy', '2025')];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  window.__inflight = window.__inflight || 0;
  if (!window.__wrapped) {
    const f = window.fetch;
    window.fetch = async (...a) => { window.__inflight++; try { return await f(...a); } finally { window.__inflight--; } };
    window.__wrapped = true;
  }
  const svg = document.getElementById('k-svg');
  const settled = () => window.__inflight === 0 && svg.getAttribute('aria-busy') !== 'true' && svg.querySelector('.layers').children.length <= 1;
  async function step(s) {
    const t0 = performance.now();
    let last = t0, gap = 0, run = true;
    const tick = (t) => { gap = Math.max(gap, t - last); last = t; if (run) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    s.run();
    await sleep(30);
    while (!settled() && performance.now() - t0 < 30000) await sleep(20);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    run = false;
    return { name: s.name, ms: Math.round(performance.now() - t0), gap: Math.round(gap), nodes: document.getElementsByTagName('*').length };
  }
  window.__durability = 'running';
  (async () => {
    const out = [];
    for (let r = 0; r < ROUNDS; r++) for (const s of STEPS) { out.push(await step(s)); window.__progress = out.length; }
    const by = {};
    for (const o of out) (by[o.name] ||= []).push(o);
    const med = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
    window.__durability = {
      steps: Object.fromEntries(Object.entries(by).map(([k, v]) => [k, { medianMs: med(v.map((o) => o.ms)), maxMs: Math.max(...v.map((o) => o.ms)), maxGapMs: Math.max(...v.map((o) => o.gap)) }])),
      nodesFirstRound: out[STEPS.length - 1].nodes, nodesLastRound: out[out.length - 1].nodes, n: out.length,
    };
  })();
})();

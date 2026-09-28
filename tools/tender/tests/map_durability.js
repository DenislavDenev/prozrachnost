// Durability run of the map (/map): the same transitions many times over, in a real browser. Paste into the page's
// console; it runs in the background and leaves its result in window.__durability (window.__progress counts steps).
// For each kind of transition: the time from the click to a settled map (no request in flight, no flight, one
// layer), and the longest gap between two frames while it ran (a frozen page shows as a long gap); at the end the
// number of DOM nodes, to see that nothing piles up. Results of 28.09.2026 are in docs/methodology.md, section 7.
(() => {
  const ROUNDS = 5;
  const STEPS = ['#by [data-k="supplier"]', '#o [data-k="eu"]', '#o [data-k="world"]', '#o [data-k="eu"]', '#o [data-k="bg"]',
    '#o [data-k="world"]', '#o [data-k="bg"]', '#by [data-k="buyer"]', '#level [data-k="oblast"]', '#level [data-k="muni"]'];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  window.__inflight = window.__inflight || 0;
  if (!window.__wrapped) {
    const f = window.fetch;
    window.fetch = async (...a) => { window.__inflight++; try { return await f(...a); } finally { window.__inflight--; } };
    window.__wrapped = true;
  }
  const settled = () => window.__inflight === 0 && !document.querySelector('svg.flying, svg.loading')
    && [...document.querySelectorAll('g.layers')].every((g) => g.children.length <= 1);
  async function step(sel) {
    const b = document.querySelector(sel);
    if (!b) return { sel, skipped: true };
    const t0 = performance.now();
    let last = t0, gap = 0, run = true;
    const tick = (t) => { gap = Math.max(gap, t - last); last = t; if (run) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    b.click();
    await sleep(30);
    while (!settled() && performance.now() - t0 < 30000) await sleep(20);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    run = false;
    return { sel, ms: Math.round(performance.now() - t0), gap: Math.round(gap), nodes: document.getElementsByTagName('*').length };
  }
  window.__durability = 'running';
  (async () => {
    const out = [];
    for (let r = 0; r < ROUNDS; r++) for (const s of STEPS) { out.push(await step(s)); window.__progress = out.length; }
    const by = {};
    for (const o of out.filter((o) => !o.skipped)) (by[o.sel] ||= []).push(o);
    const med = (a) => [...a].sort((x, y) => x - y)[Math.floor(a.length / 2)];
    window.__durability = {
      steps: Object.entries(by).map(([s, a]) => ({ step: s, n: a.length, medianMs: med(a.map((o) => o.ms)), maxMs: Math.max(...a.map((o) => o.ms)),
        medianGap: med(a.map((o) => o.gap)), maxGap: Math.max(...a.map((o) => o.gap)) })),
      nodesFirstRound: out[STEPS.length - 1].nodes, nodesLastRound: out[out.length - 1].nodes,
    };
  })();
  return 'started';
})();

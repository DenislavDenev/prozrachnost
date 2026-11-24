-- pub_subsidii.health: what the sources page counts its problems from (the freshness of the archive is counted at the request)
SELECT 'source', f.source, f.title_bg, NULL::boolean, f.last_ok_at, (extract(epoch FROM f.stale_after) / 3600)::int, NULL::text
FROM ops.freshness f WHERE f.tool = 'subsidii' AND f.stale_after IS NOT NULL
UNION ALL
SELECT 'hold', h.source || ' ' || h.ref, 'ФГ ' || h.ref || ': задържан файл', NULL, h.first_at, (extract(epoch FROM s.hold_alert_after) / 3600)::int, h.reason
FROM ops.hold h JOIN ops.source s USING (tool, source) WHERE h.tool = 'subsidii'
UNION ALL
SELECT 'check', c.name, c.title_bg, CASE WHEN c.gating THEN c.ok ELSE true END, c.checked_at, NULL, 'очаквано ' || c.expected || ', получено ' || c.actual
FROM ops.check_latest c WHERE c.tool = 'subsidii' AND c.kind NOT IN ('publish', 'parity')
UNION ALL
SELECT 'run', x.step, 'стъпка ' || x.step, x.status <> 'failed', x.started_at, NULL, left(x.error, 300)
FROM (SELECT DISTINCT ON (step) step, status, started_at, error FROM ops.run WHERE tool = 'subsidii' AND status NOT IN ('skipped', 'running') ORDER BY step, run_id DESC) x

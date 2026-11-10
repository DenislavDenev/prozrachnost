SELECT url, last_read, last_ok, status, error, rows FROM naselenie.source_state
UNION ALL
SELECT s.url, f.last_read_at, f.last_ok_at,
       CASE WHEN f.last_status IN ('loaded', 'confirmed', 'unchanged') THEN 'ok' WHEN f.last_status = 'held' THEN 'held'
            WHEN f.last_status IS NULL THEN 'none' ELSE 'invalid' END,
       NULL, (SELECT count(*)::int FROM core.settlement WHERE gone_at IS NULL)
FROM ops.freshness f JOIN ops.source s USING (tool, source)
WHERE f.tool = 'danni' AND f.source = 'ekatte'

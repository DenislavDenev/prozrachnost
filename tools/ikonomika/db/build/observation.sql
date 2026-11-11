-- The clean tables from the current loads of the landing zone, in one transaction (ingest/store.py build()).
-- A fact that is new is added; a fact whose value or flag the source changed, or that left a confirmed answer, gets
-- superseded_at and its new version is added. Nothing is deleted.

-- the series: one per indicator, dimensions and place
INSERT INTO ikonomika.series (indicator_code, dims, geo)
SELECT DISTINCT 'ikonomika.' || o.indicator, o.dims, o.geo
FROM ikonomika_src.observation o
JOIN ops.load l ON l.load_id = o.load_id AND l.is_current
ON CONFLICT (indicator_code, geo, dims) DO NOTHING;

-- a place of Bulgaria points to core.nuts, also when core.nuts learns the code after the series was made
UPDATE ikonomika.series s SET nuts_code = n.nuts_code
FROM core.nuts n WHERE n.nuts_code = s.geo AND s.nuts_code IS NULL;

-- Two current loads can hold the same cell: the windows of the BNB archive overlap (until 11.11.2026 the daily read
-- started 31 days back, the full read at the quarter). The newest load gives the number, as the newest write did in
-- the old database.
CREATE TEMP TABLE build_fact ON COMMIT DROP AS
SELECT DISTINCT ON (s.series_id, o.period) s.series_id, o.period, o.value, o.flag, o.load_id, l.loaded_at
FROM ikonomika_src.observation o
JOIN ops.load l ON l.load_id = o.load_id AND l.is_current
JOIN ikonomika.series s ON s.indicator_code = 'ikonomika.' || o.indicator AND s.geo = o.geo AND s.dims = o.dims
ORDER BY s.series_id, o.period, l.loaded_at DESC, l.load_id DESC;
CREATE UNIQUE INDEX ON build_fact (series_id, period);
ANALYZE build_fact;

UPDATE ikonomika.observation c SET superseded_at = now()
WHERE c.superseded_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM build_fact b WHERE b.series_id = c.series_id AND b.period = c.period
                    AND b.value IS NOT DISTINCT FROM c.value AND b.flag IS NOT DISTINCT FROM c.flag);

INSERT INTO ikonomika.observation (series_id, period, period_kind, period_start, period_end, value, flag, load_id, first_seen_at)
SELECT b.series_id, b.period, p.period_kind, p.period_start, p.period_end, b.value, b.flag, b.load_id, b.loaded_at
FROM build_fact b
CROSS JOIN LATERAL ikonomika.period_of(b.period) p
WHERE NOT EXISTS (SELECT 1 FROM ikonomika.observation c
                  WHERE c.series_id = b.series_id AND c.period = b.period AND c.superseded_at IS NULL);

-- the names of the categories from the answers that are current: a name is updated, never removed
INSERT INTO ikonomika.dim_label (indicator_code, dim, code, label)
SELECT DISTINCT ON (x.indicator, x.dim, x.code) 'ikonomika.' || x.indicator, x.dim, x.code, x.label
FROM ikonomika_src.label x JOIN ops.load l ON l.load_id = x.load_id AND l.is_current
ORDER BY x.indicator, x.dim, x.code, l.loaded_at DESC
ON CONFLICT (indicator_code, dim, code) DO UPDATE SET label = EXCLUDED.label WHERE ikonomika.dim_label.label <> EXCLUDED.label;

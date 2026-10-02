-- Gold of one snapshot (%(fy)s, %(snap)s): the aggregates, kept for every snapshot. Run with client-side parameters.
-- The recipients are the ОБЩО rows whose span contains the snapshot day; the payments the payment rows likewise.
DELETE FROM gold.observation WHERE dataset = 'subsidii.paid' AND period_start = %(starts)s AND published_at = %(snap)s;
DELETE FROM gold.summary WHERE fy = %(fy)s AND snap_day = %(snap)s;
DELETE FROM gold.municipality_fy WHERE fy = %(fy)s AND snap_day = %(snap)s;
DELETE FROM gold.payment_agg WHERE fy = %(fy)s AND snap_day = %(snap)s;

CREATE TEMP TABLE r ON COMMIT DROP AS
SELECT h.id, g.kind, g.municipality_id, h.efgz, h.ezfrs, h.nb, h.total
FROM silver.holder h JOIN gold.beneficiary g ON g.beneficiary_id = h.beneficiary_id
WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s);

CREATE TEMP TABLE p ON COMMIT DROP AS
SELECT g.kind, g.municipality_id, p.measure_id, p.efgz, p.ezfrs, p.nb
FROM silver.payment p JOIN gold.beneficiary g ON g.beneficiary_id = p.beneficiary_id
WHERE p.fy = %(fy)s AND p.from_day <= %(snap)s AND (p.to_day IS NULL OR p.to_day >= %(snap)s);

INSERT INTO gold.municipality_fy (fy, snap_day, municipality_id, kind, recipients, efgz, ezfrs, nb, total)
SELECT %(fy)s, %(snap)s, municipality_id, kind, count(*), sum(efgz), sum(ezfrs), sum(nb), sum(total)
FROM r WHERE municipality_id IS NOT NULL GROUP BY municipality_id, kind;

INSERT INTO gold.payment_agg (fy, snap_day, municipality_id, measure_id, kind, payments, efgz, ezfrs, nb, total)
SELECT %(fy)s, %(snap)s, municipality_id, measure_id, kind, count(*), sum(efgz), sum(ezfrs), sum(nb),
       coalesce(sum(efgz), 0) + coalesce(sum(ezfrs), 0) + coalesce(sum(nb), 0)
FROM p WHERE municipality_id IS NOT NULL GROUP BY municipality_id, measure_id, kind;

-- scopes: all recipients and each kind. The top 1 percent is of the recipient ROWS (a name in a municipality), never of people.
INSERT INTO gold.summary (fy, snap_day, scope, recipients, payments, efgz, ezfrs, nb, total, top1_n, top1_total, build_id)
WITH scoped AS (SELECT 'all' AS scope, * FROM r UNION ALL SELECT kind, * FROM r),
ranked AS (SELECT scope, total, row_number() OVER (PARTITION BY scope ORDER BY total DESC NULLS LAST) AS rk,
                  count(*) OVER (PARTITION BY scope) AS n FROM scoped),
top AS (SELECT scope, count(*) AS top1_n, sum(total) AS top1_total FROM ranked WHERE rk <= greatest(1, ceil(n * 0.01)) GROUP BY scope),
agg AS (SELECT scope, count(*) AS recipients, sum(efgz) AS efgz, sum(ezfrs) AS ezfrs, sum(nb) AS nb, sum(total) AS total FROM scoped GROUP BY scope),
pay AS (SELECT s.scope, count(p.*) AS payments FROM (VALUES ('all'), ('legal'), ('sole_trader'), ('natural')) s(scope)
        LEFT JOIN p ON s.scope = 'all' OR p.kind = s.scope GROUP BY s.scope)
SELECT %(fy)s, %(snap)s, agg.scope, agg.recipients, pay.payments, agg.efgz, agg.ezfrs, agg.nb, agg.total, top.top1_n, top.top1_total, %(build)s
FROM agg JOIN top USING (scope) JOIN pay USING (scope);

-- the municipalities the report names that the list does not: kept apart with their amount (never dropped silently)
DELETE FROM gold.unmatched WHERE kind = 'municipality';
INSERT INTO gold.unmatched (kind, key, n, amount, reason)
SELECT 'municipality', b.oblast || ' | ' || b.obshtina, count(*), sum(h.total), 'няма такава община в справочника'
FROM silver.holder h JOIN silver.beneficiary b ON b.id = h.beneficiary_id JOIN gold.beneficiary g ON g.beneficiary_id = h.beneficiary_id
WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s) AND g.municipality_id IS NULL
GROUP BY b.oblast, b.obshtina;

-- the shared observation contract: the amount of a fund in a municipality for a financial year, with the original beside it
INSERT INTO gold.observation (dataset, indicator, place_id, scope, period_kind, period_start, period_end, value, unit, value_original, unit_original,
                              status, published_at, read_at, source_url, raw_sha256, build_id)
SELECT 'subsidii.paid', i.indicator, m.municipality_id, m.scope, 'fiscal-year', y.starts, y.ends,
       CASE WHEN y.eur_per_unit IS NULL THEN NULL ELSE round(i.amount * y.eur_per_unit, 2) END,
       CASE WHEN y.eur_per_unit IS NULL THEN NULL ELSE 'EUR' END, i.amount, y.currency,
       CASE WHEN %(snap)s <= y.ends THEN 'provisional' ELSE 'final' END, %(snap)s, %(snap)s,
       'https://seu.dfz.bg/seu/f?p=727:8110:::NO', %(sha)s, %(build)s
FROM (SELECT municipality_id, scope, sum(efgz) AS efgz, sum(ezfrs) AS ezfrs, sum(nb) AS nb, sum(total) AS total
      FROM (SELECT municipality_id, 'all' AS scope, efgz, ezfrs, nb, total FROM gold.municipality_fy WHERE fy = %(fy)s AND snap_day = %(snap)s
            UNION ALL SELECT municipality_id, kind, efgz, ezfrs, nb, total FROM gold.municipality_fy WHERE fy = %(fy)s AND snap_day = %(snap)s) x
      GROUP BY municipality_id, scope) m
CROSS JOIN LATERAL (VALUES ('subsidii.paid.efgz', m.efgz), ('subsidii.paid.ezfrs', m.ezfrs), ('subsidii.paid.nb', m.nb), ('subsidii.paid.total', m.total)) i(indicator, amount)
JOIN gold.fiscal_year y ON y.fy = %(fy)s
WHERE i.amount IS NOT NULL;

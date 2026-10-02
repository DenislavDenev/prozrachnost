-- One day of gold.category_day from silver, in euro. Parameters: %(d)s the day, %(ms)s its month's first day,
-- %(pms)s the first day of the month 7 days before it (a chain's earlier price may be there), %(rate)s the BGN per EUR.
-- Flags: 29 = retail anomalies (1 retail > 5000, 4 category not in the list, 8 conflicting prices, 16 jump);
-- 31 also takes 2 (promotion not below the retail price). Copies of another chain's file and chains that did not file are out.
DROP TABLE IF EXISTS pg_temp.cur;
-- the chain's previous filing (up to 7 days before): the day its price is compared with for the price index
CREATE TEMP TABLE prevday ON COMMIT DROP AS
SELECT cd.chain_id, max(cd.day) AS day FROM silver.chain_day cd
WHERE cd.day < %(d)s AND cd.day >= %(d)s::date - 7 AND cd.filed AND cd.copy_of IS NULL GROUP BY cd.chain_id;
CREATE TEMP TABLE cur ON COMMIT DROP AS
SELECT s.store_id, s.product_id, st.chain_id, c.eik, g.municipality_id, g.nuts3, c.national_price, s.category,
       s.retail::numeric / 10000 / CASE WHEN cd.currency = 'BGN' THEN %(rate)s ELSE 1 END AS retail,
       CASE WHEN s.promo > 0 AND s.promo < s.retail AND (s.flags & 31) = 0
            THEN s.promo::numeric / 10000 / CASE WHEN cd.currency = 'BGN' THEN %(rate)s ELSE 1 END END AS promo,
       pv.day AS prev_day, CASE WHEN pcd.currency = 'BGN' THEN %(rate)s ELSE 1 END AS prev_rate
FROM silver.price_span s
JOIN silver.store st USING (store_id)
JOIN silver.chain_day cd ON cd.chain_id = st.chain_id AND cd.day = %(d)s AND cd.filed AND cd.copy_of IS NULL
JOIN gold.store g ON g.store_id = s.store_id
JOIN gold.chain c ON c.eik = g.chain_eik
LEFT JOIN prevday pv ON pv.chain_id = st.chain_id
LEFT JOIN silver.chain_day pcd ON pcd.chain_id = pv.chain_id AND pcd.day = pv.day
WHERE s.from_day >= %(ms)s AND s.from_day <= %(d)s AND coalesce(s.to_day, 'infinity') >= %(d)s
  AND (s.flags & 29) = 0 AND s.category BETWEEN 1 AND 101;
CREATE INDEX ON cur (store_id, product_id);
ANALYZE cur;

-- the same shops and products at the previous filing: the matched rows of the price index (equal prices count as matched)
CREATE TEMP TABLE pair ON COMMIT DROP AS
SELECT n.eik, n.municipality_id, n.nuts3, n.national_price, n.category,
       ln(n.retail / (o.retail::numeric / 10000 / n.prev_rate)) AS lr
FROM cur n
JOIN silver.price_span o ON o.store_id = n.store_id AND o.product_id = n.product_id
     AND o.from_day >= %(pms)s AND o.from_day <= n.prev_day AND coalesce(o.to_day, 'infinity') >= n.prev_day
     AND (o.flags & 29) = 0 AND o.retail > 0
WHERE n.prev_day IS NOT NULL;

DELETE FROM gold.municipality_day WHERE day = %(d)s;
INSERT INTO gold.municipality_day (day, municipality_id, n_stores, n_chains)
SELECT %(d)s, municipality_id, count(DISTINCT store_id), count(DISTINCT chain_id) FROM cur
WHERE municipality_id IS NOT NULL AND NOT national_price GROUP BY municipality_id;

DELETE FROM gold.category_day WHERE day = %(d)s;

INSERT INTO gold.category_day (day, scope, key, category, n_prices, n_stores, n_chains, min_eur, median_eur, max_eur,
                               promo_prices, promo_share, median_effective_eur, matched, changed, sum_ln)
SELECT %(d)s, x.scope, x.key, x.category, x.n_prices, x.n_stores, x.n_chains, x.min_eur, x.median_eur, x.max_eur, x.promo_prices,
       x.promo_prices::numeric / x.n_prices, x.median_eff, coalesce(p.matched, 0), coalesce(p.changed, 0), coalesce(p.sum_ln, 0)
FROM (
  SELECT 'country' AS scope, '' AS key, category, count(*) AS n_prices, count(DISTINCT store_id) AS n_stores, count(DISTINCT chain_id) AS n_chains,
         min(retail) AS min_eur, percentile_cont(0.5) WITHIN GROUP (ORDER BY retail) AS median_eur, max(retail) AS max_eur,
         count(promo) AS promo_prices, percentile_cont(0.5) WITHIN GROUP (ORDER BY coalesce(promo, retail)) AS median_eff
  FROM cur GROUP BY category
  UNION ALL
  SELECT 'chain', eik, category, count(*), count(DISTINCT store_id), 1, min(retail), percentile_cont(0.5) WITHIN GROUP (ORDER BY retail), max(retail),
         count(promo), percentile_cont(0.5) WITHIN GROUP (ORDER BY coalesce(promo, retail))
  FROM cur GROUP BY eik, category
  UNION ALL
  SELECT 'oblast', nuts3, category, count(*), count(DISTINCT store_id), count(DISTINCT chain_id), min(retail), percentile_cont(0.5) WITHIN GROUP (ORDER BY retail), max(retail),
         count(promo), percentile_cont(0.5) WITHIN GROUP (ORDER BY coalesce(promo, retail))
  FROM cur WHERE nuts3 IS NOT NULL AND NOT national_price GROUP BY nuts3, category
  UNION ALL
  SELECT 'municipality', municipality_id, category, count(*), count(DISTINCT store_id), count(DISTINCT chain_id), min(retail), percentile_cont(0.5) WITHIN GROUP (ORDER BY retail), max(retail),
         count(promo), percentile_cont(0.5) WITHIN GROUP (ORDER BY coalesce(promo, retail))
  FROM cur WHERE municipality_id IS NOT NULL AND NOT national_price GROUP BY municipality_id, category
) x
LEFT JOIN (
  SELECT 'country' AS scope, '' AS key, category, count(*) AS matched, count(*) FILTER (WHERE lr <> 0) AS changed, sum(lr) AS sum_ln FROM pair GROUP BY category
  UNION ALL SELECT 'chain', eik, category, count(*), count(*) FILTER (WHERE lr <> 0), sum(lr) FROM pair GROUP BY eik, category
  UNION ALL SELECT 'oblast', nuts3, category, count(*), count(*) FILTER (WHERE lr <> 0), sum(lr) FROM pair WHERE nuts3 IS NOT NULL AND NOT national_price GROUP BY nuts3, category
  UNION ALL SELECT 'municipality', municipality_id, category, count(*), count(*) FILTER (WHERE lr <> 0), sum(lr) FROM pair WHERE municipality_id IS NOT NULL AND NOT national_price GROUP BY municipality_id, category
) p ON p.scope = x.scope AND p.key = x.key AND p.category = x.category;

-- promotions: a promotional price is a real discount when it is at least 1 percent below the lowest retail price of the same shop
-- and product in the 30 days before; `raised` marks a retail price that was put up (1 percent or more) above that lowest price.
DELETE FROM gold.promo_day WHERE day = %(d)s;
CREATE TEMP TABLE prior ON COMMIT DROP AS
SELECT n.store_id, n.product_id, min(o.retail::numeric / 10000 / CASE WHEN cd2.currency = 'BGN' THEN %(rate)s ELSE 1 END) AS prior
FROM cur n
JOIN silver.price_span o ON o.store_id = n.store_id AND o.product_id = n.product_id AND o.from_day >= %(p30)s AND o.from_day < %(d)s
     AND coalesce(o.to_day, 'infinity') >= %(d)s::date - 30 AND (o.flags & 29) = 0
JOIN silver.store st2 ON st2.store_id = o.store_id
JOIN silver.chain_day cd2 ON cd2.chain_id = st2.chain_id AND cd2.day = o.from_day
WHERE n.promo IS NOT NULL GROUP BY 1, 2;
INSERT INTO gold.promo_day (day, chain_eik, n_promos, n_real, n_none, n_raised, n_nohistory, avg_declared, avg_real, window_days)
SELECT %(d)s, n.eik, count(*), count(*) FILTER (WHERE p.prior IS NOT NULL AND n.promo <= p.prior * 0.99),
       count(*) FILTER (WHERE p.prior IS NOT NULL AND n.promo > p.prior * 0.99),
       count(*) FILTER (WHERE p.prior IS NOT NULL AND n.promo > p.prior * 0.99 AND n.retail >= p.prior * 1.01),
       count(*) FILTER (WHERE p.prior IS NULL),
       avg(1 - n.promo / n.retail), avg(1 - n.promo / p.prior) FILTER (WHERE p.prior IS NOT NULL),
       least(30, %(d)s::date - (SELECT min(day) FROM silver.day WHERE status = 'built'))
FROM cur n LEFT JOIN prior p ON p.store_id = n.store_id AND p.product_id = n.product_id
WHERE n.promo IS NOT NULL GROUP BY n.eik;

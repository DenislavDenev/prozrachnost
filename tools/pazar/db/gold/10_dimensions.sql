-- Dimensions of gold from silver. Run after every built day; cheap (a few thousand stores).
INSERT INTO gold.chain (eik, name_bg, eik_valid, first_day, national_price)
SELECT c.eik, c.name, c.eik_valid, c.first_day,
       -- a chain whose only shop says in its name that it is online (an address with .bg or the word "онлайн") sells to the whole country
       coalesce((SELECT count(*) = 1 AND bool_and(s.name ~* '(онлайн|online|\.bg)') FROM silver.store s WHERE s.chain_id = c.chain_id), false)
FROM silver.chain c
ON CONFLICT (eik) DO UPDATE SET name_bg = EXCLUDED.name_bg, eik_valid = EXCLUDED.eik_valid, national_price = EXCLUDED.national_price;

INSERT INTO gold.store (store_id, chain_eik, place_raw, ekatte, municipality_id, oblast, nuts3, name, first_day)
SELECT s.store_id, c.eik, s.place_raw, s.ekatte, p.municipality_id, p.oblast, p.nuts3, s.name, s.first_day
FROM silver.store s JOIN silver.chain c USING (chain_id) LEFT JOIN gold.place p ON p.ekatte = s.ekatte
ON CONFLICT (store_id) DO UPDATE SET ekatte = EXCLUDED.ekatte, municipality_id = EXCLUDED.municipality_id, oblast = EXCLUDED.oblast, nuts3 = EXCLUDED.nuts3;

INSERT INTO gold.product (product_id, chain_eik, code, name, category, first_day)
SELECT p.product_id, c.eik, p.code, p.name, p.category, p.first_day
FROM silver.product p JOIN silver.chain c USING (chain_id)
WHERE p.product_id > (SELECT coalesce(max(product_id), 0) FROM gold.product)
ON CONFLICT (product_id) DO NOTHING;

-- stores that cannot be put on a municipality: the place is not an ЕКАТТЕ of the register
TRUNCATE gold.unmatched;
INSERT INTO gold.unmatched (kind, key, n, reason)
SELECT 'store_place', coalesce(place_raw, ''), count(*), CASE WHEN ekatte IS NULL THEN 'не е код на населено място' ELSE 'няма го в ЕКАТТЕ' END
FROM gold.store WHERE municipality_id IS NULL GROUP BY place_raw, ekatte;

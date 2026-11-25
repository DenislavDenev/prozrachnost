-- Пазар, gold: dimensions with the shared keys (ЕИК, ЕКАТТЕ, municipality id) and the daily observations.
-- Money here is in euro (numeric): the BGN rows of silver are converted at 1.95583 by the currency found for the chain's day.
CREATE TABLE gold.category (
  code smallint PRIMARY KEY, name_bg text NOT NULL,       -- the list of КЗП (art. 55b ZVERB), source_url is the document
  group_bg text NOT NULL,                                  -- the group heading of the portal (kolkostruva.bg/compare)
  source_url text NOT NULL, group_source_url text NOT NULL);
CREATE TABLE gold.municipality (
  id text PRIMARY KEY, name_bg text NOT NULL, oblast text NOT NULL, nuts3 text NOT NULL, nuts2 text NOT NULL, nuts1 text NOT NULL);
CREATE TABLE gold.place (
  ekatte text PRIMARY KEY, name text NOT NULL, kind text NOT NULL, obshtina text NOT NULL,
  municipality_id text NOT NULL REFERENCES gold.municipality, oblast text NOT NULL, nuts3 text NOT NULL);
CREATE TABLE gold.chain (
  eik text PRIMARY KEY, name_bg text NOT NULL, eik_valid boolean NOT NULL, first_day date NOT NULL,
  national_price boolean NOT NULL,          -- one online shop for the whole country: never a shop on the map
  source text NOT NULL DEFAULT 'kolkostruva');
CREATE TABLE gold.store (
  store_id integer PRIMARY KEY, chain_eik text NOT NULL REFERENCES gold.chain, place_raw text NOT NULL, ekatte text,
  municipality_id text, oblast text, nuts3 text, name text NOT NULL, first_day date NOT NULL);
CREATE INDEX store_chain ON gold.store (chain_eik);
CREATE INDEX store_municipality ON gold.store (municipality_id);
CREATE TABLE gold.product (
  product_id integer PRIMARY KEY, chain_eik text NOT NULL REFERENCES gold.chain, code text NOT NULL, name text NOT NULL,
  category integer, first_day date NOT NULL);
CREATE INDEX product_chain_code ON gold.product (chain_eik, code);
CREATE INDEX product_category ON gold.product (category);
CREATE TABLE gold.unmatched (
  kind text NOT NULL, key text NOT NULL, n integer NOT NULL, reason text NOT NULL, PRIMARY KEY (kind, key));

-- one row per day, category, and scope: the whole country, a chain (key = ЕИК), an oblast (key = NUTS 3 code) or a
-- municipality (key = municipality id). Only rows that are not marked anomalies and are in the list of 101 categories.
CREATE TABLE gold.category_day (
  day date NOT NULL, scope text NOT NULL, key text NOT NULL, category smallint NOT NULL,
  n_prices integer NOT NULL, n_stores integer NOT NULL, n_chains integer NOT NULL,
  min_eur numeric(12,4) NOT NULL, median_eur numeric(12,4) NOT NULL, max_eur numeric(12,4) NOT NULL,
  promo_prices integer NOT NULL, promo_share numeric(7,6) NOT NULL, median_effective_eur numeric(12,4) NOT NULL,
  matched integer NOT NULL DEFAULT 0, changed integer NOT NULL DEFAULT 0, sum_ln numeric(16,8) NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope, key, category)
) PARTITION BY RANGE (day);
CREATE INDEX category_day_lookup ON gold.category_day (scope, key, category, day);
CREATE INDEX category_day_brin ON gold.category_day USING brin (day);

-- shops and chains that filed in a municipality on a day (the map shows a municipality only with 3 shops of 2 chains)
CREATE TABLE gold.municipality_day (
  day date NOT NULL, municipality_id text NOT NULL, n_stores integer NOT NULL, n_chains integer NOT NULL,
  PRIMARY KEY (day, municipality_id));

CREATE TABLE gold.day (
  day date PRIMARY KEY, built_at timestamptz NOT NULL DEFAULT now(), rows integer NOT NULL, secs numeric,
  silver_sha256 text NOT NULL);       -- the ZIP the day was built from

CREATE FUNCTION gold.ensure_month(d date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE s date := date_trunc('month', d)::date;
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS gold.category_day_%s PARTITION OF gold.category_day FOR VALUES FROM (%L) TO (%L)',
                 to_char(s, 'YYYYMM'), s, (s + interval '1 month')::date);
END $$;

-- the flags that keep a price out of the retail figures: 1 retail > 5000, 4 category not in the list, 8 conflicting
-- prices, 16 jump; 2 (promotion not below the price) keeps only the promotion out. See ingest/parse.py.

-- Пазар: the bronze pointer, the silver tables shaped like the source, the staging table of one day.
-- Prices are kept as spans (store, product, from_day, to_day, price): a row lasts while the price is the same.
-- Money is an integer in 1e-4 of the unit the chain submitted (BGN before its switch to the euro, EUR after);
-- the currency of a chain's day is in silver.chain_day.currency, found from the data (see docs/methodology.md).
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS stage;
CREATE SCHEMA IF NOT EXISTS gold;

-- bronze: where the raw ZIP is (the archive of Наблюдател; nothing is copied) and what we read from it
CREATE TABLE ops.raw_file (
  source text NOT NULL, ref text NOT NULL, path text NOT NULL, sha256 text NOT NULL, bytes bigint NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, ref, sha256));
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL);  -- new-record | rewritten | removed | held | confirmed | invalid | gone
CREATE INDEX change_log_ref ON ops.change_log (source, ref, detected_at);
CREATE TABLE ops.held (ref text PRIMARY KEY, sha256 text NOT NULL, first_seen timestamptz NOT NULL, reason text);
CREATE TABLE ops.job_run (
  id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz, status text, report jsonb);

-- silver
CREATE TABLE silver.chain (
  chain_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  eik text NOT NULL UNIQUE,              -- from the file name, "BG" removed, text: leading zeros stay
  eik_valid boolean NOT NULL,            -- the check digit is right
  name text NOT NULL,                    -- "<chain> (<firm>)" as in the file name, the last one seen
  first_day date NOT NULL);
CREATE TABLE silver.store (
  store_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chain_id integer NOT NULL REFERENCES silver.chain,
  place_raw text NOT NULL,               -- "Населено място" as submitted
  ekatte text,                           -- five digits when place_raw is one (the files lose leading zeros)
  district text,                         -- 68134-02 when the file gives a district of Sofia, Plovdiv or Varna
  name text NOT NULL,                    -- "Търговски обект" as submitted (name and address)
  first_day date NOT NULL,
  UNIQUE (chain_id, place_raw, name));
CREATE TABLE silver.product (
  product_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chain_id integer NOT NULL REFERENCES silver.chain,
  code text NOT NULL,                    -- "Код на продукта", the chain's own code, text: "001314" is not "1314"
  name text NOT NULL,                    -- the last name seen
  category integer,                      -- the last category seen
  first_day date NOT NULL,
  UNIQUE (chain_id, code));
CREATE TABLE silver.day (
  day date PRIMARY KEY,
  status text NOT NULL,                  -- built | held | invalid
  zip_sha256 text NOT NULL, zip_path text NOT NULL, zip_bytes bigint NOT NULL,
  files integer, chains integer, stores integer,
  records integer, valid integer, dups integer, bad integer, blank integer,
  spans_opened integer, spans_closed integer,
  anomalies jsonb NOT NULL DEFAULT '{}', note text,
  built_at timestamptz NOT NULL DEFAULT now(), build_secs numeric);
CREATE TABLE silver.chain_day (
  day date NOT NULL, chain_id integer NOT NULL REFERENCES silver.chain,
  file_name text NOT NULL, delimiter text, bom boolean, encoding text, truncated boolean NOT NULL DEFAULT false,
  records integer NOT NULL, valid integer NOT NULL, dups integer NOT NULL, bad integer NOT NULL, stores integer NOT NULL,
  filed boolean NOT NULL,                -- the file holds prices that were read (not an unreadable or empty file)
  error text,                            -- the whole file is unreadable: header, name
  copy_of text,                          -- the file repeats the rows of another chain's file: ЕИК, or "group"
  currency text NOT NULL,                -- BGN | EUR, found from the data
  matched integer, switch_share numeric, -- evidence for the currency: rows with an earlier price, share at the rate
  PRIMARY KEY (day, chain_id));
CREATE INDEX chain_day_chain ON silver.chain_day (chain_id, day);
CREATE TABLE silver.bad_row (
  day date NOT NULL, chain_id integer REFERENCES silver.chain, file_name text NOT NULL,
  line_no integer NOT NULL, reason text NOT NULL, raw text NOT NULL);
CREATE INDEX bad_row_day ON silver.bad_row (day, chain_id);

CREATE TABLE silver.price_span (
  store_id integer NOT NULL, product_id integer NOT NULL,
  from_day date NOT NULL, to_day date,   -- NULL: still the price of the last built day of the month
  retail integer NOT NULL, promo integer, category integer, flags smallint NOT NULL
) PARTITION BY RANGE (from_day);
-- a span never crosses a month: on the 1st the standing prices are written again, so a day is read from one partition
CREATE INDEX price_span_open ON silver.price_span (store_id, product_id) WHERE to_day IS NULL;
CREATE INDEX price_span_product ON silver.price_span (product_id, from_day);
CREATE INDEX price_span_from_brin ON silver.price_span USING brin (from_day);

CREATE FUNCTION silver.ensure_month(d date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE s date := date_trunc('month', d)::date;
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS silver.price_span_%s PARTITION OF silver.price_span FOR VALUES FROM (%L) TO (%L)',
                 to_char(s, 'YYYYMM'), s, (s + interval '1 month')::date);
END $$;

CREATE UNLOGGED TABLE stage.day_row (
  chain_id integer NOT NULL, store_id integer NOT NULL, product_id integer NOT NULL,
  retail integer NOT NULL, promo integer, category integer, flags smallint NOT NULL);
CREATE UNLOGGED TABLE stage.closed (store_id integer NOT NULL, product_id integer NOT NULL, retail integer NOT NULL);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pazar_web') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA ops, silver, gold TO pazar_web';
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA ops, silver, gold TO pazar_web';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA ops, silver, gold GRANT SELECT ON TABLES TO pazar_web';
  END IF;
END $$;

-- Субсидии и помощи, gold: dimensions with the shared keys (id of the municipality) and the observations per snapshot.
-- Amounts keep the currency of the year; gold.fiscal_year.eur_per_unit converts (BGN at 1.95583), NULL while the currency
-- of a year is not confirmed. Aggregates are kept for EVERY snapshot (fy, snap_day): history is never overwritten, and any
-- two snapshots can be put side by side. Recipients are not copied: they are read from the spans of silver through
-- gold.recipient, which also applies the rule for the names of natural persons.

CREATE TABLE gold.municipality (
  id text PRIMARY KEY, name_bg text NOT NULL, oblast text NOT NULL, nuts3 text NOT NULL, nuts2 text NOT NULL, nuts1 text NOT NULL);
-- how a pair (oblast, municipality) of the report was found in the list of municipalities
CREATE TABLE gold.place_match (
  oblast_raw text NOT NULL, obshtina_raw text NOT NULL, municipality_id text NOT NULL REFERENCES gold.municipality,
  method text NOT NULL,          -- name (the same name in the same oblast) | alias (a row of db/ref/municipality_alias.csv)
  evidence text, PRIMARY KEY (oblast_raw, obshtina_raw));
CREATE TABLE gold.fund (
  code text PRIMARY KEY, name_bg text NOT NULL, short_bg text NOT NULL, source_column text NOT NULL, ord integer NOT NULL);
CREATE TABLE gold.fiscal_year (
  fy integer PRIMARY KEY, starts date NOT NULL, ends date NOT NULL, label_bg text NOT NULL,
  currency text, eur_per_unit numeric(20,12), unit_evidence text,
  in_form boolean NOT NULL, gone_at date, first_snap date, last_snap date,
  names_until date NOT NULL);     -- the names of natural persons are shown until this day
CREATE TABLE gold.measure (
  measure_id integer PRIMARY KEY, code text NOT NULL, name text NOT NULL, objective text NOT NULL,
  family text NOT NULL, family_bg text NOT NULL);
-- one row per recipient as the source names it (name, last name, oblast, municipality): see ingest/names.py
CREATE TABLE gold.beneficiary (
  beneficiary_id bigint PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('legal', 'sole_trader', 'natural')),
  name_norm text,                 -- only for legal entities: the search key. NULL for people
  org_id text,                    -- legal entities with the same normalized name share it. NOT a company number
  municipality_id text REFERENCES gold.municipality);
CREATE INDEX beneficiary_org ON gold.beneficiary (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX beneficiary_municipality ON gold.beneficiary (municipality_id);
CREATE TABLE gold.org (
  org_id text PRIMARY KEY, name_norm text NOT NULL UNIQUE, name_bg text NOT NULL);
-- relations to other sources (plan 26, point 4): a name match is a candidate, never published as a link
CREATE TABLE gold.xwalk (
  kind text NOT NULL, a_source text NOT NULL, a_key text NOT NULL, b_source text NOT NULL, b_key text NOT NULL,
  method text NOT NULL,           -- identifier | official-list | manual | candidate
  evidence text NOT NULL, valid_from date NOT NULL, valid_to date, verified_at date, verified_by text,
  PRIMARY KEY (kind, a_source, a_key, b_source, b_key, valid_from));
CREATE TABLE gold.unmatched (
  kind text NOT NULL, key text NOT NULL, n integer NOT NULL, amount numeric(18,2), reason text NOT NULL,
  PRIMARY KEY (kind, key));

CREATE TABLE gold.meta (key text PRIMARY KEY, value text NOT NULL);   -- e.g. the version of the rules of ingest/names.py

CREATE TABLE gold.build (
  build_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fy integer NOT NULL, snap_day date NOT NULL, started timestamptz NOT NULL DEFAULT now(), finished timestamptz,
  rows jsonb, checks jsonb, ok boolean, code_sha text);
CREATE UNIQUE INDEX build_snapshot ON gold.build (fy, snap_day);

-- per snapshot: totals by kind of recipient (from the ОБЩО rows) and the concentration of the money
CREATE TABLE gold.summary (
  fy integer NOT NULL, snap_day date NOT NULL, scope text NOT NULL,   -- all | legal | sole_trader | natural
  recipients integer NOT NULL, payments integer NOT NULL,
  efgz numeric(18,2), ezfrs numeric(18,2), nb numeric(18,2), total numeric(18,2),
  top1_n integer, top1_total numeric(18,2),                          -- the top 1% of the recipient rows by total
  build_id bigint, PRIMARY KEY (fy, snap_day, scope));
CREATE TABLE gold.municipality_fy (
  fy integer NOT NULL, snap_day date NOT NULL, municipality_id text NOT NULL REFERENCES gold.municipality, kind text NOT NULL,
  recipients integer NOT NULL, efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), total numeric(16,2),
  PRIMARY KEY (fy, snap_day, municipality_id, kind));
CREATE TABLE gold.payment_agg (
  fy integer NOT NULL, snap_day date NOT NULL, municipality_id text NOT NULL REFERENCES gold.municipality,
  measure_id integer NOT NULL REFERENCES gold.measure, kind text NOT NULL,
  payments integer NOT NULL, efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), total numeric(16,2),
  PRIMARY KEY (fy, snap_day, municipality_id, measure_id, kind));
CREATE INDEX payment_agg_measure ON gold.payment_agg (fy, snap_day, measure_id);
-- the shared observation contract (STANDARD 3A, plan 26 point 5): one number per row, with the original beside it
CREATE TABLE gold.observation (
  dataset text NOT NULL, indicator text NOT NULL, place_id text NOT NULL, scope text NOT NULL,
  period_kind text NOT NULL, period_start date NOT NULL, period_end date NOT NULL,
  value numeric(18,2), unit text, value_original numeric(18,2), unit_original text,
  status text NOT NULL,           -- final | provisional
  published_at date NOT NULL, read_at date NOT NULL,
  source_url text NOT NULL, raw_sha256 text NOT NULL, build_id bigint,
  PRIMARY KEY (dataset, indicator, place_id, scope, period_start, published_at));

-- the registry the documentation of the future API is made from
CREATE TABLE gold.dataset (
  id text PRIMARY KEY, title_bg text NOT NULL, description_bg text NOT NULL, source_org text NOT NULL, source_url text NOT NULL,
  channel text NOT NULL, why_not_higher text NOT NULL, licence text, licence_note text NOT NULL, distributed boolean NOT NULL,
  cadence text NOT NULL, personal_data text NOT NULL);
CREATE TABLE gold.indicator (
  code text PRIMARY KEY, title_bg text NOT NULL, unit text NOT NULL, kind text NOT NULL, definition_bg text NOT NULL, dataset text NOT NULL);

-- the day for the rule about names: the current date, or the value of the setting subsidii.today (tests only)
CREATE FUNCTION gold.today() RETURNS date LANGUAGE sql STABLE AS
$$ SELECT coalesce(nullif(current_setting('subsidii.today', true), '')::date, current_date) $$;
-- two years, as the source publishes the names of natural persons (Regulation (EU) 2021/2116, art. 98)
CREATE FUNCTION gold.name_ttl_years() RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT 2 $$;

-- the recipients (ОБЩО rows) with their spans, the municipality and the name as it may be shown. The base tables stay
-- unreadable for the web role: it sees names only through this view, which hides the name of a natural person or sole
-- trader once the financial year is older than gold.name_ttl_years() and never gives it to an export.
CREATE VIEW gold.recipient AS
SELECT h.id AS holder_id, h.fy, h.from_day, h.to_day, h.beneficiary_id, g.kind, g.org_id, g.municipality_id,
       b.oblast AS oblast_raw, b.obshtina AS obshtina_raw,
       CASE WHEN g.kind = 'legal' THEN b.name
            WHEN gold.today() < y.names_until THEN btrim(b.name || CASE WHEN b.surname = '-' THEN '' ELSE ' ' || b.surname END)
            WHEN g.kind = 'sole_trader' THEN 'физическо лице (едноличен търговец)'
            ELSE 'физическо лице' END AS name_shown,
       (g.kind <> 'legal' AND gold.today() >= y.names_until) AS name_expired,
       CASE WHEN g.kind = 'legal' THEN b.name
            WHEN g.kind = 'sole_trader' THEN 'физическо лице (едноличен търговец)'
            ELSE 'физическо лице' END AS name_export,
       h.efgz, h.ezfrs, h.nb, h.ezfrs_nb, h.total
FROM silver.holder h
JOIN silver.beneficiary b ON b.id = h.beneficiary_id
JOIN gold.beneficiary g ON g.beneficiary_id = h.beneficiary_id
JOIN gold.fiscal_year y ON y.fy = h.fy;

-- the payments (rows below a ОБЩО row) as they stood: for the page of a legal entity and the measures
CREATE VIEW gold.payment_row AS
SELECT p.id AS payment_id, p.fy, p.from_day, p.to_day, p.beneficiary_id, g.kind, g.org_id, g.municipality_id,
       m.measure_id, p.starts, p.ends, p.efgz, p.ezfrs, p.nb
FROM silver.payment p
JOIN gold.beneficiary g ON g.beneficiary_id = p.beneficiary_id
JOIN gold.measure m ON m.measure_id = p.measure_id;

-- what the pages say about the files read and the sources: no names of people here
CREATE VIEW gold.snapshot AS
SELECT fy, day, status, sha256, path, bytes, encoding, rows_file, holders, payments, total_efgz, total_ezfrs, total_nb, total_all,
       paid_efgz, paid_ezfrs, paid_nb, block_diffs, negatives, rows_added, rows_removed, note, built_at, build_secs
FROM silver.snapshot;
CREATE VIEW gold.block_diff AS
SELECT d.fy, d.day, d.line_no, d.fund, d.stated, d.summed, g.kind,
       CASE WHEN g.kind = 'legal' THEN b.name WHEN g.kind = 'sole_trader' THEN 'физическо лице (едноличен търговец)' ELSE 'физическо лице' END AS name_export,
       b.oblast, b.obshtina
FROM silver.block_diff d JOIN silver.beneficiary b ON b.id = d.beneficiary_id JOIN gold.beneficiary g ON g.beneficiary_id = d.beneficiary_id;
CREATE VIEW gold.population AS
SELECT municipality_id, address, day, persons, source_url, read_at FROM silver.population;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'subsidii_web') THEN
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA ops, gold TO subsidii_web';
    EXECUTE 'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA gold TO subsidii_web';
  END IF;
END $$;

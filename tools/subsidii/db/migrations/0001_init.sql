-- Субсидии и помощи: the bronze pointer, the silver tables shaped like the ДФЗ report, the staging tables of one file.
-- Bronze is the archive of Наблюдател (dfz/<year>/<day>.<sha12>.csv); nothing is copied, ops.raw_file points at it.
-- Silver keeps the cells as published: amounts in the currency of the year (silver.fiscal_year.currency), no sums.
-- History is never overwritten: a row is a span (from_day, to_day) over the days a file of the year was read.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS stage;
CREATE SCHEMA IF NOT EXISTS gold;

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

-- a financial year runs 16.10 to 15.10 and is named after the year in which it ends (FY2025 = 16.10.2024 to 15.10.2025)
CREATE TABLE silver.fiscal_year (
  fy integer PRIMARY KEY,
  starts date NOT NULL, ends date NOT NULL,
  currency text,                 -- BGN | EUR from db/ref/dfz_units.csv; NULL until it is confirmed for the year
  unit_evidence text,
  in_form boolean NOT NULL DEFAULT true,   -- the source offered the year at the last read of the archive
  gone_at date);                 -- the day the archive saw the year leave the source's form; the data stays

-- one row per file of the archive that was read: the check of the file against itself
CREATE TABLE silver.snapshot (
  fy integer NOT NULL REFERENCES silver.fiscal_year, day date NOT NULL,
  status text NOT NULL,          -- built | held | invalid
  sha256 text NOT NULL, path text NOT NULL, bytes bigint NOT NULL, encoding text,
  rows_file integer,             -- lines after the header by the archive's own count (newlines minus one)
  holders integer, payments integer,
  total_efgz numeric(18,2), total_ezfrs numeric(18,2), total_nb numeric(18,2), total_all numeric(18,2),   -- the ОБЩО rows
  paid_efgz numeric(18,2), paid_ezfrs numeric(18,2), paid_nb numeric(18,2),                                  -- the payment rows
  block_diffs integer, negatives integer,
  rows_added integer, rows_removed integer,
  note text, built_at timestamptz NOT NULL DEFAULT now(), build_secs numeric,
  PRIMARY KEY (fy, day));

-- a recipient as the source names it, in a municipality: the four cells together (the same name in two municipalities is two)
CREATE TABLE silver.beneficiary (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL, surname text NOT NULL, oblast text NOT NULL, obshtina text NOT NULL,
  first_day date NOT NULL,
  UNIQUE (name, surname, oblast, obshtina));
CREATE TABLE silver.measure (
  id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code text NOT NULL, name text NOT NULL, objective text NOT NULL,   -- "Код", "Интервенция", "Специфична цел"; "-" is kept as "-"
  UNIQUE (code, name, objective));

-- the ОБЩО row of a recipient: its totals by fund, as published
CREATE TABLE silver.holder (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fy integer NOT NULL REFERENCES silver.fiscal_year, key text NOT NULL, occ integer NOT NULL,
  beneficiary_id bigint NOT NULL REFERENCES silver.beneficiary,
  grp text NOT NULL,
  efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), ezfrs_nb numeric(16,2), total numeric(16,2),
  from_day date NOT NULL, to_day date);          -- NULL: still in the newest file of the year
CREATE UNIQUE INDEX holder_identity ON silver.holder (fy, key, occ, from_day);
CREATE INDEX holder_open ON silver.holder (fy, key, occ) WHERE to_day IS NULL;
CREATE INDEX holder_beneficiary ON silver.holder (beneficiary_id, fy);
CREATE INDEX holder_span ON silver.holder (fy, from_day, to_day);

-- a payment row: one amount in the column of a fund. It belongs to the recipient of the block it stands in; when the
-- name on the row differs from the name of the block (a lost dash) the row's own text is in *_variant.
CREATE TABLE silver.payment (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  fy integer NOT NULL REFERENCES silver.fiscal_year, key text NOT NULL, occ integer NOT NULL,
  beneficiary_id bigint NOT NULL REFERENCES silver.beneficiary,
  name_variant text, surname_variant text,
  measure_id integer NOT NULL REFERENCES silver.measure,
  starts date, ends date,
  efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2),
  from_day date NOT NULL, to_day date);
CREATE UNIQUE INDEX payment_identity ON silver.payment (fy, key, beneficiary_id, occ, from_day);
CREATE INDEX payment_open ON silver.payment (fy, key, beneficiary_id, occ) WHERE to_day IS NULL;
CREATE INDEX payment_beneficiary ON silver.payment (beneficiary_id, fy);
CREATE INDEX payment_measure ON silver.payment (measure_id, fy);
CREATE INDEX payment_span ON silver.payment (fy, from_day, to_day);

-- a recipient whose payments do not add up to its own ОБЩО row, fund by fund: the source's inconsistency, kept as it is
CREATE TABLE silver.block_diff (
  fy integer NOT NULL, day date NOT NULL, line_no integer NOT NULL,     -- the data line of the ОБЩО row in the file
  beneficiary_id bigint NOT NULL REFERENCES silver.beneficiary,
  fund text NOT NULL, stated numeric(16,2) NOT NULL, summed numeric(16,2) NOT NULL,
  PRIMARY KEY (fy, day, line_no, fund));

-- the number of inhabitants of a municipality, read from the tool Население (a copy with its date, never a live call)
CREATE TABLE silver.population (
  municipality_id text NOT NULL, address text NOT NULL,       -- permanent | current
  day date NOT NULL, persons integer NOT NULL, source_url text NOT NULL, read_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (municipality_id, address, day));

-- one file under load: unlogged, emptied for every file
CREATE UNLOGGED TABLE stage.line (
  total boolean NOT NULL, block integer NOT NULL, n integer NOT NULL, key text NOT NULL, occ integer,
  name text, surname text, grp text, oblast text, obshtina text,
  o_name text, o_surname text,                   -- the name and last name of the block's ОБЩО row
  code text, measure text, objective text, starts date, ends date,
  efgz numeric, ezfrs numeric, nb numeric, ezfrs_nb numeric, total_amount numeric,
  efgz_t numeric, ezfrs_t numeric, nb_t numeric,
  beneficiary_id bigint, measure_id integer);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'subsidii_web') THEN
    EXECUTE 'GRANT USAGE ON SCHEMA ops, gold TO subsidii_web';
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA ops, gold TO subsidii_web';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA ops, gold GRANT SELECT ON TABLES TO subsidii_web';
  END IF;
END $$;

-- gold: normalised observations with the keys the other tools share. A structure is an oblast (NUTS 3 code, the key of
-- Население and Бюджет), a general directorate of the Ministry, or the country. A row of a table is a kind of crime.
CREATE TABLE gold.structure (
  code text PRIMARY KEY, name text NOT NULL, kind text NOT NULL,   -- oblast | gd | country
  oblast text, sort integer NOT NULL);
CREATE TABLE gold.structure_alias (alias text PRIMARY KEY, code text NOT NULL REFERENCES gold.structure);

CREATE TABLE gold.family (code text PRIMARY KEY, title text NOT NULL, sort integer NOT NULL);
CREATE TABLE gold.indicator (
  code text PRIMARY KEY, title text NOT NULL, unit text NOT NULL, kind text NOT NULL,   -- count | rate | share
  definition text NOT NULL, example text, formula text, sort integer NOT NULL);

-- the table that stands for (year, family) in gold: the fuller one when a year has two versions
CREATE TABLE gold.source_table (
  year integer NOT NULL, family text NOT NULL REFERENCES gold.family, resource_uri text NOT NULL, sha256 text NOT NULL,
  set_uri text NOT NULL, template text NOT NULL, n_rows integer NOT NULL, issues integer NOT NULL, note text,
  built_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (year, family));

-- a row of a table (a kind of crime, a structure) in one block; the printed text is kept
CREATE TABLE gold.crime_row (
  year integer NOT NULL, family text NOT NULL, structure_code text NOT NULL REFERENCES gold.structure, row_no integer NOT NULL,
  code text NOT NULL, text text NOT NULL, level integer NOT NULL, parent_row_no integer, is_total boolean NOT NULL,
  PRIMARY KEY (year, family, structure_code, row_no));

CREATE TABLE gold.observation (
  year integer NOT NULL, family text NOT NULL, structure_code text NOT NULL, row_no integer NOT NULL,
  indicator text NOT NULL REFERENCES gold.indicator,
  period_kind text NOT NULL DEFAULT 'year', period_start date NOT NULL, period_end date NOT NULL,
  value numeric,                    -- NULL: the source printed nothing (or a dash, or a text); never a zero
  value_text text NOT NULL,         -- as printed
  template text NOT NULL, resource_uri text NOT NULL, raw_sha256 text NOT NULL,
  PRIMARY KEY (year, family, structure_code, row_no, indicator),
  FOREIGN KEY (year, family, structure_code, row_no) REFERENCES gold.crime_row);
CREATE INDEX observation_ind ON gold.observation (family, indicator, year);
CREATE INDEX observation_struct ON gold.observation (structure_code, year);

-- a name of a structure that is not in the list: the row is kept out of gold and stays visible here
CREATE TABLE gold.unmatched (
  year integer NOT NULL, family text NOT NULL, name text NOT NULL, reason text NOT NULL, resource_uri text NOT NULL,
  PRIMARY KEY (year, family, name, resource_uri));

-- the checks of a build; status ok | differs | source (the difference is in the original and is named)
CREATE TABLE gold.check_result (
  id bigserial PRIMARY KEY, checked_at timestamptz NOT NULL DEFAULT now(),
  year integer NOT NULL, family text NOT NULL, check_id text NOT NULL, scope text NOT NULL,
  expected numeric, got numeric, diff numeric, status text NOT NULL, gating boolean NOT NULL, detail text,
  resource_uri text, sha256 text);
CREATE INDEX check_result_year ON gold.check_result (year, family, check_id);
CREATE TABLE gold.build (
  build_id bigserial PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  ok boolean, report jsonb);

-- Престъпност и пожари: the bronze pointers, the silver tables shaped like the source (one table of the Ministry of the
-- Interior = one resource = rows and cells as the sheet prints them), the log of changes and holds.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- bronze: where the raw answer is (the archive of Наблюдател; nothing is copied) and what we read from it
CREATE TABLE ops.raw_file (
  source text NOT NULL, ref text NOT NULL, path text NOT NULL, sha256 text NOT NULL, bytes bigint NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, ref, sha256));
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL);  -- new-record | rewritten | removed | held | confirmed | invalid | gone | duplicate
CREATE INDEX change_log_ref ON ops.change_log (source, ref, detected_at);
CREATE TABLE ops.held (ref text PRIMARY KEY, sha256 text NOT NULL, first_seen timestamptz NOT NULL, reason text);
CREATE TABLE ops.job_run (
  id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz, status text, report jsonb);

-- the sets of the Ministry of the Interior that are read, with the terms of use data.egov.bg gives them
CREATE TABLE silver.dataset (
  set_uri text PRIMARY KEY, title text NOT NULL, kind text NOT NULL,      -- police | crime-old | bulletin | road-agg
  year integer,                                                           -- the year of a yearly set
  terms_of_use_id text NOT NULL DEFAULT '',                               -- 1 CC0, 2 CC BY, empty: not stated
  licence text NOT NULL, shared boolean NOT NULL, licence_checked date NOT NULL,
  source_updated date,                                                    -- updated_at of the set on the portal
  listed integer,                                                         -- resources in the list the archive holds
  list_sha256 text, list_path text, list_read_at timestamptz);

-- one resource as it was read: a version of a table is (resource_uri, sha256); the history stays
CREATE TABLE silver.resource (
  resource_uri text NOT NULL, sha256 text NOT NULL, set_uri text NOT NULL REFERENCES silver.dataset,
  name text NOT NULL, version text, source_updated_at timestamptz,
  path text NOT NULL, bytes bigint NOT NULL, read_at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL,                      -- table | empty (the portal holds no table) | invalid (shape error, nothing stored)
  title text, family text, template text,  -- family: a kind of table that is published (see ingest/templates.py)
  n_rows integer, n_cols integer, n_blocks integer, issues integer NOT NULL DEFAULT 0, note text,
  status text NOT NULL,                    -- built | held | invalid
  is_current boolean NOT NULL DEFAULT false,
  PRIMARY KEY (resource_uri, sha256));
CREATE UNIQUE INDEX resource_current ON silver.resource (resource_uri) WHERE is_current;
CREATE INDEX resource_set ON silver.resource (set_uri);

CREATE TABLE silver.col (
  resource_uri text NOT NULL, sha256 text NOT NULL, col_no integer NOT NULL, label text NOT NULL,
  PRIMARY KEY (resource_uri, sha256, col_no),
  FOREIGN KEY (resource_uri, sha256) REFERENCES silver.resource ON DELETE CASCADE);
CREATE TABLE silver.row (
  resource_uri text NOT NULL, sha256 text NOT NULL, row_no integer NOT NULL, block_no integer NOT NULL,
  structure text NOT NULL DEFAULT '',      -- the name in the header of a block (structure after structure), else empty
  code text NOT NULL DEFAULT '',           -- the printed number ("4.7."), empty when the row has none
  label text NOT NULL,                     -- as printed
  text text NOT NULL,                      -- without the marker
  marker text NOT NULL DEFAULT '',         -- "" | "·" | "-"
  level integer NOT NULL,                  -- 0 total, 1 point, 2 sub-point, 3 "·", 4 "-" (indicative: the source numbering has gaps)
  is_total boolean NOT NULL,
  PRIMARY KEY (resource_uri, sha256, row_no),
  FOREIGN KEY (resource_uri, sha256) REFERENCES silver.resource ON DELETE CASCADE);
CREATE TABLE silver.cell (
  resource_uri text NOT NULL, sha256 text NOT NULL, row_no integer NOT NULL, col_no integer NOT NULL,
  text text NOT NULL,                      -- as printed, empty when empty
  value numeric,                           -- NULL for empty, a dash or a text that is not a number
  issue boolean NOT NULL DEFAULT false,    -- a text where a number belongs (not a dash)
  PRIMARY KEY (resource_uri, sha256, row_no, col_no),
  FOREIGN KEY (resource_uri, sha256, row_no) REFERENCES silver.row ON DELETE CASCADE);
CREATE INDEX cell_issue ON silver.cell (resource_uri, sha256) WHERE issue;

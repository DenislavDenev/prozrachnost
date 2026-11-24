-- Закони и решения: ops (what was read, held, changed), silver (the tables of the source, typed, versioned),
-- ref (the lists we copy: municipalities, the institution -> municipality links).
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS ref;

CREATE TABLE ops.job_run (
  id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  status text NOT NULL DEFAULT 'running', code_sha text, inputs jsonb, stats jsonb, error text
);

-- bronze: the files of the Наблюдател archive we built from, by sha256 (the file stays in the archive; nothing is copied)
CREATE TABLE ops.raw_file (
  id bigserial PRIMARY KEY, source text NOT NULL, ref text NOT NULL, path text NOT NULL, sha256 text NOT NULL,
  bytes bigint NOT NULL, fetched_at timestamptz NOT NULL, first_built_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, ref, sha256)
);

-- what the source changed after we first read it (STANDARD 1Б)
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL  -- new-record | rewritten | removed | held | confirmed | invalid | gone
);
CREATE INDEX ON ops.change_log (detected_at);
CREATE INDEX ON ops.change_log (source, ref);

-- an answer with fewer records than we hold waits here until a read a day later gives the same file
CREATE TABLE ops.held (
  source text NOT NULL, ref text NOT NULL, sha256 text NOT NULL, rows integer NOT NULL,
  first_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, ref)
);

-- the state of the last read of each resource
CREATE TABLE ops.source_state (
  source text NOT NULL, ref text NOT NULL, last_read timestamptz, last_ok timestamptz, last_change timestamptz,
  status text, error text, rows integer, sha256 text, version text, role text,
  PRIMARY KEY (source, ref)
);

-- the counts of each build against what the source says (or the other reports of the source say)
CREATE TABLE ops.reconciliation (
  id bigserial PRIMARY KEY, run_id bigint, checked_at timestamptz NOT NULL DEFAULT now(), name text NOT NULL,
  expected bigint, actual bigint, ok boolean NOT NULL, kind text NOT NULL,   -- kind: block | known | info
  note text
);
CREATE INDEX ON ops.reconciliation (name, checked_at DESC);

-- ref: copies of lists, with the date and where they are from (db/ref)
CREATE TABLE ref.municipality (
  id text PRIMARY KEY, name_bg text NOT NULL, name_en text, oblast text NOT NULL, nuts3 text, nuts2 text, nuts1 text
);
CREATE TABLE ref.institution_municipality (
  institution_id integer PRIMARY KEY, municipality_id text NOT NULL REFERENCES ref.municipality, method text NOT NULL,
  evidence text NOT NULL, checked date NOT NULL, checked_by text NOT NULL
);

-- silver: the same data as the source, typed. Every table: valid_from/valid_to (a changed record is a new version, the old
-- one stays), row_sha (hash of the data columns), raw_sha256 (the file it came from).
CREATE TABLE silver.pris_act (
  id bigserial PRIMARY KEY, pris_id integer NOT NULL, origin text NOT NULL CHECK (origin IN ('current', 'archive')),
  doc_num text NOT NULL, accepted date NOT NULL, about text NOT NULL, about_raw text NOT NULL, legal_act_type text NOT NULL,
  legal_reason text, importer text, protocol text, public_consultation_number text, gazette_number integer,
  gazette_year_raw text, gazette_year smallint, version text, active boolean NOT NULL, published date NOT NULL,
  deleted date, confidential boolean NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.pris_act (pris_id) WHERE valid_to IS NULL;
CREATE INDEX ON silver.pris_act (pris_id, valid_from);

CREATE TABLE silver.pris_institution (
  id bigserial PRIMARY KEY, pris_id integer NOT NULL, ord integer NOT NULL, institution_id integer NOT NULL, name text NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.pris_institution (pris_id, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.pris_tag (
  id bigserial PRIMARY KEY, pris_id integer NOT NULL, ord integer NOT NULL, tag text NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.pris_tag (pris_id, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.pris_related (
  id bigserial PRIMARY KEY, pris_id integer NOT NULL, ord integer NOT NULL, relation_type text NOT NULL,
  related_pris_id integer NOT NULL, act_type text NOT NULL, act_name text NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.pris_related (pris_id, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.consultation (
  id bigserial PRIMARY KEY, reg_num text NOT NULL, consultation_type text NOT NULL, name text NOT NULL, description text,
  description_raw text NOT NULL, act_type text, date_open date NOT NULL, date_close date NOT NULL, short_term_reason text,
  active boolean NOT NULL, policy_area text NOT NULL, legislative_program_id integer, operational_program_id integer,
  institution_id integer NOT NULL, institution_name text, institution_address text, proposal_ways text, law_name text,
  law_id integer, pris_id integer, comment_count integer NOT NULL, comment_first date, comment_last date,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.consultation (reg_num) WHERE valid_to IS NULL;
CREATE INDEX ON silver.consultation (reg_num, valid_from);

CREATE TABLE silver.consultation_file (
  id bigserial PRIMARY KEY, reg_num text NOT NULL, ord integer NOT NULL, kind text NOT NULL, doc_date date, link text NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.consultation_file (reg_num, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.strategy_doc (
  id bigserial PRIMARY KEY, doc_key text NOT NULL, name text NOT NULL, level text, policy_area text, doc_type text,
  act_link text, pris_act_id integer, accepting_institution_type text, document_date date, public_consultation_number text,
  active boolean NOT NULL, date_accepted date, date_accepted_raw text NOT NULL, date_expiring date, date_expiring_raw text,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.strategy_doc (doc_key) WHERE valid_to IS NULL;

CREATE TABLE silver.strategy_author (
  id bigserial PRIMARY KEY, doc_key text NOT NULL, ord integer NOT NULL, institution text NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.strategy_author (doc_key, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.strategy_file (
  id bigserial PRIMARY KEY, doc_key text NOT NULL, ord integer NOT NULL, name text, path text NOT NULL, version text,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.strategy_file (doc_key, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.strategy_sub (
  id bigserial PRIMARY KEY, doc_key text NOT NULL, ord integer NOT NULL, sub_id integer NOT NULL, parent_sub_id integer,
  name text NOT NULL, level text, policy_area text, doc_type text, pris_act_id integer, date_accepted date, date_expiring date,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.strategy_sub (doc_key, ord) WHERE valid_to IS NULL;

CREATE TABLE silver.impact_contract (
  id bigserial PRIMARY KEY, ic_key text NOT NULL, institution_id integer NOT NULL, institution_name text NOT NULL,
  contract_date date, price_bgn numeric(14, 2), eik text, executor text, executor_kind text NOT NULL,
  subject text, description text, active boolean NOT NULL,
  valid_from timestamptz NOT NULL, valid_to timestamptz, row_sha text NOT NULL, raw_sha256 text NOT NULL
);
CREATE UNIQUE INDEX ON silver.impact_contract (ic_key) WHERE valid_to IS NULL;

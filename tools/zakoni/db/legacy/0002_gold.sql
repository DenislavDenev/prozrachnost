-- gold: normalised objects with the keys of the source, built from silver by db/gold/01_build.sql at every build
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE gold.build (
  build_id bigserial PRIMARY KEY, built_at timestamptz NOT NULL DEFAULT now(), run_id bigint, code_sha text,
  rows jsonb, checks jsonb, ok boolean NOT NULL DEFAULT false
);

CREATE TABLE gold.act (            -- an act of the Council of Ministers (ПРИС); key pris_id, a new version of the source is a change, not a new act
  pris_id integer PRIMARY KEY, doc_num text NOT NULL, accepted date NOT NULL, year smallint NOT NULL,
  act_type text NOT NULL, about text NOT NULL, about_original text NOT NULL, legal_reason text, importer text,
  protocol text, consultation_reg_num text, gazette_number integer, gazette_year smallint, active boolean NOT NULL,
  confidential boolean NOT NULL, origin text NOT NULL, tags text[], relations integer NOT NULL,
  source text NOT NULL, source_url text NOT NULL, raw_sha256 text NOT NULL, first_seen date NOT NULL, last_seen date NOT NULL
);
CREATE INDEX ON gold.act (accepted);
CREATE INDEX ON gold.act (act_type, accepted);
CREATE INDEX act_fts ON gold.act USING gin (to_tsvector('simple', about));

CREATE TABLE gold.act_institution (pris_id integer NOT NULL REFERENCES gold.act, institution_id integer NOT NULL, name text NOT NULL, PRIMARY KEY (pris_id, institution_id));
CREATE TABLE gold.act_relation (
  pris_id integer NOT NULL REFERENCES gold.act, ord integer NOT NULL, relation_type text NOT NULL, related_pris_id integer NOT NULL,
  related_in_data boolean NOT NULL, act_type text NOT NULL, act_name text NOT NULL, PRIMARY KEY (pris_id, ord)
);

CREATE TABLE gold.consultation (   -- key reg_num
  reg_num text PRIMARY KEY, number integer NOT NULL, name text NOT NULL, description text, level text NOT NULL, act_type text,
  opened date NOT NULL, closes date NOT NULL, days integer NOT NULL, short_term boolean NOT NULL, short_term_applies boolean NOT NULL,
  short_term_reason text, reason_given boolean NOT NULL, policy_area text NOT NULL, archived_area boolean NOT NULL,
  institution_id integer NOT NULL, institution_name text, municipality_id text, law_name text, law_id integer,
  act_pris_id integer, comment_count integer NOT NULL, files integer NOT NULL,
  source text NOT NULL, source_url text NOT NULL, raw_sha256 text NOT NULL, first_seen date NOT NULL, last_seen date NOT NULL
);
CREATE INDEX ON gold.consultation (opened);
CREATE INDEX ON gold.consultation (closes);
CREATE INDEX ON gold.consultation (institution_id);
CREATE INDEX ON gold.consultation (municipality_id);
CREATE INDEX consultation_fts ON gold.consultation USING gin (to_tsvector('simple', name));

CREATE TABLE gold.strategy_doc (
  doc_key text PRIMARY KEY, name text NOT NULL, level text, policy_area text, doc_type text, authority text,
  act_pris_id integer, consultation_reg_num text, accepted date, expires date, active boolean NOT NULL,
  authors text[], files integer NOT NULL, source text NOT NULL, source_url text NOT NULL, raw_sha256 text NOT NULL,
  first_seen date NOT NULL, last_seen date NOT NULL
);
CREATE INDEX ON gold.strategy_doc (accepted);

CREATE TABLE gold.impact_contract (
  ic_key text PRIMARY KEY, institution_id integer NOT NULL, institution_name text NOT NULL, contract_date date,
  price_bgn numeric(14, 2), price_eur numeric(14, 2), eik text, executor text, executor_kind text NOT NULL,
  subject text, description text, source text NOT NULL, source_url text NOT NULL, raw_sha256 text NOT NULL
);

-- every row that did not get a key it should have: the row stays, the key is empty, the reason is here
CREATE TABLE gold.unmatched (
  build_id bigint NOT NULL, tbl text NOT NULL, key text NOT NULL, field text NOT NULL, value text, reason text NOT NULL
);
CREATE INDEX ON gold.unmatched (tbl, field);

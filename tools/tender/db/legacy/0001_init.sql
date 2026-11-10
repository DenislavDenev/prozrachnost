-- Persistent state only. Everything derived from the sources lives in schema `live`, rebuilt by
-- ingest.build into `stage` and swapped in (see db/derive/). Nothing here is rebuilt.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS tr;
CREATE SCHEMA IF NOT EXISTS ed;

-- ---------- runs ----------
CREATE TABLE ops.job_run (
  id            bigserial PRIMARY KEY,
  step          text NOT NULL,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running' CHECK (status IN ('running','ok','failed','skipped')),
  code_sha      text,
  rule_versions jsonb,
  inputs        jsonb,
  stats         jsonb,
  error         text
);
CREATE INDEX ON ops.job_run (step, started_at DESC);

-- one row per raw EOP file seen; provenance for every derived row
CREATE TABLE ops.eop_file (
  day        date NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('tenders','contracts','annexes','ocds')),
  key        text NOT NULL,
  sha256     text NOT NULL,
  size       bigint NOT NULL,
  fetched_at timestamptz NOT NULL,
  PRIMARY KEY (day, kind)
);
CREATE TABLE ops.eop_day (
  day        date PRIMARY KEY,
  published  boolean NOT NULL,
  checked_at timestamptz NOT NULL
);

-- ECB reference rates, EUR per 1 unit of `currency` is 1/rate (ECB quotes units per EUR)
CREATE TABLE ops.fx_rate (
  day      date NOT NULL,
  currency text NOT NULL,
  per_eur  numeric NOT NULL,
  PRIMARY KEY (currency, day)
);

-- ---------- Търговски регистър ----------
CREATE TABLE tr.deed (
  eik           text PRIMARY KEY,
  status        text NOT NULL CHECK (status IN ('ok','absent','error')),
  fetched_at    timestamptz NOT NULL,
  sha256        text,
  name          text,
  legal_form    text,
  deed_status   text,
  seat          text,
  capital_eur   numeric,
  registered_on date,
  error         text
);

CREATE TABLE tr.queue (
  eik         text PRIMARY KEY,
  reason      text NOT NULL,          -- contract | holder | name_search | change | refresh
  priority    int  NOT NULL DEFAULT 5, -- lower first
  depth       int  NOT NULL DEFAULT 0, -- hops from a contract company
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','failed')),
  attempts    int  NOT NULL DEFAULT 0,
  next_at     timestamptz NOT NULL DEFAULT now(),
  last_error  text,
  enqueued_at timestamptz NOT NULL DEFAULT now(),
  done_at     timestamptz
);
CREATE INDEX ON tr.queue (status, priority, next_at);

-- role facts extracted from the latest successful read of each partida (replaced per eik)
CREATE TABLE tr.role (
  eik             text NOT NULL,
  sub_uic         text NOT NULL,
  field_ident     text NOT NULL,
  role            text NOT NULL,
  holder_kind     text NOT NULL CHECK (holder_kind IN ('person','entity')),
  holder_id       text NOT NULL,   -- hash | local:... | ЕИК | name:...
  holder_name     text NOT NULL,
  indent_type     text,
  share           text,
  country         text,
  entry_no        text NOT NULL,
  valid_from      date NOT NULL,
  valid_to        date,
  uncertain_after date,
  observed_at     timestamptz NOT NULL
);
CREATE INDEX ON tr.role (eik);
CREATE INDEX ON tr.role (holder_id);

-- a natural person the register identifies by its hash; `id` is our public identifier
CREATE TABLE tr.person (
  id           text PRIMARY KEY,           -- ULID, stable, shown in URLs
  indent       text NOT NULL UNIQUE,       -- register hash; never shown
  indent_type  text,
  name         text NOT NULL,              -- latest registered spelling
  name_key     text NOT NULL,
  first_seen   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tr.person (name_key);

-- name searches for new partidas of known persons
CREATE TABLE tr.name_search (
  name_key    text PRIMARY KEY,
  total       int,
  status      text NOT NULL CHECK (status IN ('done','ambiguous','failed')),
  searched_at timestamptz NOT NULL
);

-- the portal's daily change list, read at D+1 and D+14
CREATE TABLE tr.change_day (
  day       date NOT NULL,
  pass      int  NOT NULL,   -- 1 = D+1, 2 = D+14
  next_page int  NOT NULL DEFAULT 1,
  done      boolean NOT NULL DEFAULT false,
  PRIMARY KEY (day, pass)
);

-- ---------- editorial ----------
CREATE TABLE ed.tag (
  id          bigserial PRIMARY KEY,
  entity_type text NOT NULL CHECK (entity_type IN ('company','person','buyer','contract')),
  entity_id   text NOT NULL,
  code        text NOT NULL,
  note        text NOT NULL,          -- the basis, shown with the tag
  author      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  removed_at  timestamptz
);
CREATE TABLE ed.tag_audit (
  id        bigserial PRIMARY KEY,
  tag_id    bigint NOT NULL REFERENCES ed.tag(id),
  action    text NOT NULL,
  author    text NOT NULL,
  at        timestamptz NOT NULL DEFAULT now(),
  before    jsonb,
  after     jsonb
);

-- public-figure candidates and their review
CREATE TABLE ed.public_figure (
  person_id    text NOT NULL REFERENCES tr.person(id),
  wikidata_qid text NOT NULL,
  label        text NOT NULL,
  description  text,
  photo_file   text,       -- Commons file name
  photo_license text,
  photo_author text,
  status       text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','rejected')),
  reviewed_by  text,
  reviewed_at  timestamptz,
  found_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (person_id, wikidata_qid)
);

CREATE TABLE ed.article (
  id           bigserial PRIMARY KEY,
  entity_type  text NOT NULL CHECK (entity_type IN ('company','person')),
  entity_id    text NOT NULL,
  url          text NOT NULL,
  title        text NOT NULL,
  source       text,
  published_at timestamptz,
  match_basis  text NOT NULL,   -- e.g. 'name+eik', 'name+company'
  status       text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','rejected')),
  reviewed_by  text,
  reviewed_at  timestamptz,
  found_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (entity_type, entity_id, url)
);

-- Икономика: every number is one row of live.series; ops.* records the reads, what changed and what waits
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS live;

CREATE TABLE ops.job_run (
  id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  status text NOT NULL DEFAULT 'running', code_sha text, inputs jsonb, stats jsonb, error text
);

-- every answer of a source as it was read: raw/<source>/<date>/<name>, by sha256
CREATE TABLE ops.raw_file (
  id bigserial PRIMARY KEY, source text NOT NULL, ref text NOT NULL, path text NOT NULL, sha256 text NOT NULL,
  bytes bigint NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ops.raw_file (source, ref, fetched_at DESC);

-- what a source changed after we first read it (STANDARD 1Б)
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL  -- new-record | rewritten | removed | held | confirmed | invalid | gone
);
CREATE INDEX ON ops.change_log (detected_at);

-- an answer that would remove what we hold waits here until a read at least a day later gives the same sha
CREATE TABLE ops.held (
  source text NOT NULL, ref text NOT NULL, sha256 text NOT NULL, rows integer NOT NULL,
  first_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, ref)
);

-- the state of each read: one row per indicator, and per BNB window
CREATE TABLE ops.source_state (
  source text NOT NULL, ref text NOT NULL,
  last_read timestamptz, last_ok timestamptz, last_change timestamptz, last_new_period timestamptz,
  status text, error text, rows integer, updated text, label text,
  PRIMARY KEY (source, ref)
);

CREATE TABLE live.series (
  indicator text NOT NULL,
  dims jsonb NOT NULL,      -- every dimension except geo and time, e.g. {"unit": "RCH_A", "coicop18": "CP01"}
  geo text NOT NULL,
  time text NOT NULL,       -- as the source writes it: 2025, 2025-Q2, 2025-S1, 2026-08, 2026-09-25
  value numeric,            -- NULL = the source has a flag but no value ("няма данни", never 0)
  flag text,                -- Eurostat status: p provisional, e estimated, b break in series, ...
  PRIMARY KEY (indicator, dims, geo, time)
);
CREATE INDEX ON live.series (indicator, geo, time);

-- the source's names of the categories (Eurostat publishes English, French and German only)
CREATE TABLE live.dim_label (
  indicator text NOT NULL, dim text NOT NULL, code text NOT NULL, label text NOT NULL,
  PRIMARY KEY (indicator, dim, code)
);

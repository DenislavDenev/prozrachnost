CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE live.source (
 url text PRIMARY KEY, sha text NOT NULL, as_of date, kind text NOT NULL,
 payload jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ops.raw_file (
 id bigserial PRIMARY KEY, url text NOT NULL, sha text NOT NULL, path text NOT NULL,
 fetched_at timestamptz NOT NULL DEFAULT now(), UNIQUE(url, sha)
);
CREATE TABLE ops.source_state (
 url text PRIMARY KEY, last_read timestamptz, last_ok timestamptz,
 status text NOT NULL, error text, rows integer
);
CREATE TABLE ops.held (
 url text PRIMARY KEY, sha text NOT NULL, first_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ops.change_log (
 id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
 source text NOT NULL, ref text NOT NULL, field text, old text, new text, cause text NOT NULL
);

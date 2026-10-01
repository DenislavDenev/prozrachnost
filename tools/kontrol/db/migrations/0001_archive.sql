CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS stage;
CREATE SCHEMA IF NOT EXISTS src;
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE live.snapshot (
 source text PRIMARY KEY, payload jsonb NOT NULL, sha256 text NOT NULL,
 published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE stage.snapshot (LIKE live.snapshot INCLUDING ALL);
CREATE TABLE src.resource (
 source text PRIMARY KEY, url text NOT NULL, read_at timestamptz, status text NOT NULL,
 error text, row_count integer, page_count integer, source_count integer,
 scope text, license text NOT NULL DEFAULT 'Не е установен свободен лиценз'
);
CREATE TABLE ops.raw_file (
 source text NOT NULL, url text NOT NULL, path text NOT NULL,
 sha256 text NOT NULL, bytes bigint NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(source,url,sha256)
);
CREATE TABLE ops.version (
 source text NOT NULL, ref text NOT NULL, sha256 text NOT NULL, payload jsonb NOT NULL,
 detected_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(source,ref,sha256)
);
CREATE TABLE ops.held (source text PRIMARY KEY, sha256 text NOT NULL, first_seen timestamptz NOT NULL);
CREATE TABLE ops.change_log (
 id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
 source text NOT NULL, ref text NOT NULL, field text, old text, new text, cause text NOT NULL
);
CREATE TABLE ops.job_run (
 id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, status text NOT NULL, report jsonb
);

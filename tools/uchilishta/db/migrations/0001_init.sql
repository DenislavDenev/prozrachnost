CREATE SCHEMA IF NOT EXISTS live;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE live.publication (
  school_year text PRIMARY KEY, exam_resource text NOT NULL, register_resource text NOT NULL,
  exam_sha text NOT NULL, register_sha text NOT NULL,
  exam_updated_at text NOT NULL, register_updated_at text NOT NULL, school_count integer NOT NULL,
  matched_count integer NOT NULL, unmatched jsonb NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE live.school (
  school_year text NOT NULL, neispuo text NOT NULL, name text NOT NULL,
  oblast text NOT NULL, municipality text NOT NULL, town text NOT NULL,
  matched boolean NOT NULL, PRIMARY KEY (school_year, neispuo)
);
CREATE INDEX school_name_idx ON live.school (school_year, name);
CREATE TABLE live.exam_result (
  school_year text NOT NULL, neispuo text NOT NULL, subject text NOT NULL,
  takers integer, score numeric, scale text NOT NULL,
  PRIMARY KEY (school_year, neispuo, subject),
  FOREIGN KEY (school_year, neispuo) REFERENCES live.school (school_year, neispuo)
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
  school_year text PRIMARY KEY, sha text NOT NULL, first_at timestamptz NOT NULL
);
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL
);

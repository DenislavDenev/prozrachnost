CREATE TABLE live.dzi_publication (
  resource text PRIMARY KEY,
  school_year text NOT NULL,
  session text NOT NULL CHECK (session IN ('may', 'august')),
  kind text NOT NULL CHECK (kind IN ('mandatory', 'optional')),
  sha text NOT NULL,
  updated_at text NOT NULL,
  register_resource text,
  register_sha text,
  verification text NOT NULL CHECK (verification IN ('code', 'no-code')),
  source_rows integer NOT NULL,
  school_count integer NOT NULL,
  aggregate_count integer NOT NULL,
  result_count integer NOT NULL,
  matched_count integer NOT NULL,
  unmatched jsonb NOT NULL,
  anomalies jsonb NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dzi_publication_year_idx ON live.dzi_publication (school_year DESC, session, kind);

CREATE TABLE live.dzi_result (
  resource text NOT NULL REFERENCES live.dzi_publication(resource) ON DELETE CASCADE,
  row_number integer NOT NULL,
  neispuo text,
  school text NOT NULL,
  oblast text NOT NULL,
  municipality text NOT NULL,
  town text NOT NULL,
  subject text NOT NULL,
  takers integer,
  score numeric,
  is_school boolean NOT NULL,
  matched boolean,
  PRIMARY KEY (resource, row_number, subject)
);
CREATE INDEX dzi_result_school_idx ON live.dzi_result (neispuo) WHERE neispuo IS NOT NULL;
CREATE INDEX dzi_result_subject_idx ON live.dzi_result (subject);

CREATE TABLE ops.dzi_held (
  resource text PRIMARY KEY,
  sha text NOT NULL,
  first_at timestamptz NOT NULL
);

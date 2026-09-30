CREATE TABLE live.status_publication (
  resource text PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('protected','central')),
  school_year text NOT NULL,
  sha text NOT NULL,
  updated_at text NOT NULL,
  row_count integer NOT NULL,
  school_rows integer NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, school_year)
);

CREATE TABLE live.status_row (
  resource text NOT NULL REFERENCES live.status_publication(resource) ON DELETE CASCADE,
  row_number integer NOT NULL,
  neispuo text NOT NULL,
  name text NOT NULL,
  town text NOT NULL,
  scope text,
  is_school boolean NOT NULL,
  PRIMARY KEY (resource, row_number)
);
CREATE INDEX status_row_school_idx ON live.status_row (neispuo) WHERE is_school;

CREATE TABLE ops.status_held (
  resource text PRIMARY KEY,
  sha text NOT NULL,
  first_at timestamptz NOT NULL
);

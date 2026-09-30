CREATE TABLE live.nvo_publication (
  resource text PRIMARY KEY,
  exam text NOT NULL CHECK (exam IN ('nvo4', 'nvo10')),
  school_year text NOT NULL,
  sha text NOT NULL,
  updated_at text NOT NULL,
  register_resource text,
  register_sha text,
  verification text NOT NULL CHECK (verification IN ('code', 'no-code')),
  school_count integer NOT NULL,
  subject_count integer NOT NULL,
  result_count integer NOT NULL,
  matched_count integer NOT NULL,
  unmatched jsonb NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (exam, school_year)
);
CREATE INDEX nvo_publication_year_idx ON live.nvo_publication (school_year DESC, exam);

CREATE TABLE live.nvo_result (
  resource text NOT NULL REFERENCES live.nvo_publication(resource) ON DELETE CASCADE,
  neispuo text NOT NULL,
  school text NOT NULL,
  oblast text NOT NULL,
  municipality text NOT NULL,
  town text NOT NULL,
  subject text NOT NULL,
  takers integer,
  score numeric,
  matched boolean,
  PRIMARY KEY (resource, neispuo, subject)
);
CREATE INDEX nvo_result_school_idx ON live.nvo_result (neispuo);
CREATE INDEX nvo_result_subject_idx ON live.nvo_result (subject);

CREATE TABLE ops.nvo_held (
  resource text PRIMARY KEY,
  sha text NOT NULL,
  first_at timestamptz NOT NULL
);

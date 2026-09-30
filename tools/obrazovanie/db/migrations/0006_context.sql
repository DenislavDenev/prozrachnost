CREATE TABLE live.context_publication (
  school_year text PRIMARY KEY,
  pupils_resource text NOT NULL,
  classes_resource text NOT NULL,
  pupils_sha text NOT NULL,
  classes_sha text NOT NULL,
  pupils_updated text NOT NULL,
  classes_updated text NOT NULL,
  kind_count integer NOT NULL,
  grade_1_12_students integer NOT NULL,
  reported_groups numeric NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE live.context_kind (
  school_year text NOT NULL REFERENCES live.context_publication(school_year) ON DELETE CASCADE,
  kind text NOT NULL,
  institutions integer NOT NULL,
  grades jsonb NOT NULL,
  preschool integer NOT NULL,
  reported_pupils integer NOT NULL,
  reported_groups numeric NOT NULL,
  PRIMARY KEY (school_year, kind)
);

CREATE TABLE ops.context_held (
  school_year text PRIMARY KEY,
  sha text NOT NULL,
  first_at timestamptz NOT NULL
);

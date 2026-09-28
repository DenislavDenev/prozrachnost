-- Парламент, stage 2: every sitting from 1879 with its stenogram and video, the assemblies and their MPs (profiles,
-- memberships with dates, the same person across assemblies), the official absences and penalties
ALTER TABLE live.sitting ALTER COLUMN assembly DROP NOT NULL;   -- before 2009 the heading does not say: by date
ALTER TABLE live.sitting ADD COLUMN video text[];      -- the recording's parts (2010-), on parliament.bg
ALTER TABLE live.sitting ADD COLUMN pdf text;          -- the scanned stenogram (before 1992)
ALTER TABLE live.sitting ADD COLUMN steno_sha text;    -- the stenogram text we hold (NULL: not published yet)

-- every assembly from the Constituent one (db/ref/assemblies.csv; from the 39th the API's dates win): no is the
-- ordinary assembly's number (1950-1990 continue it: I НС 1950 is 27), a grand one is 100 + its number, the
-- Constituent Assembly 100
CREATE TABLE live.assembly (
  no smallint PRIMARY KEY, kind text NOT NULL, name text NOT NULL, start date NOT NULL, "end" date, api_id integer UNIQUE
);

-- a group, committee, delegation or friendship group of an assembly (archive/bg/<id>, and the ones in memberships)
CREATE TABLE live.body (
  id integer PRIMARY KEY, assembly smallint, kind text, name text NOT NULL, since date, until date,
  n_start integer, n_end integer, n_total integer
);
CREATE INDEX ON live.body (assembly, kind);

-- an MP's profile in one assembly (mp-profile): the Assembly's own number; person: the same person in every
-- assembly (the Assembly lists the earlier ones; linked where the full name is once in that assembly)
CREATE TABLE live.profile (
  id integer PRIMARY KEY, assembly smallint NOT NULL, name text NOT NULL, district text, list text,
  profession text, languages text, person integer, read_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON live.profile (assembly, name);
CREATE INDEX ON live.profile (person);
CREATE TABLE live.profile_past (profile integer NOT NULL REFERENCES live.profile ON DELETE CASCADE, assembly smallint NOT NULL,
                                PRIMARY KEY (profile, assembly));

-- where an MP was and what they were, with the dates (the assembly itself, groups, committees ...)
CREATE TABLE live.membership (
  id integer PRIMARY KEY, profile integer NOT NULL REFERENCES live.profile ON DELETE CASCADE, body integer NOT NULL,
  body_name text NOT NULL, body_kind smallint, role text, since date, until date
);
CREATE INDEX ON live.membership (profile);
CREATE INDEX ON live.membership (body);

-- the roll call's MP -> the profile, where the full name is once in both (ingest/stats.py)
ALTER TABLE live.mp ADD COLUMN profile integer;

-- the official absences (kind 1 the plenary, 2 a committee) and the chair's penalties: the Assembly shows only the
-- last months, what we read is kept
CREATE TABLE live.absence (
  id integer PRIMARY KEY, date date NOT NULL, profile integer NOT NULL, name text NOT NULL, body integer, body_name text,
  kind smallint, first_seen timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON live.absence (profile, date);
CREATE TABLE live.penalty (
  id integer PRIMARY KEY, date date NOT NULL, profile integer NOT NULL, name text NOT NULL, kind text NOT NULL, note text,
  by_name text, what text, first_seen timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON live.penalty (profile, date);

-- the stenogram, speech by speech (ingest/parse.speeches): no 0 is what comes before the first speech
CREATE TABLE live.speech (
  sitting integer NOT NULL REFERENCES live.sitting ON DELETE CASCADE, no smallint NOT NULL,
  role text, name text, note text, grp text, profile integer, text text NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
  PRIMARY KEY (sitting, no)
);
CREATE INDEX ON live.speech USING gin (tsv);
CREATE INDEX ON live.speech (profile);

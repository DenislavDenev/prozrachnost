-- Парламент: the sittings, every registration and vote by group and by MP; ops.* records the reads, what changed and what waits
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS live;

CREATE TABLE ops.job_run (
  id bigserial PRIMARY KEY, step text NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  status text NOT NULL DEFAULT 'running', code_sha text, inputs jsonb, stats jsonb, error text
);

-- every answer as it was read: raw/<source>/<date>/<name>, by sha256
CREATE TABLE ops.raw_file (
  id bigserial PRIMARY KEY, source text NOT NULL, ref text NOT NULL, path text NOT NULL, sha256 text NOT NULL,
  bytes bigint NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ops.raw_file (source, ref, fetched_at DESC);

-- what the source changed after we first read it
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL, ref text NOT NULL, field text, old text, new text,
  cause text NOT NULL  -- new-record | rewritten | removed | held | confirmed
);
CREATE INDEX ON ops.change_log (detected_at);

-- new files of a sitting with fewer items or votes than we hold wait here until a read a day later gives the same
CREATE TABLE ops.held (
  source text NOT NULL, ref text NOT NULL, sha256 text NOT NULL, rows integer NOT NULL,
  first_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (source, ref)
);

-- the state of each read: the roster, each month's list of sittings, each sitting
CREATE TABLE ops.source_state (
  source text NOT NULL, ref text NOT NULL,
  last_read timestamptz, last_ok timestamptz, last_change timestamptz,
  status text, error text, rows integer,
  PRIMARY KEY (source, ref)
);

-- a sitting of the plenary; gv and iv are the paths of its CSV files (NULL: not published, or only XLSX/PDF)
CREATE TABLE live.sitting (
  id integer PRIMARY KEY, date date NOT NULL, assembly smallint NOT NULL, heading text NOT NULL,
  gv text, iv text, gv_sha text, iv_sha text
);
CREATE INDEX ON live.sitting (assembly, date);

-- a registration or a vote: the result of the whole assembly, from the file by group
CREATE TABLE live.item (
  sitting integer NOT NULL REFERENCES live.sitting ON DELETE CASCADE, no smallint NOT NULL,
  kind text NOT NULL CHECK (kind IN ('registration', 'vote')), at timestamp NOT NULL, topic text,
  yes integer, no_ integer, abstain integer, voted integer,   -- a vote
  present integer, listed integer,                           -- a registration
  mismatch text,   -- where the roll call, counted by group, differs a little from the file by group (ingest/parse.check)
  PRIMARY KEY (sitting, no)
);

CREATE TABLE live.item_group (
  sitting integer NOT NULL, no smallint NOT NULL, grp text NOT NULL,
  yes integer, no_ integer, abstain integer, voted integer, present integer, listed integer,
  PRIMARY KEY (sitting, no, grp), FOREIGN KEY (sitting, no) REFERENCES live.item ON DELETE CASCADE
);

-- an MP by the number of the voting system, which is the assembly's own (the same person has another number in
-- the next assembly); the name as the roll call writes it
CREATE TABLE live.mp (
  assembly smallint NOT NULL, no integer NOT NULL, name text NOT NULL, PRIMARY KEY (assembly, no)
);

-- the roll call: + for, - against, = abstain, 0 did not vote; П present at a registration, О and Р not
CREATE TABLE live.vote (
  sitting integer NOT NULL, item smallint NOT NULL, mp integer NOT NULL, grp text NOT NULL, code char(1) NOT NULL,
  PRIMARY KEY (sitting, item, mp), FOREIGN KEY (sitting, item) REFERENCES live.item ON DELETE CASCADE
);
CREATE INDEX ON live.vote (mp, sitting);

-- the current assembly's list of MPs (coll-list-ns): the profile, the group's full name, the constituency
CREATE TABLE live.roster (
  assembly smallint NOT NULL, profile integer NOT NULL, name text NOT NULL, grp_name text, district text,
  since date, PRIMARY KEY (assembly, profile)
);

-- built from live.vote after every import (ingest/stats.py), for the pages
CREATE TABLE live.line (      -- the group's line in a vote: what most of its MPs who voted chose; NULL on a tie
  sitting integer NOT NULL, item smallint NOT NULL, grp text NOT NULL, line char(1), voters integer NOT NULL, with_line integer NOT NULL,
  PRIMARY KEY (sitting, item, grp)
);
CREATE TABLE live.mp_stat (
  assembly smallint NOT NULL, mp integer NOT NULL, name text NOT NULL, grp text NOT NULL,
  votes integer NOT NULL, voted integer NOT NULL, yes integer NOT NULL, no_ integer NOT NULL, abstain integer NOT NULL,
  regs integer NOT NULL, present integer NOT NULL, with_line integer NOT NULL, against_line integer NOT NULL,
  first date NOT NULL, last date NOT NULL, profile integer, district text,
  PRIMARY KEY (assembly, mp)
);

-- the bills since 2001 (ingest/bills.py): sponsors, committees, steps, and the votes of the hall that are theirs
CREATE TABLE live.bill (
  id integer PRIMARY KEY, sign text, date date, title text NOT NULL, final_title text, assembly smallint, session text,
  withdrawn boolean NOT NULL DEFAULT false, adopted date, dv_issue text, dv_year integer, government boolean NOT NULL DEFAULT false,
  sha text, read_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON live.bill (assembly, date);
CREATE TABLE live.bill_sponsor (bill integer NOT NULL REFERENCES live.bill ON DELETE CASCADE, pos smallint NOT NULL, profile integer,
                                name text NOT NULL, PRIMARY KEY (bill, pos));
CREATE INDEX ON live.bill_sponsor (profile);
CREATE TABLE live.bill_committee (bill integer NOT NULL REFERENCES live.bill ON DELETE CASCADE, committee integer NOT NULL, name text NOT NULL,
                                  role text, PRIMARY KEY (bill, committee));
CREATE TABLE live.bill_step (bill integer NOT NULL REFERENCES live.bill ON DELETE CASCADE, id integer NOT NULL, date date, sitting integer,
                             committee integer, committee_name text, what text, stage text, PRIMARY KEY (bill, id));
CREATE INDEX ON live.bill_step (sitting);
CREATE INDEX ON live.bill_step (date);
-- a vote of the hall on the bill: found in the sitting of a hall step by the bill's short title; reading 1, 2 or none
CREATE TABLE live.bill_item (bill integer NOT NULL REFERENCES live.bill ON DELETE CASCADE, sitting integer NOT NULL, item smallint NOT NULL,
                             reading smallint, PRIMARY KEY (bill, sitting, item));
CREATE INDEX ON live.bill_item (sitting, item);

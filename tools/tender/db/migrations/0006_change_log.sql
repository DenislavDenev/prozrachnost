-- What the sources changed after we first read it. No source announces a change, so every lane compares
-- what it reads with what it holds and writes the difference here (docs/methodology.md, 9).
CREATE TABLE ops.change_log (
  id bigserial PRIMARY KEY,
  detected_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,     -- eop-file | eop-record | offers | tr | sebra
  ref text NOT NULL,        -- day/kind, contract id, tender id, ЕИК, resource uri
  field text,               -- the field that changed; NULL for a whole file or answer
  old text,
  new text,
  cause text NOT NULL       -- rewritten: the same published item changed | new-record: a later publication
                            -- held: a smaller answer kept back until a second read | confirmed: the second read agreed
);
CREATE INDEX ON ops.change_log (source, detected_at);

-- the normalize rules of what is live: a record diff is only meaningful between builds of the same rules
CREATE TABLE IF NOT EXISTS ops.published (id int PRIMARY KEY, at timestamptz, eop_last_day date, tr_last_read timestamptz);
ALTER TABLE ops.published ADD COLUMN IF NOT EXISTS rules text;

-- an answer with fewer offers (or a partida gone) replaces what we hold only when a second read agrees
ALTER TABLE eopsvc.queue ADD COLUMN held_sha text;
ALTER TABLE tr.queue ADD COLUMN held_sha text;
ALTER TABLE sebra.resource ADD COLUMN held_sha text;
-- why a partida we already hold is read again: 'change' (the portal's change list) or 'rotate' (the
-- periodic re-read that measures what the change list misses); cleared after the read
ALTER TABLE tr.queue ADD COLUMN why text;

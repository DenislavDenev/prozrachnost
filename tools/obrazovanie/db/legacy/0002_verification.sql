ALTER TABLE live.publication
  ADD COLUMN verification text NOT NULL DEFAULT 'code'
  CHECK (verification IN ('code', 'no-code'));

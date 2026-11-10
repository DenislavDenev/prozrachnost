-- A partida found by name search whose holders match no tracked person hash or company ЕИК is a
-- namesake: it is kept as 'unrelated' (raw XML stays on disk) and contributes no roles or persons.
ALTER TABLE tr.deed DROP CONSTRAINT deed_status_check;
ALTER TABLE tr.deed ADD CONSTRAINT deed_status_check CHECK (status IN ('ok','absent','error','unrelated'));

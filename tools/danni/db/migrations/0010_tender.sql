-- What Тендер needs from the foundation when it moves into the shared database.

-- 1. Search by name in a serving table: trigram indexes. A trusted extension, in schema public, where every role
--    of the database may use it. A serving layer copied out to another database needs the extension there too.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. The change log of Тендер names more causes than the shared list had: a day of the source that arrived late,
--    a listing that changed without the content, a row that came back, a rule of ours that re-read an answer, a
--    count of the source that does not match, and why a register entry was read again.
ALTER TABLE ops.change_log DROP CONSTRAINT change_log_cause_ck;
ALTER TABLE ops.change_log ADD CONSTRAINT change_log_cause_ck CHECK (cause IN (
  'new-record', 'rewritten', 'removed', 'held', 'confirmed', 'invalid', 'gone', 'erased',
  'late', 'listing', 'back', 'reparsed', 'mismatch', 'read', 'change', 'rotate'));
COMMENT ON COLUMN ops.change_log.cause IS 'Защо е редът: нов запис, пренаписан, махнат, задържан, потвърден, невалиден, изчезнал, изтрит по искане; закъснял ден, сменен само списък, върнал се ред, препрочетен по наше правило, несъвпадащ брояч, и причината за повторно четене (read, change, rotate).';

-- 3. The organisations of core.org come from the tables of Тендер. They were read from its own database (schema
--    live); now they are schema tender here. It is the same resource, so its history keeps one name.
UPDATE ops.load SET ref = 'tender.company+tender.buyer' WHERE tool = 'danni' AND source = 'org-tender' AND ref = 'live.company+live.buyer';
UPDATE ops.hold SET ref = 'tender.company+tender.buyer' WHERE tool = 'danni' AND source = 'org-tender' AND ref = 'live.company+live.buyer';
UPDATE ops.change_log SET ref = 'tender.company+tender.buyer' WHERE tool = 'danni' AND source = 'org-tender' AND ref = 'live.company+live.buyer';

-- What the procedure pages of ЦАИС ЕОП add to the open data (ingest/eop_offers.py, audit 27.09.2026).
-- Runs before 60_offers.sql, which copies the lot status into offer_lot.
SET search_path = stage, public;

-- Termination is announced as a message („Решение за прекратяване …“), not through the notice's isCancelled
-- field, which stays „Не“. A decision that names no lot and is not partial cancels the procedure: its state and
-- every lot without a contract become 'cancelled'. A partial one („частично“, „позиция № 2“, „ОП 2“, with the
-- award decision) cancels only the lots it numbers; the others keep their status. A contract always wins.
CREATE TEMP TABLE termination ON COMMIT DROP AS
SELECT t.unp, a.title,
       a.title !~* '(частич|позици|\mОП\s*№?\s*\d|определяне\s+на\s+изпълнител)' AS whole,
       ARRAY(SELECT m[1]::int FROM regexp_matches(a.title, '(?:позици[яи]|\mОП)\s*№?\s*(\d+)', 'gi') m) AS lots
FROM eopsvc.announcement a JOIN tender t ON t.tender_id = a.tender_id::text
WHERE a.gone_at IS NULL AND a.title ~* 'прекрат' AND a.title !~* '(отмен|отказ)';

UPDATE lot l SET status = 'cancelled'
WHERE l.status IN ('open', 'no_contract', 'unawarded')
  AND EXISTS (SELECT 1 FROM termination x WHERE x.unp = l.unp AND (x.whole OR l.lot_no = ANY(x.lots)));

UPDATE tender t SET state = 'cancelled'
WHERE t.state IN ('open', 'no_contract', 'unawarded')
  AND EXISTS (SELECT 1 FROM termination x WHERE x.unp = t.unp AND x.whole);

-- the name of each kind of publication (PublicationFormType), learnt from the notices that are in both
-- sources; the decisions are only on the page, their name is the one the page shows (checked 27.09.2026)
CREATE TABLE form_type AS
SELECT p.form_type, mode() WITHIN GROUP (ORDER BY n.notice_type) AS name, count(*) AS seen
FROM eopsvc.publication p JOIN notice n ON n.notice_id = p.id::text
WHERE p.form_type IS NOT NULL AND n.notice_type IS NOT NULL
GROUP BY 1;
INSERT INTO form_type SELECT * FROM (VALUES (32, 'Решение по чл. 22, ал. 1 от ЗОП', 0)) v(form_type, name, seen)
WHERE NOT EXISTS (SELECT 1 FROM form_type f WHERE f.form_type = v.form_type);

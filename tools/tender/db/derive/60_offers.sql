-- Offers per procedure and lot (ingest/eop_offers.py fills eopsvc.offer from the public procedure pages).
-- Prices are in the currency of the procedure (BGN before 2026): EUR at the fixed rate, other
-- currencies are left without a EUR value. `won`: the bidder (or a consortium member) is a supplier of a
-- contract of the same procedure and lot (lot 0 = the procedure as a whole: any of its contracts),
-- the contract's lot taken from ЦАИС ЕОП when read (contract_lot), else from the open data.
SET search_path = stage, public;

-- the lot of each contract and the lot titles as ЦАИС ЕОП numbers them (the open data's lotIdentifier on
-- contracts does not always follow it); the estimate of a lot comes from the open data lot of the same title
CREATE FUNCTION lot_key(t text) RETURNS text LANGUAGE sql IMMUTABLE AS
  $f$ SELECT upper(regexp_replace(regexp_replace(coalesce(t, ''), '^\s*(обособена\s+позиция\s*(№\s*)?)?\d+\s*[.:)-]?\s*', '', 'i'), '[^[:alnum:]]', '', 'g')) $f$;

CREATE TABLE contract_lot AS
SELECT c.id AS contract_id, t.unp, cl.lot_no
FROM eopsvc.contract_lot cl JOIN tender t ON t.tender_id = cl.tender_id::text
JOIN contract c ON c.unp = t.unp AND c.contract_number = cl.contract_id;
CREATE INDEX ON contract_lot (contract_id);

CREATE TABLE offer_lot AS
SELECT t.unp, l.lot_no, l.title,
       (SELECT o.estimated_eur FROM lot o WHERE o.unp = t.unp AND lot_key(o.title) = lot_key(l.title) AND lot_key(l.title) <> '' LIMIT 1) AS estimated_eur,
       (SELECT o.status FROM lot o WHERE o.unp = t.unp AND lot_key(o.title) = lot_key(l.title) AND lot_key(l.title) <> '' LIMIT 1) AS status
FROM eopsvc.lot l JOIN tender t ON t.tender_id = l.tender_id::text;
CREATE INDEX ON offer_lot (unp);

CREATE TABLE offer AS
SELECT t.unp, o.tender_id, o.lot_no, o.round, o.offer_id, o.bidder_name, o.bidder_eik, o.consortium,
       o.submitted_at, o.price, o.price_opened, t.currency,
       CASE upper(coalesce(t.currency, 'BGN')) WHEN 'EUR' THEN o.price WHEN 'BGN' THEN round(o.price / 1.95583, 2) END AS price_eur,
       co.key AS company_key,
       EXISTS (SELECT 1 FROM contract c JOIN contract_supplier s ON s.contract_id = c.id
               LEFT JOIN contract_lot k ON k.contract_id = c.id
               WHERE c.unp = t.unp AND (o.lot_no = 0 OR coalesce(k.lot_no, c.lot_no, 0) = o.lot_no)
                 AND (s.eik = o.bidder_eik OR s.eik IN (SELECT m->>'eik' FROM jsonb_array_elements(coalesce(o.consortium, '[]')) m))) AS won,
       o.fetched_at
FROM eopsvc.offer o
JOIN tender t ON t.tender_id = o.tender_id::text
LEFT JOIN company co ON co.key = 'eik:' || o.bidder_eik;

CREATE INDEX ON offer (unp, lot_no);
CREATE INDEX ON offer (company_key);

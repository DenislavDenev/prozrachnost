-- Offers per procedure and lot (ingest/eop_offers.py fills eopsvc.offer from the public procedure pages).
-- Prices are in the currency of the procedure (BGN before 2026): EUR at the fixed rate, other
-- currencies are left without a EUR value. `won`: the bidder (or a consortium member) is a supplier of a
-- contract of the same procedure and lot.
SET search_path = stage, public;

CREATE TABLE offer AS
SELECT t.unp, o.tender_id, o.lot_no, o.round, o.offer_id, o.bidder_name, o.bidder_eik, o.consortium,
       o.submitted_at, o.price, o.price_opened, t.currency,
       CASE upper(coalesce(t.currency, 'BGN')) WHEN 'EUR' THEN o.price WHEN 'BGN' THEN round(o.price / 1.95583, 2) END AS price_eur,
       co.key AS company_key,
       EXISTS (SELECT 1 FROM contract c JOIN contract_supplier s ON s.contract_id = c.id
               WHERE c.unp = t.unp AND coalesce(c.lot_no, 0) = o.lot_no
                 AND (s.eik = o.bidder_eik OR s.eik IN (SELECT m->>'eik' FROM jsonb_array_elements(coalesce(o.consortium, '[]')) m))) AS won,
       o.fetched_at
FROM eopsvc.offer o
JOIN tender t ON t.tender_id = o.tender_id::text
LEFT JOIN company co ON co.key = 'eik:' || o.bidder_eik;

CREATE INDEX ON offer (unp, lot_no);
CREATE INDEX ON offer (company_key);

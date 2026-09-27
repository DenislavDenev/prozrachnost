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

CREATE INDEX ON company (upper(name));

CREATE TABLE offer AS
SELECT t.unp, o.tender_id, o.lot_no, o.round, o.offer_id, o.bidder_name, o.bidder_eik, o.submitter_name, o.submitter_eik, o.consortium,
       o.submitted_at, o.price, o.price_opened, t.currency,
       CASE upper(coalesce(t.currency, 'BGN')) WHEN 'EUR' THEN o.price WHEN 'BGN' THEN round(o.price / 1.95583, 2) END AS price_eur,
       coalesce(co.key, byname.key) AS company_key,
       EXISTS (SELECT 1 FROM contract c JOIN contract_supplier s ON s.contract_id = c.id
               LEFT JOIN contract_lot k ON k.contract_id = c.id
               WHERE c.unp = t.unp AND (o.lot_no = 0 OR coalesce(k.lot_no, c.lot_no, 0) = o.lot_no)
                 AND (s.eik = o.bidder_eik OR s.eik IN (SELECT m->>'eik' FROM jsonb_array_elements(coalesce(o.consortium, '[]')) m))) AS won,
       o.fetched_at
FROM eopsvc.offer o
JOIN tender t ON t.tender_id = o.tender_id::text
LEFT JOIN company co ON co.key = 'eik:' || o.bidder_eik
-- a participant without an ЕИK (it came from a person's account): the only company of exactly that name
LEFT JOIN LATERAL (SELECT min(c.key) AS key FROM company c WHERE o.bidder_eik IS NULL AND o.bidder_name <> 'Физическо лице'
                   AND c.eik IS NOT NULL AND upper(c.name) = upper(o.bidder_name) HAVING count(*) = 1) byname ON true;

CREATE INDEX ON offer (unp, lot_no);
CREATE INDEX ON offer (company_key);

-- tags from the offers (methodology 4, 7b); per company, last 3 years of offers
INSERT INTO tag_def VALUES
 ('not_lowest_win', 'statistical', 'Печели без най-ниската цена',
  'позиции с критерий „най-ниска цена“, спечелени с оферта над най-ниската отворена цена', 3, 2, NULL, 'v1'),
 ('frequent_loser', 'statistical', 'Често участва, не печели',
  'позиции с оферта от фирмата / спечелени от тях', 3, 10, 0, 'v1');

CREATE TEMP TABLE lot_low ON COMMIT DROP AS
SELECT o.unp, o.lot_no, min(o.price_eur) AS low,
       bool_or(c.award_method ILIKE '%цена%' AND c.award_method NOT ILIKE '%качество%') AS price_only
FROM offer o LEFT JOIN contract c ON c.unp = o.unp
WHERE o.price_eur IS NOT NULL AND o.submitted_at >= current_date - interval '3 years'
GROUP BY 1, 2;

INSERT INTO tag
SELECT 'company', o.company_key, 'not_lowest_win',
       format('%s пъти за последните 3 г. спечели позиция с критерий „най-ниска цена“ с оферта над най-ниската', count(*)),
       jsonb_build_object('lots', count(*), 'unps', (array_agg(DISTINCT o.unp))[1:20], 'window_years', 3)
FROM offer o JOIN lot_low l ON l.unp = o.unp AND l.lot_no = o.lot_no
WHERE o.won AND l.price_only AND o.company_key IS NOT NULL AND o.price_eur > l.low * 1.0001
GROUP BY o.company_key HAVING count(*) >= 2
ON CONFLICT DO NOTHING;

INSERT INTO tag
SELECT 'company', company_key, 'frequent_loser',
       format('Участва с оферта в %s позиции за последните 3 г. и не спечели нито една', lots),
       jsonb_build_object('lots', lots, 'window_years', 3)
FROM (SELECT company_key, count(DISTINCT (unp, lot_no)) AS lots, bool_or(won) AS any_won FROM offer
      WHERE company_key IS NOT NULL AND submitted_at >= current_date - interval '3 years' GROUP BY 1) x
WHERE lots >= 10 AND NOT any_won
ON CONFLICT DO NOTHING;

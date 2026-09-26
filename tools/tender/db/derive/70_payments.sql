-- Budget payments (СЕБРА, ingest/sebra.py) matched to our companies and buyers. The lists have no ЕИК
-- column, so the match is by exact name after dropping the legal form and everything but letters and
-- digits ("СОФАРМА ТРЕЙДИНГ АД" = „Софарма трейдинг“, which the registry keeps without the form;
-- "ЕООД СОФАРМАСИ 11" = „Софармаси 11“ ЕООД), and only when that name belongs to exactly one company
-- (one buyer). A receiver written with its ЕИК ("СОФАРМА ТРЕЙДИНГ 103267194") is matched by the ЕИК.
-- A payment whose receiver matches nothing stays with its name only.
SET search_path = stage, public;

CREATE FUNCTION pay_key(t text) RETURNS text LANGUAGE sql IMMUTABLE AS
  $f$ SELECT nullif(regexp_replace(regexp_replace(upper(t), '\m(ЕООД|ООД|ЕАД|АД|ЕТ|КДА|КД|СД|АДСИЦ|ЕАДСИЦ)\M', '', 'g'),
                                   '[^[:alnum:]]', '', 'g'), '') $f$;

CREATE TABLE payment AS
WITH co AS (SELECT pay_key(name) k, min(key) AS key FROM company WHERE name IS NOT NULL GROUP BY 1 HAVING count(DISTINCT key) = 1),
     bu AS (SELECT pay_key(name) k, min(eik) AS eik FROM buyer WHERE name IS NOT NULL GROUP BY 1 HAVING count(*) = 1)
SELECT p.resource_uri, p.row_no, p.settlement_date, p.receiver_name, p.is_person, p.receiver_iban,
       p.amount, p.currency,
       CASE upper(coalesce(p.currency, 'BGN')) WHEN 'EUR' THEN p.amount WHEN 'BGN' THEN round(p.amount / 1.95583, 2) END AS amount_eur,
       p.reason, p.reg_date, p.reg_no, p.pay_code, p.fin_code, p.fin_name, p.organization, p.primary_organization, p.primary_org_code,
       CASE WHEN NOT p.is_person THEN coalesce(ce.key, co.key) END AS company_key,
       bu.eik AS buyer_eik
FROM sebra.payment p
LEFT JOIN co ON co.k = pay_key(p.receiver_name)
LEFT JOIN company ce ON ce.eik = substring(p.receiver_name FROM '(?:^|\D)(\d{9}|\d{13})(?:\D|$)')
LEFT JOIN bu ON bu.k = pay_key(p.organization);

CREATE INDEX ON payment (company_key);
CREATE INDEX ON payment (buyer_eik);
CREATE INDEX ON payment (settlement_date);
CREATE INDEX ON payment (primary_org_code);

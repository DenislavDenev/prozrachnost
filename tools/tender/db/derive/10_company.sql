-- Companies and persons in stage. A company is a party key: 'eik:<ЕИК>' when the ЕИК is valid,
-- else 'name:<normalized name>'. The Trade Register partida is keyed by the 9-digit ЕИК, so a
-- 13-digit BULSTAT of a branch/unit joins its parent partida through left(eik, 9).
SET search_path = stage, public;

-- contracts that count in money sums: framework ceilings are shown but not summed (methodology 3)
CREATE VIEW contract_summable AS
SELECT * FROM contract WHERE NOT is_framework;

CREATE TABLE company AS
WITH parties AS (
  SELECT party_key, eik, name FROM contract_supplier
  UNION ALL SELECT party_key, eik, name FROM subcontract
  UNION ALL SELECT 'eik:' || eik, eik, clean_name(name) FROM tr.deed WHERE status = 'ok'
  -- companies that hold roles but whose own partida is not read yet still get a page
  UNION ALL SELECT 'eik:' || holder_id, holder_id, clean_name(holder_name) FROM tr.role
            WHERE holder_kind = 'entity' AND holder_id ~ '^\d{9}$'
), modal AS (
  SELECT DISTINCT ON (party_key) party_key, eik, name
  FROM (SELECT party_key, max(eik) OVER (PARTITION BY party_key) AS eik, name, count(*) AS n
        FROM parties WHERE name IS NOT NULL GROUP BY party_key, eik, name) x
  ORDER BY party_key, n DESC, name
), keys AS (SELECT DISTINCT party_key FROM parties)
SELECT k.party_key AS key,
       coalesce(m.eik, CASE WHEN k.party_key LIKE 'eik:%' THEN substr(k.party_key, 5) END) AS eik,
       coalesce(clean_name(d.name), m.name) AS name,
       d.legal_form, d.seat, d.registered_on, d.deed_status, d.capital_eur,
       d.status AS tr_status, d.fetched_at AS tr_fetched_at,
       EXISTS (SELECT 1 FROM buyer b WHERE b.eik = coalesce(m.eik, substr(k.party_key, 5))
                                         OR left(b.eik, 9) = left(coalesce(m.eik, substr(k.party_key, 5)), 9))
         AS is_public_body
FROM keys k
LEFT JOIN modal m ON m.party_key = k.party_key
LEFT JOIN tr.deed d ON d.eik = left(coalesce(m.eik, substr(k.party_key, 5)), 9) AND k.party_key LIKE 'eik:%';
ALTER TABLE company ADD PRIMARY KEY (key);
CREATE INDEX ON company (eik);
CREATE INDEX ON company (left(eik, 9));

-- per-company contract rollup (joint contracts counted in full for each member, flagged)
CREATE TABLE company_stats AS
SELECT s.party_key AS key,
       count(DISTINCT c.id) AS contracts,
       count(DISTINCT c.id) FILTER (WHERE s.joint) AS joint_contracts,
       sum(c.amount_eur) FILTER (WHERE NOT c.is_framework) AS amount_eur,
       sum(c.amount_eur) FILTER (WHERE NOT c.is_framework AND NOT s.joint) AS amount_eur_sole,
       count(DISTINCT c.id) FILTER (WHERE c.amount_eur IS NULL) AS contracts_without_value,
       count(DISTINCT c.id) FILTER (WHERE c.is_framework) AS frameworks,
       count(DISTINCT c.buyer_eik) AS buyers,
       min(c.effective_date) AS first_contract, max(c.effective_date) AS last_contract
FROM contract_supplier s JOIN contract c ON c.id = s.contract_id
GROUP BY s.party_key;
ALTER TABLE company_stats ADD PRIMARY KEY (key);

CREATE TABLE buyer_stats AS
SELECT c.buyer_eik AS eik, count(*) AS contracts,
       sum(c.amount_eur) FILTER (WHERE NOT c.is_framework) AS amount_eur,
       count(*) FILTER (WHERE c.amount_eur IS NULL) AS contracts_without_value,
       count(DISTINCT s.party_key) AS suppliers,
       min(c.effective_date) AS first_contract, max(c.effective_date) AS last_contract,
       (SELECT count(*) FROM tender t WHERE t.buyer_eik = c.buyer_eik) AS tenders
FROM contract c LEFT JOIN contract_supplier s ON s.contract_id = c.id AND s.position = 0
WHERE c.buyer_eik IS NOT NULL
GROUP BY c.buyer_eik;
ALTER TABLE buyer_stats ADD PRIMARY KEY (eik);

-- per-procedure rollup; a procedure's date is its notice date, else its first contract
CREATE TABLE tender_stats AS
SELECT c.unp, count(*) AS contracts,
       sum(c.amount_eur) FILTER (WHERE NOT c.is_framework) AS amount_eur,
       min(c.effective_date) AS first_contract,
       count(*) FILTER (WHERE c.offers_count = 1) AS single_bid
FROM contract c WHERE c.unp IS NOT NULL GROUP BY c.unp;
ALTER TABLE tender_stats ADD PRIMARY KEY (unp);

-- persons the register identifies by hash; public id is tr.person.id, the hash never leaves the db
CREATE TABLE person AS
SELECT p.id, clean_person(p.name) AS name, p.name_key, p.indent_type FROM tr.person p;
ALTER TABLE person ADD PRIMARY KEY (id);
CREATE INDEX ON person (name_key);

CREATE INDEX ON contract (buyer_eik);
CREATE INDEX ON contract (unp);
CREATE INDEX ON contract (effective_date);
CREATE INDEX ON contract_supplier (party_key);
CREATE INDEX ON contract_supplier (left(eik, 9));
CREATE INDEX ON subcontract (party_key);
CREATE INDEX ON amendment (contract_id);

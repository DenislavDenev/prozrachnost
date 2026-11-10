-- Which people the pages may show (the owner's decision of 10.11.2026, the list of cases of the project).
--
-- A manager or an owner is shown with the full name only in a role in an entity that received public money, and in
-- that role only. Everybody else in the Trade Register is not shown. "Received public money" is: the entity is a
-- supplier (alone or as a member of a joint supplier) of a contract, or the receiver of a payment from the budget
-- (СЕБРА). An offer that did not win is not public money.
--
-- The serving layer holds no key of a person. The register's identifier never leaves the landing zone, and our own
-- number of the person (tr_person.id) stays on the ingest side. A page still needs something to address a person
-- by, and the network needs a node: that is `shown`, a random token made anew by every build. It lives for one
-- build, so it links nothing between two publications, and nothing can be computed from it.
SET search_path = tender_stage, public;

CREATE TABLE public_money AS
SELECT node, bool_or(by_contract) AS has_contract, bool_or(NOT by_contract) AS has_payment
FROM (SELECT node, true AS by_contract FROM node_contract
      UNION ALL
      SELECT 'c:' || left(substr(company_key, 5), 9), false FROM payment WHERE company_key LIKE 'eik:%') x
GROUP BY node;
ALTER TABLE public_money ADD PRIMARY KEY (node);

CREATE TABLE person_token AS
SELECT h.holder AS node, left(h.holder, 2) || replace(gen_random_uuid()::text, '-', '') AS shown
FROM (SELECT DISTINCT e.holder FROM edge e JOIN public_money m ON m.node = e.company
      WHERE e.holder_kind = 'person' AND e.holder IS NOT NULL) h;
ALTER TABLE person_token ADD PRIMARY KEY (node);
ALTER TABLE person_token ADD UNIQUE (shown);

-- the roles of people that are shown: only those in an entity with public money
CREATE TABLE shown_person_edge AS
SELECT e.id, t.shown AS holder, e.company, e.holder_kind, e.holder_name, e.role, e.view, e.share, e.field_ident, e.entry_no,
       e.sub_uic, e.valid_from, e.valid_to, e.uncertain_after, e.observed_at, e.in_branch, e.share_pct
FROM edge e JOIN person_token t ON t.node = e.holder JOIN public_money m ON m.node = e.company
WHERE e.holder_kind = 'person';
CREATE INDEX ON shown_person_edge (holder);

-- the people the register tells apart, with a page of their own; the coverage of the name search rides along,
-- so the serving layer needs no table keyed by a name
CREATE TABLE shown_person AS
SELECT substr(t.shown, 3) AS id, p.name, p.name_key, p.indent_type,
       s.status AS search_status, s.total AS search_total, s.searched_at
FROM person p JOIN person_token t ON t.node = 'p:' || p.id
LEFT JOIN tender_src.tr_name_search s ON s.name_key = p.name_key;
ALTER TABLE shown_person ADD PRIMARY KEY (id);

-- as person_stats (20_network.sql), over the shown roles only
CREATE TABLE shown_person_stats AS
WITH roles AS (
  SELECT substr(holder, 3) AS id, count(DISTINCT company) AS companies,
         count(DISTINCT company) FILTER (WHERE valid_to IS NULL AND uncertain_after IS NULL) AS active,
         min(valid_from) AS first_role
  FROM shown_person_edge WHERE holder LIKE 'p:%' GROUP BY 1
), pc AS (
  SELECT DISTINCT substr(e.holder, 3) AS id, c.id AS contract_id, c.amount_eur, c.is_framework
  FROM shown_person_edge e JOIN node_contract k ON k.node = e.company JOIN contract c ON c.id = k.contract_id
  WHERE e.holder LIKE 'p:%' AND c.effective_date >= e.valid_from
    AND (e.valid_to IS NULL OR c.effective_date < e.valid_to)
), money AS (
  SELECT id, count(*) AS contracts, sum(amount_eur) FILTER (WHERE NOT is_framework) AS amount_eur
  FROM pc GROUP BY id
)
SELECT r.id, r.companies, r.active, r.first_role, coalesce(m.contracts, 0) AS contracts, m.amount_eur
FROM roles r LEFT JOIN money m USING (id);
ALTER TABLE shown_person_stats ADD PRIMARY KEY (id);

CREATE TABLE shown_person_node AS
SELECT DISTINCT ON (id) id, kind, label, ref FROM (
  SELECT 'p:' || id AS id, 'person' AS kind, name AS label, id AS ref FROM shown_person
  UNION ALL
  SELECT holder, 'local_person', holder_name, NULL FROM shown_person_edge WHERE holder LIKE 'l:%'
) x ORDER BY id, label IS NULL, kind;
ALTER TABLE shown_person_node ADD PRIMARY KEY (id);

CREATE TABLE shown_person_search AS
SELECT 'person'::text AS kind, p.id AS ref, p.name AS label,
       'Лице · ' || count(DISTINCT e.company) || ' дружества' AS sub,
       p.name_key AS key, count(DISTINCT e.company)::numeric AS weight
FROM shown_person p JOIN shown_person_edge e ON e.holder = 'p:' || p.id
GROUP BY p.id, p.name, p.name_key;
ALTER TABLE shown_person_search ADD PRIMARY KEY (ref);

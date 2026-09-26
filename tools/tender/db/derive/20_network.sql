-- Edges of the network of related persons and companies (methodology 1).
-- Node ids: 'p:<person id>' (hash-identified person), 'l:<md5>' (person known only inside one
-- partida), 'c:<ЕИК 9>' (a company with a partida), 'f:<md5>' (a foreign/unregistered entity by name).
SET search_path = stage, public;

CREATE TABLE edge AS
SELECT
  row_number() OVER (ORDER BY r.eik, r.field_ident, r.entry_no, r.holder_id) AS id,
  CASE
    WHEN r.holder_kind = 'person' AND r.holder_id NOT LIKE 'local:%' THEN 'p:' || p.id
    WHEN r.holder_kind = 'person' THEN 'l:' || md5(r.holder_id)
    WHEN r.holder_id ~ '^\d{9}$' THEN 'c:' || r.holder_id
    WHEN r.holder_id ~ '^\d{13}$' THEN 'c:' || left(r.holder_id, 9)
    ELSE 'f:' || md5(r.holder_id)
  END AS holder,
  'c:' || r.eik AS company,
  r.holder_kind, r.holder_name, r.role,
  CASE WHEN r.role IN ('partner', 'sole_owner', 'trader') THEN 'ownership' ELSE 'management' END AS view,
  r.share,
  r.field_ident, r.entry_no, r.sub_uic, r.valid_from, r.valid_to, r.uncertain_after, r.observed_at,
  -- a sub-partida other than the main one is a branch: the role is in the branch, shown as such
  (r.sub_uic <> '' AND r.sub_uic IS NOT NULL AND r.role = 'branch_manager') AS in_branch
FROM tr.role r
LEFT JOIN tr.person p ON p.indent = r.holder_id
WHERE r.role <> 'beneficial_owner'          -- collected, never shown (methodology 1)
  AND r.holder_id <> r.eik;                 -- a company listed in its own partida is not an edge
CREATE INDEX ON edge (holder);
CREATE INDEX ON edge (company);
CREATE INDEX ON edge (view);

-- share as a percent where the register gives it: '50%' directly, or an amount over the capital
ALTER TABLE edge ADD COLUMN share_pct numeric;
UPDATE edge e SET share_pct = CASE
    WHEN e.share ~ '\d+([.,]\d+)?\s*%' THEN replace(substring(e.share FROM '(\d+(?:[.,]\d+)?)\s*%'), ',', '.')::numeric
    WHEN e.share ~ '^\s*\d+([.,]\d+)?' AND d.capital_eur > 0 THEN
      round(100 * replace(substring(e.share FROM '^\s*(\d+(?:[.,]\d+)?)'), ',', '.')::numeric
            / (CASE WHEN e.share ILIKE '%EUR%' OR e.share ILIKE '%евро%' THEN d.capital_eur
                    ELSE d.capital_eur * 1.95583 END), 4)
  END
FROM tr.deed d WHERE d.eik = substr(e.company, 3) AND e.share IS NOT NULL;
UPDATE edge SET share_pct = NULL WHERE share_pct > 100.0001 OR share_pct < 0;
UPDATE edge SET share_pct = 100 WHERE role = 'sole_owner';

-- node labels for the graph and profiles
CREATE TABLE node AS
SELECT DISTINCT ON (id) id, kind, label, ref FROM (
  SELECT 'p:' || id AS id, 'person' AS kind, name AS label, id AS ref FROM person
  UNION ALL
  SELECT holder, CASE WHEN holder LIKE 'l:%' THEN 'local_person' ELSE 'foreign_entity' END,
         holder_name, NULL FROM edge WHERE holder LIKE 'l:%' OR holder LIKE 'f:%'
  UNION ALL
  SELECT 'c:' || left(eik, 9), 'company', name, key FROM company WHERE eik ~ '^\d{9}(\d{4})?$'
  UNION ALL
  SELECT company, 'company', NULL, 'eik:' || substr(company, 3) FROM edge
) x ORDER BY id, label IS NULL, kind;
ALTER TABLE node ADD PRIMARY KEY (id);

-- the contracts a company node won (supplier or joint member), for network sums
CREATE TABLE node_contract AS
SELECT DISTINCT 'c:' || left(s.eik, 9) AS node, s.contract_id, s.joint
FROM contract_supplier s WHERE s.eik ~ '^\d{9}(\d{4})?$';
CREATE INDEX ON node_contract (node);
CREATE INDEX ON node_contract (contract_id);

-- per-person rollup for the persons list: companies, active roles, and contracts of those
-- companies signed while the person held a role there (methodology 2), each contract once
CREATE TABLE person_stats AS
WITH roles AS (
  SELECT substr(holder, 3) AS id, count(DISTINCT company) AS companies,
         count(DISTINCT company) FILTER (WHERE valid_to IS NULL AND uncertain_after IS NULL) AS active,
         min(valid_from) AS first_role
  FROM edge WHERE holder LIKE 'p:%' GROUP BY 1
), pc AS (
  SELECT DISTINCT substr(e.holder, 3) AS id, c.id AS contract_id, c.amount_eur, c.is_framework
  FROM edge e JOIN node_contract k ON k.node = e.company JOIN contract c ON c.id = k.contract_id
  WHERE e.holder LIKE 'p:%' AND c.effective_date >= e.valid_from
    AND (e.valid_to IS NULL OR c.effective_date < e.valid_to)
), money AS (
  SELECT id, count(*) AS contracts, sum(amount_eur) FILTER (WHERE NOT is_framework) AS amount_eur
  FROM pc GROUP BY id
)
SELECT r.id, r.companies, r.active, r.first_role, coalesce(m.contracts, 0) AS contracts, m.amount_eur
FROM roles r LEFT JOIN money m USING (id);
ALTER TABLE person_stats ADD PRIMARY KEY (id);

-- filled by ingest.networks (union-find over edge per view and mode)
CREATE TABLE network_component (
  view text NOT NULL, mode text NOT NULL, node text NOT NULL, component bigint NOT NULL,
  PRIMARY KEY (view, mode, node)
);
CREATE TABLE network_component_stats (
  view text NOT NULL, mode text NOT NULL, component bigint NOT NULL, nodes int NOT NULL,
  persons int NOT NULL, companies int NOT NULL, contracts int NOT NULL, amount_eur numeric,
  PRIMARY KEY (view, mode, component)
);

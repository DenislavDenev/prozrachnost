-- Every recipient of the snapshot has a row in gold.beneficiary (kind and municipality decided): else it would be silently left out.
SELECT 'получатели без златен ред: ' || count(*) AS problem
FROM silver.holder h
WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s)
  AND NOT EXISTS (SELECT 1 FROM gold.beneficiary g WHERE g.beneficiary_id = h.beneficiary_id)
HAVING count(*) > 0;

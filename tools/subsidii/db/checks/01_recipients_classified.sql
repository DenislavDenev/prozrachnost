-- Every recipient of the snapshot has a row in subsidii.beneficiary (kind and municipality decided): else it would be silently left out.
SELECT 'получатели без вид и община: ' || count(*) AS problem
FROM subsidii_src.holder h
WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s)
  AND NOT EXISTS (SELECT 1 FROM subsidii.beneficiary g WHERE g.beneficiary_id = h.beneficiary_id)
HAVING count(*) > 0;

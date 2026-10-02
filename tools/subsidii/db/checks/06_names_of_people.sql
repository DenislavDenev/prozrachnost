-- The rule for names: after the term, the view shows no name of a natural person or sole trader, and an export never has one.
SELECT 'име на физическо лице извън срока или в износ: ' || count(*) AS problem
FROM gold.recipient r JOIN silver.beneficiary b ON b.id = r.beneficiary_id
WHERE r.fy = %(fy)s AND r.kind <> 'legal'
  AND ((r.name_expired AND r.name_shown = btrim(b.name || CASE WHEN b.surname = '-' THEN '' ELSE ' ' || b.surname END))
       OR r.name_export = btrim(b.name || CASE WHEN b.surname = '-' THEN '' ELSE ' ' || b.surname END))
HAVING count(*) > 0;

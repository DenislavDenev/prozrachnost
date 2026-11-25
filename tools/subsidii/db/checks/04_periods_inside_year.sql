-- The dates of the payments are inside the financial year they were read for (16.10 to 15.10).
SELECT 'плащания извън финансовата година ' || %(fy)s || ': ' || count(*) AS problem
FROM subsidii_src.payment p JOIN subsidii.source_year y ON y.fy = p.fy
WHERE p.fy = %(fy)s AND p.from_day <= %(snap)s AND (p.to_day IS NULL OR p.to_day >= %(snap)s) AND (p.starts < y.starts OR p.ends > y.ends)
HAVING count(*) > 0;

-- The gold totals are the totals of the file, to the cent: the summary against the ОБЩО rows of the file, and the
-- municipalities (with the ones the list does not know) against the summary, fund by fund.
SELECT 'сбор ' || f.fund || ': файл ' || f.stated || ', злато по общини ' || coalesce(f.by_municipality, 0) || ' и непознати ' || coalesce(f.unmatched, 0) AS problem
FROM (
  SELECT x.fund, x.stated, x.summary, m.v AS by_municipality, u.v AS unmatched
  FROM (SELECT 'ЕФГЗ' AS fund, s.total_efgz AS stated, a.efgz AS summary FROM silver.snapshot s JOIN gold.summary a ON a.fy = s.fy AND a.snap_day = s.day AND a.scope = 'all' WHERE s.fy = %(fy)s AND s.day = %(snap)s
        UNION ALL SELECT 'ЕЗФРС', s.total_ezfrs, a.ezfrs FROM silver.snapshot s JOIN gold.summary a ON a.fy = s.fy AND a.snap_day = s.day AND a.scope = 'all' WHERE s.fy = %(fy)s AND s.day = %(snap)s
        UNION ALL SELECT 'НБ', s.total_nb, a.nb FROM silver.snapshot s JOIN gold.summary a ON a.fy = s.fy AND a.snap_day = s.day AND a.scope = 'all' WHERE s.fy = %(fy)s AND s.day = %(snap)s
        UNION ALL SELECT 'Общо', s.total_all, a.total FROM silver.snapshot s JOIN gold.summary a ON a.fy = s.fy AND a.snap_day = s.day AND a.scope = 'all' WHERE s.fy = %(fy)s AND s.day = %(snap)s) x
  LEFT JOIN LATERAL (SELECT CASE x.fund WHEN 'ЕФГЗ' THEN sum(efgz) WHEN 'ЕЗФРС' THEN sum(ezfrs) WHEN 'НБ' THEN sum(nb) ELSE sum(total) END AS v
                     FROM gold.municipality_fy WHERE fy = %(fy)s AND snap_day = %(snap)s) m ON true
  LEFT JOIN LATERAL (SELECT sum(h.v) AS v FROM (
        SELECT CASE x.fund WHEN 'ЕФГЗ' THEN h.efgz WHEN 'ЕЗФРС' THEN h.ezfrs WHEN 'НБ' THEN h.nb ELSE h.total END AS v
        FROM silver.holder h JOIN gold.beneficiary g ON g.beneficiary_id = h.beneficiary_id
        WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s) AND g.municipality_id IS NULL) h) u ON true
) f
WHERE f.stated IS DISTINCT FROM f.summary OR coalesce(f.by_municipality, 0) + coalesce(f.unmatched, 0) IS DISTINCT FROM coalesce(f.summary, 0);

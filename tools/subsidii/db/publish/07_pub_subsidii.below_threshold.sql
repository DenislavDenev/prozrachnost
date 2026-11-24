-- pub_subsidii.below_threshold: the natural persons and sole traders with the threshold or less, as a count and sums by
-- municipality for every good snapshot: no row, no name (the exact complement of the people in pub_subsidii.recipient)
SELECT k.fy, k.snap_day, g.municipality_code, g.kind, count(*), sum(h.efgz), sum(h.ezfrs), sum(h.nb), sum(h.total)
FROM subsidii.build k
JOIN subsidii_src.holder h ON h.fy = k.fy AND h.from_day <= k.snap_day AND (h.to_day IS NULL OR h.to_day >= k.snap_day)
JOIN subsidii.beneficiary g USING (beneficiary_id)
JOIN subsidii.fiscal_year y ON y.fy = k.fy
WHERE k.ok AND g.kind <> 'legal' AND g.municipality_code IS NOT NULL
  AND (y.eur_per_unit IS NULL OR h.total IS NULL OR h.total * y.eur_per_unit <= subsidii.name_threshold_eur())
GROUP BY k.fy, k.snap_day, g.municipality_code, g.kind

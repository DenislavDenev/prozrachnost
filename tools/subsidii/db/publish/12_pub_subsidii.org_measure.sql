-- pub_subsidii.org_measure: the payments of a legal entity by measure, for every good snapshot
SELECT g.org_id, k.fy, k.snap_day, p.measure_id, count(*)::int, sum(p.efgz), sum(p.ezfrs), sum(p.nb)
FROM subsidii.build k
JOIN subsidii_src.payment p ON p.fy = k.fy AND p.from_day <= k.snap_day AND (p.to_day IS NULL OR p.to_day >= k.snap_day)
JOIN subsidii.beneficiary g USING (beneficiary_id)
WHERE k.ok AND g.kind = 'legal'
GROUP BY g.org_id, k.fy, k.snap_day, p.measure_id

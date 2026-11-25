-- pub_subsidii.concentration: the share of the top 1 percent of the recipient ROWS (never people) of the country, each
-- oblast and each municipality, by kind and for all, for every good snapshot. It is counted from every row, the ones
-- below the threshold too, so the pages need no row of a person to show it. Rows in an unknown municipality are left out.
WITH r AS (
  SELECT k.fy, k.snap_day, g.kind, g.municipality_code, m.oblast_code, h.total
  FROM subsidii.build k
  JOIN subsidii_src.holder h ON h.fy = k.fy AND h.from_day <= k.snap_day AND (h.to_day IS NULL OR h.to_day >= k.snap_day)
  JOIN subsidii.beneficiary g USING (beneficiary_id)
  JOIN core.municipality m ON m.municipality_code = g.municipality_code
  WHERE k.ok),
placed AS (
  SELECT fy, snap_day, 'bg' AS place_level, 'BG' AS place, kind, total FROM r
  UNION ALL SELECT fy, snap_day, 'oblast', oblast_code, kind, total FROM r
  UNION ALL SELECT fy, snap_day, 'municipality', municipality_code, kind, total FROM r),
scoped AS (SELECT * FROM placed UNION ALL SELECT fy, snap_day, place_level, place, 'all', total FROM placed),
ranked AS (
  SELECT fy, snap_day, place_level, place, kind, total,
         row_number() OVER (PARTITION BY fy, snap_day, place_level, place, kind ORDER BY total DESC NULLS LAST) AS rk,
         count(*) OVER (PARTITION BY fy, snap_day, place_level, place, kind) AS n
  FROM scoped)
SELECT fy, snap_day, place_level, place, kind, max(n)::int, sum(total),
       (count(*) FILTER (WHERE rk <= greatest(1, ceil(n * 0.01))))::int, sum(total) FILTER (WHERE rk <= greatest(1, ceil(n * 0.01)))
FROM ranked GROUP BY fy, snap_day, place_level, place, kind

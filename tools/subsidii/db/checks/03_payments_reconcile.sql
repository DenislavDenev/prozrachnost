-- The payments by municipality and measure add up to the payment rows of the file, fund by fund (the rows of a recipient in a
-- municipality the list does not know are left out and counted in subsidii.unmatched, so they are taken off here).
SELECT 'плащания ' || f.fund || ': файл ' || f.stated || ', изградено ' || coalesce(f.gold, 0) || ', без известна община ' || coalesce(f.off, 0) AS problem
FROM (
  SELECT x.fund, x.stated,
         (SELECT CASE x.fund WHEN 'ЕФГЗ' THEN sum(efgz) WHEN 'ЕЗФРС' THEN sum(ezfrs) ELSE sum(nb) END FROM subsidii.payment_agg WHERE fy = %(fy)s AND snap_day = %(snap)s) AS gold,
         (SELECT sum(CASE x.fund WHEN 'ЕФГЗ' THEN p.efgz WHEN 'ЕЗФРС' THEN p.ezfrs ELSE p.nb END)
          FROM subsidii_src.payment p JOIN subsidii.beneficiary g ON g.beneficiary_id = p.beneficiary_id
          WHERE p.fy = %(fy)s AND p.from_day <= %(snap)s AND (p.to_day IS NULL OR p.to_day >= %(snap)s) AND g.municipality_code IS NULL) AS off
  FROM (SELECT 'ЕФГЗ' AS fund, paid_efgz AS stated FROM subsidii.snapshot WHERE fy = %(fy)s AND day = %(snap)s
        UNION ALL SELECT 'ЕЗФРС', paid_ezfrs FROM subsidii.snapshot WHERE fy = %(fy)s AND day = %(snap)s
        UNION ALL SELECT 'НБ', paid_nb FROM subsidii.snapshot WHERE fy = %(fy)s AND day = %(snap)s) x
) f
WHERE coalesce(f.gold, 0) + coalesce(f.off, 0) IS DISTINCT FROM coalesce(f.stated, 0);

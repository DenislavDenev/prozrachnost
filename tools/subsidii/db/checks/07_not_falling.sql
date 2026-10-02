-- The number of recipients does not fall by more than a tenth against the previous snapshot of the same year.
SELECT 'получателите паднаха от ' || p.recipients || ' на ' || a.recipients AS problem
FROM gold.summary a JOIN LATERAL (SELECT recipients FROM gold.summary WHERE fy = a.fy AND scope = 'all' AND snap_day < a.snap_day ORDER BY snap_day DESC LIMIT 1) p ON true
WHERE a.fy = %(fy)s AND a.snap_day = %(snap)s AND a.scope = 'all' AND a.recipients < p.recipients * 0.9;

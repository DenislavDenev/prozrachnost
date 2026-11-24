-- Recipients in a municipality the list does not know: at most 0.2 percent of the recipients, else the list or the source changed.
SELECT 'получатели в непознати общини: ' || coalesce(sum(n), 0) || ' от ' || (SELECT recipients FROM subsidii.summary WHERE fy = %(fy)s AND snap_day = %(snap)s AND scope = 'all') AS problem
FROM subsidii.unmatched WHERE kind = 'municipality'
HAVING coalesce(sum(n), 0) * 500 > (SELECT recipients FROM subsidii.summary WHERE fy = %(fy)s AND snap_day = %(snap)s AND scope = 'all');

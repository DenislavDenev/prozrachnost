SELECT f.source, f.last_read_at, f.last_ok_at,
       CASE f.source WHEN 'kolkostruva' THEN (SELECT max(day) FROM pazar.day WHERE status = 'built') ELSE (SELECT max(week) FROM pazar.fuel_week) END
FROM ops.freshness f WHERE f.tool = 'pazar'

SELECT w.row_no, w.scp, w.name, w.lat, w.lon, w.air, w.surface, w.humidity, w.wind,
       to_char(w.observed AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
FROM patna_obstanovka.weather w

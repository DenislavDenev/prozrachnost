SELECT t.row_no, t.scp, t.name, t.lat, t.lon, t.count15, t.count60, t.speed15,
       to_char(t.observed AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'), b.bearing
FROM patna_obstanovka.traffic t LEFT JOIN patna_obstanovka.station_bearing b USING (scp)

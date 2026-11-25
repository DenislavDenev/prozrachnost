SELECT row_no::text, row_no, crash_at, lat, lon, crash_type, killed, injured
FROM patna_obstanovka.crash WHERE superseded_at IS NULL

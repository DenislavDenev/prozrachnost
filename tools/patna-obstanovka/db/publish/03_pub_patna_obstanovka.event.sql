-- closed roads, then closed lanes, then incidents, each in the order of its file
SELECT (row_number() OVER (ORDER BY array_position(ARRAY['lima_r01', 'lima_r02', 'lima_d01'], e.source), e.row_no))::int,
       e.record_id, e.kind, e.title, e.detail, e.lat, e.lon, e.valid_from, e.valid_until, e.xml_url
FROM patna_obstanovka.event e

SELECT c.row_no::text, m.section_id, m.distance_m
FROM patna_obstanovka.crash_match m
JOIN ops.load l ON l.load_id = m.load_id AND l.tool = 'patna-obstanovka' AND l.source = 'matches' AND l.is_current
JOIN patna_obstanovka.crash c ON c.crash_key = m.crash_key AND c.superseded_at IS NULL
JOIN patna_obstanovka.risk_section r ON r.section_id = m.section_id AND r.superseded_at IS NULL

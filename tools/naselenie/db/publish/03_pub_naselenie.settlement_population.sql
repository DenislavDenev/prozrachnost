SELECT sp.as_of, sp.row_no, sp.municipality_code, g.municipality, sp.source_name, sp.ekatte, sp.permanent, sp.current, sp.both_here
FROM naselenie.settlement_population sp
JOIN naselenie.snapshot s ON s.as_of = sp.as_of AND s.table_kind = 'detailed'
JOIN naselenie_src.grao_row g ON g.load_id = s.load_id AND g.row_kind = 'settlement' AND g.row_no = sp.row_no
WHERE sp.superseded_at IS NULL

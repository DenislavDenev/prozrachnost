SELECT p.as_of, p.table_kind, p.municipality_code, p.permanent, p.current, p.both_here
FROM naselenie.population p
WHERE p.superseded_at IS NULL

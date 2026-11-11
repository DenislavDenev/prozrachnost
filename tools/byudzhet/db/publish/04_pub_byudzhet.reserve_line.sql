SELECT as_of, row_no, line, original, source_unit, currency, amount, 'reserve'
FROM byudzhet.reserve_line WHERE superseded_at IS NULL

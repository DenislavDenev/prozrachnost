SELECT period_end, row_no, line, law_original, actual_original, source_unit, currency, law_amount, actual_amount, pct, 'state'
FROM byudzhet.state_line WHERE superseded_at IS NULL

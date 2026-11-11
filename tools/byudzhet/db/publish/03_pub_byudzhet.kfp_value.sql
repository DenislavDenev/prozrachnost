SELECT period_end, row_no, col_no, budget_type, item, original, source_unit, currency, amount, 'kfp'
FROM byudzhet.kfp_value WHERE superseded_at IS NULL

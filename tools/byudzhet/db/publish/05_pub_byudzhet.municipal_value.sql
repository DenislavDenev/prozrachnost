SELECT as_of, kind, municipality_code, measure, original, source_unit, currency, amount
FROM byudzhet.municipal_value WHERE superseded_at IS NULL

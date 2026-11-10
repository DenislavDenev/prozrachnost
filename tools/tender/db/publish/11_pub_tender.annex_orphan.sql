SELECT row_number() OVER (ORDER BY t)::int, unp, contract_number, lot_no, published_at, source_day, last_value, current_value, currency, reason FROM tender.annex_orphan t

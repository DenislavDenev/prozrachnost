SELECT row_number() OVER (ORDER BY t)::int, contract_id, unp, lot_no FROM tender.contract_lot t

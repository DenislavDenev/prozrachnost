SELECT contract_id, party_key, eik, name, percent, amount, row_number() OVER (PARTITION BY contract_id ORDER BY t)::int FROM tender.subcontract t

SELECT p.product_id, p.chain_id, c.eik, p.code, p.name, p.category, p.first_day FROM pazar.product p JOIN pazar.chain c USING (chain_id)

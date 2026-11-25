SELECT s.store_id, s.chain_id, s.place_raw, s.ekatte, s.name, s.first_day, s.settlement_ekatte, p.municipality_id, p.nuts3
FROM pazar.store s JOIN pazar.store_place p USING (store_id)

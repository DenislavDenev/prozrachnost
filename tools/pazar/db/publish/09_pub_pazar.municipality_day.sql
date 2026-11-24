-- window: day >= %(since)s::date
SELECT day, municipality_id, n_stores, n_chains FROM pazar.municipality_day

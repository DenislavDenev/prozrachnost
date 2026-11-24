-- window: day >= %(since)s::date
SELECT day, chain_eik, n_promos, n_real, n_none, n_raised, n_nohistory, avg_declared, avg_real, window_days FROM pazar.promo_day

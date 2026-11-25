-- window: day >= %(since)s::date
SELECT day, scope, key, category, n_prices, n_stores, n_chains, min_eur, median_eur, max_eur, promo_prices, promo_share,
       median_effective_eur, matched, changed, sum_ln
FROM pazar.category_day

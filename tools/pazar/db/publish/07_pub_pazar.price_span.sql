-- window: from_day >= date_trunc('month', %(since)s::date)::date AND coalesce(to_day, 'infinity') >= %(since)s::date
-- a day changes the spans of its month that were open the day before (closed or still open) and adds its own
SELECT store_id, product_id, from_day,
       (row_number() OVER (PARTITION BY store_id, product_id, from_day ORDER BY retail, promo NULLS FIRST, category NULLS FIRST, flags))::smallint AS seq,
       to_day, retail, promo, category, flags
FROM pazar.price_span

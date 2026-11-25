-- The plan of the daily observations (db/build/20_category_day.sql). Measured on 25.11.2026 on 2025-11-25 (1.4 million prices):
-- about 300 s a day before, 37 s after.
-- 1. The earlier price of the same shop and product (the price index, the lowest price of the 30 days before) is found by
--    an index on the shop, the product and the first day; before, the join went through (product_id, from_day) and read
--    every shop of the product.
-- 2. The condition (flags & 29) = 0 keeps almost every row, but without statistics the planner guesses 0,5 % of them and
--    chooses nested loops for millions of rows. Statistics on the expression, per month partition (statistics on the
--    partitioned table are not used when a single partition is read).
CREATE INDEX price_span_store_product_day ON pazar.price_span (store_id, product_id, from_day);

CREATE OR REPLACE FUNCTION pazar.ensure_month(d date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE s date := date_trunc('month', d)::date;
        m text := to_char(date_trunc('month', d), 'YYYYMM');
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS pazar.price_span_%s PARTITION OF pazar.price_span FOR VALUES FROM (%L) TO (%L)',
                 m, s, (s + interval '1 month')::date);
  EXECUTE format('CREATE STATISTICS IF NOT EXISTS pazar.price_span_%s_flags ON ((flags & 29)) FROM pazar.price_span_%s', m, m);
  EXECUTE format('CREATE TABLE IF NOT EXISTS pazar.category_day_%s PARTITION OF pazar.category_day FOR VALUES FROM (%L) TO (%L)',
                 m, s, (s + interval '1 month')::date);
END $$;
COMMENT ON FUNCTION pazar.ensure_month(date) IS 'Дяловете на месеца на деня в pazar.price_span и pazar.category_day и статистиката на дяла на интервалите за условието (flags & 29) = 0.';

DO $$
DECLARE p record;
BEGIN
  FOR p IN SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
           WHERE i.inhparent = 'pazar.price_span'::regclass LOOP
    EXECUTE format('CREATE STATISTICS IF NOT EXISTS pazar.%s_flags ON ((flags & 29)) FROM pazar.%I', p.relname, p.relname);
    EXECUTE format('ANALYZE pazar.%I', p.relname);
  END LOOP;
END $$;

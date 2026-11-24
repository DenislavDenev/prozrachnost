-- Promotions against the shop's own earlier prices: per day and chain, how many promotional prices are below the lowest
-- retail price of the same shop and product in the 30 days before (a real discount) and how many are not.
CREATE TABLE gold.promo_day (
  day date NOT NULL, chain_eik text NOT NULL,
  n_promos integer NOT NULL, n_real integer NOT NULL, n_none integer NOT NULL, n_raised integer NOT NULL, n_nohistory integer NOT NULL,
  avg_declared numeric(7,5), avg_real numeric(7,5), window_days integer NOT NULL,
  PRIMARY KEY (day, chain_eik));
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pazar_web') THEN
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA gold TO pazar_web';
  END IF;
END $$;

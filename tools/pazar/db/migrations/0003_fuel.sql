-- Fuel: the weekly Oil Bulletin of the European Commission as submitted (EUR per 1000 litres, with and without taxes).
CREATE TABLE silver.fuel_week (
  week date NOT NULL,                    -- the Monday of the price week
  geo text NOT NULL,                     -- BG, EU (average of the Union), EUR (euro area)
  fuel text NOT NULL,                    -- euro95, diesel, LPG
  with_tax numeric(12,4), wo_tax numeric(12,4),
  unit text NOT NULL DEFAULT 'EUR/1000 l',
  source_sha256 text NOT NULL,           -- the XLSX this value was last read from (ops.raw_file)
  PRIMARY KEY (week, geo, fuel));
CREATE TABLE silver.fuel_read (id bigserial PRIMARY KEY, read_at timestamptz NOT NULL, sha256 text NOT NULL, weeks integer NOT NULL, newest date NOT NULL);
-- gold: price per litre, one row per week, country and fuel
CREATE VIEW gold.fuel_week AS
SELECT week, geo, fuel, round(with_tax / 1000, 4) AS with_tax_eur_l, round(wo_tax / 1000, 4) AS wo_tax_eur_l, source_sha256
FROM silver.fuel_week;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pazar_web') THEN
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA silver, gold TO pazar_web';
  END IF;
END $$;

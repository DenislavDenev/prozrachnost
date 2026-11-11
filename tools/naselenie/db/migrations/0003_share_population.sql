-- Население gives the registrations to the other tools: any web role of the project (group pz_web) may read the
-- serving layer of Население. Бюджет divides by them ("на регистрирано лице") instead of reading ГРАО a second time.
-- Only the serving layer is shared; the clean tables and the landing zone stay with this tool.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_web') THEN
    GRANT USAGE ON SCHEMA pub_naselenie TO pz_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_naselenie TO pz_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_naselenie GRANT SELECT ON TABLES TO pz_web;
  END IF;
END $$;

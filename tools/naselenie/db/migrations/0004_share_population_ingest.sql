-- The serving layer of Население is readable by the ingest roles too (group pz_ingest), not only by the web roles: a
-- tool that compares what its pages show (Бюджет, step parity) reads it as the pages do. Read only; the clean tables
-- and the landing zone stay with this tool.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    GRANT USAGE ON SCHEMA pub_naselenie TO pz_ingest;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_naselenie TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_naselenie GRANT SELECT ON TABLES TO pz_ingest;
  END IF;
END $$;

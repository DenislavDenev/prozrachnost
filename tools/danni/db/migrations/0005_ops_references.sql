-- A tool's landing and clean tables point to ops.load and ops.run with foreign keys, and a foreign key needs
-- the REFERENCES privilege on the table it points to. 0003 gave it on core only.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    GRANT REFERENCES ON ALL TABLES IN SCHEMA ops TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT REFERENCES ON TABLES TO pz_ingest;
  END IF;
END $$;

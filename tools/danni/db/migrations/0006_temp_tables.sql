-- 0003 took every privilege on the database from PUBLIC, and with it the right to make temporary tables.
-- A load stages its rows in a temporary table before it compares them with the stored ones.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO pz_ingest', current_database());
  END IF;
END $$;

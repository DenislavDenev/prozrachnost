-- The schema the public API reads, the licence guard, and the shared privileges.
DO $$ BEGIN IF to_regnamespace('api_v1') IS NULL THEN CREATE SCHEMA api_v1; END IF; END $$;
COMMENT ON SCHEMA api_v1 IS 'Изгледите за публичния API. Само набори с право на разпространение.';

INSERT INTO core.dataset (dataset, tool, title_bg, description_bg, source_org, channel, licence, licence_url,
                          licence_checked_on, is_distributable)
VALUES ('danni.catalog', 'danni', 'Каталог на наборите', 'Описанието на наборите и показателите на Прозрачност.',
        'Прозрачност', 'derived', 'CC BY 4.0', 'https://creativecommons.org/licenses/by/4.0/', '2026-11-10', true);
INSERT INTO core.dataset_table (schema_name, table_name, dataset) VALUES
  ('core', 'dataset', 'danni.catalog'), ('core', 'indicator', 'danni.catalog'),
  ('core', 'unit', 'danni.catalog'), ('core', 'period_kind', 'danni.catalog');

CREATE VIEW api_v1.dataset AS
SELECT dataset, tool, title_bg, description_bg, source_org, source_url, channel, licence, licence_url,
       licence_checked_on, is_distributable, personal_data, cadence
FROM core.dataset;
COMMENT ON VIEW api_v1.dataset IS 'Всички набори, с лиценза им. Данните на набор без право на разпространение не са в API-то.';

CREATE VIEW api_v1.indicator AS
SELECT i.indicator_code, i.dataset, i.title_bg, i.unit_code, u.name_bg AS unit_bg, i.kind, i.definition_bg, i.official_code
FROM core.indicator i JOIN core.unit u USING (unit_code);
COMMENT ON VIEW api_v1.indicator IS 'Регистърът на показателите.';

-- Every table an api view stands on, through any number of views, must belong to a dataset that may be
-- distributed and holds no names of private persons. Anything else in the api schema that is not a view is listed too.
CREATE VIEW core.api_licence_violation AS
WITH RECURSIVE api AS (
  SELECT c.oid, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname ~ '^api_v[0-9]+$' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
), dep AS (
  SELECT a.oid AS api_rel, d.refobjid AS rel
  FROM api a
  JOIN pg_rewrite r ON r.ev_class = a.oid
  JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid
                  AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> a.oid
  UNION
  SELECT dep.api_rel, d.refobjid
  FROM dep
  JOIN pg_class c ON c.oid = dep.rel AND c.relkind IN ('v', 'm')
  JOIN pg_rewrite r ON r.ev_class = c.oid
  JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid
                  AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> c.oid
)
SELECT dep.api_rel::regclass::text AS api_view, n.nspname AS schema_name, c.relname AS table_name, t.dataset,
       CASE WHEN t.dataset IS NULL THEN 'таблицата не е записана към набор'
            WHEN NOT ds.is_distributable THEN 'наборът няма право на разпространение'
            ELSE 'наборът съдържа имена на физически лица' END AS reason
FROM dep
JOIN pg_class c ON c.oid = dep.rel AND c.relkind IN ('r', 'p', 'f')
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN core.dataset_table t ON t.schema_name = n.nspname AND t.table_name = c.relname
LEFT JOIN core.dataset ds ON ds.dataset = t.dataset
WHERE t.dataset IS NULL OR NOT ds.is_distributable OR ds.personal_data = 'names'
UNION ALL
SELECT a.oid::regclass::text, NULL, NULL, NULL, 'в схемата за API-то има таблица, а не изглед'
FROM api a WHERE a.relkind NOT IN ('v', 'm');
COMMENT ON VIEW core.api_licence_violation IS 'Изгледи за API-то, които стъпват на набор без право на разпространение. Трябва да е празен.';

INSERT INTO ops.check_rule (tool, name, title_bg, kind, sql, tolerance, gating) VALUES
  ('danni', 'api_licence', 'Изгледите за API-то стъпват само на набори с право на разпространение', 'licence',
   'SELECT 0, count(*) FROM core.api_licence_violation', 0, true);

-- Privileges. The group roles are created by the provision script (roles are not part of a database);
-- where a role is missing (a bare test cluster) its grants are skipped.
DO $$
DECLARE db text := current_database();
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', db);
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pz_ingest', db);
    GRANT USAGE ON SCHEMA core, ops TO pz_ingest;
    GRANT SELECT, REFERENCES ON ALL TABLES IN SCHEMA core TO pz_ingest;
    GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO pz_ingest;
    GRANT DELETE ON ops.hold TO pz_ingest;
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops TO pz_ingest;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core TO pz_ingest;
    -- a tool describes its own datasets, but never decides that one may be distributed
    GRANT INSERT (dataset, tool, title_bg, description_bg, source_org, source_url, channel, licence, licence_url, personal_data, cadence),
          UPDATE (tool, title_bg, description_bg, source_org, source_url, channel, licence, licence_url, personal_data, cadence)
      ON core.dataset TO pz_ingest;
    GRANT INSERT, UPDATE, DELETE ON core.dataset_table TO pz_ingest;
    GRANT INSERT, UPDATE ON core.indicator, core.unit TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, REFERENCES ON TABLES TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT, INSERT, UPDATE ON TABLES TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT USAGE ON SEQUENCES TO pz_ingest;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_web') THEN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pz_web', db);
    GRANT USAGE ON SCHEMA core, ops TO pz_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA core, ops TO pz_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA core, ops GRANT SELECT ON TABLES TO pz_web;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_ro') THEN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO api_ro', db);
    GRANT USAGE ON SCHEMA api_v1 TO api_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA api_v1 TO api_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA api_v1 GRANT SELECT ON TABLES TO api_ro;
  END IF;
END $$;

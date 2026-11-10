-- Ingest and serving are two parts. Everything so far (raw files, landing zone, clean tables, dimensions, ledger)
-- is the ingest side. The serving side is the schemas pub and pub_<tool>: the finished tables the pages and the
-- API read. It is self-contained: no foreign key and no view leaves it, so it can be exported whole (a dump, a
-- replica, files) and hosted elsewhere. It changes only through a publication: one transaction with its own number.
DO $$ BEGIN IF to_regnamespace('pub') IS NULL THEN CREATE SCHEMA pub; END IF; END $$;
COMMENT ON SCHEMA pub IS 'Слоят за показване, общата част: версиите на публикуването, местата и каталогът на наборите. Сайтовете и API-то четат само от pub и pub_<инструмент>.';

CREATE TABLE pub.publication (
  publication_id bigint GENERATED ALWAYS AS IDENTITY,
  tool           text NOT NULL,
  published_at   timestamptz NOT NULL DEFAULT now(),
  code_sha       text,
  run_id         bigint,
  changes        jsonb NOT NULL,
  CONSTRAINT publication_pk PRIMARY KEY (publication_id)
);
CREATE INDEX publication_tool_idx ON pub.publication (tool, publication_id DESC);
COMMENT ON TABLE pub.publication IS 'Всяко публикуване: една транзакция, която е сменила слоя за показване. Най-големият номер е версията, която четящите виждат.';
COMMENT ON COLUMN pub.publication.publication_id IS 'Номерът на версията. Расте с всяко публикуване на който и да е инструмент; по него се обезсилва кешът пред сайта.';
COMMENT ON COLUMN pub.publication.run_id IS 'Пускането, което е публикувало (номер от дневника на приемането; без външен ключ, защото дневникът не е част от слоя за показване).';
COMMENT ON COLUMN pub.publication.changes IS 'За всяка сменена таблица: колко реда са добавени и колко махнати.';

CREATE TABLE pub.nuts (
  nuts_code   text NOT NULL,
  level       smallint NOT NULL,
  name_bg     text NOT NULL,
  label_bg    text NOT NULL,
  name_en     text,
  parent_code text,
  CONSTRAINT nuts_pk PRIMARY KEY (nuts_code),
  CONSTRAINT nuts_parent_fk FOREIGN KEY (parent_code) REFERENCES pub.nuts (nuts_code) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub.nuts IS 'Статистическите райони по NUTS: държава (0), макрорайон (1), район (2), област (3).';

CREATE TABLE pub.oblast (
  oblast_code text NOT NULL,
  name_bg     text NOT NULL,
  label_bg    text NOT NULL,
  name_en     text,
  nuts3_code  text NOT NULL,
  CONSTRAINT oblast_pk PRIMARY KEY (oblast_code),
  CONSTRAINT oblast_nuts3_fk FOREIGN KEY (nuts3_code) REFERENCES pub.nuts (nuts_code) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub.oblast IS 'Областите с кода им по НСИ и по NUTS 3. label_bg е името за показване.';

CREATE TABLE pub.municipality (
  municipality_code text NOT NULL,
  oblast_code       text NOT NULL,
  name_bg           text NOT NULL,
  name_en           text,
  eik               text NOT NULL,
  drawn_as_eik      text NOT NULL,
  center_ekatte     text,
  is_gone           boolean NOT NULL,
  CONSTRAINT municipality_pk PRIMARY KEY (municipality_code),
  CONSTRAINT municipality_eik_uq UNIQUE (eik),
  CONSTRAINT municipality_oblast_fk FOREIGN KEY (oblast_code) REFERENCES pub.oblast (oblast_code) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub.municipality IS 'Общините: код по НСИ, ЕИК на администрацията (с него страниците адресират общината) и областта.';
COMMENT ON COLUMN pub.municipality.is_gone IS 'Общината вече не е в ЕКАТТЕ. Остава, защото стари числа сочат към нея.';

CREATE TABLE pub.settlement (
  ekatte            text NOT NULL,
  kind_prefix       text NOT NULL,
  name_bg           text NOT NULL,
  name_en           text,
  municipality_code text NOT NULL,
  is_gone           boolean NOT NULL,
  CONSTRAINT settlement_pk PRIMARY KEY (ekatte),
  CONSTRAINT settlement_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX settlement_municipality_idx ON pub.settlement (municipality_code);
COMMENT ON TABLE pub.settlement IS 'Населените места по ЕКАТТЕ.';

CREATE TABLE pub.dataset (
  dataset            text NOT NULL,
  tool               text NOT NULL,
  title_bg           text NOT NULL,
  description_bg     text,
  source_org         text NOT NULL,
  source_url         text,
  channel            text NOT NULL,
  licence            text NOT NULL,
  licence_url        text,
  licence_checked_on date,
  is_displayable     boolean NOT NULL,
  is_distributable   boolean NOT NULL,
  personal_data      text NOT NULL,
  cadence            text,
  CONSTRAINT dataset_pk PRIMARY KEY (dataset)
);
COMMENT ON TABLE pub.dataset IS 'Каталогът на наборите: източник, канал, лиценз, и дали наборът се показва на страниците и дали се раздава през API-то.';

CREATE TABLE pub.indicator (
  indicator_code text NOT NULL,
  dataset        text NOT NULL,
  title_bg       text NOT NULL,
  unit_code      text NOT NULL,
  unit_bg        text NOT NULL,
  kind           text NOT NULL,
  definition_bg  text,
  official_code  text,
  CONSTRAINT indicator_pk PRIMARY KEY (indicator_code),
  CONSTRAINT indicator_dataset_fk FOREIGN KEY (dataset) REFERENCES pub.dataset (dataset) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub.indicator IS 'Регистърът на показателите с единицата им.';

-- Two different rights. Showing a number on our page with a link to its source is one; handing the dataset out
-- through the API and as files is a stricter one. Both are off until the licence list of danni says otherwise.
ALTER TABLE core.dataset ADD COLUMN is_displayable boolean NOT NULL DEFAULT false;
UPDATE core.dataset SET is_displayable = true WHERE is_distributable;
ALTER TABLE core.dataset ADD CONSTRAINT dataset_displayable_ck CHECK (NOT is_distributable OR is_displayable);
COMMENT ON COLUMN core.dataset.is_displayable IS 'Наборът може да е в слоя за показване (на страниците, с връзка към източника). Сменя се само от списъка с лицензите на danni.';
COMMENT ON COLUMN core.dataset.is_distributable IS 'Наборът може да се раздава през API-то и като файлове. По-строго от is_displayable; сменя се само от списъка с лицензите на danni.';

INSERT INTO core.dataset_table (schema_name, table_name, dataset) VALUES
  ('pub', 'publication', 'danni.catalog'), ('pub', 'dataset', 'danni.catalog'), ('pub', 'indicator', 'danni.catalog');

-- The api views stand on the serving layer, like everything a reader sees.
DROP VIEW api_v1.indicator;
DROP VIEW api_v1.dataset;
CREATE VIEW api_v1.dataset AS
SELECT dataset, tool, title_bg, description_bg, source_org, source_url, channel, licence, licence_url,
       licence_checked_on, is_displayable, is_distributable, personal_data, cadence
FROM pub.dataset;
COMMENT ON VIEW api_v1.dataset IS 'Всички набори, с лиценза им. Данните на набор без право на разпространение не са в API-то.';
CREATE VIEW api_v1.indicator AS
SELECT indicator_code, dataset, title_bg, unit_code, unit_bg, kind, definition_bg, official_code FROM pub.indicator;
COMMENT ON VIEW api_v1.indicator IS 'Регистърът на показателите.';
CREATE VIEW api_v1.publication AS
SELECT publication_id, tool, published_at, changes FROM pub.publication;
COMMENT ON VIEW api_v1.publication IS 'Версиите на публикуването. Най-големият номер е действащата версия.';

-- What must never be true of the serving layer. Empty when all is well; a publication is refused otherwise.
CREATE VIEW core.serving_violation AS
WITH serving AS (
  SELECT c.oid, c.relkind, n.nspname, c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE (n.nspname = 'pub' OR n.nspname LIKE 'pub\_%' OR n.nspname ~ '^api_v[0-9]+$') AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
)
SELECT s.nspname || '.' || s.relname AS relation,
       CASE WHEN t.dataset IS NULL THEN 'таблицата не е записана към набор'
            WHEN d.personal_data = 'names' THEN 'наборът съдържа имена на физически лица'
            ELSE 'наборът няма право да се показва' END AS reason
FROM serving s
LEFT JOIN core.dataset_table t ON t.schema_name = s.nspname AND t.table_name = s.relname
LEFT JOIN core.dataset d ON d.dataset = t.dataset
WHERE s.relkind IN ('r', 'p', 'f') AND s.nspname !~ '^api_v'
  AND (t.dataset IS NULL OR NOT d.is_displayable OR d.personal_data = 'names')
UNION ALL
SELECT s.nspname || '.' || s.relname, 'външен ключ извън слоя за показване: ' || k.confrelid::regclass::text
FROM serving s JOIN pg_constraint k ON k.conrelid = s.oid AND k.contype = 'f'
WHERE k.confrelid NOT IN (SELECT oid FROM serving)
UNION ALL
SELECT DISTINCT s.nspname || '.' || s.relname, 'изгледът чете извън слоя за показване: ' || d.refobjid::regclass::text
FROM serving s
JOIN pg_rewrite r ON r.ev_class = s.oid
JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = r.oid AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> s.oid
WHERE s.relkind IN ('v', 'm') AND d.refobjid NOT IN (SELECT oid FROM serving);
COMMENT ON VIEW core.serving_violation IS 'Нарушения на слоя за показване: таблица без набор с право на показване, външен ключ или изглед, който излиза извън слоя. Трябва да е празен.';

ALTER TABLE ops.check_rule DROP CONSTRAINT check_rule_kind_ck;
ALTER TABLE ops.check_rule ADD CONSTRAINT check_rule_kind_ck
  CHECK (kind IN ('reconcile', 'unique', 'not_null', 'fk', 'unmatched', 'drop', 'parity', 'parser', 'licence', 'serving', 'publish'));
COMMENT ON COLUMN ops.check_rule.kind IS 'Видът на правилото. publish: изпълнява се вътре в транзакцията на публикуването, срещу новото съдържание, и при неуспех тя се отменя.';
INSERT INTO ops.check_rule (tool, name, title_bg, kind, sql, tolerance, gating) VALUES
  ('danni', 'serving_isolated', 'Слоят за показване не стига до нищо извън себе си и държи само набори с право на показване', 'serving',
   'SELECT 0, count(*) FROM core.serving_violation', 0, true);

-- The web roles read the serving layer and nothing else; until now they could read core and the ledger.
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_web') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA core, ops, danni_src FROM pz_web;
    REVOKE ALL ON SCHEMA core, ops, danni_src FROM pz_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA core, ops REVOKE SELECT ON TABLES FROM pz_web;
    GRANT USAGE ON SCHEMA pub TO pz_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub TO pz_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub GRANT SELECT ON TABLES TO pz_web;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pz_ingest') THEN
    GRANT USAGE ON SCHEMA pub TO pz_ingest;
    GRANT SELECT, REFERENCES ON ALL TABLES IN SCHEMA pub TO pz_ingest;
    GRANT INSERT ON pub.publication TO pz_ingest;
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA pub TO pz_ingest;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub GRANT SELECT, REFERENCES ON TABLES TO pz_ingest;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_ro') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA api_v1 TO api_ro;
  END IF;
END $$;

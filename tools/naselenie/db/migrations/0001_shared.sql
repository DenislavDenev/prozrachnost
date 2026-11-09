-- Население in the shared database: the landing zone naselenie_src and the clean tables naselenie.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the places are core.*.
-- The schema of the old database of its own is kept in db/legacy for reference.
DO $$ BEGIN
  IF to_regnamespace('naselenie_src') IS NULL THEN CREATE SCHEMA naselenie_src; END IF;
  IF to_regnamespace('naselenie') IS NULL THEN CREATE SCHEMA naselenie; END IF;
END $$;
COMMENT ON SCHEMA naselenie_src IS 'Население, приемна зона: таблиците на ГРАО, ред по ред, както са публикувани.';
COMMENT ON SCHEMA naselenie IS 'Население: адресните регистрации по община и по населено място, с ключове към core.';

CREATE TABLE naselenie_src.grao_catalog (
  load_id bigint NOT NULL,
  url     text NOT NULL,
  CONSTRAINT grao_catalog_pk PRIMARY KEY (load_id, url),
  CONSTRAINT grao_catalog_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE naselenie_src.grao_catalog IS 'Връзките към таблиците в каталога на ГРАО (tables.html).';

CREATE TABLE naselenie_src.grao_file (
  load_id    bigint NOT NULL,
  as_of      date NOT NULL,
  table_kind text NOT NULL,
  CONSTRAINT grao_file_pk PRIMARY KEY (load_id),
  CONSTRAINT grao_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT grao_file_kind_ck CHECK (table_kind IN ('monthly', 'detailed'))
);
COMMENT ON TABLE naselenie_src.grao_file IS 'Една публикувана таблица на ГРАО: датата ѝ и видът (месечна по общини или подробна по населени места).';

CREATE TABLE naselenie_src.grao_row (
  load_id      bigint NOT NULL,
  row_kind     text NOT NULL,
  row_no       integer NOT NULL,
  oblast       text NOT NULL,
  municipality text,
  name         text,
  permanent    integer,
  current      integer,
  both_here    integer,
  CONSTRAINT grao_row_pk PRIMARY KEY (load_id, row_kind, row_no),
  CONSTRAINT grao_row_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT grao_row_kind_ck CHECK (row_kind IN ('municipality', 'settlement', 'subdivision')),
  CONSTRAINT grao_row_numbers_ck CHECK (row_kind = 'subdivision' OR (permanent IS NOT NULL AND current IS NOT NULL AND both_here IS NOT NULL))
);
COMMENT ON TABLE naselenie_src.grao_row IS 'Редовете на таблицата с имената, както са в ГРАО: общински сбор, населено място или подразделение (район, кметство).';
COMMENT ON COLUMN naselenie_src.grao_row.row_no IS 'Поредният номер на реда от този вид във файла.';
COMMENT ON COLUMN naselenie_src.grao_row.both_here IS 'Лица с постоянен и настоящ адрес в същото място.';

CREATE TABLE naselenie.population (
  population_id     bigint GENERATED ALWAYS AS IDENTITY,
  municipality_code text NOT NULL,
  as_of             date NOT NULL,
  table_kind        text NOT NULL,
  permanent         integer NOT NULL,
  current           integer NOT NULL,
  both_here         integer NOT NULL,
  load_id           bigint NOT NULL,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  superseded_at     timestamptz,
  CONSTRAINT population_pk PRIMARY KEY (population_id),
  CONSTRAINT population_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT population_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT population_kind_ck CHECK (table_kind IN ('monthly', 'detailed')),
  CONSTRAINT population_numbers_ck CHECK (permanent >= 0 AND current >= 0 AND both_here >= 0 AND both_here <= LEAST(permanent, current))
);
CREATE UNIQUE INDEX population_current_uq ON naselenie.population (as_of, table_kind, municipality_code) WHERE superseded_at IS NULL;
CREATE INDEX population_municipality_idx ON naselenie.population (municipality_code, as_of) WHERE superseded_at IS NULL;
CREATE INDEX population_load_idx ON naselenie.population (load_id);
COMMENT ON TABLE naselenie.population IS 'Адресни регистрации по община към дата, от месечната или от подробната таблица на ГРАО. Двете се пазят отделно.';
COMMENT ON COLUMN naselenie.population.as_of IS 'Датата, към която е таблицата на ГРАО.';
COMMENT ON COLUMN naselenie.population.table_kind IS 'monthly: месечната таблица по общини; detailed: сборът на общината в подробната таблица по населени места.';
COMMENT ON COLUMN naselenie.population.permanent IS 'Регистрирани по постоянен адрес.';
COMMENT ON COLUMN naselenie.population.current IS 'Регистрирани по настоящ адрес.';
COMMENT ON COLUMN naselenie.population.both_here IS 'С постоянен и настоящ адрес в същото място.';
COMMENT ON COLUMN naselenie.population.superseded_at IS 'Кога ГРАО е публикувала друга стойност за същата община и дата. NULL: действащият ред.';

CREATE TABLE naselenie.settlement_population (
  settlement_population_id bigint GENERATED ALWAYS AS IDENTITY,
  municipality_code text NOT NULL,
  as_of             date NOT NULL,
  source_name       text NOT NULL,
  name_no           smallint NOT NULL,
  row_no            integer NOT NULL,
  ekatte            text,
  permanent         integer NOT NULL,
  current           integer NOT NULL,
  both_here         integer NOT NULL,
  load_id           bigint NOT NULL,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  superseded_at     timestamptz,
  CONSTRAINT settlement_population_pk PRIMARY KEY (settlement_population_id),
  CONSTRAINT settlement_population_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT settlement_population_ekatte_fk FOREIGN KEY (ekatte) REFERENCES core.settlement (ekatte),
  CONSTRAINT settlement_population_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT settlement_population_numbers_ck CHECK (permanent >= 0 AND current >= 0 AND both_here >= 0 AND both_here <= LEAST(permanent, current))
);
CREATE UNIQUE INDEX settlement_population_current_uq ON naselenie.settlement_population (as_of, municipality_code, source_name, name_no)
  WHERE superseded_at IS NULL;
CREATE INDEX settlement_population_ekatte_idx ON naselenie.settlement_population (ekatte, as_of) WHERE superseded_at IS NULL;
CREATE INDEX settlement_population_load_idx ON naselenie.settlement_population (load_id);
COMMENT ON TABLE naselenie.settlement_population IS 'Адресни регистрации по ред на подробната таблица на ГРАО: населено място или квартал, с името от източника.';
COMMENT ON COLUMN naselenie.settlement_population.source_name IS 'Името на реда, както е в ГРАО (ГР.БАНСКО). Кварталите не са отделни населени места.';
COMMENT ON COLUMN naselenie.settlement_population.name_no IS 'Пореден номер сред редовете със същото име в общината; 1, освен при две места с едно име.';
COMMENT ON COLUMN naselenie.settlement_population.row_no IS 'Поредният номер на реда във файла, за подредба както в източника.';
COMMENT ON COLUMN naselenie.settlement_population.ekatte IS 'ЕКАТТЕ само при точно едно съвпадение по община, вид и име. Иначе NULL: не се предполага.';

-- the published tables: one row for each table of ГРАО that is current
CREATE VIEW naselenie.snapshot AS
SELECT DISTINCT ON (f.as_of, f.table_kind) f.as_of, f.table_kind, l.ref AS url, f.load_id, l.loaded_at
FROM naselenie_src.grao_file f JOIN ops.load l USING (load_id)
WHERE l.is_current
ORDER BY f.as_of, f.table_kind, l.loaded_at DESC, l.load_id DESC;
COMMENT ON VIEW naselenie.snapshot IS 'Действащите публикувани таблици на ГРАО: дата, вид и адрес. Ако за една дата и вид има два адреса, важи последно прочетеният.';

-- the last read of every address, in the words the sources page uses
CREATE VIEW naselenie.source_state AS
SELECT DISTINCT ON (l.source, l.ref) l.ref AS url, l.loaded_at AS last_read,
       (SELECT max(o.loaded_at) FROM ops.load o WHERE o.tool = l.tool AND o.source = l.source AND o.ref = l.ref
          AND o.status IN ('loaded', 'confirmed', 'unchanged')) AS last_ok,
       CASE WHEN l.status IN ('loaded', 'confirmed', 'unchanged') THEN 'ok' WHEN l.status = 'held' THEN 'held' ELSE 'invalid' END AS status,
       l.reason AS error, l.rows
FROM ops.load l
WHERE l.tool = 'naselenie'
ORDER BY l.source, l.ref, l.loaded_at DESC, l.load_id DESC;
COMMENT ON VIEW naselenie.source_state IS 'Последното четене на всеки адрес на ГРАО: кога, с какъв резултат и колко реда.';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'naselenie_web') THEN
    GRANT USAGE ON SCHEMA naselenie, naselenie_src TO naselenie_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA naselenie, naselenie_src TO naselenie_web;
  END IF;
END $$;

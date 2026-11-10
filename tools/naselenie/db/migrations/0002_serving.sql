-- The serving layer of Население: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()), and it points only to the
-- shared serving tables pub.*, so it can be copied out with them and without the rest of the database.
DO $$ BEGIN IF to_regnamespace('pub_naselenie') IS NULL THEN CREATE SCHEMA pub_naselenie; END IF; END $$;
COMMENT ON SCHEMA pub_naselenie IS 'Население, слой за показване: действащите публикувани таблици на ГРАО, готови за страниците.';

CREATE TABLE pub_naselenie.snapshot (
  as_of      date NOT NULL,
  table_kind text NOT NULL,
  url        text NOT NULL,
  CONSTRAINT snapshot_pk PRIMARY KEY (as_of, table_kind),
  CONSTRAINT snapshot_kind_ck CHECK (table_kind IN ('monthly', 'detailed'))
);
COMMENT ON TABLE pub_naselenie.snapshot IS 'Публикуваните таблици на ГРАО: дата, вид (месечна по общини или подробна по населени места) и адресът на източника.';

CREATE TABLE pub_naselenie.population (
  as_of             date NOT NULL,
  table_kind        text NOT NULL,
  municipality_code text NOT NULL,
  permanent         integer NOT NULL,
  current           integer NOT NULL,
  both_here         integer NOT NULL,
  CONSTRAINT population_pk PRIMARY KEY (as_of, table_kind, municipality_code),
  CONSTRAINT population_snapshot_fk FOREIGN KEY (as_of, table_kind) REFERENCES pub_naselenie.snapshot (as_of, table_kind) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT population_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX population_municipality_idx ON pub_naselenie.population (municipality_code, as_of);
COMMENT ON TABLE pub_naselenie.population IS 'Адресни регистрации по община към дата, от месечната или от подробната таблица на ГРАО. Двете се пазят отделно.';
COMMENT ON COLUMN pub_naselenie.population.permanent IS 'Регистрирани по постоянен адрес.';
COMMENT ON COLUMN pub_naselenie.population.current IS 'Регистрирани по настоящ адрес.';
COMMENT ON COLUMN pub_naselenie.population.both_here IS 'С постоянен и настоящ адрес в същото място.';

CREATE TABLE pub_naselenie.settlement_population (
  as_of               date NOT NULL,
  row_no              integer NOT NULL,
  municipality_code   text NOT NULL,
  source_municipality text NOT NULL,
  source_name         text NOT NULL,
  ekatte              text,
  permanent           integer NOT NULL,
  current             integer NOT NULL,
  both_here           integer NOT NULL,
  CONSTRAINT settlement_population_pk PRIMARY KEY (as_of, row_no),
  CONSTRAINT settlement_population_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT settlement_population_ekatte_fk FOREIGN KEY (ekatte) REFERENCES pub.settlement (ekatte) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX settlement_population_municipality_idx ON pub_naselenie.settlement_population (municipality_code, as_of);
COMMENT ON TABLE pub_naselenie.settlement_population IS 'Редовете на подробната таблица на ГРАО: населено място или квартал, с името от източника, в реда на източника.';
COMMENT ON COLUMN pub_naselenie.settlement_population.row_no IS 'Поредният номер на реда във файла на ГРАО.';
COMMENT ON COLUMN pub_naselenie.settlement_population.source_municipality IS 'Името на общината, както го пише ГРАО.';
COMMENT ON COLUMN pub_naselenie.settlement_population.ekatte IS 'ЕКАТТЕ само при еднозначно съвпадение. Иначе NULL: не се предполага.';

CREATE TABLE pub_naselenie.source_state (
  url       text NOT NULL,
  last_read timestamptz,
  last_ok   timestamptz,
  status    text NOT NULL,
  error     text,
  rows      integer,
  CONSTRAINT source_state_pk PRIMARY KEY (url)
);
COMMENT ON TABLE pub_naselenie.source_state IS 'Последното четене на всеки адрес на източник: кога, с какъв резултат и колко реда.';

-- The web role reads the serving layer only. Until now it could read the clean tables and the landing zone.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'naselenie_web') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA naselenie, naselenie_src FROM naselenie_web;
    REVOKE ALL ON SCHEMA naselenie, naselenie_src FROM naselenie_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA naselenie, naselenie_src REVOKE SELECT ON TABLES FROM naselenie_web;
    GRANT USAGE ON SCHEMA pub_naselenie TO naselenie_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_naselenie TO naselenie_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_naselenie GRANT SELECT ON TABLES TO naselenie_web;
  END IF;
END $$;

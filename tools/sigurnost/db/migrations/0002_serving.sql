-- The serving layer of Сигурност: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()), and it points only to the
-- shared serving tables pub.*, so it can be copied out with them and without the rest of the database.
DO $$ BEGIN IF to_regnamespace('pub_sigurnost') IS NULL THEN CREATE SCHEMA pub_sigurnost; END IF; END $$;
COMMENT ON SCHEMA pub_sigurnost IS 'Сигурност, слой за показване: публикуваните таблици на МВР, сверките и състоянието на източниците, готови за страниците.';

CREATE TABLE pub_sigurnost.structure (
  code      text NOT NULL,
  name      text NOT NULL,
  kind      text NOT NULL,
  nuts_code text,
  sort      integer NOT NULL,
  CONSTRAINT structure_pk PRIMARY KEY (code),
  CONSTRAINT structure_nuts_fk FOREIGN KEY (nuts_code) REFERENCES pub.nuts (nuts_code) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sigurnost.structure IS 'Структурите на МВР: областните дирекции (с NUTS 3 на областта), главните дирекции и страната.';

CREATE TABLE pub_sigurnost.indicator (
  code       text NOT NULL,
  title      text NOT NULL,
  unit       text NOT NULL,
  kind       text NOT NULL,
  definition text NOT NULL,
  example    text,
  formula    text,
  sort       integer NOT NULL,
  CONSTRAINT indicator_pk PRIMARY KEY (code)
);
COMMENT ON TABLE pub_sigurnost.indicator IS 'Показателите с определение, условен пример и формула.';

CREATE TABLE pub_sigurnost.source_table (
  year         integer NOT NULL,
  family       text NOT NULL,
  resource_uri text NOT NULL,
  raw_sha256   text NOT NULL,
  set_uri      text NOT NULL,
  template     text NOT NULL,
  n_rows       integer NOT NULL,
  issues       integer NOT NULL,
  note         text,
  CONSTRAINT source_table_pk PRIMARY KEY (year, family)
);
COMMENT ON TABLE pub_sigurnost.source_table IS 'Таблицата на МВР зад всяка публикувана (година, вид).';

CREATE TABLE pub_sigurnost.crime_row (
  year           integer NOT NULL,
  family         text NOT NULL,
  structure_code text NOT NULL,
  row_no         integer NOT NULL,
  code           text NOT NULL,
  text           text NOT NULL,
  level          integer NOT NULL,
  parent_row_no  integer,
  is_total       boolean NOT NULL,
  CONSTRAINT crime_row_pk PRIMARY KEY (year, family, structure_code, row_no),
  CONSTRAINT crime_row_table_fk FOREIGN KEY (year, family) REFERENCES pub_sigurnost.source_table (year, family) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT crime_row_structure_fk FOREIGN KEY (structure_code) REFERENCES pub_sigurnost.structure (code) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sigurnost.crime_row IS 'Ред на публикувана таблица в блока на структурата си.';

CREATE TABLE pub_sigurnost.observation (
  year           integer NOT NULL,
  family         text NOT NULL,
  structure_code text NOT NULL,
  row_no         integer NOT NULL,
  indicator      text NOT NULL,
  period_kind    text NOT NULL,
  period_start   date NOT NULL,
  period_end     date NOT NULL,
  value          numeric,
  value_text     text NOT NULL,
  template       text NOT NULL,
  resource_uri   text NOT NULL,
  raw_sha256     text NOT NULL,
  CONSTRAINT observation_pk PRIMARY KEY (year, family, structure_code, row_no, indicator),
  CONSTRAINT observation_row_fk FOREIGN KEY (year, family, structure_code, row_no) REFERENCES pub_sigurnost.crime_row DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT observation_indicator_fk FOREIGN KEY (indicator) REFERENCES pub_sigurnost.indicator (code) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX observation_ind_idx ON pub_sigurnost.observation (family, indicator, year);
CREATE INDEX observation_struct_idx ON pub_sigurnost.observation (structure_code, year);
COMMENT ON TABLE pub_sigurnost.observation IS 'Една стойност на публикувана таблица; NULL, когато източникът не е отпечатал число, никога нула.';

CREATE TABLE pub_sigurnost.reconciliation (
  year         integer NOT NULL,
  family       text NOT NULL,
  n            integer NOT NULL,
  check_id     text NOT NULL,
  scope        text NOT NULL,
  expected     numeric,
  got          numeric,
  diff         numeric,
  status       text NOT NULL,
  gating       boolean NOT NULL,
  detail       text,
  resource_uri text NOT NULL,
  CONSTRAINT reconciliation_pk PRIMARY KEY (year, family, n)
);
COMMENT ON TABLE pub_sigurnost.reconciliation IS 'Сверките на таблиците: ok, differs или source (разликата е в оригинала).';

CREATE TABLE pub_sigurnost.unmatched (
  year         integer NOT NULL,
  family       text NOT NULL,
  name         text NOT NULL,
  reason       text NOT NULL,
  resource_uri text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (year, family, name, resource_uri)
);
COMMENT ON TABLE pub_sigurnost.unmatched IS 'Имена на структури, които не са в списъка.';

CREATE TABLE pub_sigurnost.dataset (
  set_uri         text NOT NULL,
  title           text NOT NULL,
  kind            text NOT NULL,
  year            integer,
  terms_of_use_id text NOT NULL,
  licence         text NOT NULL,
  shared          boolean NOT NULL,
  licence_checked date NOT NULL,
  source_updated  date,
  listed          integer,
  built           integer NOT NULL,
  invalid         integer NOT NULL,
  empty           integer NOT NULL,
  last_read       timestamptz,
  CONSTRAINT dataset_pk PRIMARY KEY (set_uri)
);
COMMENT ON TABLE pub_sigurnost.dataset IS 'Наборите на МВР с лиценза и броя на прочетените, невалидните и празните им ресурси.';

CREATE TABLE pub_sigurnost.link (
  resource_uri      text NOT NULL,
  set_uri           text NOT NULL,
  name              text NOT NULL,
  url               text NOT NULL,
  period            date,
  source_updated_at timestamptz,
  CONSTRAINT link_pk PRIMARY KEY (resource_uri),
  CONSTRAINT link_dataset_fk FOREIGN KEY (set_uri) REFERENCES pub_sigurnost.dataset (set_uri) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sigurnost.link IS 'Месечните бюлетини на МВР: връзки към PDF на mvr.bg с месеца им.';

CREATE TABLE pub_sigurnost.resource_note (
  resource_uri text NOT NULL,
  set_uri      text NOT NULL,
  set_year     integer,
  family       text,
  n_rows       integer NOT NULL,
  issues       integer NOT NULL,
  title_year   integer,
  CONSTRAINT resource_note_pk PRIMARY KEY (resource_uri)
);
COMMENT ON TABLE pub_sigurnost.resource_note IS 'Действащите таблици с клетки с текст на мястото на число или със заглавие за друга година.';

CREATE TABLE pub_sigurnost.held (
  ref      text NOT NULL,
  reason   text,
  first_at timestamptz NOT NULL,
  CONSTRAINT held_pk PRIMARY KEY (ref)
);
COMMENT ON TABLE pub_sigurnost.held IS 'Задържаните отговори: по-малки от пазените, чакат второ четене.';

CREATE TABLE pub_sigurnost.change (
  change_id   bigint NOT NULL,
  detected_at timestamptz NOT NULL,
  ref         text NOT NULL,
  field       text,
  old         text,
  new         text,
  cause       text NOT NULL,
  CONSTRAINT change_pk PRIMARY KEY (change_id)
);
COMMENT ON TABLE pub_sigurnost.change IS 'Последните 30 промени в дневника на източниците.';

CREATE TABLE pub_sigurnost.state_note (
  kind text NOT NULL,
  n    integer NOT NULL,
  text text NOT NULL,
  CONSTRAINT state_note_pk PRIMARY KEY (kind, n)
);
COMMENT ON TABLE pub_sigurnost.state_note IS 'Проблемите и бележките за свежестта към края на последната стъпка.';

-- the web role reads the serving layer only (enroll.sh gives the same on the server)
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'sigurnost_web') THEN
    GRANT USAGE ON SCHEMA pub_sigurnost TO sigurnost_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_sigurnost TO sigurnost_web;
  END IF;
END $$;

-- The serving layer of Икономика: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()) and points to nothing outside
-- itself, so it can be copied out with pub.* and without the rest of the database.
DO $$ BEGIN IF to_regnamespace('pub_ikonomika') IS NULL THEN CREATE SCHEMA pub_ikonomika; END IF; END $$;
COMMENT ON SCHEMA pub_ikonomika IS 'Икономика, слой за показване: действащите числа на показателите, готови за страниците.';

-- the columns keep the names the pages have always read (indicator, dims, geo, time)
CREATE TABLE pub_ikonomika.series (
  series_id bigint NOT NULL,
  indicator text NOT NULL,
  dims      jsonb NOT NULL,
  geo       text NOT NULL,
  time      text NOT NULL,
  value     numeric,
  flag      text,
  CONSTRAINT series_pk PRIMARY KEY (series_id, time)
);
-- every query of the pages names the indicator, most also the place and the period (in the old database the same
-- index was used, the key over jsonb only by its first column)
CREATE INDEX series_indicator_geo_time_idx ON pub_ikonomika.series (indicator, geo, time);
COMMENT ON TABLE pub_ikonomika.series IS 'Действащото число на всяка серия за всеки период: показател, измерения, място, период, стойност и флаг на източника. NULL е „няма данни“.';
COMMENT ON COLUMN pub_ikonomika.series.series_id IS 'Номерът на серията.';
COMMENT ON COLUMN pub_ikonomika.series.indicator IS 'Показателят: редът в db/ref/indicators.csv или rates, bank (ЕЦБ), mf_forecast (МФ), fx_bgn, fx_eur (БНБ).';
COMMENT ON COLUMN pub_ikonomika.series.dims IS 'Измеренията на източника без мястото и периода; единицата е тук.';
COMMENT ON COLUMN pub_ikonomika.series.geo IS 'Мястото с кода на източника.';
COMMENT ON COLUMN pub_ikonomika.series.time IS 'Периодът, както го пише източникът.';
COMMENT ON COLUMN pub_ikonomika.series.value IS 'Числото в единицата на източника.';
COMMENT ON COLUMN pub_ikonomika.series.flag IS 'Флагът на източника.';

CREATE TABLE pub_ikonomika.dim_label (
  indicator text NOT NULL,
  dim       text NOT NULL,
  code      text NOT NULL,
  label     text NOT NULL,
  CONSTRAINT dim_label_pk PRIMARY KEY (indicator, dim, code)
);
COMMENT ON TABLE pub_ikonomika.dim_label IS 'Имената на категориите, както ги дава източникът на показателя.';

CREATE TABLE pub_ikonomika.source_state (
  source          text NOT NULL,
  ref             text NOT NULL,
  last_read       timestamptz,
  last_ok         timestamptz,
  last_change     timestamptz,
  last_new_period timestamptz,
  status          text,
  error           text,
  rows            integer,
  updated         text,
  label           text,
  CONSTRAINT source_state_pk PRIMARY KEY (source, ref)
);
COMMENT ON TABLE pub_ikonomika.source_state IS 'Последното четене на всеки показател и на курсовете на БНБ: кога, с какъв резултат, колко реда.';

CREATE TABLE pub_ikonomika.hold (
  source   text NOT NULL,
  ref      text NOT NULL,
  rows     integer NOT NULL,
  first_at timestamptz NOT NULL,
  CONSTRAINT hold_pk PRIMARY KEY (source, ref)
);
COMMENT ON TABLE pub_ikonomika.hold IS 'Отговорите, задържани до второ четене: в тях липсват редове, които пазим.';

CREATE TABLE pub_ikonomika.change_count (
  source text NOT NULL,
  cause  text NOT NULL,
  n      integer NOT NULL,
  CONSTRAINT change_count_pk PRIMARY KEY (source, cause)
);
COMMENT ON TABLE pub_ikonomika.change_count IS 'Колко промени е направил всеки източник в последните 30 дни преди публикуването, по причина.';

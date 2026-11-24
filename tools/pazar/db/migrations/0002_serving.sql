-- The serving layer of Пазар: the finished tables its pages read, and nothing else do they read. It is filled only by
-- a publication (db/publish/*.sql through pipeline.publish()). It points only to the shared serving tables pub.*, so
-- it can be copied out with them and without the rest of the database.
-- The spans and the daily observations are large and change only at their end: their publish files have a window
-- (the days since the earliest one that changed), and both tables are in monthly partitions like the clean ones.
DO $$ BEGIN IF to_regnamespace('pub_pazar') IS NULL THEN CREATE SCHEMA pub_pazar; END IF; END $$;
COMMENT ON SCHEMA pub_pazar IS 'Пазар, слой за показване: веригите, обектите, продуктите, цените като интервали и дневните наблюдения, готови за страниците.';

CREATE TABLE pub_pazar.category (
  code             smallint NOT NULL,
  name_bg          text NOT NULL,
  group_bg         text NOT NULL,
  source_url       text NOT NULL,
  group_source_url text NOT NULL,
  CONSTRAINT category_pk PRIMARY KEY (code)
);
COMMENT ON TABLE pub_pazar.category IS 'Категориите на КЗП с групите на портала.';

CREATE TABLE pub_pazar.chain (
  chain_id       integer NOT NULL,
  eik            text NOT NULL,
  name_bg        text NOT NULL,
  eik_valid      boolean NOT NULL,
  first_day      date NOT NULL,
  national_price boolean NOT NULL,
  CONSTRAINT chain_pk PRIMARY KEY (chain_id),
  CONSTRAINT chain_eik_uq UNIQUE (eik)
);
COMMENT ON TABLE pub_pazar.chain IS 'Веригите: ЕИК, име от файла, първи ден, дали е онлайн магазин за цялата страна.';

CREATE TABLE pub_pazar.store (
  store_id        integer NOT NULL,
  chain_id        integer NOT NULL,
  place_raw       text NOT NULL,
  ekatte          text,
  name            text NOT NULL,
  first_day       date NOT NULL,
  settlement      text,
  municipality_id text,
  nuts3           text,
  CONSTRAINT store_pk PRIMARY KEY (store_id),
  CONSTRAINT store_chain_fk FOREIGN KEY (chain_id) REFERENCES pub_pazar.chain DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT store_settlement_fk FOREIGN KEY (settlement) REFERENCES pub.settlement (ekatte) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT store_municipality_fk FOREIGN KEY (municipality_id) REFERENCES pub.municipality (eik) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX store_chain ON pub_pazar.store (chain_id);
COMMENT ON TABLE pub_pazar.store IS 'Обектите: мястото и името, както са подадени, и общината (ЕИК) и областта (NUTS 3), когато мястото е в ЕКАТТЕ.';

CREATE TABLE pub_pazar.product (
  product_id integer NOT NULL,
  chain_id   integer NOT NULL,
  chain_eik  text NOT NULL,
  code       text NOT NULL,
  name       text NOT NULL,
  category   integer,
  first_day  date NOT NULL,
  CONSTRAINT product_pk PRIMARY KEY (product_id),
  CONSTRAINT product_chain_fk FOREIGN KEY (chain_id) REFERENCES pub_pazar.chain DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX product_chain_code ON pub_pazar.product (chain_eik, code);
CREATE INDEX product_category ON pub_pazar.product (category);
COMMENT ON TABLE pub_pazar.product IS 'Продуктите на веригите по техния собствен код, с последното име и категория.';

CREATE TABLE pub_pazar.day (
  day        date NOT NULL,
  status     text NOT NULL,
  files      integer,
  chains     integer,
  stores     integer,
  records    integer,
  valid      integer,
  dups       integer,
  bad        integer,
  blank      integer,
  anomalies  jsonb NOT NULL,
  note       text,
  CONSTRAINT day_pk PRIMARY KEY (day)
);
COMMENT ON TABLE pub_pazar.day IS 'Дните на „Колко струва“: построен, задържан или невалиден, с броя на записите и аномалиите.';

CREATE TABLE pub_pazar.chain_day (
  day       date NOT NULL,
  chain_id  integer NOT NULL,
  file_name text NOT NULL,
  truncated boolean NOT NULL,
  records   integer NOT NULL,
  valid     integer NOT NULL,
  dups      integer NOT NULL,
  bad       integer NOT NULL,
  stores    integer NOT NULL,
  filed     boolean NOT NULL,
  error     text,
  copy_of   text,
  currency  text NOT NULL,
  CONSTRAINT chain_day_pk PRIMARY KEY (day, chain_id),
  CONSTRAINT chain_day_chain_fk FOREIGN KEY (chain_id) REFERENCES pub_pazar.chain DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX chain_day_chain ON pub_pazar.chain_day (chain_id, day);
COMMENT ON TABLE pub_pazar.chain_day IS 'Файлът на всяка верига в деня: подаден ли е, колко реда, копие ли е, в каква валута.';

-- `seq` tells apart two spans of the same shop and product from the same day (two prices filed the same day, both
-- kept and marked); it is their order by content, so the same spans always get the same numbers
CREATE TABLE pub_pazar.price_span (
  store_id   integer NOT NULL,
  product_id integer NOT NULL,
  from_day   date NOT NULL,
  seq        smallint NOT NULL,
  to_day     date,
  retail     integer NOT NULL,
  promo      integer,
  category   integer,
  flags      smallint NOT NULL,
  CONSTRAINT price_span_pk PRIMARY KEY (store_id, product_id, from_day, seq)
) PARTITION BY RANGE (from_day);
CREATE INDEX price_span_product ON pub_pazar.price_span (product_id, from_day);
COMMENT ON TABLE pub_pazar.price_span IS 'Цените като интервали, както са подадени: един ред, докато цената на продукта в обекта е същата. Дял на месец.';
COMMENT ON COLUMN pub_pazar.price_span.seq IS 'Поредният номер на интервала сред тези със същия обект, продукт и начален ден (две цени в един ден).';

CREATE TABLE pub_pazar.category_day (
  day                  date NOT NULL,
  scope                text NOT NULL,
  key                  text NOT NULL,
  category             smallint NOT NULL,
  n_prices             integer NOT NULL,
  n_stores             integer NOT NULL,
  n_chains             integer NOT NULL,
  min_eur              numeric(12,4) NOT NULL,
  median_eur           numeric(12,4) NOT NULL,
  max_eur              numeric(12,4) NOT NULL,
  promo_prices         integer NOT NULL,
  promo_share          numeric(7,6) NOT NULL,
  median_effective_eur numeric(12,4) NOT NULL,
  matched              integer NOT NULL,
  changed              integer NOT NULL,
  sum_ln               numeric(16,8) NOT NULL,
  CONSTRAINT category_day_pk PRIMARY KEY (day, scope, key, category)
) PARTITION BY RANGE (day);
CREATE INDEX category_day_lookup ON pub_pazar.category_day (scope, key, category, day);
COMMENT ON TABLE pub_pazar.category_day IS 'Дневните наблюдения по категория и място, в евро. Дял на месец.';

CREATE TABLE pub_pazar.municipality_day (
  day             date NOT NULL,
  municipality_id text NOT NULL,
  n_stores        integer NOT NULL,
  n_chains        integer NOT NULL,
  CONSTRAINT municipality_day_pk PRIMARY KEY (day, municipality_id),
  CONSTRAINT municipality_day_municipality_fk FOREIGN KEY (municipality_id) REFERENCES pub.municipality (eik) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_pazar.municipality_day IS 'Обекти и вериги с цени в общината в деня.';

CREATE TABLE pub_pazar.promo_day (
  day          date NOT NULL,
  chain_eik    text NOT NULL,
  n_promos     integer NOT NULL,
  n_real       integer NOT NULL,
  n_none       integer NOT NULL,
  n_raised     integer NOT NULL,
  n_nohistory  integer NOT NULL,
  avg_declared numeric(7,5),
  avg_real     numeric(7,5),
  window_days  integer NOT NULL,
  CONSTRAINT promo_day_pk PRIMARY KEY (day, chain_eik),
  CONSTRAINT promo_day_chain_fk FOREIGN KEY (chain_eik) REFERENCES pub_pazar.chain (eik) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_pazar.promo_day IS 'Промоциите на веригата в деня срещу собствените цени на обекта в 30-те дни преди това.';

CREATE TABLE pub_pazar.aggregate_day (
  day           date NOT NULL,
  rows          integer NOT NULL,
  CONSTRAINT aggregate_day_pk PRIMARY KEY (day)
);
COMMENT ON TABLE pub_pazar.aggregate_day IS 'Дните с изградени дневни наблюдения.';

CREATE TABLE pub_pazar.fuel_week (
  week           date NOT NULL,
  geo            text NOT NULL,
  fuel           text NOT NULL,
  with_tax_eur_l numeric(12,4),
  wo_tax_eur_l   numeric(12,4),
  source_sha256  text NOT NULL,
  CONSTRAINT fuel_week_pk PRIMARY KEY (week, geo, fuel)
);
COMMENT ON TABLE pub_pazar.fuel_week IS 'Седмичните цени на горивата от бюлетина на ЕК, в евро за литър, с и без данъци.';

CREATE TABLE pub_pazar.source_state (
  source    text NOT NULL,
  last_read timestamptz,
  last_ok   timestamptz,
  newest    date,
  CONSTRAINT source_state_pk PRIMARY KEY (source)
);
COMMENT ON TABLE pub_pazar.source_state IS 'Последното четене на всеки източник и най-новият му ден.';

CREATE TABLE pub_pazar.hold (
  ref      text NOT NULL,
  first_at timestamptz NOT NULL,
  reason   text,
  CONSTRAINT hold_pk PRIMARY KEY (ref)
);
COMMENT ON TABLE pub_pazar.hold IS 'Задържаните до второ четене отговори: ден или бюлетин, от кога и защо.';

CREATE TABLE pub_pazar.run (
  run_id      bigint NOT NULL,
  step        text NOT NULL,
  started_at  timestamptz NOT NULL,
  finished_at timestamptz,
  status      text NOT NULL,
  CONSTRAINT run_pk PRIMARY KEY (run_id)
);
COMMENT ON TABLE pub_pazar.run IS 'Последните пускания на стъпките на инструмента.';

CREATE TABLE pub_pazar.problem (
  problem text NOT NULL,
  CONSTRAINT problem_pk PRIMARY KEY (problem)
);
COMMENT ON TABLE pub_pazar.problem IS 'Проблемите към последното публикуване: остарели източници, задържания над срока, неуспешни правила.';

-- a month of the two partitioned serving tables, registered to the dataset like every serving table
CREATE FUNCTION pub_pazar.ensure_month(d date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE s date := date_trunc('month', d)::date; ym text := to_char(date_trunc('month', d), 'YYYYMM');
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS pub_pazar.price_span_%s PARTITION OF pub_pazar.price_span FOR VALUES FROM (%L) TO (%L)',
                 ym, s, (s + interval '1 month')::date);
  EXECUTE format('CREATE TABLE IF NOT EXISTS pub_pazar.category_day_%s PARTITION OF pub_pazar.category_day FOR VALUES FROM (%L) TO (%L)',
                 ym, s, (s + interval '1 month')::date);
  INSERT INTO core.dataset_table (schema_name, table_name, dataset)
  VALUES ('pub_pazar', 'price_span_' || ym, 'pazar.kolkostruva'), ('pub_pazar', 'category_day_' || ym, 'pazar.kolkostruva')
  ON CONFLICT (schema_name, table_name) DO UPDATE SET dataset = EXCLUDED.dataset;
END $$;
COMMENT ON FUNCTION pub_pazar.ensure_month(date) IS 'Дяловете на месеца в pub_pazar.price_span и pub_pazar.category_day, записани към набора.';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pazar_web') THEN
    GRANT USAGE ON SCHEMA pub_pazar TO pazar_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_pazar TO pazar_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_pazar GRANT SELECT ON TABLES TO pazar_web;
  END IF;
END $$;

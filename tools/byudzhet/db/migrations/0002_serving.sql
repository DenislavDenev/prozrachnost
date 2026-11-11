-- The serving layer of Бюджет: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()), and it points only to the
-- shared serving tables pub.*, so it can be copied out with them and without the rest of the database.
-- The registrations per municipality (the denominator "per person") are those Население publishes in pub_naselenie.
DO $$ BEGIN IF to_regnamespace('pub_byudzhet') IS NULL THEN CREATE SCHEMA pub_byudzhet; END IF; END $$;
COMMENT ON SCHEMA pub_byudzhet IS 'Бюджет, слой за показване: действащите таблици на МФ, готови за страниците.';

CREATE TABLE pub_byudzhet.report (
  kind           text NOT NULL,
  period_end     date NOT NULL,
  uri            text NOT NULL,
  version        text NOT NULL,
  currency       char(3) NOT NULL,
  reconciliation text NOT NULL,
  CONSTRAINT report_pk PRIMARY KEY (kind, period_end),
  CONSTRAINT report_kind_ck CHECK (kind IN ('state', 'kfp', 'reserve', 'debt', 'indicators'))
);
COMMENT ON TABLE pub_byudzhet.report IS 'Публикуваните таблици на МФ: вид, период, ресурс в data.egov.bg, валута и сверката.';
COMMENT ON COLUMN pub_byudzhet.report.uri IS 'Идентификаторът на ресурса в data.egov.bg (адресът е https://data.egov.bg/data/resourceView/<uri>).';

CREATE TABLE pub_byudzhet.state_line (
  period_end      date NOT NULL,
  row_no          integer NOT NULL,
  line            text NOT NULL,
  law_original    numeric,
  actual_original numeric,
  source_unit     text NOT NULL,
  currency        char(3) NOT NULL,
  law_amount      numeric(20,2),
  actual_amount   numeric(20,2),
  pct             numeric,
  kind            text NOT NULL DEFAULT 'state' CHECK (kind = 'state'),
  CONSTRAINT state_line_pk PRIMARY KEY (period_end, row_no),
  CONSTRAINT state_line_report_fk FOREIGN KEY (kind, period_end) REFERENCES pub_byudzhet.report (kind, period_end) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_byudzhet.state_line IS 'Изпълнението на държавния бюджет по пера, натрупано от началото на годината, в реда на източника.';
COMMENT ON COLUMN pub_byudzhet.state_line.law_original IS 'Годишният план, както е в източника (в source_unit).';
COMMENT ON COLUMN pub_byudzhet.state_line.actual_original IS 'Отчетът, както е в източника (в source_unit).';
COMMENT ON COLUMN pub_byudzhet.state_line.law_amount IS 'Планът във валутата, развит от милиони.';
COMMENT ON COLUMN pub_byudzhet.state_line.actual_amount IS 'Отчетът във валутата, развит от милиони.';
COMMENT ON COLUMN pub_byudzhet.state_line.pct IS 'Изпълнението спрямо закона в проценти.';
COMMENT ON COLUMN pub_byudzhet.state_line.kind IS 'Винаги state; служи за връзката с таблицата report.';

CREATE TABLE pub_byudzhet.kfp_value (
  period_end  date NOT NULL,
  row_no      integer NOT NULL,
  col_no      integer NOT NULL,
  budget_type text NOT NULL,
  item        text NOT NULL,
  original    numeric,
  source_unit text NOT NULL,
  currency    char(3) NOT NULL,
  amount      numeric(20,2),
  kind        text NOT NULL DEFAULT 'kfp' CHECK (kind = 'kfp'),
  CONSTRAINT kfp_value_pk PRIMARY KEY (period_end, row_no, col_no),
  CONSTRAINT kfp_value_report_fk FOREIGN KEY (kind, period_end) REFERENCES pub_byudzhet.report (kind, period_end) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_byudzhet.kfp_value IS 'КФП: стойност на перо (колона) за вид бюджет (ред), натрупано от началото на годината.';
COMMENT ON COLUMN pub_byudzhet.kfp_value.original IS 'Стойността, както е в източника (в source_unit). NULL: празна клетка, не нула.';
COMMENT ON COLUMN pub_byudzhet.kfp_value.amount IS 'Стойността във валутата, развита от милиони.';
COMMENT ON COLUMN pub_byudzhet.kfp_value.kind IS 'Винаги kfp; служи за връзката с таблицата report.';

CREATE TABLE pub_byudzhet.reserve_line (
  as_of       date NOT NULL,
  row_no      integer NOT NULL,
  line        text NOT NULL,
  original    numeric NOT NULL,
  source_unit text NOT NULL,
  currency    char(3) NOT NULL,
  amount      numeric(20,2) NOT NULL,
  kind        text NOT NULL DEFAULT 'reserve' CHECK (kind = 'reserve'),
  CONSTRAINT reserve_line_pk PRIMARY KEY (as_of, row_no),
  CONSTRAINT reserve_line_report_fk FOREIGN KEY (kind, as_of) REFERENCES pub_byudzhet.report (kind, period_end) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_byudzhet.reserve_line IS 'Фискалният резерв към края на месеца: ред 0 е общият размер, следват съставните части.';
COMMENT ON COLUMN pub_byudzhet.reserve_line.original IS 'Стойността, както е в източника (в source_unit).';
COMMENT ON COLUMN pub_byudzhet.reserve_line.amount IS 'Стойността във валутата, развита от милиони.';
COMMENT ON COLUMN pub_byudzhet.reserve_line.kind IS 'Винаги reserve; служи за връзката с таблицата report.';

CREATE TABLE pub_byudzhet.municipal_value (
  as_of             date NOT NULL,
  kind              text NOT NULL,
  municipality_code text NOT NULL,
  measure           text NOT NULL,
  original          numeric,
  source_unit       text NOT NULL,
  currency          char(3) NOT NULL,
  amount            numeric(20,2),
  CONSTRAINT municipal_value_pk PRIMARY KEY (as_of, kind, municipality_code, measure),
  CONSTRAINT municipal_value_report_fk FOREIGN KEY (kind, as_of) REFERENCES pub_byudzhet.report (kind, period_end) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT municipal_value_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX municipal_value_municipality_idx ON pub_byudzhet.municipal_value (municipality_code, as_of);
COMMENT ON TABLE pub_byudzhet.municipal_value IS 'Показател на община към края на тримесечието: остатъчен дълг или финансов показател.';
COMMENT ON COLUMN pub_byudzhet.municipal_value.measure IS 'debt: остатъчна главница; overdue: просрочени задължения; liabilities: задължения за разходи; commitments: поети ангажименти за разходи.';
COMMENT ON COLUMN pub_byudzhet.municipal_value.original IS 'Стойността, както е в източника (в source_unit). NULL: празна клетка, не нула.';
COMMENT ON COLUMN pub_byudzhet.municipal_value.amount IS 'Стойността във валутата.';

CREATE TABLE pub_byudzhet.source_state (
  ref     text NOT NULL,
  kind    text NOT NULL,
  name    text,
  period  date,
  read_at timestamptz,
  status  text NOT NULL,
  error   text,
  version text,
  CONSTRAINT source_state_pk PRIMARY KEY (ref)
);
COMMENT ON TABLE pub_byudzhet.source_state IS 'Последното четене на всеки ресурс на МФ: кога, с какъв резултат, версия и период.';

CREATE TABLE pub_byudzhet.problem (
  problem text NOT NULL,
  CONSTRAINT problem_pk PRIMARY KEY (problem)
);
COMMENT ON TABLE pub_byudzhet.problem IS 'Проблемите на източниците към последното публикуване: остарели таблици, неуспешни четения, задържания.';

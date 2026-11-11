-- Бюджет in the shared database: the landing zone byudzhet_src and the clean tables byudzhet.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the municipalities are core.*.
-- The schema of the old database of its own is kept in db/legacy for reference.
DO $$ BEGIN
  IF to_regnamespace('byudzhet_src') IS NULL THEN CREATE SCHEMA byudzhet_src; END IF;
  IF to_regnamespace('byudzhet') IS NULL THEN CREATE SCHEMA byudzhet; END IF;
END $$;
COMMENT ON SCHEMA byudzhet_src IS 'Бюджет, приемна зона: каталозите и таблиците на МФ в data.egov.bg, клетка по клетка, както са публикувани.';
COMMENT ON SCHEMA byudzhet IS 'Бюджет: държавният бюджет, КФП, фискалният резерв и показателите на общините, с ключове към core.';

-- ---------------------------------------------------------------- landing

CREATE TABLE byudzhet_src.egov_catalog (
  load_id    bigint NOT NULL,
  row_no     integer NOT NULL,
  uri        text NOT NULL,
  name       text NOT NULL,
  version    text,
  updated_at text,
  CONSTRAINT egov_catalog_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT egov_catalog_uri_uq UNIQUE (load_id, uri),
  CONSTRAINT egov_catalog_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE byudzhet_src.egov_catalog IS 'Ресурсите на един набор на МФ, както ги връща listResources на data.egov.bg.';
COMMENT ON COLUMN byudzhet_src.egov_catalog.updated_at IS 'Часът на последна промяна, както го пише порталът (текст, без часова зона).';

CREATE TABLE byudzhet_src.egov_file (
  load_id    bigint NOT NULL,
  kind       text NOT NULL,
  uri        text NOT NULL,
  name       text NOT NULL,
  version    text NOT NULL,
  updated_at text,
  CONSTRAINT egov_file_pk PRIMARY KEY (load_id),
  CONSTRAINT egov_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT egov_file_kind_ck CHECK (kind IN ('state', 'kfp', 'reserve', 'debt', 'indicators'))
);
COMMENT ON TABLE byudzhet_src.egov_file IS 'Един ресурс на МФ: набор, адрес, име (от него е периодът) и версия в портала.';
COMMENT ON COLUMN byudzhet_src.egov_file.kind IS 'state: държавен бюджет; kfp: КФП; reserve: фискален резерв; debt: общински дълг; indicators: финансови показатели на общините.';

CREATE TABLE byudzhet_src.egov_row (
  load_id bigint NOT NULL,
  row_no  integer NOT NULL,
  cells   text[] NOT NULL,
  CONSTRAINT egov_row_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT egov_row_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE byudzhet_src.egov_row IS 'Редовете на таблицата, както ги връща getResourceData: клетките са текст, заглавието е ред 0. Празните редове на портала ([null]) не са тук.';

-- ---------------------------------------------------------------- clean tables
-- A table of МФ for a period is one report. A new version of it supersedes the rows of the old one
-- (superseded_at), so every earlier state can still be read. Money: the original number and unit as the
-- source writes them, the amount in the unit of its currency, and the amount in euro.

CREATE TABLE byudzhet.report (
  report_id      bigint GENERATED ALWAYS AS IDENTITY,
  kind           text NOT NULL,
  period_kind    text NOT NULL,
  period_end     date NOT NULL,
  uri            text NOT NULL,
  version        text NOT NULL,
  currency       char(3) NOT NULL,
  reconciliation text NOT NULL,
  load_id        bigint NOT NULL,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  superseded_at  timestamptz,
  CONSTRAINT report_pk PRIMARY KEY (report_id),
  CONSTRAINT report_period_kind_fk FOREIGN KEY (period_kind) REFERENCES core.period_kind (period_kind),
  CONSTRAINT report_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code),
  CONSTRAINT report_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT report_kind_ck CHECK (kind IN ('state', 'kfp', 'reserve', 'debt', 'indicators'))
);
CREATE UNIQUE INDEX report_current_uq ON byudzhet.report (kind, period_end) WHERE superseded_at IS NULL;
CREATE INDEX report_load_idx ON byudzhet.report (load_id);
COMMENT ON TABLE byudzhet.report IS 'Една таблица на МФ за един период: вид, период, ресурс, валута и проверката, която парсерът е минал.';
COMMENT ON COLUMN byudzhet.report.period_kind IS 'ytd: натрупано от началото на годината (държавен бюджет, КФП); day: наличност към датата (резерв, дълг, показатели).';
COMMENT ON COLUMN byudzhet.report.period_end IS 'Последният ден на периода или датата на наличността.';
COMMENT ON COLUMN byudzhet.report.currency IS 'Валутата на таблицата: лева до 2025 г., евро от 2026 г.';
COMMENT ON COLUMN byudzhet.report.reconciliation IS 'Сверката, която таблицата е минала при четене (сборове, салда).';
COMMENT ON COLUMN byudzhet.report.superseded_at IS 'Кога МФ е публикувало друга версия за същия период. NULL: действащият ред.';

CREATE TABLE byudzhet.state_line (
  state_line_id   bigint GENERATED ALWAYS AS IDENTITY,
  period_end      date NOT NULL,
  row_no          integer NOT NULL,
  row_key         text NOT NULL,
  line            text NOT NULL,
  law_original    numeric,
  actual_original numeric,
  source_unit     text NOT NULL,
  currency        char(3) NOT NULL,
  law_amount      numeric(20,2),
  actual_amount   numeric(20,2),
  law_eur         numeric(20,2),
  actual_eur      numeric(20,2),
  pct             numeric,
  load_id         bigint NOT NULL,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  superseded_at   timestamptz,
  CONSTRAINT state_line_pk PRIMARY KEY (state_line_id),
  CONSTRAINT state_line_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code),
  CONSTRAINT state_line_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE UNIQUE INDEX state_line_current_uq ON byudzhet.state_line (period_end, row_key) WHERE superseded_at IS NULL;
CREATE INDEX state_line_load_idx ON byudzhet.state_line (load_id);
COMMENT ON TABLE byudzhet.state_line IS 'Изпълнението на държавния бюджет по пера, натрупано от началото на годината до края на месеца.';
COMMENT ON COLUMN byudzhet.state_line.row_no IS 'Поредният номер на перото в таблицата на МФ.';
COMMENT ON COLUMN byudzhet.state_line.row_key IS 'Мястото на перото в йерархията (раздел, група, перо). Еднакви надписи в различни групи имат различен ключ.';
COMMENT ON COLUMN byudzhet.state_line.line IS 'Надписът на перото, както е в източника.';
COMMENT ON COLUMN byudzhet.state_line.law_original IS 'Годишният план по закона, както е в източника (в source_unit).';
COMMENT ON COLUMN byudzhet.state_line.actual_original IS 'Отчетът от началото на годината, както е в източника (в source_unit).';
COMMENT ON COLUMN byudzhet.state_line.source_unit IS 'Единицата на източника: „млн. BGN“ или „млн. EUR“.';
COMMENT ON COLUMN byudzhet.state_line.currency IS 'Валутата на сумите.';
COMMENT ON COLUMN byudzhet.state_line.law_amount IS 'Планът във валутата, развит от милиони.';
COMMENT ON COLUMN byudzhet.state_line.actual_amount IS 'Отчетът във валутата, развит от милиони.';
COMMENT ON COLUMN byudzhet.state_line.law_eur IS 'Планът в евро (левът по 1,95583).';
COMMENT ON COLUMN byudzhet.state_line.actual_eur IS 'Отчетът в евро (левът по 1,95583).';
COMMENT ON COLUMN byudzhet.state_line.pct IS 'Изпълнението спрямо закона в проценти: от източника или отчет / план, когато източникът няма колона.';
COMMENT ON COLUMN byudzhet.state_line.superseded_at IS 'Кога е заменен от нова версия на таблицата. NULL: действащият ред.';

CREATE TABLE byudzhet.kfp_value (
  kfp_value_id  bigint GENERATED ALWAYS AS IDENTITY,
  period_end    date NOT NULL,
  row_no        integer NOT NULL,
  col_no        integer NOT NULL,
  budget_type   text NOT NULL,
  item          text NOT NULL,
  original      numeric,
  source_unit   text NOT NULL,
  currency      char(3) NOT NULL,
  amount        numeric(20,2),
  amount_eur    numeric(20,2),
  load_id       bigint NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  superseded_at timestamptz,
  CONSTRAINT kfp_value_pk PRIMARY KEY (kfp_value_id),
  CONSTRAINT kfp_value_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code),
  CONSTRAINT kfp_value_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE UNIQUE INDEX kfp_value_current_uq ON byudzhet.kfp_value (period_end, budget_type, item) WHERE superseded_at IS NULL;
CREATE INDEX kfp_value_load_idx ON byudzhet.kfp_value (load_id);
COMMENT ON TABLE byudzhet.kfp_value IS 'Консолидираната фискална програма: стойност на перо за вид бюджет, натрупано от началото на годината.';
COMMENT ON COLUMN byudzhet.kfp_value.row_no IS 'Поредният номер на вида бюджет (ред) в таблицата на МФ.';
COMMENT ON COLUMN byudzhet.kfp_value.col_no IS 'Поредният номер на перото (колона) в таблицата на МФ.';
COMMENT ON COLUMN byudzhet.kfp_value.budget_type IS 'Видът бюджет (ред), както е в източника.';
COMMENT ON COLUMN byudzhet.kfp_value.item IS 'Перото (колона), както е в източника.';
COMMENT ON COLUMN byudzhet.kfp_value.original IS 'Стойността, както е в източника (в source_unit). NULL: празна клетка, не нула.';
COMMENT ON COLUMN byudzhet.kfp_value.source_unit IS 'Единицата на източника: „млн. BGN“ или „млн. EUR“.';
COMMENT ON COLUMN byudzhet.kfp_value.currency IS 'Валутата на сумата.';
COMMENT ON COLUMN byudzhet.kfp_value.amount IS 'Стойността във валутата, развита от милиони.';
COMMENT ON COLUMN byudzhet.kfp_value.amount_eur IS 'Стойността в евро (левът по 1,95583).';
COMMENT ON COLUMN byudzhet.kfp_value.superseded_at IS 'Кога е заменена от нова версия на таблицата. NULL: действащият ред.';

CREATE TABLE byudzhet.reserve_line (
  reserve_line_id bigint GENERATED ALWAYS AS IDENTITY,
  as_of           date NOT NULL,
  row_no          integer NOT NULL,
  line            text NOT NULL,
  original        numeric NOT NULL,
  source_unit     text NOT NULL,
  currency        char(3) NOT NULL,
  amount          numeric(20,2) NOT NULL,
  amount_eur      numeric(20,2),
  load_id         bigint NOT NULL,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  superseded_at   timestamptz,
  CONSTRAINT reserve_line_pk PRIMARY KEY (reserve_line_id),
  CONSTRAINT reserve_line_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code),
  CONSTRAINT reserve_line_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE UNIQUE INDEX reserve_line_current_uq ON byudzhet.reserve_line (as_of, row_no) WHERE superseded_at IS NULL;
CREATE INDEX reserve_line_load_idx ON byudzhet.reserve_line (load_id);
COMMENT ON TABLE byudzhet.reserve_line IS 'Фискалният резерв към края на месеца: общият размер и съставните му части. Наличност, не поток.';
COMMENT ON COLUMN byudzhet.reserve_line.row_no IS 'Поредният номер на реда в таблицата на МФ; ред 0 е общият размер.';
COMMENT ON COLUMN byudzhet.reserve_line.line IS 'Надписът на реда, както е в източника.';
COMMENT ON COLUMN byudzhet.reserve_line.original IS 'Стойността, както е в източника (в source_unit).';
COMMENT ON COLUMN byudzhet.reserve_line.source_unit IS 'Единицата на източника: „млн. BGN“ или „млн. EUR“.';
COMMENT ON COLUMN byudzhet.reserve_line.currency IS 'Валутата на сумата.';
COMMENT ON COLUMN byudzhet.reserve_line.amount IS 'Стойността във валутата, развита от милиони.';
COMMENT ON COLUMN byudzhet.reserve_line.amount_eur IS 'Стойността в евро (левът по 1,95583).';
COMMENT ON COLUMN byudzhet.reserve_line.superseded_at IS 'Кога е заменен от нова версия на таблицата. NULL: действащият ред.';

CREATE TABLE byudzhet.municipal_value (
  municipal_value_id bigint GENERATED ALWAYS AS IDENTITY,
  as_of              date NOT NULL,
  kind               text NOT NULL,
  municipality_code  text NOT NULL,
  measure            text NOT NULL,
  original           numeric,
  source_unit        text NOT NULL,
  currency           char(3) NOT NULL,
  amount             numeric(20,2),
  amount_eur         numeric(20,2),
  load_id            bigint NOT NULL,
  first_seen_at      timestamptz NOT NULL DEFAULT now(),
  superseded_at      timestamptz,
  CONSTRAINT municipal_value_pk PRIMARY KEY (municipal_value_id),
  CONSTRAINT municipal_value_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT municipal_value_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code),
  CONSTRAINT municipal_value_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT municipal_value_kind_ck CHECK (kind IN ('debt', 'indicators')),
  CONSTRAINT municipal_value_measure_ck CHECK ((kind = 'debt' AND measure = 'debt')
    OR (kind = 'indicators' AND measure IN ('overdue', 'liabilities', 'commitments')))
);
CREATE UNIQUE INDEX municipal_value_current_uq ON byudzhet.municipal_value (as_of, kind, municipality_code, measure)
  WHERE superseded_at IS NULL;
CREATE INDEX municipal_value_municipality_idx ON byudzhet.municipal_value (municipality_code, as_of) WHERE superseded_at IS NULL;
CREATE INDEX municipal_value_load_idx ON byudzhet.municipal_value (load_id);
COMMENT ON TABLE byudzhet.municipal_value IS 'Показател на община към края на тримесечието: остатъчен дълг или финансов показател. Наличност, не поток.';
COMMENT ON COLUMN byudzhet.municipal_value.kind IS 'debt: регистър „Общински дълг“; indicators: финансови показатели за общините.';
COMMENT ON COLUMN byudzhet.municipal_value.measure IS 'debt: остатъчна главница; overdue: просрочени задължения; liabilities: задължения за разходи; commitments: поети ангажименти за разходи.';
COMMENT ON COLUMN byudzhet.municipal_value.original IS 'Стойността, както е в източника (в source_unit). NULL: празна клетка, не нула.';
COMMENT ON COLUMN byudzhet.municipal_value.source_unit IS 'Единицата на източника: BGN до 2025 г., EUR от 2026 г.';
COMMENT ON COLUMN byudzhet.municipal_value.currency IS 'Валутата на сумата.';
COMMENT ON COLUMN byudzhet.municipal_value.amount IS 'Стойността във валутата.';
COMMENT ON COLUMN byudzhet.municipal_value.amount_eur IS 'Стойността в евро (левът по 1,95583).';
COMMENT ON COLUMN byudzhet.municipal_value.superseded_at IS 'Кога е заменен от нова версия на таблицата. NULL: действащият ред.';

CREATE TABLE byudzhet.known_gap (
  uri      text NOT NULL,
  sha256   text NOT NULL,
  kind     text NOT NULL,
  name     text NOT NULL,
  reason   text NOT NULL,
  CONSTRAINT known_gap_pk PRIMARY KEY (uri, sha256)
);
COMMENT ON TABLE byudzhet.known_gap IS 'Ресурси на МФ, за които е проверено, че източникът е невалиден (същите байтове): не са проблем при всяко четене. Зарежда се от db/ref/source_gaps.json при всяка стъпка.';
COMMENT ON COLUMN byudzhet.known_gap.sha256 IS 'sha256 на проверения невалиден файл. Друг файл за същия ресурс отново е проблем.';

-- the last read of every resource, in the words the sources page uses
CREATE VIEW byudzhet.source_state AS
WITH last AS (
  SELECT DISTINCT ON (l.source, l.ref) l.source AS kind, l.ref, l.loaded_at, l.status, l.reason, l.load_id, l.raw_file_id
  FROM ops.load l WHERE l.tool = 'byudzhet' AND l.source <> 'egov-catalog'
  ORDER BY l.source, l.ref, l.loaded_at DESC, l.load_id DESC),
cat AS (
  SELECT DISTINCT ON (c.uri) c.uri, c.name, c.version
  FROM byudzhet_src.egov_catalog c JOIN ops.load l USING (load_id)
  WHERE l.tool = 'byudzhet' AND l.is_current ORDER BY c.uri, l.loaded_at DESC),
file AS (
  SELECT DISTINCT ON (f.uri) f.uri, f.name, f.version
  FROM byudzhet_src.egov_file f JOIN ops.load l USING (load_id) ORDER BY f.uri, l.loaded_at DESC, l.load_id DESC)
SELECT last.ref, last.kind, COALESCE(cat.name, file.name) AS name,
       (SELECT r.period_end FROM byudzhet.report r WHERE r.uri = last.ref AND r.superseded_at IS NULL) AS period,
       last.loaded_at AS read_at,
       CASE WHEN cat.uri IS NULL THEN 'липсва при източника'
            WHEN last.status IN ('loaded', 'confirmed', 'unchanged') THEN 'наред'
            WHEN last.status = 'held' THEN 'задържан до второ четене'
            ELSE 'невалиден отговор' END AS status,
       CASE WHEN cat.uri IS NULL THEN 'Ресурсът липсва в актуалния каталог; последните валидни данни са запазени'
            WHEN last.status IN ('rejected') THEN last.reason END AS error,
       COALESCE(cat.version, file.version) AS version,
       (SELECT f.sha256 FROM ops.raw_file f WHERE f.raw_file_id = last.raw_file_id) AS raw_sha256
FROM last LEFT JOIN cat ON cat.uri = last.ref LEFT JOIN file ON file.uri = last.ref;
COMMENT ON VIEW byudzhet.source_state IS 'Последното четене на всеки ресурс на МФ: кога, с какъв резултат, и дали още е в каталога.';

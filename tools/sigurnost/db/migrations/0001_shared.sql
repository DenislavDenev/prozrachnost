-- Сигурност in the shared database (from 24.11.2026): the landing zone sigurnost_src and the clean tables sigurnost.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the oblasts are core.nuts.
-- The schema of the old database of its own (ops, silver, gold) is kept in db/legacy for reference.
DO $$ BEGIN
  IF to_regnamespace('sigurnost_src') IS NULL THEN CREATE SCHEMA sigurnost_src; END IF;
  IF to_regnamespace('sigurnost') IS NULL THEN CREATE SCHEMA sigurnost; END IF;
END $$;
COMMENT ON SCHEMA sigurnost_src IS 'Сигурност, приемна зона: списъците на наборите на МВР и таблиците им, ред по ред и клетка по клетка, както ги дава data.egov.bg.';
COMMENT ON SCHEMA sigurnost IS 'Сигурност: престъпленията по години, видове и структури на МВР, с ключ към областите в core.';

-- ---------------------------------------------------------------- landing zone

CREATE TABLE sigurnost_src.list_entry (
  load_id       bigint NOT NULL,
  resource_uri  text NOT NULL,
  name          text NOT NULL,
  resource_type text NOT NULL,
  version       text NOT NULL,
  updated_at    text NOT NULL,
  resource_url  text NOT NULL,
  CONSTRAINT list_entry_pk PRIMARY KEY (load_id, resource_uri),
  CONSTRAINT list_entry_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sigurnost_src.list_entry IS 'Един ресурс от списъка на набор, както порталът го дава (списъкът е в архива на Наблюдател).';
COMMENT ON COLUMN sigurnost_src.list_entry.resource_type IS 'Видът на ресурса по портала („Файл“, „Хиперлинк“ ...); празно, когато не е даден.';
COMMENT ON COLUMN sigurnost_src.list_entry.updated_at IS 'Часът на обновяване по портала, както е написан.';
COMMENT ON COLUMN sigurnost_src.list_entry.resource_url IS 'Адресът на хиперлинк (файл на mvr.bg); празно за таблица.';

CREATE TABLE sigurnost_src.resource (
  load_id           bigint NOT NULL,
  resource_uri      text NOT NULL,
  set_uri           text NOT NULL,
  raw_sha256        text NOT NULL,
  name              text NOT NULL,
  version           text NOT NULL,
  source_updated_at timestamptz,
  read_at           timestamptz NOT NULL,
  kind              text NOT NULL,
  title             text,
  family            text,
  template          text,
  title_year        integer,
  n_rows            integer NOT NULL,
  n_cols            integer NOT NULL,
  n_blocks          integer NOT NULL,
  issues            integer NOT NULL,
  note              text,
  CONSTRAINT resource_pk PRIMARY KEY (load_id),
  CONSTRAINT resource_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT resource_kind_ck CHECK (kind IN ('table', 'empty'))
);
CREATE INDEX resource_uri_idx ON sigurnost_src.resource (resource_uri);
COMMENT ON TABLE sigurnost_src.resource IS 'Една прочетена версия на таблица на МВР (лист от Excel в JSON). Коя е действащата казва ops.load.is_current.';
COMMENT ON COLUMN sigurnost_src.resource.raw_sha256 IS 'sha256 на файла в архива на Наблюдател.';
COMMENT ON COLUMN sigurnost_src.resource.read_at IS 'Кога архивът е прочел този отговор на портала.';
COMMENT ON COLUMN sigurnost_src.resource.kind IS 'table: таблица; empty: порталът не държи таблица (празен отговор).';
COMMENT ON COLUMN sigurnost_src.resource.family IS 'Видът публикувана таблица (по заглавните редове, не по името): types, structures, main, types_by_structure, econ_by_structure; празно за друга таблица.';
COMMENT ON COLUMN sigurnost_src.resource.template IS 'Отпечатък на шаблона: сравнение между години има само при един шаблон.';
COMMENT ON COLUMN sigurnost_src.resource.title_year IS 'Годината в заглавието на таблицата („Полицейска статистика 2015“), когато я има.';
COMMENT ON COLUMN sigurnost_src.resource.issues IS 'Клетки с текст на мястото на число.';
COMMENT ON COLUMN sigurnost_src.resource.note IS 'Какво в оригинала е необичайно: колона без заглавие, повторен ред, заглавие за друга година.';

CREATE TABLE sigurnost_src.col (
  load_id bigint NOT NULL,
  col_no  integer NOT NULL,
  label   text NOT NULL,
  CONSTRAINT col_pk PRIMARY KEY (load_id, col_no),
  CONSTRAINT col_resource_fk FOREIGN KEY (load_id) REFERENCES sigurnost_src.resource (load_id)
);
COMMENT ON TABLE sigurnost_src.col IS 'Колона със стойности: заглавието ѝ, сглобено от заглавните редове отгоре надолу.';

CREATE TABLE sigurnost_src.row (
  load_id   bigint NOT NULL,
  row_no    integer NOT NULL,
  block_no  integer NOT NULL,
  structure text NOT NULL,
  code      text NOT NULL,
  label     text NOT NULL,
  text      text NOT NULL,
  marker    text NOT NULL,
  level     integer NOT NULL,
  is_total  boolean NOT NULL,
  dup       boolean NOT NULL,
  CONSTRAINT row_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT row_resource_fk FOREIGN KEY (load_id) REFERENCES sigurnost_src.resource (load_id),
  CONSTRAINT row_marker_ck CHECK (marker IN ('', '·', '-'))
);
COMMENT ON TABLE sigurnost_src.row IS 'Ред на таблица в блока си, както е отпечатан.';
COMMENT ON COLUMN sigurnost_src.row.structure IS 'Името в заглавието на блока (структура след структура); празно в таблица с един блок.';
COMMENT ON COLUMN sigurnost_src.row.code IS 'Отпечатаният номер („4.7.“); празно, когато редът няма.';
COMMENT ON COLUMN sigurnost_src.row.text IS 'Надписът без знака отпред.';
COMMENT ON COLUMN sigurnost_src.row.marker IS 'Знакът отпред: „·“ или „-“ за част „в това число“.';
COMMENT ON COLUMN sigurnost_src.row.level IS '0 общ ред, 1 точка, 2 подточка, 3 „·“, 4 „-“ (ориентировъчно: номерацията на МВР има пропуски).';
COMMENT ON COLUMN sigurnost_src.row.dup IS 'Точно повторение на по-ранен номериран ред: пази се тук, не влиза в чистите таблици и в сверките.';

CREATE TABLE sigurnost_src.cell (
  load_id bigint NOT NULL,
  row_no  integer NOT NULL,
  col_no  integer NOT NULL,
  text    text NOT NULL,
  value   numeric,
  issue   boolean NOT NULL,
  CONSTRAINT cell_pk PRIMARY KEY (load_id, row_no, col_no),
  CONSTRAINT cell_row_fk FOREIGN KEY (load_id, row_no) REFERENCES sigurnost_src.row (load_id, row_no)
);
COMMENT ON TABLE sigurnost_src.cell IS 'Клетка със стойност: текстът, както е отпечатан, и числото, когато е число.';
COMMENT ON COLUMN sigurnost_src.cell.value IS 'NULL за празно, тире или текст: никога нула.';
COMMENT ON COLUMN sigurnost_src.cell.issue IS 'Текст на мястото на число (не тире).';

CREATE VIEW sigurnost_src.current_resource AS
SELECT r.* FROM sigurnost_src.resource r JOIN ops.load l USING (load_id) WHERE l.is_current;
COMMENT ON VIEW sigurnost_src.current_resource IS 'Действащата версия на всяка таблица.';

-- ---------------------------------------------------------------- clean tables

CREATE TABLE sigurnost.dataset (
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
  list_load_id    bigint,
  CONSTRAINT dataset_pk PRIMARY KEY (set_uri),
  CONSTRAINT dataset_list_fk FOREIGN KEY (list_load_id) REFERENCES ops.load (load_id),
  CONSTRAINT dataset_kind_ck CHECK (kind IN ('police', 'crime-old', 'bulletin', 'road-agg')),
  CONSTRAINT dataset_terms_ck CHECK (terms_of_use_id IN ('', '1', '2'))
);
COMMENT ON TABLE sigurnost.dataset IS 'Наборите на МВР в data.egov.bg, които инструментът чете от архива, с условията им за ползване (db/ref/datasets.csv).';
COMMENT ON COLUMN sigurnost.dataset.kind IS 'police: Полицейска статистика по години; crime-old: 2014-2015; bulletin: месечният бюлетин (връзки); road-agg: тежките ПТП.';
COMMENT ON COLUMN sigurnost.dataset.terms_of_use_id IS 'Условията на портала: 1 CC0, 2 CC BY, празно: не са посочени.';
COMMENT ON COLUMN sigurnost.dataset.shared IS 'Посочен ли е свободен лиценз.';
COMMENT ON COLUMN sigurnost.dataset.listed IS 'Ресурси в последния списък на набора в архива; NULL, когато архивът още няма списък.';
COMMENT ON COLUMN sigurnost.dataset.list_load_id IS 'Действащото зареждане на списъка.';

CREATE TABLE sigurnost.link (
  resource_uri      text NOT NULL,
  set_uri           text NOT NULL,
  name              text NOT NULL,
  url               text NOT NULL,
  period            date,
  source_updated_at timestamptz,
  list_load_id      bigint NOT NULL,
  CONSTRAINT link_pk PRIMARY KEY (resource_uri),
  CONSTRAINT link_dataset_fk FOREIGN KEY (set_uri) REFERENCES sigurnost.dataset (set_uri),
  CONSTRAINT link_list_fk FOREIGN KEY (list_load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sigurnost.link IS 'Ресурсите-хиперлинкове (месечният бюлетин е връзки към PDF на mvr.bg): пазят се като връзки с месеца си, не се отварят.';
COMMENT ON COLUMN sigurnost.link.period IS 'Първият ден на месеца на бюлетина, от името му; NULL, когато името не го казва.';

CREATE TABLE sigurnost.structure (
  code      text NOT NULL,
  name      text NOT NULL,
  kind      text NOT NULL,
  oblast    text,
  nuts_code text,
  sort      integer NOT NULL,
  CONSTRAINT structure_pk PRIMARY KEY (code),
  CONSTRAINT structure_nuts_fk FOREIGN KEY (nuts_code) REFERENCES core.nuts (nuts_code),
  CONSTRAINT structure_kind_ck CHECK (kind IN ('oblast', 'gd', 'country')),
  CONSTRAINT structure_oblast_ck CHECK ((kind = 'oblast') = (nuts_code IS NOT NULL))
);
COMMENT ON TABLE sigurnost.structure IS 'Структурите на МВР в таблиците: областните дирекции (ключ: NUTS 3 на областта), главните дирекции и страната.';
COMMENT ON COLUMN sigurnost.structure.nuts_code IS 'Областта (NUTS 3) на областна дирекция; NULL за главна дирекция и за страната.';

CREATE TABLE sigurnost.structure_alias (
  alias text NOT NULL,
  code  text NOT NULL,
  CONSTRAINT structure_alias_pk PRIMARY KEY (alias),
  CONSTRAINT structure_alias_fk FOREIGN KEY (code) REFERENCES sigurnost.structure (code)
);
COMMENT ON TABLE sigurnost.structure_alias IS 'Изписванията на структурите в таблиците (с малки букви, без кавички), от db/ref/structure.csv.';

CREATE TABLE sigurnost.family (
  code  text NOT NULL,
  title text NOT NULL,
  sort  integer NOT NULL,
  CONSTRAINT family_pk PRIMARY KEY (code)
);
COMMENT ON TABLE sigurnost.family IS 'Видовете таблици, които се публикуват.';

CREATE TABLE sigurnost.indicator (
  code       text NOT NULL,
  title      text NOT NULL,
  unit       text NOT NULL,
  kind       text NOT NULL,
  definition text NOT NULL,
  example    text,
  formula    text,
  sort       integer NOT NULL,
  CONSTRAINT indicator_pk PRIMARY KEY (code),
  CONSTRAINT indicator_kind_ck CHECK (kind IN ('count', 'rate', 'share'))
);
COMMENT ON TABLE sigurnost.indicator IS 'Показателите на колоните, с определение, условен пример и формула (db/ref/indicators.csv).';

CREATE TABLE sigurnost.source_table (
  year         integer NOT NULL,
  family       text NOT NULL,
  load_id      bigint NOT NULL,
  resource_uri text NOT NULL,
  raw_sha256   text NOT NULL,
  set_uri      text NOT NULL,
  template     text NOT NULL,
  n_rows       integer NOT NULL,
  issues       integer NOT NULL,
  note         text,
  built_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT source_table_pk PRIMARY KEY (year, family),
  CONSTRAINT source_table_family_fk FOREIGN KEY (family) REFERENCES sigurnost.family (code),
  CONSTRAINT source_table_resource_fk FOREIGN KEY (load_id) REFERENCES sigurnost_src.resource (load_id),
  CONSTRAINT source_table_dataset_fk FOREIGN KEY (set_uri) REFERENCES sigurnost.dataset (set_uri)
);
COMMENT ON TABLE sigurnost.source_table IS 'Таблицата, която стои зад (година, вид): по-пълната, когато годината има две версии.';

CREATE TABLE sigurnost.crime_row (
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
  CONSTRAINT crime_row_table_fk FOREIGN KEY (year, family) REFERENCES sigurnost.source_table (year, family),
  CONSTRAINT crime_row_structure_fk FOREIGN KEY (structure_code) REFERENCES sigurnost.structure (code)
);
COMMENT ON TABLE sigurnost.crime_row IS 'Ред на таблица (вид престъпление или структура) в един блок; отпечатаният текст се пази.';
COMMENT ON COLUMN sigurnost.crime_row.parent_row_no IS 'Най-близкият по-горен ред с по-малко ниво; NULL за общия ред и за точка.';

CREATE TABLE sigurnost.observation (
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
  CONSTRAINT observation_row_fk FOREIGN KEY (year, family, structure_code, row_no) REFERENCES sigurnost.crime_row,
  CONSTRAINT observation_indicator_fk FOREIGN KEY (indicator) REFERENCES sigurnost.indicator (code),
  CONSTRAINT observation_period_fk FOREIGN KEY (period_kind) REFERENCES core.period_kind (period_kind)
);
CREATE INDEX observation_ind_idx ON sigurnost.observation (family, indicator, year);
CREATE INDEX observation_struct_idx ON sigurnost.observation (structure_code, year);
COMMENT ON TABLE sigurnost.observation IS 'Една стойност: година, вид таблица, структура, ред и показател.';
COMMENT ON COLUMN sigurnost.observation.value IS 'NULL: източникът не е отпечатал число (празно, тире или текст); никога нула.';
COMMENT ON COLUMN sigurnost.observation.value_text IS 'Стойността, както е отпечатана.';

CREATE TABLE sigurnost.unmatched (
  year         integer NOT NULL,
  family       text NOT NULL,
  name         text NOT NULL,
  reason       text NOT NULL,
  resource_uri text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (year, family, name, resource_uri)
);
COMMENT ON TABLE sigurnost.unmatched IS 'Име на структура, което не е в списъка: редът не влиза в чистите таблици и стои видим тук.';

CREATE TABLE sigurnost.reconciliation (
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
  raw_sha256   text NOT NULL,
  checked_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT reconciliation_pk PRIMARY KEY (year, family, n),
  CONSTRAINT reconciliation_status_ck CHECK (status IN ('ok', 'differs', 'source'))
);
COMMENT ON TABLE sigurnost.reconciliation IS 'Сверките на последната проверена таблица за (година, вид): ok, differs (спира публикуването, когато е задължителна) или source (разликата е в оригинала и е назована).';

CREATE TABLE sigurnost.state_note (
  kind text NOT NULL,
  n    integer NOT NULL,
  text text NOT NULL,
  CONSTRAINT state_note_pk PRIMARY KEY (kind, n),
  CONSTRAINT state_note_kind_ck CHECK (kind IN ('problem', 'info'))
);
COMMENT ON TABLE sigurnost.state_note IS 'Проблемите и бележките за свежестта към края на последната стъпка, за страницата „Източници“.';

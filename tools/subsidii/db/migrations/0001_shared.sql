-- Субсидии и помощи in the shared database (24.11.2026). The schemas are made by enroll.sh; this file makes the tables.
-- The old database `subsidii` (schemas silver, gold, ops, stage: db/legacy) is not touched.
--
-- subsidii_src  the rows of the ДФЗ report as published, with the names, kept as spans over the days the archive read a
--               file of the year (a row lives from the first file that has it to the last one). Nothing is overwritten.
-- subsidii      the clean tables: the kind of each recipient, its municipality (core.municipality), the measures, the
--               years, the check of each file, and the aggregates of every snapshot. No names of people here.
-- The ledger (runs, files, loads, holds, changes, rules) is the shared ops; the serving layer is 0002_serving.sql.
DO $$ BEGIN
  IF to_regnamespace('subsidii_src') IS NULL THEN CREATE SCHEMA subsidii_src; END IF;
  IF to_regnamespace('subsidii') IS NULL THEN CREATE SCHEMA subsidii; END IF;
END $$;
COMMENT ON SCHEMA subsidii_src IS 'Субсидии и помощи, приемна зона: редовете на справката на ДФЗ с имената, интервали по дни на четене.';
COMMENT ON SCHEMA subsidii IS 'Субсидии и помощи, чисти таблици: вид на получателя, община, мерки, години, проверка на всеки файл, сборовете на всеки прочит.';

-- ---------------------------------------------------------------- the years and the files

CREATE TABLE subsidii.source_year (
  fy            integer NOT NULL,
  starts        date NOT NULL,
  ends          date NOT NULL,
  currency      text,
  unit_evidence text,
  in_form       boolean NOT NULL DEFAULT true,
  gone_at       date,
  CONSTRAINT source_year_pk PRIMARY KEY (fy),
  CONSTRAINT source_year_currency_ck CHECK (currency IN ('BGN', 'EUR'))
);
COMMENT ON TABLE subsidii.source_year IS 'Финансова година на справката (16.10 до 15.10, с името на годината, в която завършва), както я вижда архивът.';
COMMENT ON COLUMN subsidii.source_year.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.source_year.starts IS 'Първият ден: 16.10 на предходната година.';
COMMENT ON COLUMN subsidii.source_year.ends IS 'Последният ден: 15.10.';
COMMENT ON COLUMN subsidii.source_year.currency IS 'Валутата на сумите (BGN или EUR) от db/ref/dfz_units.csv; NULL, докато не е потвърдена.';
COMMENT ON COLUMN subsidii.source_year.unit_evidence IS 'С какво е потвърдена валутата.';
COMMENT ON COLUMN subsidii.source_year.in_form IS 'Дали справката още предлага годината при последното четене на архива.';
COMMENT ON COLUMN subsidii.source_year.gone_at IS 'Денят, в който годината изчезна от справката; данните остават.';

CREATE TABLE subsidii.snapshot (
  fy           integer NOT NULL,
  day          date NOT NULL,
  status       text NOT NULL,
  sha256       text NOT NULL,
  path         text NOT NULL,
  bytes        bigint NOT NULL,
  encoding     text,
  rows_file    integer,
  holders      integer,
  payments     integer,
  total_efgz   numeric(18,2),
  total_ezfrs  numeric(18,2),
  total_nb     numeric(18,2),
  total_all    numeric(18,2),
  paid_efgz    numeric(18,2),
  paid_ezfrs   numeric(18,2),
  paid_nb      numeric(18,2),
  block_diffs  integer,
  negatives    integer,
  rows_added   integer,
  rows_removed integer,
  note         text,
  load_id      bigint,
  built_at     timestamptz NOT NULL DEFAULT now(),
  build_secs   numeric,
  CONSTRAINT snapshot_pk PRIMARY KEY (fy, day),
  CONSTRAINT snapshot_year_fk FOREIGN KEY (fy) REFERENCES subsidii.source_year (fy),
  CONSTRAINT snapshot_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT snapshot_status_ck CHECK (status IN ('built', 'held', 'invalid'))
);
COMMENT ON TABLE subsidii.snapshot IS 'Един файл на архива за една година и ден: проверката му срещу самия него и какво е записано от него.';
COMMENT ON COLUMN subsidii.snapshot.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.snapshot.day IS 'Денят на четене на файла (от името му в архива).';
COMMENT ON COLUMN subsidii.snapshot.status IS 'built (записан), held (задържан до второ четене), invalid (отказан).';
COMMENT ON COLUMN subsidii.snapshot.sha256 IS 'sha256 на файла.';
COMMENT ON COLUMN subsidii.snapshot.path IS 'Пътят на файла в архива на Наблюдател.';
COMMENT ON COLUMN subsidii.snapshot.bytes IS 'Размерът на файла.';
COMMENT ON COLUMN subsidii.snapshot.encoding IS 'Кодировката, с която е прочетен (справката казва UTF-8, а е cp1251).';
COMMENT ON COLUMN subsidii.snapshot.rows_file IS 'Редовете след заглавния.';
COMMENT ON COLUMN subsidii.snapshot.holders IS 'Редовете ОБЩО: получател в община.';
COMMENT ON COLUMN subsidii.snapshot.payments IS 'Редовете на плащания.';
COMMENT ON COLUMN subsidii.snapshot.total_efgz IS 'Сборът на „ЕФГЗ - Общо“ от редовете ОБЩО, във валутата на годината.';
COMMENT ON COLUMN subsidii.snapshot.total_ezfrs IS 'Сборът на „ЕЗФРС - Общо“ от редовете ОБЩО.';
COMMENT ON COLUMN subsidii.snapshot.total_nb IS 'Сборът на „НБ - Общо“ от редовете ОБЩО.';
COMMENT ON COLUMN subsidii.snapshot.total_all IS 'Сборът на „Обща сума“ от редовете ОБЩО.';
COMMENT ON COLUMN subsidii.snapshot.paid_efgz IS 'Сборът на колоната ЕФГЗ от редовете на плащанията.';
COMMENT ON COLUMN subsidii.snapshot.paid_ezfrs IS 'Сборът на колоната ЕЗФРС от редовете на плащанията.';
COMMENT ON COLUMN subsidii.snapshot.paid_nb IS 'Сборът на колоната НБ от редовете на плащанията.';
COMMENT ON COLUMN subsidii.snapshot.block_diffs IS 'Получатели, чиито плащания не дават реда им ОБЩО (несъответствие в самия източник).';
COMMENT ON COLUMN subsidii.snapshot.negatives IS 'Отрицателни суми във файла.';
COMMENT ON COLUMN subsidii.snapshot.rows_added IS 'Нови интервали от този файл.';
COMMENT ON COLUMN subsidii.snapshot.rows_removed IS 'Затворени интервали от този файл.';
COMMENT ON COLUMN subsidii.snapshot.note IS 'Защо е задържан или отказан, или че е приет при второ четене.';
COMMENT ON COLUMN subsidii.snapshot.load_id IS 'Зареждането в ops.load, с което файлът е приет.';
COMMENT ON COLUMN subsidii.snapshot.built_at IS 'Кога е записан.';
COMMENT ON COLUMN subsidii.snapshot.build_secs IS 'Колко секунди е отнело зареждането.';

-- ---------------------------------------------------------------- the landing zone: the rows with the names

CREATE TABLE subsidii_src.beneficiary (
  beneficiary_id bigint GENERATED ALWAYS AS IDENTITY,
  name           core.person_name NOT NULL,
  surname        core.person_name NOT NULL,
  oblast         text NOT NULL,
  obshtina       text NOT NULL,
  first_day      date NOT NULL,
  load_id        bigint NOT NULL,
  CONSTRAINT beneficiary_pk PRIMARY KEY (beneficiary_id),
  CONSTRAINT beneficiary_uq UNIQUE (name, surname, oblast, obshtina),
  CONSTRAINT beneficiary_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE subsidii_src.beneficiary IS 'Получател, както го назовава справката, в община: четирите клетки заедно (едно име в две общини са двама).';
COMMENT ON COLUMN subsidii_src.beneficiary.beneficiary_id IS 'Наш номер; не напуска приемната зона и чистите таблици.';
COMMENT ON COLUMN subsidii_src.beneficiary.name IS '„Име или код на бенефициент“: името на физическото лице или наименованието на фирмата.';
COMMENT ON COLUMN subsidii_src.beneficiary.surname IS '„Фамилно име на бенефициент“; „-“ при фирма и едноличен търговец.';
COMMENT ON COLUMN subsidii_src.beneficiary.oblast IS 'Областта, както я пише справката.';
COMMENT ON COLUMN subsidii_src.beneficiary.obshtina IS 'Общината, както я пише справката.';
COMMENT ON COLUMN subsidii_src.beneficiary.first_day IS 'Денят на първия файл, в който се появява.';
COMMENT ON COLUMN subsidii_src.beneficiary.load_id IS 'Зареждането, което го е записало първо.';

CREATE TABLE subsidii_src.measure (
  measure_id integer GENERATED ALWAYS AS IDENTITY,
  code       text NOT NULL,
  name       text NOT NULL,
  objective  text NOT NULL,
  CONSTRAINT measure_pk PRIMARY KEY (measure_id),
  CONSTRAINT measure_uq UNIQUE (code, name, objective)
);
COMMENT ON TABLE subsidii_src.measure IS 'Мярката на плащане, както е в справката: „Код“, „Интервенция“, „Специфична цел“ („-“ остава „-“).';
COMMENT ON COLUMN subsidii_src.measure.measure_id IS 'Наш номер.';
COMMENT ON COLUMN subsidii_src.measure.code IS '„Код“.';
COMMENT ON COLUMN subsidii_src.measure.name IS '„Интервенция“.';
COMMENT ON COLUMN subsidii_src.measure.objective IS '„Специфична цел“.';

CREATE TABLE subsidii_src.holder (
  holder_id      bigint GENERATED ALWAYS AS IDENTITY,
  fy             integer NOT NULL,
  key            text NOT NULL,
  occ            integer NOT NULL,
  beneficiary_id bigint NOT NULL,
  grp            text NOT NULL,
  efgz           numeric(16,2),
  ezfrs          numeric(16,2),
  nb             numeric(16,2),
  ezfrs_nb       numeric(16,2),
  total          numeric(16,2),
  from_day       date NOT NULL,
  to_day         date,
  load_id        bigint NOT NULL,
  CONSTRAINT holder_pk PRIMARY KEY (holder_id),
  CONSTRAINT holder_beneficiary_fk FOREIGN KEY (beneficiary_id) REFERENCES subsidii_src.beneficiary (beneficiary_id),
  CONSTRAINT holder_year_fk FOREIGN KEY (fy) REFERENCES subsidii.source_year (fy),
  CONSTRAINT holder_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE UNIQUE INDEX holder_identity ON subsidii_src.holder (fy, key, occ, from_day);
CREATE INDEX holder_open ON subsidii_src.holder (fy, key, occ) WHERE to_day IS NULL;
CREATE INDEX holder_beneficiary ON subsidii_src.holder (beneficiary_id, fy);
CREATE INDEX holder_span ON subsidii_src.holder (fy, from_day, to_day);
CREATE INDEX holder_load ON subsidii_src.holder (load_id);
COMMENT ON TABLE subsidii_src.holder IS 'Редът ОБЩО на получател: сумите му по фондове, както са публикувани, за дните от from_day до to_day.';
COMMENT ON COLUMN subsidii_src.holder.holder_id IS 'Наш номер на интервала.';
COMMENT ON COLUMN subsidii_src.holder.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii_src.holder.key IS 'md5 на клетките на реда: самоличността на съдържанието му.';
COMMENT ON COLUMN subsidii_src.holder.occ IS 'Пореден номер на еднакъв ред във файла.';
COMMENT ON COLUMN subsidii_src.holder.beneficiary_id IS 'Получателят.';
COMMENT ON COLUMN subsidii_src.holder.grp IS '„Група“.';
COMMENT ON COLUMN subsidii_src.holder.efgz IS '„ЕФГЗ - Общо“, във валутата на годината.';
COMMENT ON COLUMN subsidii_src.holder.ezfrs IS '„ЕЗФРС - Общо“.';
COMMENT ON COLUMN subsidii_src.holder.nb IS '„НБ - Общо“.';
COMMENT ON COLUMN subsidii_src.holder.ezfrs_nb IS '„ЕЗФРС и НБ - Общо“.';
COMMENT ON COLUMN subsidii_src.holder.total IS '„Обща сума“.';
COMMENT ON COLUMN subsidii_src.holder.from_day IS 'Денят на първия файл с този ред.';
COMMENT ON COLUMN subsidii_src.holder.to_day IS 'Денят на последния файл с този ред; NULL, докато е в най-новия файл на годината.';
COMMENT ON COLUMN subsidii_src.holder.load_id IS 'Зареждането, което е отворило интервала.';

CREATE TABLE subsidii_src.payment (
  payment_id      bigint GENERATED ALWAYS AS IDENTITY,
  fy              integer NOT NULL,
  key             text NOT NULL,
  occ             integer NOT NULL,
  beneficiary_id  bigint NOT NULL,
  name_variant    core.person_name,
  surname_variant core.person_name,
  measure_id      integer NOT NULL,
  starts          date,
  ends            date,
  efgz            numeric(16,2),
  ezfrs           numeric(16,2),
  nb              numeric(16,2),
  from_day        date NOT NULL,
  to_day          date,
  load_id         bigint NOT NULL,
  CONSTRAINT payment_pk PRIMARY KEY (payment_id),
  CONSTRAINT payment_beneficiary_fk FOREIGN KEY (beneficiary_id) REFERENCES subsidii_src.beneficiary (beneficiary_id),
  CONSTRAINT payment_measure_fk FOREIGN KEY (measure_id) REFERENCES subsidii_src.measure (measure_id),
  CONSTRAINT payment_year_fk FOREIGN KEY (fy) REFERENCES subsidii.source_year (fy),
  CONSTRAINT payment_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE UNIQUE INDEX payment_identity ON subsidii_src.payment (fy, key, beneficiary_id, occ, from_day);
CREATE INDEX payment_open ON subsidii_src.payment (fy, key, beneficiary_id, occ) WHERE to_day IS NULL;
CREATE INDEX payment_beneficiary ON subsidii_src.payment (beneficiary_id, fy);
CREATE INDEX payment_measure ON subsidii_src.payment (measure_id, fy);
CREATE INDEX payment_span ON subsidii_src.payment (fy, from_day, to_day);
CREATE INDEX payment_load ON subsidii_src.payment (load_id);
COMMENT ON TABLE subsidii_src.payment IS 'Ред на плащане: една сума в колоната на един фонд. Принадлежи на получателя от блока, в който стои.';
COMMENT ON COLUMN subsidii_src.payment.payment_id IS 'Наш номер на интервала.';
COMMENT ON COLUMN subsidii_src.payment.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii_src.payment.key IS 'md5 на клетките на реда.';
COMMENT ON COLUMN subsidii_src.payment.occ IS 'Пореден номер на еднакъв ред на същия получател във файла.';
COMMENT ON COLUMN subsidii_src.payment.beneficiary_id IS 'Получателят на блока.';
COMMENT ON COLUMN subsidii_src.payment.name_variant IS 'Името на реда, когато се различава от това на реда ОБЩО (изгубено тире).';
COMMENT ON COLUMN subsidii_src.payment.surname_variant IS 'Фамилията на реда, когато се различава от тази на реда ОБЩО.';
COMMENT ON COLUMN subsidii_src.payment.measure_id IS 'Мярката.';
COMMENT ON COLUMN subsidii_src.payment.starts IS '„Начална дата“.';
COMMENT ON COLUMN subsidii_src.payment.ends IS '„Крайна дата“.';
COMMENT ON COLUMN subsidii_src.payment.efgz IS '„ЕФГЗ“.';
COMMENT ON COLUMN subsidii_src.payment.ezfrs IS '„ЕЗФРС“.';
COMMENT ON COLUMN subsidii_src.payment.nb IS '„НБ“.';
COMMENT ON COLUMN subsidii_src.payment.from_day IS 'Денят на първия файл с този ред.';
COMMENT ON COLUMN subsidii_src.payment.to_day IS 'Денят на последния файл с този ред; NULL, докато е в най-новия.';
COMMENT ON COLUMN subsidii_src.payment.load_id IS 'Зареждането, което е отворило интервала.';

CREATE TABLE subsidii_src.block_diff (
  fy             integer NOT NULL,
  day            date NOT NULL,
  line_no        integer NOT NULL,
  fund           text NOT NULL,
  beneficiary_id bigint NOT NULL,
  stated         numeric(16,2) NOT NULL,
  summed         numeric(16,2) NOT NULL,
  CONSTRAINT block_diff_pk PRIMARY KEY (fy, day, line_no, fund),
  CONSTRAINT block_diff_beneficiary_fk FOREIGN KEY (beneficiary_id) REFERENCES subsidii_src.beneficiary (beneficiary_id),
  CONSTRAINT block_diff_snapshot_fk FOREIGN KEY (fy, day) REFERENCES subsidii.snapshot (fy, day)
);
COMMENT ON TABLE subsidii_src.block_diff IS 'Получател, чиито плащания не дават реда му ОБЩО по някой фонд: несъответствието на източника, както е.';
COMMENT ON COLUMN subsidii_src.block_diff.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii_src.block_diff.day IS 'Денят на файла.';
COMMENT ON COLUMN subsidii_src.block_diff.line_no IS 'Редът ОБЩО във файла (1 е първият ред след заглавния).';
COMMENT ON COLUMN subsidii_src.block_diff.fund IS 'efgz, ezfrs или nb.';
COMMENT ON COLUMN subsidii_src.block_diff.beneficiary_id IS 'Получателят.';
COMMENT ON COLUMN subsidii_src.block_diff.stated IS 'Сумата на реда ОБЩО.';
COMMENT ON COLUMN subsidii_src.block_diff.summed IS 'Сборът на плащанията му.';

-- one file under load: unlogged, emptied for every file
CREATE UNLOGGED TABLE subsidii_src.line (
  total boolean NOT NULL, block integer NOT NULL, n integer NOT NULL, key text NOT NULL, occ integer,
  name core.person_name, surname core.person_name, grp text, oblast text, obshtina text,
  o_name core.person_name, o_surname core.person_name,
  code text, measure text, objective text, starts date, ends date,
  efgz numeric, ezfrs numeric, nb numeric, ezfrs_nb numeric, total_amount numeric,
  efgz_t numeric, ezfrs_t numeric, nb_t numeric,
  beneficiary_id bigint, measure_id integer
);
COMMENT ON TABLE subsidii_src.line IS 'Работна таблица: редовете на един файл, докато се зарежда; изпразва се за всеки файл.';

-- ---------------------------------------------------------------- the clean tables

CREATE TABLE subsidii.fiscal_year (
  fy            integer NOT NULL,
  starts        date NOT NULL,
  ends          date NOT NULL,
  label_bg      text NOT NULL,
  currency      text,
  eur_per_unit  numeric(20,12),
  unit_evidence text,
  in_form       boolean NOT NULL,
  gone_at       date,
  first_snap    date,
  last_snap     date,
  names_until   date NOT NULL,
  CONSTRAINT fiscal_year_pk PRIMARY KEY (fy),
  CONSTRAINT fiscal_year_currency_fk FOREIGN KEY (currency) REFERENCES core.currency (currency_code)
);
COMMENT ON TABLE subsidii.fiscal_year IS 'Финансовата година за страниците: етикет, валута и курс към евро, първи и последен прочит.';
COMMENT ON COLUMN subsidii.fiscal_year.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.fiscal_year.starts IS 'Първият ден.';
COMMENT ON COLUMN subsidii.fiscal_year.ends IS 'Последният ден.';
COMMENT ON COLUMN subsidii.fiscal_year.label_bg IS 'Етикетът с датите.';
COMMENT ON COLUMN subsidii.fiscal_year.currency IS 'Валутата на сумите; NULL, докато не е потвърдена.';
COMMENT ON COLUMN subsidii.fiscal_year.eur_per_unit IS 'Евро за една единица от валутата (1/1,95583 за лев); NULL без потвърдена валута.';
COMMENT ON COLUMN subsidii.fiscal_year.unit_evidence IS 'С какво е потвърдена валутата.';
COMMENT ON COLUMN subsidii.fiscal_year.in_form IS 'Дали справката още предлага годината.';
COMMENT ON COLUMN subsidii.fiscal_year.gone_at IS 'Кога годината изчезна от справката.';
COMMENT ON COLUMN subsidii.fiscal_year.first_snap IS 'Първият приет файл на годината.';
COMMENT ON COLUMN subsidii.fiscal_year.last_snap IS 'Последният приет файл на годината.';
COMMENT ON COLUMN subsidii.fiscal_year.names_until IS 'До кой ден се показва маскираното име на физическо лице: две години след края на годината, както ДФЗ публикува.';

CREATE TABLE subsidii.measure (
  measure_id integer NOT NULL,
  code       text NOT NULL,
  name       text NOT NULL,
  objective  text NOT NULL,
  family     text NOT NULL,
  family_bg  text NOT NULL,
  CONSTRAINT measure_pk PRIMARY KEY (measure_id),
  CONSTRAINT measure_src_fk FOREIGN KEY (measure_id) REFERENCES subsidii_src.measure (measure_id)
);
COMMENT ON TABLE subsidii.measure IS 'Мярката с групата ѝ по кода („I.4“ е от група I).';
COMMENT ON COLUMN subsidii.measure.measure_id IS 'Номерът на мярката.';
COMMENT ON COLUMN subsidii.measure.code IS 'Кодът.';
COMMENT ON COLUMN subsidii.measure.name IS 'Интервенцията.';
COMMENT ON COLUMN subsidii.measure.objective IS 'Специфичната цел.';
COMMENT ON COLUMN subsidii.measure.family IS 'Групата по кода; „-“ без код.';
COMMENT ON COLUMN subsidii.measure.family_bg IS 'Етикетът на групата.';

CREATE TABLE subsidii.org (
  org_id    text NOT NULL,
  name_norm text NOT NULL,
  name_bg   text NOT NULL,
  CONSTRAINT org_pk PRIMARY KEY (org_id),
  CONSTRAINT org_norm_uq UNIQUE (name_norm)
);
COMMENT ON TABLE subsidii.org IS 'Юридическо лице по нормализирано наименование. Не е ЕИК: ДФЗ не дава ЕИК.';
COMMENT ON COLUMN subsidii.org.org_id IS 'Първите 12 знака на sha1 от нормализираното наименование.';
COMMENT ON COLUMN subsidii.org.name_norm IS 'Ключът за търсене: главни букви, латинските двойници като кирилица, без препинателни знаци.';
COMMENT ON COLUMN subsidii.org.name_bg IS 'Наименованието, както е в първия ред с него.';

CREATE TABLE subsidii.beneficiary (
  beneficiary_id    bigint NOT NULL,
  kind              text NOT NULL,
  name_norm         text,
  org_id            text,
  municipality_code text,
  CONSTRAINT beneficiary_pk PRIMARY KEY (beneficiary_id),
  CONSTRAINT beneficiary_src_fk FOREIGN KEY (beneficiary_id) REFERENCES subsidii_src.beneficiary (beneficiary_id),
  CONSTRAINT beneficiary_org_fk FOREIGN KEY (org_id) REFERENCES subsidii.org (org_id),
  CONSTRAINT beneficiary_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT beneficiary_kind_ck CHECK (kind IN ('legal', 'sole_trader', 'natural')),
  CONSTRAINT beneficiary_people_ck CHECK (kind = 'legal' OR (name_norm IS NULL AND org_id IS NULL))
);
CREATE INDEX beneficiary_org ON subsidii.beneficiary (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX beneficiary_municipality ON subsidii.beneficiary (municipality_code);
COMMENT ON TABLE subsidii.beneficiary IS 'Видът на получателя и общината му (ingest/names.py). Хора не се групират и нямат ключ за търсене.';
COMMENT ON COLUMN subsidii.beneficiary.beneficiary_id IS 'Получателят в приемната зона.';
COMMENT ON COLUMN subsidii.beneficiary.kind IS 'legal (юридическо лице), sole_trader (ЕТ, носи името на човек), natural (физическо лице).';
COMMENT ON COLUMN subsidii.beneficiary.name_norm IS 'Ключът за търсене, само за юридически лица.';
COMMENT ON COLUMN subsidii.beneficiary.org_id IS 'Юридическото лице по наименование, само за юридически лица.';
COMMENT ON COLUMN subsidii.beneficiary.municipality_code IS 'Общината по код на НСИ; NULL, когато справката назовава непозната община.';

CREATE TABLE subsidii.place_match (
  oblast_raw        text NOT NULL,
  obshtina_raw      text NOT NULL,
  municipality_code text NOT NULL,
  method            text NOT NULL,
  evidence          text,
  CONSTRAINT place_match_pk PRIMARY KEY (oblast_raw, obshtina_raw),
  CONSTRAINT place_match_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT place_match_method_ck CHECK (method IN ('name', 'alias'))
);
COMMENT ON TABLE subsidii.place_match IS 'Как двойката (област, община) на справката е намерена сред общините.';
COMMENT ON COLUMN subsidii.place_match.oblast_raw IS 'Областта в справката.';
COMMENT ON COLUMN subsidii.place_match.obshtina_raw IS 'Общината в справката.';
COMMENT ON COLUMN subsidii.place_match.municipality_code IS 'Общината.';
COMMENT ON COLUMN subsidii.place_match.method IS 'name: същото име в същата област; alias: ред от db/ref/municipality_alias.csv.';
COMMENT ON COLUMN subsidii.place_match.evidence IS 'Доказателството за псевдонима.';

CREATE TABLE subsidii.meta (
  key   text NOT NULL,
  value text NOT NULL,
  CONSTRAINT meta_pk PRIMARY KEY (key)
);
COMMENT ON TABLE subsidii.meta IS 'Служебни стойности, например версията на правилата за вида на получателя.';
COMMENT ON COLUMN subsidii.meta.key IS 'Името.';
COMMENT ON COLUMN subsidii.meta.value IS 'Стойността.';

CREATE TABLE subsidii.build (
  build_id bigint GENERATED ALWAYS AS IDENTITY,
  fy       integer NOT NULL,
  snap_day date NOT NULL,
  started  timestamptz NOT NULL DEFAULT now(),
  finished timestamptz,
  rows     jsonb,
  checks   jsonb,
  ok       boolean,
  code_sha text,
  CONSTRAINT build_pk PRIMARY KEY (build_id),
  CONSTRAINT build_snapshot_uq UNIQUE (fy, snap_day)
);
COMMENT ON TABLE subsidii.build IS 'Изграждането на сборовете на един прочит и проверките му; ok е истина само когато всички минават.';
COMMENT ON COLUMN subsidii.build.build_id IS 'Номер.';
COMMENT ON COLUMN subsidii.build.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.build.snap_day IS 'Денят на прочита.';
COMMENT ON COLUMN subsidii.build.started IS 'Начало.';
COMMENT ON COLUMN subsidii.build.finished IS 'Край.';
COMMENT ON COLUMN subsidii.build.rows IS 'Записаните редове по таблица.';
COMMENT ON COLUMN subsidii.build.checks IS 'Нарушенията, ако има.';
COMMENT ON COLUMN subsidii.build.ok IS 'Дали всички проверки минават.';
COMMENT ON COLUMN subsidii.build.code_sha IS 'Комитът на кода.';

CREATE TABLE subsidii.summary (
  fy         integer NOT NULL,
  snap_day   date NOT NULL,
  scope      text NOT NULL,
  recipients integer NOT NULL,
  payments   integer NOT NULL,
  efgz       numeric(18,2),
  ezfrs      numeric(18,2),
  nb         numeric(18,2),
  total      numeric(18,2),
  top1_n     integer,
  top1_total numeric(18,2),
  build_id   bigint,
  CONSTRAINT summary_pk PRIMARY KEY (fy, snap_day, scope),
  CONSTRAINT summary_scope_ck CHECK (scope IN ('all', 'legal', 'sole_trader', 'natural'))
);
COMMENT ON TABLE subsidii.summary IS 'За всеки прочит: сборовете по вид получател от редовете ОБЩО и концентрацията (най-горният 1% от редовете, не от хората).';
COMMENT ON COLUMN subsidii.summary.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.summary.snap_day IS 'Денят на прочита.';
COMMENT ON COLUMN subsidii.summary.scope IS 'all или видът на получателя.';
COMMENT ON COLUMN subsidii.summary.recipients IS 'Редовете ОБЩО (получател в община).';
COMMENT ON COLUMN subsidii.summary.payments IS 'Редовете на плащания.';
COMMENT ON COLUMN subsidii.summary.efgz IS 'ЕФГЗ, във валутата на годината.';
COMMENT ON COLUMN subsidii.summary.ezfrs IS 'ЕЗФРСР.';
COMMENT ON COLUMN subsidii.summary.nb IS 'Национален бюджет.';
COMMENT ON COLUMN subsidii.summary.total IS 'Обща сума.';
COMMENT ON COLUMN subsidii.summary.top1_n IS 'Колко реда са най-горният 1%.';
COMMENT ON COLUMN subsidii.summary.top1_total IS 'Колко получават те.';
COMMENT ON COLUMN subsidii.summary.build_id IS 'Изграждането.';

CREATE TABLE subsidii.municipality_fy (
  fy                integer NOT NULL,
  snap_day          date NOT NULL,
  municipality_code text NOT NULL,
  kind              text NOT NULL,
  recipients        integer NOT NULL,
  efgz              numeric(16,2),
  ezfrs             numeric(16,2),
  nb                numeric(16,2),
  total             numeric(16,2),
  CONSTRAINT municipality_fy_pk PRIMARY KEY (fy, snap_day, municipality_code, kind),
  CONSTRAINT municipality_fy_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code)
);
COMMENT ON TABLE subsidii.municipality_fy IS 'Сборовете на редовете ОБЩО по община и вид получател, за всеки прочит.';
COMMENT ON COLUMN subsidii.municipality_fy.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.municipality_fy.snap_day IS 'Денят на прочита.';
COMMENT ON COLUMN subsidii.municipality_fy.municipality_code IS 'Общината.';
COMMENT ON COLUMN subsidii.municipality_fy.kind IS 'Видът на получателя.';
COMMENT ON COLUMN subsidii.municipality_fy.recipients IS 'Редовете ОБЩО.';
COMMENT ON COLUMN subsidii.municipality_fy.efgz IS 'ЕФГЗ.';
COMMENT ON COLUMN subsidii.municipality_fy.ezfrs IS 'ЕЗФРСР.';
COMMENT ON COLUMN subsidii.municipality_fy.nb IS 'Национален бюджет.';
COMMENT ON COLUMN subsidii.municipality_fy.total IS 'Обща сума.';

CREATE TABLE subsidii.payment_agg (
  fy                integer NOT NULL,
  snap_day          date NOT NULL,
  municipality_code text NOT NULL,
  measure_id        integer NOT NULL,
  kind              text NOT NULL,
  payments          integer NOT NULL,
  efgz              numeric(16,2),
  ezfrs             numeric(16,2),
  nb                numeric(16,2),
  total             numeric(16,2),
  CONSTRAINT payment_agg_pk PRIMARY KEY (fy, snap_day, municipality_code, measure_id, kind),
  CONSTRAINT payment_agg_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT payment_agg_measure_fk FOREIGN KEY (measure_id) REFERENCES subsidii.measure (measure_id)
);
CREATE INDEX payment_agg_measure ON subsidii.payment_agg (fy, snap_day, measure_id);
COMMENT ON TABLE subsidii.payment_agg IS 'Плащанията по община, мярка и вид получател, за всеки прочит.';
COMMENT ON COLUMN subsidii.payment_agg.fy IS 'Финансовата година.';
COMMENT ON COLUMN subsidii.payment_agg.snap_day IS 'Денят на прочита.';
COMMENT ON COLUMN subsidii.payment_agg.municipality_code IS 'Общината.';
COMMENT ON COLUMN subsidii.payment_agg.measure_id IS 'Мярката.';
COMMENT ON COLUMN subsidii.payment_agg.kind IS 'Видът на получателя.';
COMMENT ON COLUMN subsidii.payment_agg.payments IS 'Редовете на плащания.';
COMMENT ON COLUMN subsidii.payment_agg.efgz IS 'ЕФГЗ.';
COMMENT ON COLUMN subsidii.payment_agg.ezfrs IS 'ЕЗФРСР.';
COMMENT ON COLUMN subsidii.payment_agg.nb IS 'Национален бюджет.';
COMMENT ON COLUMN subsidii.payment_agg.total IS 'Сборът на трите.';

CREATE TABLE subsidii.observation (
  indicator_code    text NOT NULL,
  municipality_code text NOT NULL,
  scope             text NOT NULL,
  period_kind       text NOT NULL,
  period_start      date NOT NULL,
  period_end        date NOT NULL,
  value_eur         numeric(20,2),
  value_original    numeric(20,2),
  currency          text,
  status            text NOT NULL,
  published_at      date NOT NULL,
  raw_sha256        text NOT NULL,
  build_id          bigint,
  CONSTRAINT observation_pk PRIMARY KEY (indicator_code, municipality_code, scope, period_start, published_at),
  CONSTRAINT observation_indicator_fk FOREIGN KEY (indicator_code) REFERENCES core.indicator (indicator_code),
  CONSTRAINT observation_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT observation_status_ck CHECK (status IN ('final', 'provisional'))
);
COMMENT ON TABLE subsidii.observation IS 'Сумата на фонд в община за финансова година, по прочит, с оригинала до евровата стойност (общата форма за API-то).';
COMMENT ON COLUMN subsidii.observation.indicator_code IS 'Показателят.';
COMMENT ON COLUMN subsidii.observation.municipality_code IS 'Общината.';
COMMENT ON COLUMN subsidii.observation.scope IS 'all или видът на получателя.';
COMMENT ON COLUMN subsidii.observation.period_kind IS 'fiscal-year.';
COMMENT ON COLUMN subsidii.observation.period_start IS 'Началото на финансовата година.';
COMMENT ON COLUMN subsidii.observation.period_end IS 'Краят на финансовата година.';
COMMENT ON COLUMN subsidii.observation.value_eur IS 'В евро; NULL без потвърдена валута.';
COMMENT ON COLUMN subsidii.observation.value_original IS 'Във валутата на справката.';
COMMENT ON COLUMN subsidii.observation.currency IS 'Валутата на справката.';
COMMENT ON COLUMN subsidii.observation.status IS 'provisional преди края на годината, final след него.';
COMMENT ON COLUMN subsidii.observation.published_at IS 'Денят на прочита.';
COMMENT ON COLUMN subsidii.observation.raw_sha256 IS 'sha256 на файла.';
COMMENT ON COLUMN subsidii.observation.build_id IS 'Изграждането.';

CREATE TABLE subsidii.unmatched (
  kind   text NOT NULL,
  key    text NOT NULL,
  n      integer NOT NULL,
  amount numeric(18,2),
  reason text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (kind, key)
);
COMMENT ON TABLE subsidii.unmatched IS 'Каквото не е съпоставено (община, която справочникът не знае), с броя и сумата, никога пропуснато мълчаливо.';
COMMENT ON COLUMN subsidii.unmatched.kind IS 'Какво: municipality.';
COMMENT ON COLUMN subsidii.unmatched.key IS 'Областта и общината на справката.';
COMMENT ON COLUMN subsidii.unmatched.n IS 'Редовете ОБЩО.';
COMMENT ON COLUMN subsidii.unmatched.amount IS 'Сборът им.';
COMMENT ON COLUMN subsidii.unmatched.reason IS 'Защо.';

-- the day for the rule about names: the current date, or the setting subsidii.today (tests only)
CREATE FUNCTION subsidii.today() RETURNS date LANGUAGE sql STABLE AS
$$ SELECT coalesce(nullif(current_setting('subsidii.today', true), '')::date, current_date) $$;
COMMENT ON FUNCTION subsidii.today() IS 'Днешният ден за правилото за имената; в тестовете се задава с subsidii.today.';
-- two years, as the source publishes the names of natural persons (Regulation (EU) 2021/2116, art. 98)
CREATE FUNCTION subsidii.name_ttl_years() RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT 2 $$;
COMMENT ON FUNCTION subsidii.name_ttl_years() IS 'Колко години след края на финансовата година се показва маскираното име: колкото ДФЗ публикува имената.';
-- the threshold of the source itself: Regulation (EU) 2021/2116, art. 98, lets a member state withhold the name of a natural
-- person who received this much or less in a year; ДФЗ uses 1 250 euro (the smallest total of a person in FY2025 is 2 445,74 лв.)
CREATE FUNCTION subsidii.name_threshold_eur() RETURNS numeric LANGUAGE sql IMMUTABLE AS $$ SELECT 1250::numeric $$;
COMMENT ON FUNCTION subsidii.name_threshold_eur() IS 'Прагът на ДФЗ за имената (1 250 € за година): физическо лице с толкова или по-малко е само в сборовете.';

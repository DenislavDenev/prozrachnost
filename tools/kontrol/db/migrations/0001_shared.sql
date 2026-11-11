-- Одити и контрол in the shared database: the landing zone kontrol_src and the clean tables kontrol.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; organisations are core.org.
-- The schema of the old database of its own (JSON documents in live.snapshot) is kept in db/legacy for reference.
-- A column of a record table has the name of the field the parser gives (ingest/parse.py, ingest/pdf.py), with the
-- source's identifier as record_id; the parser's record is rebuilt from these columns exactly (store.record()).
DO $$ BEGIN
  IF to_regnamespace('kontrol_src') IS NULL THEN CREATE SCHEMA kontrol_src; END IF;
  IF to_regnamespace('kontrol') IS NULL THEN CREATE SCHEMA kontrol; END IF;
END $$;
COMMENT ON SCHEMA kontrol_src IS 'Одити и контрол, приемна зона: прочетеното от Сметната палата, АДФИ и КЗК, по таблица за всеки вид запис.';
COMMENT ON SCHEMA kontrol IS 'Одити и контрол: одитни доклади, доклади и списъци на АДФИ, инспекции, решения на КЗК и архивираните документи.';

-- ---------------------------------------------------------------- landing

CREATE TABLE kontrol_src.read (
  load_id          bigint NOT NULL,
  source           text NOT NULL,
  pages            integer NOT NULL,
  occurrence_count integer NOT NULL,
  unique_count     integer NOT NULL,
  source_count     integer,
  scope            text NOT NULL,
  manifest         jsonb NOT NULL,
  CONSTRAINT read_pk PRIMARY KEY (load_id),
  CONSTRAINT read_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.read IS 'Един пълен обход на списък: колко страници, колко появи и уникални записа, броячът на източника и описанието на обхвата.';
COMMENT ON COLUMN kontrol_src.read.occurrence_count IS 'Колко пъти се появяват записи в обхода; един документ може да е в списъка повече от веднъж.';
COMMENT ON COLUMN kontrol_src.read.unique_count IS 'Уникалните записи по идентификатора на източника.';
COMMENT ON COLUMN kontrol_src.read.source_count IS 'Броячът, който източникът сам печата (КЗК). NULL: източникът няма брояч.';
COMMENT ON COLUMN kontrol_src.read.manifest IS 'Описанието на обхода, както го дава четецът (суров запис до колоните): години, пропуски във форматите, разминавания на брояча.';

CREATE TABLE kontrol_src.audit_report (
  load_id                        bigint NOT NULL,
  record_id                      text NOT NULL,
  source                         text NOT NULL,
  categories                     text[] NOT NULL,
  title                          text NOT NULL,
  url                            text NOT NULL,
  published_on                   date,
  publication_year               text,
  report_period                  text,
  kind                           text,
  sector                         text,
  auditee                        text,
  eik                            text,
  unp                            text,
  recommendation_text            text,
  source_discrepancy             text,
  source_kind_values             text[],
  source_sector_values           text[],
  source_publication_year_values text[],
  row_sha256                     text NOT NULL,
  CONSTRAINT audit_report_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT audit_report_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.audit_report IS 'Доклад от списък на Сметната палата (доклади, общини, след наказателни производства, изпълнение на препоръките), един ред на документ в прочита.';
COMMENT ON COLUMN kontrol_src.audit_report.row_sha256 IS 'sha256 на записа, както го дава парсерът (същото като версията в старата база).';

CREATE TABLE kontrol_src.adfi_report (
  load_id             bigint NOT NULL,
  record_id           text NOT NULL,
  source              text NOT NULL,
  categories          text[] NOT NULL,
  title               text NOT NULL,
  url                 text NOT NULL,
  published_on        date,
  publication_year    text,
  report_period       text,
  kind                text,
  sector              text,
  auditee             text,
  eik                 text,
  unp                 text,
  recommendation_text text,
  basis               text,
  updated_label       boolean NOT NULL,
  row_sha256          text NOT NULL,
  CONSTRAINT adfi_report_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT adfi_report_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.adfi_report IS 'Публикуван доклад от финансова инспекция на АДФИ, ред от регистъра /bg/34.';

CREATE TABLE kontrol_src.adfi_list (
  load_id             bigint NOT NULL,
  record_id           text NOT NULL,
  source              text NOT NULL,
  categories          text[] NOT NULL,
  title               text NOT NULL,
  url                 text NOT NULL,
  published_on        date,
  publication_year    text,
  report_period       text,
  kind                text,
  sector              text,
  auditee             text,
  eik                 text,
  unp                 text,
  recommendation_text text,
  inspection_count    integer,
  inspection_status   text NOT NULL,
  row_sha256          text NOT NULL,
  CONSTRAINT adfi_list_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT adfi_list_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.adfi_list IS 'Тримесечен списък на приключилите финансови инспекции на АДФИ (/bg/18).';

CREATE TABLE kontrol_src.inspection (
  load_id             bigint NOT NULL,
  record_id           text NOT NULL,
  source              text NOT NULL,
  categories          text[] NOT NULL,
  title               text NOT NULL,
  url                 text NOT NULL,
  published_on        date,
  publication_year    text,
  report_period       text,
  kind                text,
  sector              text,
  auditee             text,
  eik                 text,
  unp                 text,
  recommendation_text text,
  source_row          integer NOT NULL,
  document_id         text NOT NULL,
  document_page       integer NOT NULL,
  document_sha256     text NOT NULL,
  excerpt             text,
  excerpt_page        integer,
  result_text         text,
  violations_count    integer,
  detail_status       text,
  report_no           text,
  result_page         integer,
  row_sha256          text NOT NULL,
  CONSTRAINT inspection_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT inspection_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.inspection IS 'Ред от тримесечен списък на АДФИ: една приключила финансова инспекция, с пореден номер и страница в документа.';

CREATE TABLE kontrol_src.decision (
  load_id             bigint NOT NULL,
  record_id           text NOT NULL,
  source              text NOT NULL,
  categories          text[] NOT NULL,
  title               text NOT NULL,
  url                 text NOT NULL,
  published_on        date,
  publication_year    text,
  report_period       text,
  kind                text,
  sector              text,
  auditee             text,
  eik                 text,
  unp                 text,
  recommendation_text text,
  case_no             text,
  source_case_id      text,
  act_date            date NOT NULL,
  act_no              text NOT NULL,
  law                 text,
  outcome_text        text,
  source_gap          text,
  pager_discrepancy   jsonb,
  row_sha256          text NOT NULL,
  CONSTRAINT decision_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT decision_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.decision IS 'Решение на КЗК от търсенето в регистъра, всички закони.';
COMMENT ON COLUMN kontrol_src.decision.pager_discrepancy IS 'Разминаване в брояча на страницата, както е в източника (суров запис): само на първия ред на последната страница.';

CREATE TABLE kontrol_src.document (
  load_id         bigint NOT NULL,
  record_id       text NOT NULL,
  url             text NOT NULL,
  document_sha256 text NOT NULL,
  text_available  boolean NOT NULL,
  excerpt         text,
  excerpt_page    integer,
  text_status     text NOT NULL,
  unp             text,
  fetch_url       text,
  fetch_method    text,
  CONSTRAINT document_pk PRIMARY KEY (load_id),
  CONSTRAINT document_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE kontrol_src.document IS 'Един прочетен документ (PDF, DOC) на запис: хешът му и каквото е прочетено от текстовия му слой.';

-- ---------------------------------------------------------------- clean tables
-- Built whole from the current loads in one transaction (store.build): the records of the five lists of the National
-- Audit Office merge into one row per document (parse.canonical), every other record is one row. An ЕИК points to
-- core.org; one that is not there stays NULL and is counted by the rule org_unmatched. private_fields names the texts
-- that are replaced in the serving layer (privacy.public_text: a personal number, an e-mail, a phone, a sole trader).

CREATE TABLE kontrol.audit_report (
  record_id                      text NOT NULL,
  source                         text NOT NULL,
  categories                     text[] NOT NULL,
  title                          text NOT NULL,
  url                            text NOT NULL,
  publication_year               text,
  report_period                  text,
  kind                           text,
  sector                         text,
  recommendation_text            text,
  eik                            text,
  unp                            text,
  source_discrepancy             text,
  source_kind_values             text[],
  source_sector_values           text[],
  source_publication_year_values text[],
  private_fields                 text[] NOT NULL DEFAULT '{}',
  CONSTRAINT audit_report_pk PRIMARY KEY (record_id),
  CONSTRAINT audit_report_eik_fk FOREIGN KEY (eik) REFERENCES core.org (eik)
);
COMMENT ON TABLE kontrol.audit_report IS 'Доклад на Сметната палата: одит, последващ контрол на препоръките или доклад след наказателно производство. Един ред на документ, с всички списъци, в които е.';
COMMENT ON COLUMN kontrol.audit_report.record_id IS 'Идентификаторът на документа в сайта на Сметната палата: nao:<номер>.';
COMMENT ON COLUMN kontrol.audit_report.source IS 'Първият списък, в който е документът.';
COMMENT ON COLUMN kontrol.audit_report.categories IS 'Всички списъци, в които е документът (nao, nao-mun, nao-crim, recommendations, recommendations-mun).';
COMMENT ON COLUMN kontrol.audit_report.title IS 'Заглавието на документа, както е в източника.';
COMMENT ON COLUMN kontrol.audit_report.url IS 'Адресът на документа в сайта на Сметната палата.';
COMMENT ON COLUMN kontrol.audit_report.publication_year IS 'Годината от филтъра на източника, в която е документът. NULL: различни години в различните списъци.';
COMMENT ON COLUMN kontrol.audit_report.report_period IS 'Периодът, както е в заглавието (дати от текста), не изчислен.';
COMMENT ON COLUMN kontrol.audit_report.kind IS 'Видът одит, както го пише източникът. NULL: не е посочен или е различен в различните списъци.';
COMMENT ON COLUMN kontrol.audit_report.sector IS 'Категорията, както я пише източникът.';
COMMENT ON COLUMN kontrol.audit_report.recommendation_text IS 'Текстът за изпълнението на препоръките, както е в източника.';
COMMENT ON COLUMN kontrol.audit_report.eik IS 'ЕИК на одитирания, само ако източникът го дава и е в core.org. Източникът днес не го дава.';
COMMENT ON COLUMN kontrol.audit_report.unp IS 'Уникалният номер на поръчка, само ако е проверен в текста.';
COMMENT ON COLUMN kontrol.audit_report.source_discrepancy IS 'Бележка, когато един документ има различни класификации в източника.';
COMMENT ON COLUMN kontrol.audit_report.source_kind_values IS 'Различните видове, които източникът дава на документа.';
COMMENT ON COLUMN kontrol.audit_report.source_sector_values IS 'Различните категории, които източникът дава на документа.';
COMMENT ON COLUMN kontrol.audit_report.source_publication_year_values IS 'Различните години, в които документът е в списъците.';
COMMENT ON COLUMN kontrol.audit_report.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

CREATE TABLE kontrol.adfi_report (
  record_id        text NOT NULL,
  source           text NOT NULL,
  categories       text[] NOT NULL,
  title            text NOT NULL,
  url              text NOT NULL,
  kind             text NOT NULL,
  auditee          text NOT NULL,
  eik              text,
  basis            text,
  published_on     date NOT NULL,
  publication_year text NOT NULL,
  updated_label    boolean NOT NULL,
  private_fields   text[] NOT NULL DEFAULT '{}',
  CONSTRAINT adfi_report_pk PRIMARY KEY (record_id),
  CONSTRAINT adfi_report_eik_fk FOREIGN KEY (eik) REFERENCES core.org (eik)
);
COMMENT ON TABLE kontrol.adfi_report IS 'Публикуван доклад от финансова инспекция на АДФИ.';
COMMENT ON COLUMN kontrol.adfi_report.record_id IS 'Идентификаторът на файла в сайта на АДФИ: adfi:<номер>.';
COMMENT ON COLUMN kontrol.adfi_report.source IS 'Винаги adfi.';
COMMENT ON COLUMN kontrol.adfi_report.categories IS 'Винаги {adfi}.';
COMMENT ON COLUMN kontrol.adfi_report.title IS 'Името на файла, както е в регистъра.';
COMMENT ON COLUMN kontrol.adfi_report.url IS 'Адресът на доклада (PDF).';
COMMENT ON COLUMN kontrol.adfi_report.kind IS 'Винаги „Публикуван доклад от финансова инспекция“.';
COMMENT ON COLUMN kontrol.adfi_report.auditee IS 'Обектът на инспекцията (организация), както е в регистъра.';
COMMENT ON COLUMN kontrol.adfi_report.eik IS 'ЕИК на обекта, само ако източникът го дава и е в core.org. Източникът днес не го дава.';
COMMENT ON COLUMN kontrol.adfi_report.basis IS 'Основанието за възлагане, както е в регистъра.';
COMMENT ON COLUMN kontrol.adfi_report.published_on IS 'Датата на публикуване в регистъра (не датата на приключване).';
COMMENT ON COLUMN kontrol.adfi_report.publication_year IS 'Годината на публикуване.';
COMMENT ON COLUMN kontrol.adfi_report.updated_label IS 'Вярно, когато обектът е отбелязан в регистъра като „актуализиран“.';
COMMENT ON COLUMN kontrol.adfi_report.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

CREATE TABLE kontrol.adfi_list (
  record_id         text NOT NULL,
  source            text NOT NULL,
  categories        text[] NOT NULL,
  title             text NOT NULL,
  url               text NOT NULL,
  kind              text NOT NULL,
  report_period     text NOT NULL,
  inspection_count  integer,
  inspection_status text NOT NULL,
  private_fields    text[] NOT NULL DEFAULT '{}',
  CONSTRAINT adfi_list_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE kontrol.adfi_list IS 'Тримесечен списък на приключилите финансови инспекции на АДФИ (PDF).';
COMMENT ON COLUMN kontrol.adfi_list.record_id IS 'adfi-list:<година>-Q<тримесечие>.';
COMMENT ON COLUMN kontrol.adfi_list.source IS 'Винаги adfi-history.';
COMMENT ON COLUMN kontrol.adfi_list.categories IS 'Винаги {adfi-history}.';
COMMENT ON COLUMN kontrol.adfi_list.title IS 'Надписът на връзката, както е в източника.';
COMMENT ON COLUMN kontrol.adfi_list.url IS 'Адресът на списъка (PDF).';
COMMENT ON COLUMN kontrol.adfi_list.kind IS 'Винаги „Тримесечен списък“.';
COMMENT ON COLUMN kontrol.adfi_list.report_period IS 'Тримесечието: 2024-Q2.';
COMMENT ON COLUMN kontrol.adfi_list.inspection_count IS 'Колко инспекции са прочетени от текстовия слой. NULL: форматът е само документ.';
COMMENT ON COLUMN kontrol.adfi_list.inspection_status IS 'Какво е проверено при четенето или защо списъкът е само документ.';
COMMENT ON COLUMN kontrol.adfi_list.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

CREATE TABLE kontrol.inspection (
  record_id        text NOT NULL,
  source           text NOT NULL,
  categories       text[] NOT NULL,
  list_id          text NOT NULL,
  source_row       integer NOT NULL,
  title            text NOT NULL,
  url              text NOT NULL,
  kind             text NOT NULL,
  auditee          text NOT NULL,
  eik              text,
  report_period    text NOT NULL,
  document_page    integer NOT NULL,
  document_sha256  text NOT NULL,
  excerpt          text,
  excerpt_page     integer,
  result_text      text,
  violations_count integer,
  detail_status    text,
  report_no        text,
  result_page      integer,
  private_fields   text[] NOT NULL DEFAULT '{}',
  CONSTRAINT inspection_pk PRIMARY KEY (record_id),
  CONSTRAINT inspection_list_fk FOREIGN KEY (list_id) REFERENCES kontrol.adfi_list (record_id),
  CONSTRAINT inspection_eik_fk FOREIGN KEY (eik) REFERENCES core.org (eik),
  CONSTRAINT inspection_row_uq UNIQUE (list_id, source_row)
);
COMMENT ON TABLE kontrol.inspection IS 'Приключила финансова инспекция: ред от тримесечен списък на АДФИ. Не е отделен документ.';
COMMENT ON COLUMN kontrol.inspection.record_id IS '<списък>:inspection:<пореден номер>.';
COMMENT ON COLUMN kontrol.inspection.source IS 'Винаги adfi-history.';
COMMENT ON COLUMN kontrol.inspection.categories IS 'Винаги {adfi-history}.';
COMMENT ON COLUMN kontrol.inspection.list_id IS 'Тримесечният списък, в който е редът.';
COMMENT ON COLUMN kontrol.inspection.source_row IS 'Поредният номер в списъка, както е в източника.';
COMMENT ON COLUMN kontrol.inspection.title IS 'Името на инспектирания обект.';
COMMENT ON COLUMN kontrol.inspection.url IS 'Адресът на списъка (PDF).';
COMMENT ON COLUMN kontrol.inspection.kind IS 'Винаги „Приключила финансова инспекция“.';
COMMENT ON COLUMN kontrol.inspection.auditee IS 'Инспектираният обект (организация), както е в списъка.';
COMMENT ON COLUMN kontrol.inspection.eik IS 'ЕИК на обекта, само ако източникът го дава и е в core.org. Източникът днес не го дава.';
COMMENT ON COLUMN kontrol.inspection.report_period IS 'Тримесечието на списъка.';
COMMENT ON COLUMN kontrol.inspection.document_page IS 'Страницата в PDF, на която е редът.';
COMMENT ON COLUMN kontrol.inspection.document_sha256 IS 'sha256 на прочетения PDF.';
COMMENT ON COLUMN kontrol.inspection.excerpt IS 'Клетката от таблицата, както е в текстовия слой.';
COMMENT ON COLUMN kontrol.inspection.excerpt_page IS 'Страницата на откъса.';
COMMENT ON COLUMN kontrol.inspection.result_text IS 'Резултатът от подробните записи, само когато всички редове са съпоставени по пореден номер.';
COMMENT ON COLUMN kontrol.inspection.violations_count IS 'Броят установени нарушения от подробните записи. NULL: не е съпоставен, не нула.';
COMMENT ON COLUMN kontrol.inspection.detail_status IS 'Защо подробните резултати не са съпоставени.';
COMMENT ON COLUMN kontrol.inspection.report_no IS 'Номерът на доклада от подробните записи.';
COMMENT ON COLUMN kontrol.inspection.result_page IS 'Страницата на подробния резултат.';
COMMENT ON COLUMN kontrol.inspection.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

CREATE TABLE kontrol.decision (
  record_id        text NOT NULL,
  source           text NOT NULL,
  categories       text[] NOT NULL,
  title            text NOT NULL,
  url              text NOT NULL,
  kind             text NOT NULL,
  act_no           text NOT NULL,
  act_date         date NOT NULL,
  case_no          text,
  source_case_id   text,
  published_on     date,
  publication_year text,
  law              text,
  outcome_text     text,
  unp              text,
  source_gap       text,
  private_fields   text[] NOT NULL DEFAULT '{}',
  CONSTRAINT decision_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE kontrol.decision IS 'Решение на Комисията за защита на конкуренцията.';
COMMENT ON COLUMN kontrol.decision.record_id IS 'cpc:<производство>:decision:<номер на акта>:<дата на решението>.';
COMMENT ON COLUMN kontrol.decision.source IS 'Винаги cpc.';
COMMENT ON COLUMN kontrol.decision.categories IS 'Винаги {cpc}.';
COMMENT ON COLUMN kontrol.decision.title IS 'Номерът на акта, както е в регистъра.';
COMMENT ON COLUMN kontrol.decision.url IS 'Адресът на производството в регистъра на КЗК.';
COMMENT ON COLUMN kontrol.decision.kind IS 'Винаги „Решение“.';
COMMENT ON COLUMN kontrol.decision.act_no IS 'Номерът на акта.';
COMMENT ON COLUMN kontrol.decision.act_date IS 'Датата на решението.';
COMMENT ON COLUMN kontrol.decision.case_no IS 'Номерът на производството. NULL: източникът пише „-“.';
COMMENT ON COLUMN kontrol.decision.source_case_id IS 'Вътрешният идентификатор на производството в регистъра.';
COMMENT ON COLUMN kontrol.decision.published_on IS 'Датата на публикуване в регистъра.';
COMMENT ON COLUMN kontrol.decision.publication_year IS 'Годината на публикуване.';
COMMENT ON COLUMN kontrol.decision.law IS 'Видът производство (законът), както е в регистъра.';
COMMENT ON COLUMN kontrol.decision.outcome_text IS 'Произнасянето, само надписът; подробностите за страните не се пазят.';
COMMENT ON COLUMN kontrol.decision.unp IS 'Уникалният номер на поръчка, само ако в реда е точно един.';
COMMENT ON COLUMN kontrol.decision.source_gap IS 'Бележка, когато източникът не посочва производство.';
COMMENT ON COLUMN kontrol.decision.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

CREATE TABLE kontrol.document (
  record_id       text NOT NULL,
  url             text NOT NULL,
  document_sha256 text NOT NULL,
  text_available  boolean NOT NULL,
  excerpt         text,
  excerpt_page    integer,
  text_status     text NOT NULL,
  unp             text,
  fetch_url       text,
  fetch_method    text,
  read_at         timestamptz NOT NULL,
  private_fields  text[] NOT NULL DEFAULT '{}',
  CONSTRAINT document_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE kontrol.document IS 'Последно прочетеният документ на запис: хеш, текстов слой и кратък проверен откъс.';
COMMENT ON COLUMN kontrol.document.record_id IS 'Записът, на който е документът (доклад, списък, решение).';
COMMENT ON COLUMN kontrol.document.url IS 'Адресът на записа, за който е прочетен документът.';
COMMENT ON COLUMN kontrol.document.document_sha256 IS 'sha256 на прочетения файл.';
COMMENT ON COLUMN kontrol.document.text_available IS 'Вярно, когато файлът има текстов слой (без OCR).';
COMMENT ON COLUMN kontrol.document.excerpt IS 'Кратък откъс от първата страница: предметът и периодът на одита или заглавието на решението. Без имена.';
COMMENT ON COLUMN kontrol.document.excerpt_page IS 'Страницата на откъса.';
COMMENT ON COLUMN kontrol.document.text_status IS 'Какво е прочетено от документа или защо е само документ.';
COMMENT ON COLUMN kontrol.document.unp IS 'Уникалният номер на поръчка, само ако в текста е точно един.';
COMMENT ON COLUMN kontrol.document.fetch_url IS 'Адресът, от който е изтеглен файлът, когато е различен (бутонът в регистъра на КЗК).';
COMMENT ON COLUMN kontrol.document.fetch_method IS 'Как е изтеглен файлът, когато не е с обикновено GET.';
COMMENT ON COLUMN kontrol.document.read_at IS 'Кога е прочетен последно.';
COMMENT ON COLUMN kontrol.document.private_fields IS 'Текстовете, които не стигат до слоя за показване, защото съдържат лични данни.';

-- ---------------------------------------------------------------- views over the ledger

CREATE VIEW kontrol.record_version AS
SELECT x.record_id, x.row_sha256 AS sha256, min(l.loaded_at) AS detected_at
FROM (SELECT load_id, record_id, row_sha256 FROM kontrol_src.audit_report
      UNION ALL SELECT load_id, record_id, row_sha256 FROM kontrol_src.adfi_report
      UNION ALL SELECT load_id, record_id, row_sha256 FROM kontrol_src.adfi_list
      UNION ALL SELECT load_id, record_id, row_sha256 FROM kontrol_src.inspection
      UNION ALL SELECT load_id, record_id, row_sha256 FROM kontrol_src.decision) x
JOIN ops.load l USING (load_id)
WHERE l.tool = 'kontrol' AND l.status IN ('loaded', 'confirmed')
GROUP BY x.record_id, x.row_sha256;
COMMENT ON VIEW kontrol.record_version IS 'Всяка различна версия на записа, с кога е видяна за пръв път (неизменяемите версии на метаданните).';

CREATE VIEW kontrol.document_version AS
SELECT l.ref AS record_id, f.sha256, min(l.loaded_at) AS fetched_at
FROM ops.load l JOIN ops.raw_file f USING (raw_file_id)
WHERE l.tool = 'kontrol' AND l.source = 'document' AND l.status IN ('loaded', 'confirmed', 'unchanged')
GROUP BY l.ref, f.sha256;
COMMENT ON VIEW kontrol.document_version IS 'Всеки различен прочетен файл на документ, с кога е изтеглен за пръв път.';

CREATE VIEW kontrol.source_state AS
WITH last AS (
  SELECT DISTINCT ON (l.source) l.source, l.status, l.reason
  FROM ops.load l WHERE l.tool = 'kontrol' AND l.source <> 'document'
  ORDER BY l.source, l.loaded_at DESC, l.load_id DESC),
ok AS (
  SELECT source, max(loaded_at) AS read_at FROM ops.load
  WHERE tool = 'kontrol' AND source <> 'document' AND status IN ('loaded', 'confirmed', 'unchanged') GROUP BY source),
cur AS (
  SELECT l.source, r.unique_count, r.pages, r.source_count, r.scope
  FROM ops.load l JOIN kontrol_src.read r USING (load_id) WHERE l.tool = 'kontrol' AND l.is_current)
SELECT s.source, s.title_bg AS label, s.url,
       CASE WHEN last.status IS NULL THEN 'Няма доказан пълен прочит'
            WHEN last.status IN ('loaded', 'confirmed', 'unchanged') THEN 'наред'
            WHEN last.status = 'held' THEN 'задържан до второ четене'
            ELSE 'неуспешно четене' END AS status,
       CASE WHEN last.status = 'rejected' THEN last.reason END AS error,
       ok.read_at, cur.unique_count AS row_count, cur.pages AS page_count, cur.source_count, cur.scope,
       d.licence AS license
FROM ops.source s
LEFT JOIN last USING (source) LEFT JOIN ok USING (source) LEFT JOIN cur USING (source)
LEFT JOIN core.dataset d ON d.dataset = s.dataset
WHERE s.tool = 'kontrol' AND s.source <> 'document';
COMMENT ON VIEW kontrol.source_state IS 'Последното четене на всеки списък: състояние, кога е последният пълен прочит и какво е прочетено в него.';

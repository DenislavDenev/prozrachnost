-- The serving layer of Одити и контрол: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()) and points nowhere outside pub*.
-- A text that names a person by a personal number, an e-mail, a phone or a sole trader is replaced here
-- (kontrol.*.private_fields); the source text stays on the ingest side.
DO $$ BEGIN IF to_regnamespace('pub_kontrol') IS NULL THEN CREATE SCHEMA pub_kontrol; END IF; END $$;
COMMENT ON SCHEMA pub_kontrol IS 'Одити и контрол, слой за показване: записите на Сметната палата, АДФИ и КЗК, готови за страниците.';

CREATE TABLE pub_kontrol.audit_report (
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
  CONSTRAINT audit_report_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.audit_report IS 'Доклад на Сметната палата, един ред на документ, с всички списъци, в които е.';

CREATE TABLE pub_kontrol.adfi_report (
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
  CONSTRAINT adfi_report_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.adfi_report IS 'Публикуван доклад от финансова инспекция на АДФИ.';

CREATE TABLE pub_kontrol.adfi_list (
  record_id         text NOT NULL,
  source            text NOT NULL,
  categories        text[] NOT NULL,
  title             text NOT NULL,
  url               text NOT NULL,
  kind              text NOT NULL,
  report_period     text NOT NULL,
  inspection_count  integer,
  inspection_status text NOT NULL,
  CONSTRAINT adfi_list_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.adfi_list IS 'Тримесечен списък на приключилите финансови инспекции на АДФИ.';

CREATE TABLE pub_kontrol.inspection (
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
  CONSTRAINT inspection_pk PRIMARY KEY (record_id),
  CONSTRAINT inspection_list_fk FOREIGN KEY (list_id) REFERENCES pub_kontrol.adfi_list (record_id) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_kontrol.inspection IS 'Приключила финансова инспекция: ред от тримесечен списък на АДФИ.';

CREATE TABLE pub_kontrol.decision (
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
  CONSTRAINT decision_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.decision IS 'Решение на Комисията за защита на конкуренцията.';

CREATE TABLE pub_kontrol.document (
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
  CONSTRAINT document_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.document IS 'Последно прочетеният документ на запис: хеш, текстов слой и кратък проверен откъс.';

CREATE TABLE pub_kontrol.record_version (
  record_id   text NOT NULL,
  sha256      text NOT NULL,
  detected_at timestamptz NOT NULL,
  CONSTRAINT record_version_pk PRIMARY KEY (record_id, sha256)
);
COMMENT ON TABLE pub_kontrol.record_version IS 'Всяка различна версия на метаданните на запис, с кога е видяна за пръв път.';

CREATE TABLE pub_kontrol.document_version (
  record_id  text NOT NULL,
  sha256     text NOT NULL,
  url        text NOT NULL,
  fetched_at timestamptz NOT NULL,
  CONSTRAINT document_version_pk PRIMARY KEY (record_id, sha256)
);
COMMENT ON TABLE pub_kontrol.document_version IS 'Всеки различен прочетен файл на документ, с кога е изтеглен за пръв път.';

CREATE TABLE pub_kontrol.source_state (
  source       text NOT NULL,
  label        text NOT NULL,
  url          text,
  status       text NOT NULL,
  error        text,
  read_at      timestamptz,
  row_count    integer,
  page_count   integer,
  source_count integer,
  scope        text,
  license      text,
  CONSTRAINT source_state_pk PRIMARY KEY (source)
);
COMMENT ON TABLE pub_kontrol.source_state IS 'Последното четене на всеки списък: състояние, последен пълен прочит, брой, страници, обхват и лиценз.';

CREATE TABLE pub_kontrol.inspection_gap (
  record_id text NOT NULL,
  url       text NOT NULL,
  error     text NOT NULL,
  sha256    text NOT NULL,
  CONSTRAINT inspection_gap_pk PRIMARY KEY (record_id)
);
COMMENT ON TABLE pub_kontrol.inspection_gap IS 'Тримесечните списъци на АДФИ, чийто формат е само документ: защо и кой файл.';

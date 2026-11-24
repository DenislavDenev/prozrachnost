-- The serving layer of Държавна собственост: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()) and points nowhere outside pub*.
-- The members of boards are shown with their full name in their public role (pub.role_name, decided in
-- tools/danni/db/persons.toml); the name of a concessionaire who is not a legal entity is replaced here
-- (sobstvenost.notice.concessionaire_is_person); the source text stays on the ingest side.
DO $$ BEGIN IF to_regnamespace('pub_sobstvenost') IS NULL THEN CREATE SCHEMA pub_sobstvenost; END IF; END $$;
COMMENT ON SCHEMA pub_sobstvenost IS 'Държавна собственост, слой за показване: предприятията, бордовете по наблюдение, отчетите и концесиите, готови за страниците.';

CREATE TABLE pub_sobstvenost.enterprise (
  appk_id        text NOT NULL,
  name           text NOT NULL,
  status         text NOT NULL,
  share_text     text NOT NULL,
  share_pct      numeric,
  owner          text,
  participation  text NOT NULL,
  source_warning text,
  source_url     text NOT NULL,
  CONSTRAINT enterprise_pk PRIMARY KEY (appk_id)
);
COMMENT ON TABLE pub_sobstvenost.enterprise IS 'Публично предприятие в каталога на АППК.';

CREATE TABLE pub_sobstvenost.profile (
  appk_id      text NOT NULL,
  source_url   text NOT NULL,
  eik_original text NOT NULL,
  eik          text,
  kind         text NOT NULL,
  legal_form   text NOT NULL,
  seat         text NOT NULL,
  principal    text NOT NULL,
  kid          text NOT NULL,
  category     text NOT NULL,
  subject      text NOT NULL,
  standards    text NOT NULL,
  board_format text NOT NULL,
  report_count integer NOT NULL,
  CONSTRAINT profile_pk PRIMARY KEY (appk_id)
);
COMMENT ON TABLE pub_sobstvenost.profile IS 'Последно прочетеният профил на предприятие.';

CREATE TABLE pub_sobstvenost.profile_version (
  appk_id      text NOT NULL,
  sha256       text NOT NULL,
  observed_at  timestamptz NOT NULL,
  board_format text NOT NULL,
  CONSTRAINT profile_version_pk PRIMARY KEY (appk_id, sha256)
);
COMMENT ON TABLE pub_sobstvenost.profile_version IS 'Всяка различна версия на профил, с кога е видяна за пръв път.';

CREATE TABLE pub_sobstvenost.board_entry (
  appk_id     text NOT NULL,
  sha256      text NOT NULL,
  position    integer NOT NULL,
  member_name pub.role_name,
  country     text,
  entry       pub.role_name,
  CONSTRAINT board_entry_pk PRIMARY KEY (appk_id, sha256, position),
  CONSTRAINT board_entry_version_fk FOREIGN KEY (appk_id, sha256) REFERENCES pub_sobstvenost.profile_version (appk_id, sha256)
    DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.board_entry IS 'Членовете на органите на управление и контрол във всяка версия на профила, в публичната им роля.';

CREATE TABLE pub_sobstvenost.report (
  appk_id    text NOT NULL,
  record_id  text NOT NULL,
  report_id  text NOT NULL,
  kind       text NOT NULL,
  year       integer NOT NULL,
  quarter    integer,
  filed_on   date,
  title      text NOT NULL,
  source_url text NOT NULL,
  CONSTRAINT report_pk PRIMARY KEY (appk_id, record_id)
);
COMMENT ON TABLE pub_sobstvenost.report IS 'Отчет на предприятие по последния прочит.';

CREATE TABLE pub_sobstvenost.report_document (
  appk_id     text NOT NULL,
  record_id   text NOT NULL,
  position    integer NOT NULL,
  document_id text NOT NULL,
  title       text NOT NULL,
  url         text NOT NULL,
  CONSTRAINT report_document_pk PRIMARY KEY (appk_id, record_id, position),
  CONSTRAINT report_document_report_fk FOREIGN KEY (appk_id, record_id) REFERENCES pub_sobstvenost.report (appk_id, record_id)
    DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.report_document IS 'Документите на отчет, по реда на страницата.';

CREATE TABLE pub_sobstvenost.report_version (
  appk_id     text NOT NULL,
  record_id   text NOT NULL,
  sha256      text NOT NULL,
  observed_at timestamptz NOT NULL,
  report_id   text NOT NULL,
  kind        text NOT NULL,
  year        integer NOT NULL,
  CONSTRAINT report_version_pk PRIMARY KEY (appk_id, record_id, sha256)
);
COMMENT ON TABLE pub_sobstvenost.report_version IS 'Всяка различна версия на отчет, с кога е видяна за пръв път.';

CREATE TABLE pub_sobstvenost.report_version_document (
  appk_id     text NOT NULL,
  record_id   text NOT NULL,
  sha256      text NOT NULL,
  position    integer NOT NULL,
  document_id text NOT NULL,
  title       text NOT NULL,
  url         text NOT NULL,
  CONSTRAINT report_version_document_pk PRIMARY KEY (appk_id, record_id, sha256, position),
  CONSTRAINT report_version_document_version_fk FOREIGN KEY (appk_id, record_id, sha256)
    REFERENCES pub_sobstvenost.report_version (appk_id, record_id, sha256) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.report_version_document IS 'Документите във всяка версия на отчет.';

CREATE TABLE pub_sobstvenost.concession (
  concession_id    text NOT NULL,
  reg_number       text NOT NULL,
  status           text NOT NULL,
  procedure_status text NOT NULL,
  name             text NOT NULL,
  kind             text NOT NULL,
  subject          text NOT NULL,
  area_extent      text NOT NULL,
  conceder         text NOT NULL,
  related_id       text,
  related_reg      text,
  source_url       text NOT NULL,
  CONSTRAINT concession_pk PRIMARY KEY (concession_id)
);
COMMENT ON TABLE pub_sobstvenost.concession IS 'Партида в Националния концесионен регистър.';

CREATE TABLE pub_sobstvenost.concession_profile (
  concession_id text NOT NULL,
  source_url    text NOT NULL,
  CONSTRAINT concession_profile_pk PRIMARY KEY (concession_id)
);
COMMENT ON TABLE pub_sobstvenost.concession_profile IS 'Прочетена партида на концесия (документите и обявленията са в своите таблици).';

CREATE TABLE pub_sobstvenost.concession_document (
  concession_id text NOT NULL,
  position      integer NOT NULL,
  url           text NOT NULL,
  title         text NOT NULL,
  CONSTRAINT concession_document_pk PRIMARY KEY (concession_id, position),
  CONSTRAINT concession_document_profile_fk FOREIGN KEY (concession_id) REFERENCES pub_sobstvenost.concession_profile (concession_id)
    DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.concession_document IS 'Документите в партидата.';

CREATE TABLE pub_sobstvenost.notice (
  concession_id       text NOT NULL,
  position            integer NOT NULL,
  source_url          text NOT NULL,
  location_original   text NOT NULL,
  concessionaire_name text,
  concessionaire_eik  text,
  term_months         integer,
  CONSTRAINT notice_pk PRIMARY KEY (concession_id, position),
  CONSTRAINT notice_profile_fk FOREIGN KEY (concession_id) REFERENCES pub_sobstvenost.concession_profile (concession_id)
    DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.notice IS 'Обявление за възложена концесия. Името на концесионер, който не е юридическо лице, е заменено.';

CREATE TABLE pub_sobstvenost.notice_place (
  concession_id   text NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  oblast          text NOT NULL,
  municipality    text NOT NULL,
  CONSTRAINT notice_place_pk PRIMARY KEY (concession_id, notice_position, position),
  CONSTRAINT notice_place_notice_fk FOREIGN KEY (concession_id, notice_position) REFERENCES pub_sobstvenost.notice (concession_id, position)
    DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.notice_place IS 'Областите и общините в мястото на изпълнение, както са написани.';

CREATE TABLE pub_sobstvenost.notice_municipality (
  concession_id   text NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  name            text NOT NULL,
  CONSTRAINT notice_municipality_pk PRIMARY KEY (concession_id, notice_position, position),
  CONSTRAINT notice_municipality_notice_fk FOREIGN KEY (concession_id, notice_position)
    REFERENCES pub_sobstvenost.notice (concession_id, position) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_sobstvenost.notice_municipality IS 'Всяко „Община:“ в мястото на изпълнение.';

CREATE TABLE pub_sobstvenost.attachment (
  source        text NOT NULL,
  attachment_id text NOT NULL,
  sha256        text NOT NULL,
  first_seen    timestamptz NOT NULL,
  url           text NOT NULL,
  media_type    text NOT NULL,
  appk_id       text,
  concession_id text,
  CONSTRAINT attachment_pk PRIMARY KEY (source, attachment_id, sha256)
);
COMMENT ON TABLE pub_sobstvenost.attachment IS 'Всяка версия на архивиран оригинален файл: адрес, sha256, формат и кога е изтеглена за пръв път. Файлът не се преиздава.';

CREATE TABLE pub_sobstvenost.source_state (
  source       text NOT NULL,
  scope        text NOT NULL,
  status       text NOT NULL,
  last_read    timestamptz,
  last_success timestamptz,
  rows         integer,
  error        text,
  CONSTRAINT source_state_pk PRIMARY KEY (source, scope)
);
COMMENT ON TABLE pub_sobstvenost.source_state IS 'Последното четене на всеки обхват: каталози, профили, партиди, файлове.';

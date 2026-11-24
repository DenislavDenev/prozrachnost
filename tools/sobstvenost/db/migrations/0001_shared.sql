-- Държавна собственост in the shared database: the landing zone sobstvenost_src and the clean tables sobstvenost.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; organisations are core.org.
-- The schema of the old database of its own (JSON records in live.record, versions in ops.version) is kept in db/legacy.
-- A column of a landing table has the name of the field the parser gives (ingest/parse.py), with the source's
-- identifier as the key; store.py rebuilds the parser's record from these columns exactly, and row_sha256 is the
-- sha256 the old database gave the same record (its version).
DO $$ BEGIN
  IF to_regnamespace('sobstvenost_src') IS NULL THEN CREATE SCHEMA sobstvenost_src; END IF;
  IF to_regnamespace('sobstvenost') IS NULL THEN CREATE SCHEMA sobstvenost; END IF;
END $$;
COMMENT ON SCHEMA sobstvenost_src IS 'Държавна собственост, приемна зона: прочетеното от регистъра на АППК и от Националния концесионен регистър, по таблица за всеки вид запис.';
COMMENT ON SCHEMA sobstvenost IS 'Държавна собственост: публичните предприятия, профилите и бордовете им по наблюдение, отчетите, концесиите, обявленията и архивираните файлове.';

-- ---------------------------------------------------------------- landing: the catalogues

CREATE TABLE sobstvenost_src.read (
  load_id      bigint NOT NULL,
  pages        integer NOT NULL,
  row_count    integer NOT NULL,
  source_count integer NOT NULL,
  manifest     jsonb NOT NULL,
  CONSTRAINT read_pk PRIMARY KEY (load_id),
  CONSTRAINT read_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sobstvenost_src.read IS 'Един пълен обход на каталог (предприятията, министерствата, концесиите): колко страници и записа и броячът на източника.';
COMMENT ON COLUMN sobstvenost_src.read.source_count IS 'Броячът, който източникът сам печата: активните предприятия или министерства на началната страница на АППК, „Общо“ в търсенето на НКР.';
COMMENT ON COLUMN sobstvenost_src.read.manifest IS 'Описанието на обхода, както го дава четецът (суров запис до колоните).';

CREATE TABLE sobstvenost_src.enterprise (
  load_id        bigint NOT NULL,
  appk_id        text NOT NULL,
  name           text NOT NULL,
  status         text NOT NULL,
  share_text     text NOT NULL,
  share_pct      numeric,
  owner          text,
  participation  text NOT NULL,
  source_warning text,
  source_url     text NOT NULL,
  row_sha256     text NOT NULL,
  CONSTRAINT enterprise_pk PRIMARY KEY (load_id, appk_id),
  CONSTRAINT enterprise_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT enterprise_id_ck CHECK (appk_id ~ '^[0-9]+$'),
  CONSTRAINT enterprise_status_ck CHECK (status IN ('Активно', 'Неактивно')),
  CONSTRAINT enterprise_share_ck CHECK (share_pct BETWEEN 0 AND 100),
  CONSTRAINT enterprise_participation_ck CHECK (participation IN ('direct', 'owner_reported', 'unspecified', 'unknown'))
);
COMMENT ON TABLE sobstvenost_src.enterprise IS 'Предприятие от каталога на АППК: една карта от списъка.';
COMMENT ON COLUMN sobstvenost_src.enterprise.appk_id IS 'Номерът на предприятието в АППК (адресът CompanyDetails/<номер>).';
COMMENT ON COLUMN sobstvenost_src.enterprise.share_text IS '„Държавно участие“, както е в източника.';
COMMENT ON COLUMN sobstvenost_src.enterprise.share_pct IS 'Процентът от share_text, както е публикуван. NULL: няма процент (знакът за липса, бройка, проза), не нула.';
COMMENT ON COLUMN sobstvenost_src.enterprise.owner IS 'Текстът след процента: собственик или състояние, както е публикуван. Не доказва веригата на собственост.';
COMMENT ON COLUMN sobstvenost_src.enterprise.participation IS 'direct: държавно участие по текста; owner_reported: посочен собственик; unspecified: процент без собственик; unknown: няма процент.';
COMMENT ON COLUMN sobstvenost_src.enterprise.source_warning IS 'Бележка, когато публикуваните дялове надхвърлят 100%. Оригиналът не се поправя.';
COMMENT ON COLUMN sobstvenost_src.enterprise.row_sha256 IS 'sha256 на записа, както го дава парсерът (същото като версията в старата база).';

CREATE TABLE sobstvenost_src.ministry (
  load_id     bigint NOT NULL,
  ministry_id text NOT NULL,
  name        text NOT NULL,
  status      text NOT NULL,
  row_sha256  text NOT NULL,
  CONSTRAINT ministry_pk PRIMARY KEY (load_id, ministry_id),
  CONSTRAINT ministry_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sobstvenost_src.ministry IS 'Министерство (орган, упражняващ правата на държавата) от списъка на АППК.';

CREATE TABLE sobstvenost_src.concession (
  load_id          bigint NOT NULL,
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
  row_sha256       text NOT NULL,
  CONSTRAINT concession_pk PRIMARY KEY (load_id, concession_id),
  CONSTRAINT concession_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT concession_id_ck CHECK (concession_id ~ '^[0-9a-f-]{36}$')
);
COMMENT ON TABLE sobstvenost_src.concession IS 'Партида от търсенето в Националния концесионен регистър, всички видове.';
COMMENT ON COLUMN sobstvenost_src.concession.concession_id IS 'Идентификаторът (UUID) на партидата в НКР.';
COMMENT ON COLUMN sobstvenost_src.concession.related_id IS 'Свързана партида, когато регистърът посочва такава.';

-- ---------------------------------------------------------------- landing: one enterprise (profile and reports)

CREATE TABLE sobstvenost_src.profile (
  load_id        bigint NOT NULL,
  appk_id        text NOT NULL,
  source_url     text NOT NULL,
  eik_original   text NOT NULL,
  eik            text,
  kind           text NOT NULL,
  legal_form     text NOT NULL,
  seat           text NOT NULL,
  principal      text NOT NULL,
  kid            text NOT NULL,
  category       text NOT NULL,
  subject        text NOT NULL,
  standards      text NOT NULL,
  board_original core.person_name NOT NULL,
  board_format   text NOT NULL,
  report_count   integer NOT NULL,
  row_sha256     text NOT NULL,
  CONSTRAINT profile_pk PRIMARY KEY (load_id),
  CONSTRAINT profile_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT profile_eik_ck CHECK (eik ~ '^[0-9]{9}$'),
  CONSTRAINT profile_board_format_ck CHECK (board_format IN ('parsed_people', 'source_entries'))
);
COMMENT ON TABLE sobstvenost_src.profile IS 'Профилът на предприятие в АППК, един на прочит. Телефонът, имейлът и сайтът не се пазят.';
COMMENT ON COLUMN sobstvenost_src.profile.eik_original IS '„ЕИК/Булстат“, както е в източника.';
COMMENT ON COLUMN sobstvenost_src.profile.eik IS 'ЕИК, само когато минава контролната цифра.';
COMMENT ON COLUMN sobstvenost_src.profile.principal IS 'Органът, упражняващ правата на държавата, както е в профила.';
COMMENT ON COLUMN sobstvenost_src.profile.board_original IS 'Членовете на органите на управление и контрол: целият текст, както е в източника.';
COMMENT ON COLUMN sobstvenost_src.profile.board_format IS 'parsed_people: текстът е разделен на име и държава; source_entries: записите на източника са запазени цели, без да се отделят хора.';
COMMENT ON COLUMN sobstvenost_src.profile.report_count IS 'Колко отчета са прочетени за предприятието в този прочит.';

CREATE TABLE sobstvenost_src.board_entry (
  load_id     bigint NOT NULL,
  position    integer NOT NULL,
  member_name core.person_name,
  country     text,
  entry       core.person_name,
  CONSTRAINT board_entry_pk PRIMARY KEY (load_id, position),
  CONSTRAINT board_entry_profile_fk FOREIGN KEY (load_id) REFERENCES sobstvenost_src.profile (load_id),
  CONSTRAINT board_entry_one_ck CHECK ((member_name IS NULL) <> (entry IS NULL))
);
COMMENT ON TABLE sobstvenost_src.board_entry IS 'Член на органите на управление и контрол в един прочит на профил, по реда в източника.';
COMMENT ON COLUMN sobstvenost_src.board_entry.member_name IS 'Името, когато текстът е разделен по „Държава:“. Пази се както е.';
COMMENT ON COLUMN sobstvenost_src.board_entry.entry IS 'Записът на източника цял, когато не се дели на име и държава (може да съдържа повече лица).';

CREATE TABLE sobstvenost_src.report (
  load_id    bigint NOT NULL,
  record_id  text NOT NULL,
  appk_id    text NOT NULL,
  report_id  text NOT NULL,
  kind       text NOT NULL,
  year       integer NOT NULL,
  quarter    integer,
  filed_on   date,
  title      text NOT NULL,
  source_url text NOT NULL,
  row_sha256 text NOT NULL,
  CONSTRAINT report_pk PRIMARY KEY (load_id, record_id),
  CONSTRAINT report_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT report_kind_ck CHECK (kind IN ('annual', 'quarterly')),
  CONSTRAINT report_quarter_ck CHECK (kind = 'annual' AND quarter IS NULL OR kind = 'quarterly' AND quarter BETWEEN 1 AND 4)
);
COMMENT ON TABLE sobstvenost_src.report IS 'Годишен или тримесечен отчет на предприятие: метаданните от страницата на отчета. Финансовите числа не се извличат.';
COMMENT ON COLUMN sobstvenost_src.report.record_id IS '<вид>:<номер на отчета>:<година>:<тримесечие или annual>.';
COMMENT ON COLUMN sobstvenost_src.report.filed_on IS 'Датата на подаване, както е публикувана. NULL: няма публикувана дата.';

CREATE TABLE sobstvenost_src.report_document (
  load_id     bigint NOT NULL,
  record_id   text NOT NULL,
  position    integer NOT NULL,
  document_id text NOT NULL,
  title       text NOT NULL,
  url         text NOT NULL,
  CONSTRAINT report_document_pk PRIMARY KEY (load_id, record_id, position),
  CONSTRAINT report_document_report_fk FOREIGN KEY (load_id, record_id) REFERENCES sobstvenost_src.report (load_id, record_id)
);
COMMENT ON TABLE sobstvenost_src.report_document IS 'Документ (връзка за сваляне) в отчет, по реда на страницата.';

-- ---------------------------------------------------------------- landing: one concession (documents and notices)

CREATE TABLE sobstvenost_src.concession_profile (
  load_id       bigint NOT NULL,
  concession_id text NOT NULL,
  source_url    text NOT NULL,
  row_sha256    text NOT NULL,
  CONSTRAINT concession_profile_pk PRIMARY KEY (load_id),
  CONSTRAINT concession_profile_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sobstvenost_src.concession_profile IS 'Партидата на концесия в НКР, прочетена с документите и обявленията си.';

CREATE TABLE sobstvenost_src.concession_document (
  load_id  bigint NOT NULL,
  position integer NOT NULL,
  url      text NOT NULL,
  title    text NOT NULL,
  CONSTRAINT concession_document_pk PRIMARY KEY (load_id, position),
  CONSTRAINT concession_document_profile_fk FOREIGN KEY (load_id) REFERENCES sobstvenost_src.concession_profile (load_id)
);
COMMENT ON TABLE sobstvenost_src.concession_document IS 'Връзка към документ в партидата, по реда на страницата.';

CREATE TABLE sobstvenost_src.notice (
  load_id             bigint NOT NULL,
  position            integer NOT NULL,
  source_url          text NOT NULL,
  location_original   text NOT NULL,
  concessionaire_name text,
  concessionaire_eik  text,
  term_months         integer,
  CONSTRAINT notice_pk PRIMARY KEY (load_id, position),
  CONSTRAINT notice_profile_fk FOREIGN KEY (load_id) REFERENCES sobstvenost_src.concession_profile (load_id),
  CONSTRAINT notice_eik_ck CHECK (concessionaire_eik ~ '^[0-9]{9}$')
);
COMMENT ON TABLE sobstvenost_src.notice IS 'Обявление за възложена концесия (или актуалната информация за нея) в партидата.';
COMMENT ON COLUMN sobstvenost_src.notice.location_original IS 'Разделът за мястото на изпълнение, както е в обявлението.';
COMMENT ON COLUMN sobstvenost_src.notice.concessionaire_name IS 'Името на концесионера, както е в обявлението: дружество, едноличен търговец, земеделски производител или физическо лице.';
COMMENT ON COLUMN sobstvenost_src.notice.concessionaire_eik IS 'ЕИК на концесионера, само когато минава контролната цифра.';
COMMENT ON COLUMN sobstvenost_src.notice.term_months IS 'Срокът на концесията в месеци. NULL: обявлението не го дава.';

CREATE TABLE sobstvenost_src.notice_place (
  load_id         bigint NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  oblast          text NOT NULL,
  municipality    text NOT NULL,
  CONSTRAINT notice_place_pk PRIMARY KEY (load_id, notice_position, position),
  CONSTRAINT notice_place_notice_fk FOREIGN KEY (load_id, notice_position) REFERENCES sobstvenost_src.notice (load_id, position)
);
COMMENT ON TABLE sobstvenost_src.notice_place IS 'Двойка „Област, Община“ от мястото на изпълнение в обявлението.';

CREATE TABLE sobstvenost_src.notice_municipality (
  load_id         bigint NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  name            text NOT NULL,
  CONSTRAINT notice_municipality_pk PRIMARY KEY (load_id, notice_position, position),
  CONSTRAINT notice_municipality_notice_fk FOREIGN KEY (load_id, notice_position) REFERENCES sobstvenost_src.notice (load_id, position)
);
COMMENT ON TABLE sobstvenost_src.notice_municipality IS 'Всяко „Община:“ в мястото на изпълнение, както е написано.';

-- ---------------------------------------------------------------- landing: complete passes and archived files

CREATE TABLE sobstvenost_src.full_read (
  load_id      bigint NOT NULL,
  started_at   timestamptz NOT NULL,
  entity_count integer NOT NULL,
  CONSTRAINT full_read_pk PRIMARY KEY (load_id),
  CONSTRAINT full_read_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE sobstvenost_src.full_read IS 'Завършен пълен прочит на профилите (АППК), на партидите (НКР) или на файловете: кога е започнал и колко записа е прочел без отказан.';

CREATE TABLE sobstvenost_src.attachment (
  load_id       bigint NOT NULL,
  attachment_id text NOT NULL,
  source        text NOT NULL,
  url           text NOT NULL,
  sha256        text NOT NULL,
  media_type    text NOT NULL,
  bytes         bigint NOT NULL,
  title         text NOT NULL,
  report_id     text,
  year          integer,
  appk_id       text,
  concession_id text,
  CONSTRAINT attachment_pk PRIMARY KEY (load_id),
  CONSTRAINT attachment_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT attachment_source_ck CHECK (source = 'appk' AND appk_id IS NOT NULL OR source = 'ncr' AND concession_id IS NOT NULL),
  CONSTRAINT attachment_media_ck CHECK (media_type IN ('application/pdf', 'application/zip', 'application/x-ole-storage'))
);
COMMENT ON TABLE sobstvenost_src.attachment IS 'Архивиран оригинален файл: документ от последния годишен отчет на предприятие или от партида на концесия. Самият файл е в ops.raw_file.';
COMMENT ON COLUMN sobstvenost_src.attachment.media_type IS 'Действителният формат по първите байтове (PDF, ZIP, OLE), не по името.';

-- ---------------------------------------------------------------- clean tables
-- Built whole from the current loads in one transaction (store.build). An ЕИК points to core.org through org_eik;
-- one that is not there leaves org_eik empty and is counted by the rule org_unmatched. The ЕИК as published (with a
-- valid check digit) stays in eik.

CREATE TABLE sobstvenost.ministry (
  ministry_id text NOT NULL,
  name        text NOT NULL,
  status      text NOT NULL,
  CONSTRAINT ministry_pk PRIMARY KEY (ministry_id)
);
COMMENT ON TABLE sobstvenost.ministry IS 'Министерствата в списъка на АППК.';
COMMENT ON COLUMN sobstvenost.ministry.ministry_id IS 'Номерът в АППК (MinistryDetails/<номер>).';
COMMENT ON COLUMN sobstvenost.ministry.name IS 'Името, както е в списъка.';
COMMENT ON COLUMN sobstvenost.ministry.status IS 'Активно или Неактивно.';

CREATE TABLE sobstvenost.enterprise (
  appk_id        text NOT NULL,
  name           text NOT NULL,
  status         text NOT NULL,
  share_text     text NOT NULL,
  share_pct      numeric,
  share          numeric(9,6) GENERATED ALWAYS AS (share_pct / 100) STORED,
  owner          text,
  participation  text NOT NULL,
  source_warning text,
  source_url     text NOT NULL,
  CONSTRAINT enterprise_pk PRIMARY KEY (appk_id)
);
COMMENT ON TABLE sobstvenost.enterprise IS 'Публично предприятие в каталога на АППК, по действащия прочит.';
COMMENT ON COLUMN sobstvenost.enterprise.appk_id IS 'Номерът на предприятието в АППК.';
COMMENT ON COLUMN sobstvenost.enterprise.name IS 'Името, както е в каталога.';
COMMENT ON COLUMN sobstvenost.enterprise.status IS 'Активно или Неактивно, както е в каталога.';
COMMENT ON COLUMN sobstvenost.enterprise.share_text IS '„Държавно участие“, както е в източника.';
COMMENT ON COLUMN sobstvenost.enterprise.share_pct IS 'Публикуваният процент, както е написан (за показване). NULL: няма процент, не нула.';
COMMENT ON COLUMN sobstvenost.enterprise.share IS 'Същият процент като дял от 0 до 1.';
COMMENT ON COLUMN sobstvenost.enterprise.owner IS 'Текстът след процента: собственик или състояние. Не доказва веригата на собственост.';
COMMENT ON COLUMN sobstvenost.enterprise.participation IS 'direct, owner_reported, unspecified или unknown (виж приемната зона).';
COMMENT ON COLUMN sobstvenost.enterprise.source_warning IS 'Бележка, когато публикуваните дялове надхвърлят 100%.';
COMMENT ON COLUMN sobstvenost.enterprise.source_url IS 'Профилът в АППК.';

CREATE TABLE sobstvenost.profile (
  appk_id      text NOT NULL,
  source_url   text NOT NULL,
  eik_original text NOT NULL,
  eik          text,
  org_eik      text,
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
  read_at      timestamptz NOT NULL,
  CONSTRAINT profile_pk PRIMARY KEY (appk_id),
  CONSTRAINT profile_org_fk FOREIGN KEY (org_eik) REFERENCES core.org (eik)
);
CREATE INDEX profile_org_ix ON sobstvenost.profile (org_eik);
COMMENT ON TABLE sobstvenost.profile IS 'Последно прочетеният профил на всяко предприятие (и на излязло от каталога).';
COMMENT ON COLUMN sobstvenost.profile.appk_id IS 'Номерът на предприятието в АППК.';
COMMENT ON COLUMN sobstvenost.profile.source_url IS 'Профилът в АППК.';
COMMENT ON COLUMN sobstvenost.profile.eik_original IS '„ЕИК/Булстат“, както е в източника.';
COMMENT ON COLUMN sobstvenost.profile.eik IS 'ЕИК с вярна контролна цифра.';
COMMENT ON COLUMN sobstvenost.profile.org_eik IS 'Същият ЕИК, когато е в core.org (връзката с другите инструменти).';
COMMENT ON COLUMN sobstvenost.profile.kind IS '„Вид“ на предприятието, както е в профила.';
COMMENT ON COLUMN sobstvenost.profile.legal_form IS 'Правната форма.';
COMMENT ON COLUMN sobstvenost.profile.seat IS 'Седалище и адрес на управление на юридическото лице.';
COMMENT ON COLUMN sobstvenost.profile.principal IS 'Органът, упражняващ правата на държавата (принципалът). Не е непременно акционерът.';
COMMENT ON COLUMN sobstvenost.profile.kid IS 'Кодът и името на дейността по КИД 2008.';
COMMENT ON COLUMN sobstvenost.profile.category IS 'Категорията по Закона за счетоводството.';
COMMENT ON COLUMN sobstvenost.profile.subject IS 'Предметът на дейност.';
COMMENT ON COLUMN sobstvenost.profile.standards IS 'Счетоводните стандарти, които предприятието прилага.';
COMMENT ON COLUMN sobstvenost.profile.board_format IS 'parsed_people или source_entries (виж приемната зона).';
COMMENT ON COLUMN sobstvenost.profile.report_count IS 'Колко отчета има в прочита.';
COMMENT ON COLUMN sobstvenost.profile.read_at IS 'Кога е прочетен профилът.';

CREATE TABLE sobstvenost.report (
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
COMMENT ON TABLE sobstvenost.report IS 'Отчет на предприятие по последния прочит на профила му.';
COMMENT ON COLUMN sobstvenost.report.appk_id IS 'Предприятието.';
COMMENT ON COLUMN sobstvenost.report.record_id IS '<вид>:<номер на отчета>:<година>:<тримесечие или annual>.';
COMMENT ON COLUMN sobstvenost.report.report_id IS 'Номерът на отчета в АППК.';
COMMENT ON COLUMN sobstvenost.report.kind IS 'annual или quarterly.';
COMMENT ON COLUMN sobstvenost.report.year IS 'Отчетната година.';
COMMENT ON COLUMN sobstvenost.report.quarter IS 'Тримесечието; NULL при годишен отчет.';
COMMENT ON COLUMN sobstvenost.report.filed_on IS 'Датата на подаване. NULL: не е публикувана.';
COMMENT ON COLUMN sobstvenost.report.title IS 'Заглавието на страницата на отчета.';
COMMENT ON COLUMN sobstvenost.report.source_url IS 'Страницата на отчета в АППК.';

CREATE TABLE sobstvenost.report_document (
  appk_id     text NOT NULL,
  record_id   text NOT NULL,
  position    integer NOT NULL,
  document_id text NOT NULL,
  title       text NOT NULL,
  url         text NOT NULL,
  CONSTRAINT report_document_pk PRIMARY KEY (appk_id, record_id, position),
  CONSTRAINT report_document_report_fk FOREIGN KEY (appk_id, record_id) REFERENCES sobstvenost.report (appk_id, record_id)
);
COMMENT ON TABLE sobstvenost.report_document IS 'Документите на отчет, по реда на страницата.';
COMMENT ON COLUMN sobstvenost.report_document.appk_id IS 'Предприятието.';
COMMENT ON COLUMN sobstvenost.report_document.record_id IS 'Отчетът.';
COMMENT ON COLUMN sobstvenost.report_document.position IS 'Мястото на документа на страницата, от 1.';
COMMENT ON COLUMN sobstvenost.report_document.document_id IS 'Номерът за сваляне в АППК (Download/<номер>).';
COMMENT ON COLUMN sobstvenost.report_document.title IS 'Видът на документа, както е на страницата.';
COMMENT ON COLUMN sobstvenost.report_document.url IS 'Адресът за сваляне.';

CREATE TABLE sobstvenost.concession (
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
COMMENT ON TABLE sobstvenost.concession IS 'Партида в Националния концесионен регистър, по действащия прочит на търсенето.';
COMMENT ON COLUMN sobstvenost.concession.concession_id IS 'Идентификаторът (UUID) на партидата.';
COMMENT ON COLUMN sobstvenost.concession.reg_number IS 'Номерът на партидата.';
COMMENT ON COLUMN sobstvenost.concession.status IS 'Статусът на партидата.';
COMMENT ON COLUMN sobstvenost.concession.procedure_status IS 'Статусът на процедурата.';
COMMENT ON COLUMN sobstvenost.concession.name IS 'Наименованието на концесията.';
COMMENT ON COLUMN sobstvenost.concession.kind IS 'Видът: държавна, общинска, съвместна.';
COMMENT ON COLUMN sobstvenost.concession.subject IS 'Предметът: строителство, услуги, ползване и т.н.';
COMMENT ON COLUMN sobstvenost.concession.area_extent IS 'Обхватът на територията, както е в регистъра.';
COMMENT ON COLUMN sobstvenost.concession.conceder IS 'Концедентът.';
COMMENT ON COLUMN sobstvenost.concession.related_id IS 'Свързана партида (UUID), когато има.';
COMMENT ON COLUMN sobstvenost.concession.related_reg IS 'Номерът на свързаната партида.';
COMMENT ON COLUMN sobstvenost.concession.source_url IS 'Партидата в НКР.';

CREATE TABLE sobstvenost.concession_profile (
  concession_id text NOT NULL,
  source_url    text NOT NULL,
  read_at       timestamptz NOT NULL,
  CONSTRAINT concession_profile_pk PRIMARY KEY (concession_id)
);
COMMENT ON TABLE sobstvenost.concession_profile IS 'Последно прочетената партида на всяка концесия (документи и обявления).';
COMMENT ON COLUMN sobstvenost.concession_profile.concession_id IS 'Партидата.';
COMMENT ON COLUMN sobstvenost.concession_profile.source_url IS 'Партидата в НКР.';
COMMENT ON COLUMN sobstvenost.concession_profile.read_at IS 'Кога е прочетена.';

CREATE TABLE sobstvenost.concession_document (
  concession_id text NOT NULL,
  position      integer NOT NULL,
  url           text NOT NULL,
  title         text NOT NULL,
  CONSTRAINT concession_document_pk PRIMARY KEY (concession_id, position),
  CONSTRAINT concession_document_profile_fk FOREIGN KEY (concession_id) REFERENCES sobstvenost.concession_profile (concession_id)
);
COMMENT ON TABLE sobstvenost.concession_document IS 'Документите в партидата, по реда на страницата.';
COMMENT ON COLUMN sobstvenost.concession_document.concession_id IS 'Партидата.';
COMMENT ON COLUMN sobstvenost.concession_document.position IS 'Мястото на страницата, от 1.';
COMMENT ON COLUMN sobstvenost.concession_document.url IS 'Адресът за сваляне.';
COMMENT ON COLUMN sobstvenost.concession_document.title IS 'Текстът на връзката.';

CREATE TABLE sobstvenost.notice (
  concession_id            text NOT NULL,
  position                 integer NOT NULL,
  source_url               text NOT NULL,
  location_original        text NOT NULL,
  concessionaire_name      text,
  concessionaire_is_person boolean NOT NULL,
  concessionaire_eik       text,
  org_eik                  text,
  term_months              integer,
  CONSTRAINT notice_pk PRIMARY KEY (concession_id, position),
  CONSTRAINT notice_profile_fk FOREIGN KEY (concession_id) REFERENCES sobstvenost.concession_profile (concession_id),
  CONSTRAINT notice_org_fk FOREIGN KEY (org_eik) REFERENCES core.org (eik)
);
CREATE INDEX notice_org_ix ON sobstvenost.notice (org_eik);
COMMENT ON TABLE sobstvenost.notice IS 'Обявление за възложена концесия в партидата.';
COMMENT ON COLUMN sobstvenost.notice.concession_id IS 'Партидата.';
COMMENT ON COLUMN sobstvenost.notice.position IS 'Мястото на обявлението в партидата, от 1.';
COMMENT ON COLUMN sobstvenost.notice.source_url IS 'Обявлението в НКР.';
COMMENT ON COLUMN sobstvenost.notice.location_original IS 'Мястото на изпълнение, както е в обявлението.';
COMMENT ON COLUMN sobstvenost.notice.concessionaire_name IS 'Името на концесионера, както е в обявлението.';
COMMENT ON COLUMN sobstvenost.notice.concessionaire_is_person IS 'Вярно, когато името не е на дружество, община или друго юридическо лице (едноличен търговец, земеделски производител, физическо лице): тогава името не стига до слоя за показване.';
COMMENT ON COLUMN sobstvenost.notice.concessionaire_eik IS 'ЕИК на концесионера с вярна контролна цифра.';
COMMENT ON COLUMN sobstvenost.notice.org_eik IS 'Същият ЕИК, когато е в core.org.';
COMMENT ON COLUMN sobstvenost.notice.term_months IS 'Срокът в месеци. NULL: обявлението не го дава.';

CREATE TABLE sobstvenost.notice_place (
  concession_id   text NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  oblast          text NOT NULL,
  municipality    text NOT NULL,
  CONSTRAINT notice_place_pk PRIMARY KEY (concession_id, notice_position, position),
  CONSTRAINT notice_place_notice_fk FOREIGN KEY (concession_id, notice_position) REFERENCES sobstvenost.notice (concession_id, position)
);
COMMENT ON TABLE sobstvenost.notice_place IS 'Областите и общините в мястото на изпълнение, както са написани. Съпоставят се с общините на картата по област и име, без предположения.';
COMMENT ON COLUMN sobstvenost.notice_place.concession_id IS 'Партидата.';
COMMENT ON COLUMN sobstvenost.notice_place.notice_position IS 'Обявлението.';
COMMENT ON COLUMN sobstvenost.notice_place.position IS 'Мястото на двойката в текста, от 1.';
COMMENT ON COLUMN sobstvenost.notice_place.oblast IS 'Областта, както е написана.';
COMMENT ON COLUMN sobstvenost.notice_place.municipality IS 'Общината, както е написана.';

CREATE TABLE sobstvenost.notice_municipality (
  concession_id   text NOT NULL,
  notice_position integer NOT NULL,
  position        integer NOT NULL,
  name            text NOT NULL,
  CONSTRAINT notice_municipality_pk PRIMARY KEY (concession_id, notice_position, position),
  CONSTRAINT notice_municipality_notice_fk FOREIGN KEY (concession_id, notice_position) REFERENCES sobstvenost.notice (concession_id, position)
);
COMMENT ON TABLE sobstvenost.notice_municipality IS 'Всяко „Община:“ в мястото на изпълнение, както е написано.';
COMMENT ON COLUMN sobstvenost.notice_municipality.concession_id IS 'Партидата.';
COMMENT ON COLUMN sobstvenost.notice_municipality.notice_position IS 'Обявлението.';
COMMENT ON COLUMN sobstvenost.notice_municipality.position IS 'Мястото в текста, от 1.';
COMMENT ON COLUMN sobstvenost.notice_municipality.name IS 'Името на общината, както е написано.';

-- ---------------------------------------------------------------- versions: views over the landing zone
-- A version is a record at the time it was first seen; the first load that carried it holds its rows.

CREATE VIEW sobstvenost.profile_version AS
SELECT DISTINCT ON (p.appk_id, p.row_sha256) p.appk_id, p.row_sha256 AS sha256, l.loaded_at AS observed_at, p.board_format, p.load_id
FROM sobstvenost_src.profile p JOIN ops.load l USING (load_id)
WHERE l.tool = 'sobstvenost' AND l.status IN ('loaded', 'confirmed')
ORDER BY p.appk_id, p.row_sha256, l.loaded_at, l.load_id;
COMMENT ON VIEW sobstvenost.profile_version IS 'Всяка различна версия на профил, с кога е видяна за пръв път: така се пази бордът по наблюдение.';

CREATE VIEW sobstvenost.board_entry AS
SELECT v.appk_id, v.sha256, b.position, b.member_name, b.country, b.entry
FROM sobstvenost.profile_version v JOIN sobstvenost_src.board_entry b USING (load_id);
COMMENT ON VIEW sobstvenost.board_entry IS 'Бордът във всяка версия на профила, по реда в източника.';

CREATE VIEW sobstvenost.report_version AS
SELECT DISTINCT ON (r.appk_id, r.record_id, r.row_sha256) r.appk_id, r.record_id, r.row_sha256 AS sha256, l.loaded_at AS observed_at,
       r.report_id, r.kind, r.year, r.load_id
FROM sobstvenost_src.report r JOIN ops.load l USING (load_id)
WHERE l.tool = 'sobstvenost' AND l.status IN ('loaded', 'confirmed')
ORDER BY r.appk_id, r.record_id, r.row_sha256, l.loaded_at, l.load_id;
COMMENT ON VIEW sobstvenost.report_version IS 'Всяка различна версия на отчет (метаданни и документни връзки), с кога е видяна за пръв път.';

CREATE VIEW sobstvenost.report_version_document AS
SELECT v.appk_id, v.record_id, v.sha256, d.position, d.document_id, d.title, d.url
FROM sobstvenost.report_version v JOIN sobstvenost_src.report_document d USING (load_id, record_id);
COMMENT ON VIEW sobstvenost.report_version_document IS 'Документите във всяка версия на отчет.';

CREATE VIEW sobstvenost.attachment AS
SELECT DISTINCT ON (a.source, a.attachment_id, a.sha256) a.source, a.attachment_id, a.sha256, l.loaded_at AS first_seen, a.url,
       a.media_type, a.bytes, a.appk_id, a.concession_id
FROM sobstvenost_src.attachment a JOIN ops.load l USING (load_id)
WHERE l.tool = 'sobstvenost' AND l.status IN ('loaded', 'confirmed')
ORDER BY a.source, a.attachment_id, a.sha256, l.loaded_at, l.load_id;
COMMENT ON VIEW sobstvenost.attachment IS 'Всяка различна версия на архивиран файл (по sha256), с кога е изтеглена за пръв път.';

-- The state of each read as the sources page shows it: the catalogues by their last load; the profiles, the
-- concessions and the files by their last complete pass (a load of sobstvenost_src.full_read) and the reads after it.
CREATE VIEW sobstvenost.source_state AS
WITH l AS (SELECT source, ref, status, reason, loaded_at, load_id, is_current FROM ops.load WHERE tool = 'sobstvenost'),
cat AS (
  SELECT DISTINCT ON (source, ref) source, ref AS scope, status, reason, loaded_at
  FROM l WHERE source IN ('appk', 'ncr') ORDER BY source, ref, loaded_at DESC, load_id DESC),
cat_ok AS (
  SELECT source, ref AS scope, max(loaded_at) AS last_success FROM l
  WHERE source IN ('appk', 'ncr') AND status IN ('loaded', 'confirmed', 'unchanged') GROUP BY source, ref),
cat_rows AS (
  SELECT l.source, l.ref AS scope, r.row_count FROM l JOIN sobstvenost_src.read r USING (load_id) WHERE l.is_current),
part(source, scope, entities, full_source, full_ref, partial_error) AS (VALUES
  ('appk', 'details', 'appk-details', 'appk-full', 'details', 'Профилите и отчетните метаданни още не са прочетени докрай'),
  ('ncr', 'details', 'ncr-details', 'ncr-full', 'details', 'Партидите и обявленията още не са прочетени докрай'),
  ('appk', 'pdfs', 'appk-files', 'appk-files', '#full', 'Файловите оригинали още не са прочетени докрай'),
  ('ncr', 'pdfs', 'ncr-files', 'ncr-files', '#full', 'Файловите оригинали още не са прочетени докрай')),
pass AS (
  SELECT p.*, f.loaded_at AS full_at, f.started_at, f.entity_count,
         (SELECT max(e.loaded_at) FROM l e WHERE e.source = p.entities AND e.ref <> '#full') AS last_read,
         (SELECT count(DISTINCT e.ref) FROM l e WHERE e.source = p.entities AND e.ref <> '#full'
            AND e.status IN ('loaded', 'confirmed', 'unchanged') AND e.loaded_at > COALESCE(f.loaded_at, '-infinity')) AS since
  FROM part p LEFT JOIN LATERAL (
    SELECT x.loaded_at, r.started_at, r.entity_count FROM l x JOIN sobstvenost_src.full_read r USING (load_id)
    WHERE x.source = p.full_source AND x.ref = p.full_ref ORDER BY x.loaded_at DESC, x.load_id DESC LIMIT 1) f ON true)
SELECT c.source, c.scope,
       CASE WHEN c.status IN ('loaded', 'confirmed', 'unchanged') THEN 'ok' WHEN c.status = 'held' THEN 'held' ELSE 'invalid' END AS status,
       c.loaded_at AS last_read, o.last_success, n.row_count AS rows,
       CASE WHEN c.status IN ('held', 'rejected') THEN c.reason END AS error
FROM cat c LEFT JOIN cat_ok o USING (source, scope) LEFT JOIN cat_rows n USING (source, scope)
UNION ALL
SELECT source, scope, CASE WHEN full_at IS NOT NULL AND since = 0 THEN 'ok' ELSE 'partial' END,
       GREATEST(last_read, full_at), started_at, CASE WHEN full_at IS NOT NULL AND since = 0 THEN entity_count ELSE since END,
       CASE WHEN full_at IS NOT NULL AND since = 0 THEN NULL ELSE partial_error END
FROM pass WHERE full_at IS NOT NULL OR last_read IS NOT NULL
UNION ALL
SELECT replace(e.source, '-details', ''), 'read_profiles', 'partial', max(e.loaded_at), max(e.loaded_at), count(DISTINCT e.ref),
       'Това е броят на отделно прочетените профили. Пълното наваксване има отделен статус.'
FROM l e WHERE e.source IN ('appk-details', 'ncr-details') AND e.status IN ('loaded', 'confirmed', 'unchanged')
GROUP BY e.source;
COMMENT ON VIEW sobstvenost.source_state IS 'Последното четене на всеки обхват, както го показва страницата „Източници“: каталозите по последния прочит, профилите, партидите и файловете по последния пълен прочит.';

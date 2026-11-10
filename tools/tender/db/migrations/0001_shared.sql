-- Тендер in the shared database: the landing zone tender_src. The clean tables (schema tender) are built whole
-- by db/derive/*.sql in the build schema tender_stage and moved into tender in one transaction (ingest/build.py),
-- so their definition is the derive SQL, not a migration. The serving layer pub_tender is migration 0002.
--
-- The runs, the change log, the rules and the migrations are the shared schema ops; the municipalities and the
-- organisations are core.*. What was schema tr, eopsvc, sebra and ed in the old database of its own is here under
-- the same names with the schema as a prefix. The old schema is kept in db/legacy for reference.
DO $$ BEGIN
  IF to_regnamespace('tender_src') IS NULL THEN CREATE SCHEMA tender_src; END IF;
  IF to_regnamespace('tender') IS NULL THEN CREATE SCHEMA tender; END IF;
  IF to_regnamespace('tender_stage') IS NULL THEN CREATE SCHEMA tender_stage; END IF;
END $$;
COMMENT ON SCHEMA tender_src IS 'Тендер, приемна зона: Търговският регистър, страниците на поръчките в ЦАИС ЕОП, СЕБРА, редакторските бележки и състоянието на четенето.';
COMMENT ON SCHEMA tender IS 'Тендер, чисти таблици: поръчки, договори, фирми, лица и връзките между тях. Изграждат се цели от суровите файлове и приемната зона.';
COMMENT ON SCHEMA tender_stage IS 'Тендер, работна схема на изграждането: тук се строят чистите таблици, преди да заменят старите в една транзакция. Между две изграждания е празна.';

CREATE TABLE tender_src.eop_day (
  day date NOT NULL,
  published boolean NOT NULL,
  checked_at timestamptz NOT NULL,
  CONSTRAINT eop_day_pk PRIMARY KEY (day)
);
COMMENT ON TABLE tender_src.eop_day IS 'Отворените данни на ЦАИС ЕОП: всеки ден от 01.01.2020 г. и дали източникът го е публикувал.';

CREATE TABLE tender_src.eop_file (
  day date NOT NULL,
  kind text NOT NULL,
  key text NOT NULL,
  sha256 text NOT NULL,
  size bigint NOT NULL,
  fetched_at timestamptz NOT NULL,
  CONSTRAINT eop_file_kind_ck CHECK ((kind = ANY (ARRAY['tenders'::text, 'contracts'::text, 'annexes'::text, 'ocds'::text]))),
  CONSTRAINT eop_file_pk PRIMARY KEY (day, kind)
);
COMMENT ON TABLE tender_src.eop_file IS 'Отворените данни на ЦАИС ЕОП: четирите файла на деня (поръчки, договори, анекси, OCDS) с адреса, размера и sha256 на пазеното копие.';

CREATE TABLE tender_src.fx_rate (
  day date NOT NULL,
  currency text NOT NULL,
  per_eur numeric NOT NULL,
  CONSTRAINT fx_rate_pk PRIMARY KEY (currency, day)
);
COMMENT ON TABLE tender_src.fx_rate IS 'Референтните курсове на ЕЦБ към еврото по дни, за договори във валута, различна от лев и евро. Минават в core.fx_rate, когато Икономика премине.';

CREATE TABLE tender_src.build_state (
  id integer NOT NULL,
  at timestamptz,
  eop_last_day date,
  tr_last_read timestamptz,
  rules text,
  CONSTRAINT build_state_pk PRIMARY KEY (id)
);
COMMENT ON TABLE tender_src.build_state IS 'Един ред: кога е последното изграждане на чистите таблици, до кой ден на ЕОП стига, последното четене на регистъра и версията на правилата.';

CREATE TABLE tender_src.tr_queue (
  eik text NOT NULL,
  reason text NOT NULL,
  priority integer DEFAULT 5 NOT NULL,
  depth integer DEFAULT 0 NOT NULL,
  status text DEFAULT 'pending' NOT NULL,
  attempts integer DEFAULT 0 NOT NULL,
  next_at timestamptz DEFAULT now() NOT NULL,
  last_error text,
  enqueued_at timestamptz DEFAULT now() NOT NULL,
  done_at timestamptz,
  held_sha text,
  why text,
  CONSTRAINT tr_queue_status_ck CHECK ((status = ANY (ARRAY['pending'::text, 'done'::text, 'failed'::text]))),
  CONSTRAINT tr_queue_pk PRIMARY KEY (eik)
);
CREATE INDEX tr_queue_status_priority_next_at_idx ON tender_src.tr_queue (status, priority, next_at);
COMMENT ON TABLE tender_src.tr_queue IS 'Търговски регистър: опашката на партидите за четене, по ЕИК, с причина, приоритет и състояние.';

CREATE TABLE tender_src.tr_deed (
  eik text NOT NULL,
  status text NOT NULL,
  fetched_at timestamptz NOT NULL,
  sha256 text,
  name text,
  legal_form text,
  deed_status text,
  seat text,
  capital_eur numeric,
  registered_on date,
  error text,
  CONSTRAINT tr_deed_status_ck CHECK ((status = ANY (ARRAY['ok'::text, 'absent'::text, 'error'::text, 'unrelated'::text]))),
  CONSTRAINT tr_deed_pk PRIMARY KEY (eik)
);
COMMENT ON TABLE tender_src.tr_deed IS 'Търговски регистър: прочетените партиди, по една на ЕИК: име, правна форма, седалище, капитал, дата на регистрация.';

CREATE TABLE tender_src.tr_role (
  eik text NOT NULL,
  sub_uic text NOT NULL,
  field_ident text NOT NULL,
  role text NOT NULL,
  holder_kind text NOT NULL,
  holder_id text NOT NULL,
  holder_name core.person_name NOT NULL,
  indent_type text,
  share text,
  country text,
  entry_no text NOT NULL,
  valid_from date NOT NULL,
  valid_to date,
  uncertain_after date,
  observed_at timestamptz NOT NULL,
  CONSTRAINT tr_role_holder_kind_ck CHECK ((holder_kind = ANY (ARRAY['person'::text, 'entity'::text])))
);
CREATE INDEX tr_role_eik_idx ON tender_src.tr_role (eik);
CREATE INDEX tr_role_holder_id_idx ON tender_src.tr_role (holder_id);
COMMENT ON TABLE tender_src.tr_role IS 'Търговски регистър: ролите в партидите (управител, съдружник, едноличен собственик и други) с периода им. Държи имена на хора.';

CREATE TABLE tender_src.tr_person (
  id text NOT NULL,
  indent text NOT NULL,
  indent_type text,
  name core.person_name NOT NULL,
  name_key core.person_name NOT NULL,
  first_seen timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT tr_person_indent_uq UNIQUE (indent),
  CONSTRAINT tr_person_pk PRIMARY KEY (id)
);
CREATE INDEX tr_person_name_key_idx ON tender_src.tr_person (name_key);
COMMENT ON TABLE tender_src.tr_person IS 'Търговски регистър: лицата, които регистърът различава по свой идентификатор. Идентификаторът (indent) никога не се показва.';

CREATE TABLE tender_src.tr_name_search (
  name_key core.person_name NOT NULL,
  total integer,
  status text NOT NULL,
  searched_at timestamptz NOT NULL,
  CONSTRAINT tr_name_search_status_ck CHECK ((status = ANY (ARRAY['done'::text, 'ambiguous'::text, 'failed'::text]))),
  CONSTRAINT tr_name_search_pk PRIMARY KEY (name_key)
);
COMMENT ON TABLE tender_src.tr_name_search IS 'Търговски регистър: търсенията по име за други партиди на известно лице и резултатът им.';

CREATE TABLE tender_src.tr_change_day (
  day date NOT NULL,
  pass integer NOT NULL,
  next_page integer DEFAULT 1 NOT NULL,
  done boolean DEFAULT false NOT NULL,
  CONSTRAINT tr_change_day_pk PRIMARY KEY (day, pass)
);
COMMENT ON TABLE tender_src.tr_change_day IS 'Търговски регистър: докъде е прочетен дневният списък с промени (ден, проход, страница).';

CREATE TABLE tender_src.eopsvc_queue (
  tender_id bigint NOT NULL,
  reason text NOT NULL,
  status text DEFAULT 'pending' NOT NULL,
  attempts integer DEFAULT 0 NOT NULL,
  next_at timestamptz DEFAULT now() NOT NULL,
  fetched_at timestamptz,
  sha256 text,
  last_error text,
  held_sha text,
  CONSTRAINT eopsvc_queue_status_ck CHECK ((status = ANY (ARRAY['pending'::text, 'done'::text, 'error'::text]))),
  CONSTRAINT eopsvc_queue_pk PRIMARY KEY (tender_id)
);
CREATE INDEX eopsvc_queue_status_next_at_idx ON tender_src.eopsvc_queue (status, next_at);
COMMENT ON TABLE tender_src.eopsvc_queue IS 'Страниците на поръчките в ЦАИС ЕОП: опашката за четене, по номер на поръчката.';

CREATE TABLE tender_src.eopsvc_offer (
  tender_id bigint NOT NULL,
  lot_no integer NOT NULL,
  round integer NOT NULL,
  offer_id bigint NOT NULL,
  bidder_name text,
  bidder_eik text,
  consortium jsonb,
  submitted_at timestamptz,
  price numeric,
  price_opened boolean NOT NULL,
  fetched_at timestamptz NOT NULL,
  submitter_name text,
  submitter_eik text,
  CONSTRAINT eopsvc_offer_pk PRIMARY KEY (tender_id, lot_no, round, offer_id)
);
CREATE INDEX eopsvc_offer_bidder_eik_idx ON tender_src.eopsvc_offer (bidder_eik);
COMMENT ON TABLE tender_src.eopsvc_offer IS 'Страниците на поръчките в ЦАИС ЕОП: подадените оферти по позиция и кръг, с участника и цената след отварянето ѝ.';

CREATE TABLE tender_src.eopsvc_lot (
  tender_id bigint NOT NULL,
  lot_no integer NOT NULL,
  title text,
  CONSTRAINT eopsvc_lot_pk PRIMARY KEY (tender_id, lot_no)
);
COMMENT ON TABLE tender_src.eopsvc_lot IS 'Страниците на поръчките в ЦАИС ЕОП: обособените позиции с номера и името им.';

CREATE TABLE tender_src.eopsvc_contract_lot (
  tender_id bigint NOT NULL,
  lot_no integer NOT NULL,
  contract_id text NOT NULL,
  CONSTRAINT eopsvc_contract_lot_pk PRIMARY KEY (tender_id, contract_id)
);
COMMENT ON TABLE tender_src.eopsvc_contract_lot IS 'Страниците на поръчките в ЦАИС ЕОП: към коя позиция е всеки договор.';

CREATE TABLE tender_src.eopsvc_announcement (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,
  created_at timestamptz,
  title text NOT NULL,
  gone_at timestamptz,
  CONSTRAINT eopsvc_announcement_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE tender_src.eopsvc_announcement IS 'Страниците на поръчките в ЦАИС ЕОП: съобщенията (разяснения, протоколи, решения).';

CREATE TABLE tender_src.eopsvc_detail (
  tender_id bigint NOT NULL,
  offers_from timestamptz,
  offers_until timestamptz,
  opening_at timestamptz,
  prices_opening_at timestamptz,
  parent_tender_id bigint,
  linked jsonb,
  fetched_at timestamptz NOT NULL,
  CONSTRAINT eopsvc_detail_pk PRIMARY KEY (tender_id)
);
COMMENT ON TABLE tender_src.eopsvc_detail IS 'Страниците на поръчките в ЦАИС ЕОП: сроковете за оферти, отварянето им и свързаните поръчки.';

CREATE TABLE tender_src.eopsvc_publication (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,
  form_type integer,
  ted_number text,
  sent_at timestamptz,
  published_at timestamptz,
  gone_at timestamptz,
  CONSTRAINT eopsvc_publication_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE tender_src.eopsvc_publication IS 'Страниците на поръчките в ЦАИС ЕОП: обявленията и решенията с номера им.';

CREATE TABLE tender_src.eopsvc_appeal (
  tender_id bigint NOT NULL,
  register_id text NOT NULL,
  proceedings_number text,
  kind text,
  subjects text[],
  initiators text[],
  defendants text[],
  interim_measures boolean,
  imposed_measures integer,
  imposed_penalties integer,
  status text,
  filed_on date,
  started_on date,
  last_decision_on date,
  closed_on date,
  link text,
  gone_at timestamptz,
  CONSTRAINT eopsvc_appeal_pk PRIMARY KEY (tender_id, register_id)
);
COMMENT ON TABLE tender_src.eopsvc_appeal IS 'Страниците на поръчките в ЦАИС ЕОП: жалбите до КЗК. Име на жалбоподател физическо лице не се пази.';

CREATE TABLE tender_src.eopsvc_document (
  tender_id bigint NOT NULL,
  id bigint NOT NULL,
  name text,
  size bigint,
  created_at timestamptz,
  gone_at timestamptz,
  CONSTRAINT eopsvc_document_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE tender_src.eopsvc_document IS 'Страниците на поръчките в ЦАИС ЕОП: прикачените документи (име, размер, дата).';

CREATE TABLE tender_src.eopsvc_check_run (
  at timestamptz DEFAULT now() NOT NULL,
  ok boolean NOT NULL,
  report jsonb NOT NULL,
  mismatches jsonb,
  CONSTRAINT eopsvc_check_run_pk PRIMARY KEY (at)
);
COMMENT ON TABLE tender_src.eopsvc_check_run IS 'Проверките на четеца на оферти преди всяко четене: минала ли е и отчетът ѝ.';

CREATE TABLE tender_src.sebra_resource (
  uri text NOT NULL,
  name text NOT NULL,
  format text NOT NULL,
  updated_at timestamptz,
  sha256 text,
  rows integer,
  loaded_at timestamptz,
  held_sha text,
  CONSTRAINT sebra_resource_pk PRIMARY KEY (uri)
);
COMMENT ON TABLE tender_src.sebra_resource IS 'СЕБРА в data.egov.bg: файловете с плащания по периоди, с sha256 и броя редове.';

CREATE TABLE tender_src.sebra_payment (
  resource_uri text NOT NULL,
  row_no integer NOT NULL,
  settlement_date date,
  receiver_name text,
  is_person boolean NOT NULL,
  receiver_iban text,
  fin_code text,
  fin_name text,
  amount numeric,
  currency text,
  reason text,
  reg_date date,
  reg_no text,
  pay_code text,
  organization text,
  primary_organization text,
  primary_org_code text,
  CONSTRAINT sebra_payment_pk PRIMARY KEY (resource_uri, row_no),
  CONSTRAINT sebra_payment_resource_uri_fk FOREIGN KEY (resource_uri) REFERENCES tender_src.sebra_resource (uri)
);
CREATE INDEX sebra_payment_settlement_date_idx ON tender_src.sebra_payment (settlement_date);
COMMENT ON TABLE tender_src.sebra_payment IS 'СЕБРА: плащанията от бюджета, ред по ред. За физическо лице източникът пише „ФИЗИЧЕСКО ЛИЦЕ“ без име.';

CREATE TABLE tender_src.ed_tag (
  id bigint GENERATED BY DEFAULT AS IDENTITY,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  code text NOT NULL,
  note text NOT NULL,
  author text NOT NULL,
  created_at timestamptz DEFAULT now() NOT NULL,
  removed_at timestamptz,
  CONSTRAINT ed_tag_entity_type_ck CHECK ((entity_type = ANY (ARRAY['company'::text, 'person'::text, 'buyer'::text, 'contract'::text]))),
  CONSTRAINT ed_tag_pk PRIMARY KEY (id)
);
COMMENT ON TABLE tender_src.ed_tag IS 'Редакторски отбелязвания към фирма, лице, възложител или договор, с основанието им.';

CREATE TABLE tender_src.ed_tag_audit (
  id bigint GENERATED BY DEFAULT AS IDENTITY,
  tag_id bigint GENERATED BY DEFAULT AS IDENTITY,
  action text NOT NULL,
  author text NOT NULL,
  at timestamptz DEFAULT now() NOT NULL,
  before jsonb,
  after jsonb,
  CONSTRAINT ed_tag_audit_pk PRIMARY KEY (id),
  CONSTRAINT ed_tag_audit_tag_id_fk FOREIGN KEY (tag_id) REFERENCES tender_src.ed_tag (id)
);
COMMENT ON TABLE tender_src.ed_tag_audit IS 'Историята на редакторските отбелязвания.';

CREATE TABLE tender_src.ed_public_figure (
  person_id text NOT NULL,
  wikidata_qid text NOT NULL,
  label core.person_name NOT NULL,
  description text,
  photo_file text,
  photo_license text,
  photo_author text,
  status text DEFAULT 'candidate' NOT NULL,
  reviewed_by text,
  reviewed_at timestamptz,
  found_at timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT ed_public_figure_status_ck CHECK ((status = ANY (ARRAY['candidate'::text, 'confirmed'::text, 'rejected'::text]))),
  CONSTRAINT ed_public_figure_pk PRIMARY KEY (person_id, wikidata_qid),
  CONSTRAINT ed_public_figure_person_id_fk FOREIGN KEY (person_id) REFERENCES tender_src.tr_person (id)
);
COMMENT ON TABLE tender_src.ed_public_figure IS 'Кандидати за публични фигури, намерени по име в Wikidata. Не се показват без ръчно потвърждение.';

CREATE TABLE tender_src.ed_article (
  id bigint GENERATED BY DEFAULT AS IDENTITY,
  entity_type text NOT NULL,
  entity_id text NOT NULL,
  url text NOT NULL,
  title text NOT NULL,
  source text,
  published_at timestamptz,
  match_basis text NOT NULL,
  status text DEFAULT 'candidate' NOT NULL,
  reviewed_by text,
  reviewed_at timestamptz,
  found_at timestamptz DEFAULT now() NOT NULL,
  CONSTRAINT ed_article_entity_type_ck CHECK ((entity_type = ANY (ARRAY['company'::text, 'person'::text]))),
  CONSTRAINT ed_article_status_ck CHECK ((status = ANY (ARRAY['candidate'::text, 'confirmed'::text, 'rejected'::text]))),
  CONSTRAINT ed_article_entity_type_entity_id_url_uq UNIQUE (entity_type, entity_id, url),
  CONSTRAINT ed_article_pk PRIMARY KEY (id)
);
COMMENT ON TABLE tender_src.ed_article IS 'Кандидати за статии за фирма или лице. Показват се само потвърдените.';

COMMENT ON COLUMN tender_src.tr_person.indent IS 'Идентификаторът на лицето в регистъра (хеш). По него се свързват ролите на едно лице; никога не излиза от приемната зона.';
COMMENT ON COLUMN tender_src.tr_person.id IS 'Наш номер на лицето (ULID). Остава в приемната страна; в слоя за показване лицето няма устойчив ключ.';
COMMENT ON COLUMN tender_src.tr_role.holder_id IS 'Идентификаторът на регистъра за лице, ЕИК за дружество, или local:/name: с име, когато регистърът не дава друго.';
COMMENT ON COLUMN tender_src.eopsvc_offer.submitter_name IS 'Организацията, от чийто профил е подадена офертата, когато е различна от участника. Лицето, което я е подало, не се пази.';

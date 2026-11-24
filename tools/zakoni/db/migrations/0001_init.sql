-- Закони и решения in the shared database: the landing zone zakoni_src and the clean tables zakoni.
-- The ledger (runs, loads, holds, changes, checks) is the shared schema ops; the raw files are those of the
-- Наблюдател archive, registered in ops.raw_file by the archive itself. Nothing is downloaded by this tool.
--
-- Every accepted answer of a resource is one load (ops.load) and its rows are in zakoni_src under its load_id. The landing
-- zone keeps only the current load of each resource (landing policy `latest`): a resource is a file of up to 300 MB that
-- the source replaces whole, so older loads would repeat it. Their ledger lines stay, and so does the file in the archive.
-- What a record was before is in ops.change_log, field by field; when it was first seen and last changed travels with
-- the record from load to load (first_seen_at, changed_at).
DO $$ BEGIN
  IF to_regnamespace('zakoni_src') IS NULL THEN CREATE SCHEMA zakoni_src; END IF;
  IF to_regnamespace('zakoni') IS NULL THEN CREATE SCHEMA zakoni; END IF;
END $$;
COMMENT ON SCHEMA zakoni_src IS 'Закони и решения, приемна зона: записите на набора на АМС в data.egov.bg (ПРИС, обществени консултации, стратегически документи, договори за оценка на въздействието), както са във файла, по последното прието четене.';
COMMENT ON SCHEMA zakoni IS 'Закони и решения: актовете на Министерския съвет, обществените консултации, стратегическите документи и договорите за оценка на въздействието, с ключовете на източника.';

-- ---------------------------------------------------------------- the landing zone

CREATE TABLE zakoni_src.pris_act (
  load_id                    bigint NOT NULL,
  pris_id                    integer NOT NULL,
  origin                     text NOT NULL,
  doc_num                    text NOT NULL,
  accepted                   date NOT NULL,
  about                      text NOT NULL,
  about_raw                  text NOT NULL,
  legal_act_type             text NOT NULL,
  legal_reason               text,
  importer                   text,
  protocol                   text,
  public_consultation_number text,
  gazette_number             integer,
  gazette_year_raw           text,
  gazette_year               smallint,
  version                    text,
  active                     boolean NOT NULL,
  published                  date NOT NULL,
  deleted                    date,
  confidential               boolean NOT NULL,
  record_sha                 text NOT NULL,
  first_seen_at              timestamptz NOT NULL,
  changed_at                 timestamptz NOT NULL,
  CONSTRAINT pris_act_pk PRIMARY KEY (load_id, pris_id),
  CONSTRAINT pris_act_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT pris_act_origin_ck CHECK (origin IN ('current', 'archive'))
);
COMMENT ON TABLE zakoni_src.pris_act IS 'Акт от регистъра на Министерския съвет (ПРИС, действащи и архив), както е във файла. record_sha е хешът на записа с институциите, термините и връзките му; first_seen_at и changed_at се пренасят от четене на четене.';

CREATE TABLE zakoni_src.pris_institution (
  load_id        bigint NOT NULL,
  pris_id        integer NOT NULL,
  ord            integer NOT NULL,
  institution_id integer NOT NULL,
  name           text NOT NULL,
  CONSTRAINT pris_institution_pk PRIMARY KEY (load_id, pris_id, ord),
  CONSTRAINT pris_institution_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.pris_institution IS 'Институциите на акт от ПРИС, по реда във файла.';

CREATE TABLE zakoni_src.pris_tag (
  load_id bigint NOT NULL,
  pris_id integer NOT NULL,
  ord     integer NOT NULL,
  tag     text NOT NULL,
  CONSTRAINT pris_tag_pk PRIMARY KEY (load_id, pris_id, ord),
  CONSTRAINT pris_tag_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.pris_tag IS 'Термините на акт от ПРИС, по реда във файла.';

CREATE TABLE zakoni_src.pris_related (
  load_id         bigint NOT NULL,
  pris_id         integer NOT NULL,
  ord             integer NOT NULL,
  relation_type   text NOT NULL,
  related_pris_id integer NOT NULL,
  act_type        text NOT NULL,
  act_name        text NOT NULL,
  CONSTRAINT pris_related_pk PRIMARY KEY (load_id, pris_id, ord),
  CONSTRAINT pris_related_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.pris_related IS 'Свързаните актове на акт от ПРИС, както ги посочва регистърът.';

CREATE TABLE zakoni_src.consultation (
  load_id                bigint NOT NULL,
  reg_num                text NOT NULL,
  consultation_type      text NOT NULL,
  name                   text NOT NULL,
  description            text,
  description_raw        text NOT NULL,
  act_type               text,
  date_open              date NOT NULL,
  date_close             date NOT NULL,
  short_term_reason      text,
  active                 boolean NOT NULL,
  policy_area            text NOT NULL,
  legislative_program_id integer,
  operational_program_id integer,
  institution_id         integer NOT NULL,
  institution_name       text,
  institution_address    text,
  proposal_ways          text,
  law_name               text,
  law_id                 integer,
  pris_id                integer,
  comment_count          integer NOT NULL,
  comment_first          date,
  comment_last           date,
  record_sha             text NOT NULL,
  first_seen_at          timestamptz NOT NULL,
  changed_at             timestamptz NOT NULL,
  CONSTRAINT consultation_pk PRIMARY KEY (load_id, reg_num),
  CONSTRAINT consultation_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.consultation IS 'Обществена консултация от обединената справка на Портала за обществени консултации, както е във файла. Имената на коментиращите и лицата за контакт не се записват.';

CREATE TABLE zakoni_src.consultation_file (
  load_id  bigint NOT NULL,
  reg_num  text NOT NULL,
  ord      integer NOT NULL,
  kind     text NOT NULL,
  doc_date date,
  link     text NOT NULL,
  CONSTRAINT consultation_file_pk PRIMARY KEY (load_id, reg_num, ord),
  CONSTRAINT consultation_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.consultation_file IS 'Документите на консултация: вид, дата и връзка към портала.';

CREATE TABLE zakoni_src.strategy_doc (
  load_id                    bigint NOT NULL,
  doc_key                    text NOT NULL,
  name                       text NOT NULL,
  level                      text,
  policy_area                text,
  doc_type                   text,
  act_link                   text,
  pris_act_id                integer,
  accepting_institution_type text,
  document_date              date,
  public_consultation_number text,
  active                     boolean NOT NULL,
  date_accepted              date,
  date_accepted_raw          text NOT NULL,
  date_expiring              date,
  date_expiring_raw          text,
  record_sha                 text NOT NULL,
  first_seen_at              timestamptz NOT NULL,
  changed_at                 timestamptz NOT NULL,
  CONSTRAINT strategy_doc_pk PRIMARY KEY (load_id, doc_key),
  CONSTRAINT strategy_doc_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.strategy_doc IS 'Стратегически документ от пълната справка, както е във файла.';

CREATE TABLE zakoni_src.strategy_author (
  load_id     bigint NOT NULL,
  doc_key     text NOT NULL,
  ord         integer NOT NULL,
  institution text NOT NULL,
  CONSTRAINT strategy_author_pk PRIMARY KEY (load_id, doc_key, ord),
  CONSTRAINT strategy_author_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.strategy_author IS 'Институциите, изготвили стратегически документ.';

CREATE TABLE zakoni_src.strategy_file (
  load_id bigint NOT NULL,
  doc_key text NOT NULL,
  ord     integer NOT NULL,
  name    text,
  path    text NOT NULL,
  version text,
  CONSTRAINT strategy_file_pk PRIMARY KEY (load_id, doc_key, ord),
  CONSTRAINT strategy_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.strategy_file IS 'Файловете на стратегически документ.';

CREATE TABLE zakoni_src.strategy_sub (
  load_id       bigint NOT NULL,
  doc_key       text NOT NULL,
  ord           integer NOT NULL,
  sub_id        integer NOT NULL,
  parent_sub_id integer,
  name          text NOT NULL,
  level         text,
  policy_area   text,
  doc_type      text,
  pris_act_id   integer,
  date_accepted date,
  date_expiring date,
  CONSTRAINT strategy_sub_pk PRIMARY KEY (load_id, doc_key, ord),
  CONSTRAINT strategy_sub_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.strategy_sub IS 'Подчинените документи на стратегически документ (планове, програми).';

CREATE TABLE zakoni_src.impact_contract (
  load_id          bigint NOT NULL,
  ic_key           text NOT NULL,
  institution_id   integer NOT NULL,
  institution_name text NOT NULL,
  contract_date    date,
  price_bgn        numeric(14, 2),
  eik              text,
  executor         text,
  executor_kind    text NOT NULL,
  subject          text,
  description      text,
  active           boolean NOT NULL,
  record_sha       text NOT NULL,
  first_seen_at    timestamptz NOT NULL,
  changed_at       timestamptz NOT NULL,
  CONSTRAINT impact_contract_pk PRIMARY KEY (load_id, ic_key),
  CONSTRAINT impact_contract_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.impact_contract IS 'Договор за оценка на въздействието. Изпълнител физическо лице няма име и ЕИК (парсерът ги маха).';

CREATE TABLE zakoni_src.institution_municipality (
  load_id          bigint NOT NULL,
  institution_id   integer NOT NULL,
  municipality_eik text NOT NULL,
  method           text NOT NULL,
  evidence         text NOT NULL,
  checked          date NOT NULL,
  checked_by       text NOT NULL,
  CONSTRAINT institution_municipality_pk PRIMARY KEY (load_id, institution_id),
  CONSTRAINT institution_municipality_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE zakoni_src.institution_municipality IS 'Ръчното съответствие институция на портала към община (db/ref/institution_municipality.csv, с доказателство).';

-- ---------------------------------------------------------------- the clean tables (built whole from the current loads)

CREATE TABLE zakoni.act (
  pris_id              integer NOT NULL,
  doc_num              text NOT NULL,
  accepted             date NOT NULL,
  year                 smallint NOT NULL,
  act_type             text NOT NULL,
  about                text NOT NULL,
  about_original       text NOT NULL,
  legal_reason         text,
  importer             text,
  protocol             text,
  consultation_reg_num text,
  gazette_number       integer,
  gazette_year         smallint,
  active               boolean NOT NULL,
  confidential         boolean NOT NULL,
  origin               text NOT NULL,
  tags                 text[],
  relations            integer NOT NULL,
  source               text NOT NULL,
  source_url           text NOT NULL,
  raw_sha256           text NOT NULL,
  first_seen           date NOT NULL,
  last_seen            date NOT NULL,
  CONSTRAINT act_pk PRIMARY KEY (pris_id)
);
COMMENT ON TABLE zakoni.act IS 'Акт на Министерския съвет (ПРИС). Ключът е pris_id: нова версия при източника е промяна, не нов акт; номерът без годината не е ключ.';
COMMENT ON COLUMN zakoni.act.pris_id IS 'Номерът на акта в ПРИС.';
COMMENT ON COLUMN zakoni.act.doc_num IS 'Номерът на документа, както е в регистъра; повтаря се в различни години.';
COMMENT ON COLUMN zakoni.act.accepted IS 'Датата на издаване.';
COMMENT ON COLUMN zakoni.act.year IS 'Годината на издаване.';
COMMENT ON COLUMN zakoni.act.act_type IS 'Видът: постановления, решения, протоколни решения, разпореждания, протоколи, стенограми.';
COMMENT ON COLUMN zakoni.act.about IS 'Заглавието без HTML.';
COMMENT ON COLUMN zakoni.act.about_original IS 'Заглавието, както е във файла (с HTML).';
COMMENT ON COLUMN zakoni.act.legal_reason IS 'Правното основание.';
COMMENT ON COLUMN zakoni.act.importer IS 'Вносителят (длъжност или институция).';
COMMENT ON COLUMN zakoni.act.protocol IS 'Протоколът на заседанието.';
COMMENT ON COLUMN zakoni.act.consultation_reg_num IS 'Обществената консултация на акта, когато я има в справката; иначе празно и редът е в zakoni.unmatched.';
COMMENT ON COLUMN zakoni.act.gazette_number IS 'Броят на Държавен вестник; празно, когато годината му не е около годината на акта.';
COMMENT ON COLUMN zakoni.act.gazette_year IS 'Годината на Държавен вестник; празно, когато не се определя или не е около годината на акта.';
COMMENT ON COLUMN zakoni.act.active IS 'Действащ запис в регистъра.';
COMMENT ON COLUMN zakoni.act.confidential IS 'Поверителен акт: има го, текст няма.';
COMMENT ON COLUMN zakoni.act.origin IS 'От кой ресурс е: current (действащи) или archive (архив до 1988 г.).';
COMMENT ON COLUMN zakoni.act.tags IS 'Термините, по реда в регистъра.';
COMMENT ON COLUMN zakoni.act.relations IS 'Броят на свързаните актове, посочени от регистъра.';
COMMENT ON COLUMN zakoni.act.source IS 'Източникът с думи.';
COMMENT ON COLUMN zakoni.act.source_url IS 'Адресът на набора в data.egov.bg.';
COMMENT ON COLUMN zakoni.act.raw_sha256 IS 'sha256 на файла от архива, от който е записът.';
COMMENT ON COLUMN zakoni.act.first_seen IS 'Денят (по българско време), в който записът е прочетен за пръв път.';
COMMENT ON COLUMN zakoni.act.last_seen IS 'Денят на последната промяна на записа при източника, както я видяхме.';

CREATE TABLE zakoni.act_institution (
  pris_id        integer NOT NULL,
  institution_id integer NOT NULL,
  name           text NOT NULL,
  CONSTRAINT act_institution_pk PRIMARY KEY (pris_id, institution_id),
  CONSTRAINT act_institution_pris_id_fk FOREIGN KEY (pris_id) REFERENCES zakoni.act (pris_id) ON DELETE CASCADE
);
COMMENT ON TABLE zakoni.act_institution IS 'Институциите на акт.';
COMMENT ON COLUMN zakoni.act_institution.pris_id IS 'Актът.';
COMMENT ON COLUMN zakoni.act_institution.institution_id IS 'Номерът на институцията в ПРИС.';
COMMENT ON COLUMN zakoni.act_institution.name IS 'Името на институцията.';

CREATE TABLE zakoni.act_relation (
  pris_id         integer NOT NULL,
  ord             integer NOT NULL,
  relation_type   text NOT NULL,
  related_pris_id integer NOT NULL,
  related_in_data boolean NOT NULL,
  act_type        text NOT NULL,
  act_name        text NOT NULL,
  CONSTRAINT act_relation_pk PRIMARY KEY (pris_id, ord),
  CONSTRAINT act_relation_pris_id_fk FOREIGN KEY (pris_id) REFERENCES zakoni.act (pris_id) ON DELETE CASCADE
);
COMMENT ON TABLE zakoni.act_relation IS 'Свързан акт, както го посочва регистърът. Връзка на акт със себе си не се показва (в zakoni.unmatched).';
COMMENT ON COLUMN zakoni.act_relation.pris_id IS 'Актът.';
COMMENT ON COLUMN zakoni.act_relation.ord IS 'Мястото в списъка на регистъра.';
COMMENT ON COLUMN zakoni.act_relation.relation_type IS 'Видът на връзката (изменя, отменя …).';
COMMENT ON COLUMN zakoni.act_relation.related_pris_id IS 'Свързаният акт.';
COMMENT ON COLUMN zakoni.act_relation.related_in_data IS 'Свързаният акт е в нашите данни.';
COMMENT ON COLUMN zakoni.act_relation.act_type IS 'Видът на свързания акт.';
COMMENT ON COLUMN zakoni.act_relation.act_name IS 'Името на свързания акт.';

CREATE TABLE zakoni.consultation (
  reg_num            text NOT NULL,
  number             integer NOT NULL,
  name               text NOT NULL,
  description        text,
  level              text NOT NULL,
  act_type           text,
  opened             date NOT NULL,
  closes             date NOT NULL,
  days               integer NOT NULL,
  short_term         boolean NOT NULL,
  short_term_applies boolean NOT NULL,
  short_term_reason  text,
  reason_given       boolean NOT NULL,
  policy_area        text NOT NULL,
  archived_area      boolean NOT NULL,
  institution_id     integer NOT NULL,
  institution_name   text,
  municipality_code  text,
  law_name           text,
  law_id             integer,
  act_pris_id        integer,
  comment_count      integer NOT NULL,
  files              integer NOT NULL,
  source             text NOT NULL,
  source_url         text NOT NULL,
  raw_sha256         text NOT NULL,
  first_seen         date NOT NULL,
  last_seen          date NOT NULL,
  CONSTRAINT consultation_pk PRIMARY KEY (reg_num),
  CONSTRAINT consultation_municipality_code_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code)
);
CREATE INDEX consultation_municipality_code_idx ON zakoni.consultation (municipality_code);
COMMENT ON TABLE zakoni.consultation IS 'Обществена консултация. Ключът е регистровият номер (12692-K). Срокът в дни е датата на приключване минус датата на откриване, както ги дава порталът.';
COMMENT ON COLUMN zakoni.consultation.reg_num IS 'Регистровият номер на портала.';
COMMENT ON COLUMN zakoni.consultation.number IS 'Числото от номера (за подредба и адрес на портала).';
COMMENT ON COLUMN zakoni.consultation.name IS 'Заглавието.';
COMMENT ON COLUMN zakoni.consultation.description IS 'Описанието без HTML; имейл адресите са заменени.';
COMMENT ON COLUMN zakoni.consultation.level IS 'Видът: централно, областно, общинско.';
COMMENT ON COLUMN zakoni.consultation.act_type IS 'Видът на проекта на акт.';
COMMENT ON COLUMN zakoni.consultation.opened IS 'Датата на откриване.';
COMMENT ON COLUMN zakoni.consultation.closes IS 'Датата на приключване.';
COMMENT ON COLUMN zakoni.consultation.days IS 'Срокът в дни: приключване минус откриване.';
COMMENT ON COLUMN zakoni.consultation.short_term IS 'Срокът е под 30 дни.';
COMMENT ON COLUMN zakoni.consultation.short_term_applies IS 'Индикаторът за кратък срок: под 30 дни за проект на нормативен акт, открит от 04.11.2016 (чл. 26, ал. 4 ЗНА).';
COMMENT ON COLUMN zakoni.consultation.short_term_reason IS 'Мотивът за кратък срок, както е на портала; само тире или точка не е мотив.';
COMMENT ON COLUMN zakoni.consultation.reason_given IS 'Мотивът е посочен.';
COMMENT ON COLUMN zakoni.consultation.policy_area IS 'Областта на политика.';
COMMENT ON COLUMN zakoni.consultation.archived_area IS 'Областта на политика е архивна („Архив - …“).';
COMMENT ON COLUMN zakoni.consultation.institution_id IS 'Номерът на институцията на портала.';
COMMENT ON COLUMN zakoni.consultation.institution_name IS 'Името на институцията; понякога източникът не го дава.';
COMMENT ON COLUMN zakoni.consultation.municipality_code IS 'Общината на общинска консултация по ръчното съответствие на институцията; празно, когато няма.';
COMMENT ON COLUMN zakoni.consultation.law_name IS 'Законът, по който е проектът, когато е посочен.';
COMMENT ON COLUMN zakoni.consultation.law_id IS 'Номерът на закона на портала.';
COMMENT ON COLUMN zakoni.consultation.act_pris_id IS 'Приетият акт в ПРИС, когато го има в регистъра.';
COMMENT ON COLUMN zakoni.consultation.comment_count IS 'Броят на коментарите (имената на коментиращите не се пазят).';
COMMENT ON COLUMN zakoni.consultation.files IS 'Броят на документите.';
COMMENT ON COLUMN zakoni.consultation.source IS 'Източникът с думи.';
COMMENT ON COLUMN zakoni.consultation.source_url IS 'Адресът на консултацията на портала.';
COMMENT ON COLUMN zakoni.consultation.raw_sha256 IS 'sha256 на файла от архива, от който е записът.';
COMMENT ON COLUMN zakoni.consultation.first_seen IS 'Денят (по българско време), в който записът е прочетен за пръв път.';
COMMENT ON COLUMN zakoni.consultation.last_seen IS 'Денят на последната промяна на записа при източника, както я видяхме.';

CREATE TABLE zakoni.consultation_file (
  reg_num  text NOT NULL,
  ord      integer NOT NULL,
  kind     text NOT NULL,
  doc_date date,
  link     text NOT NULL,
  CONSTRAINT consultation_file_pk PRIMARY KEY (reg_num, ord),
  CONSTRAINT consultation_file_reg_num_fk FOREIGN KEY (reg_num) REFERENCES zakoni.consultation (reg_num) ON DELETE CASCADE
);
COMMENT ON TABLE zakoni.consultation_file IS 'Документ на консултация.';
COMMENT ON COLUMN zakoni.consultation_file.reg_num IS 'Консултацията.';
COMMENT ON COLUMN zakoni.consultation_file.ord IS 'Мястото в списъка на портала.';
COMMENT ON COLUMN zakoni.consultation_file.kind IS 'Видът на документа.';
COMMENT ON COLUMN zakoni.consultation_file.doc_date IS 'Датата на документа.';
COMMENT ON COLUMN zakoni.consultation_file.link IS 'Връзката към документа на портала.';

CREATE TABLE zakoni.strategy_doc (
  doc_key              text NOT NULL,
  name                 text NOT NULL,
  level                text,
  policy_area          text,
  doc_type             text,
  authority            text,
  act_pris_id          integer,
  consultation_reg_num text,
  accepted             date,
  expires              date,
  active               boolean NOT NULL,
  authors              text[],
  files                integer NOT NULL,
  source               text NOT NULL,
  source_url           text NOT NULL,
  raw_sha256           text NOT NULL,
  first_seen           date NOT NULL,
  last_seen            date NOT NULL,
  CONSTRAINT strategy_doc_pk PRIMARY KEY (doc_key)
);
COMMENT ON TABLE zakoni.strategy_doc IS 'Стратегически документ: национален, областен или общински.';
COMMENT ON COLUMN zakoni.strategy_doc.doc_key IS 'Ключът на документа в справката.';
COMMENT ON COLUMN zakoni.strategy_doc.name IS 'Името.';
COMMENT ON COLUMN zakoni.strategy_doc.level IS 'Нивото.';
COMMENT ON COLUMN zakoni.strategy_doc.policy_area IS 'Областта на политика.';
COMMENT ON COLUMN zakoni.strategy_doc.doc_type IS 'Видът на документа.';
COMMENT ON COLUMN zakoni.strategy_doc.authority IS 'Видът на приемащата институция.';
COMMENT ON COLUMN zakoni.strategy_doc.act_pris_id IS 'Актът на приемане в ПРИС, когато го има.';
COMMENT ON COLUMN zakoni.strategy_doc.consultation_reg_num IS 'Консултацията на документа, когато я има.';
COMMENT ON COLUMN zakoni.strategy_doc.accepted IS 'Датата на приемане.';
COMMENT ON COLUMN zakoni.strategy_doc.expires IS 'Крайната дата; празно, когато няма (9999-01-01 при източника не е дата).';
COMMENT ON COLUMN zakoni.strategy_doc.active IS 'Действащ запис.';
COMMENT ON COLUMN zakoni.strategy_doc.authors IS 'Изготвилите институции.';
COMMENT ON COLUMN zakoni.strategy_doc.files IS 'Броят на файловете.';
COMMENT ON COLUMN zakoni.strategy_doc.source IS 'Източникът с думи.';
COMMENT ON COLUMN zakoni.strategy_doc.source_url IS 'Адресът на набора в data.egov.bg.';
COMMENT ON COLUMN zakoni.strategy_doc.raw_sha256 IS 'sha256 на файла от архива, от който е записът.';
COMMENT ON COLUMN zakoni.strategy_doc.first_seen IS 'Денят (по българско време), в който записът е прочетен за пръв път.';
COMMENT ON COLUMN zakoni.strategy_doc.last_seen IS 'Денят на последната промяна на записа при източника, както я видяхме.';

CREATE TABLE zakoni.impact_contract (
  ic_key           text NOT NULL,
  institution_id   integer NOT NULL,
  institution_name text NOT NULL,
  contract_date    date,
  price_bgn        numeric(14, 2),
  price_eur        numeric(14, 2),
  eik              text,
  executor         text,
  executor_kind    text NOT NULL,
  subject          text,
  description      text,
  source           text NOT NULL,
  source_url       text NOT NULL,
  raw_sha256       text NOT NULL,
  CONSTRAINT impact_contract_pk PRIMARY KEY (ic_key)
);
COMMENT ON TABLE zakoni.impact_contract IS 'Договор за оценка на въздействието. Изпълнител физическо лице е без име и ЕИК.';
COMMENT ON COLUMN zakoni.impact_contract.ic_key IS 'Ключът на договора.';
COMMENT ON COLUMN zakoni.impact_contract.institution_id IS 'Номерът на възложителя на портала.';
COMMENT ON COLUMN zakoni.impact_contract.institution_name IS 'Възложителят.';
COMMENT ON COLUMN zakoni.impact_contract.contract_date IS 'Датата на договора.';
COMMENT ON COLUMN zakoni.impact_contract.price_bgn IS 'Цената в лева, както е при източника.';
COMMENT ON COLUMN zakoni.impact_contract.price_eur IS 'Цената в евро по фиксирания курс 1,95583.';
COMMENT ON COLUMN zakoni.impact_contract.eik IS 'ЕИК на изпълнителя с валидна контролна цифра; празно за физическо лице.';
COMMENT ON COLUMN zakoni.impact_contract.executor IS 'Изпълнителят, само когато е юридическо лице.';
COMMENT ON COLUMN zakoni.impact_contract.executor_kind IS 'Видът на изпълнителя.';
COMMENT ON COLUMN zakoni.impact_contract.subject IS 'Предметът.';
COMMENT ON COLUMN zakoni.impact_contract.description IS 'Описанието.';
COMMENT ON COLUMN zakoni.impact_contract.source IS 'Източникът с думи.';
COMMENT ON COLUMN zakoni.impact_contract.source_url IS 'Адресът на набора в data.egov.bg.';
COMMENT ON COLUMN zakoni.impact_contract.raw_sha256 IS 'sha256 на файла от архива, от който е записът.';

CREATE TABLE zakoni.unmatched (
  tbl    text NOT NULL,
  key    text NOT NULL,
  field  text NOT NULL,
  value  text,
  reason text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (tbl, key, field)
);
COMMENT ON TABLE zakoni.unmatched IS 'Ред без ключ, който би трябвало да има: редът остава с празен ключ, причината е тук.';
COMMENT ON COLUMN zakoni.unmatched.tbl IS 'Таблицата.';
COMMENT ON COLUMN zakoni.unmatched.key IS 'Ключът на реда.';
COMMENT ON COLUMN zakoni.unmatched.field IS 'Полето без ключ.';
COMMENT ON COLUMN zakoni.unmatched.value IS 'Стойността при източника.';
COMMENT ON COLUMN zakoni.unmatched.reason IS 'Защо.';

CREATE TABLE zakoni.source_state (
  ref         text NOT NULL,
  role        text NOT NULL,
  status      text,
  error       text,
  rows        integer,
  sha256      text,
  version     text,
  last_read   timestamptz,
  last_ok     timestamptz,
  last_change timestamptz,
  CONSTRAINT source_state_pk PRIMARY KEY (ref),
  CONSTRAINT source_state_role_ck CHECK (role IN ('load', 'check', 'skip', 'same'))
);
COMMENT ON TABLE zakoni.source_state IS 'Последното четене на всеки от 14-те ресурса на набора, с думите на страницата „Източници“.';
COMMENT ON COLUMN zakoni.source_state.ref IS 'Ресурсът в data.egov.bg.';
COMMENT ON COLUMN zakoni.source_state.role IS 'Какво се прави с него: load зарежда се, check за сверка, skip прочетен и преброен, same същият файл като друг.';
COMMENT ON COLUMN zakoni.source_state.status IS 'Състоянието на български.';
COMMENT ON COLUMN zakoni.source_state.error IS 'Пълният текст на проблема.';
COMMENT ON COLUMN zakoni.source_state.rows IS 'Записите във файла без легендата.';
COMMENT ON COLUMN zakoni.source_state.sha256 IS 'sha256 на прочетения файл.';
COMMENT ON COLUMN zakoni.source_state.version IS 'Версията на ресурса при източника.';
COMMENT ON COLUMN zakoni.source_state.last_read IS 'Кога архивът е прочел портала за това четене.';
COMMENT ON COLUMN zakoni.source_state.last_ok IS 'Последното успешно четене.';
COMMENT ON COLUMN zakoni.source_state.last_change IS 'Последното четене, което е сменило данните.';

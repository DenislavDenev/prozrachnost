-- The serving layer of Тендер: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()), at the end of a build whose
-- rules all hold, and it points only to the shared serving tables pub.*, so it can be copied out with them and
-- without the rest of the database. The columns are those of the clean tables the pages always read.
--
-- People: the layer holds the roles of people only in entities that received public money, the names as
-- pub.role_name (decided in tools/danni/db/persons.toml), and no key of a person: person.id and the 'p:' and 'l:'
-- node ids are random tokens that every build makes anew (db/derive/85_shown.sql).
DO $$ BEGIN IF to_regnamespace('pub_tender') IS NULL THEN CREATE SCHEMA pub_tender; END IF; END $$;
COMMENT ON SCHEMA pub_tender IS 'Тендер, слой за показване: поръчки, договори, фирми, плащания и показваните роли на лица, готови за страниците.';

CREATE TABLE pub_tender.buyer (
  eik           text NOT NULL,
  eik_valid     boolean NOT NULL,
  name          text,
  type          text,
  main_activity text,
  locality      text,
  postal_code   text,
  nuts          text,
  CONSTRAINT buyer_pk PRIMARY KEY (eik)
);
COMMENT ON TABLE pub_tender.buyer IS 'Възложителите по ЕИК, с името и адреса от отворените данни на ЦАИС ЕОП.';

CREATE TABLE pub_tender.tender (
  unp                 text NOT NULL,
  tender_id           text,
  buyer_eik           text,
  subject             text,
  procedure_type      text,
  cpv                 text,
  cpv_description     text,
  contract_type       text,
  estimated_value     numeric,
  currency            text,
  estimated_eur       numeric,
  is_eu_funded        boolean,
  european_program    text,
  lots_count          integer,
  submission_deadline timestamp,
  published_at        timestamp,
  notice_type         text,
  is_cancelled        boolean,
  execution_nuts      text,
  source_day          date,
  synthetic           boolean NOT NULL,
  state               text,
  CONSTRAINT tender_pk PRIMARY KEY (unp)
);
CREATE INDEX tender_tender_id_idx ON pub_tender.tender (tender_id);
CREATE INDEX tender_buyer_eik_idx ON pub_tender.tender (buyer_eik);
COMMENT ON TABLE pub_tender.tender IS 'Поръчките по УНП: предмет, процедура, прогнозна стойност, срок и състояние.';

CREATE TABLE pub_tender.lot (
  unp             text NOT NULL,
  lot_no          integer NOT NULL,
  title           text,
  estimated_value numeric,
  currency        text,
  cpv             text,
  estimated_eur   numeric,
  status          text,
  CONSTRAINT lot_pk PRIMARY KEY (unp, lot_no)
);
COMMENT ON TABLE pub_tender.lot IS 'Обособените позиции на поръчките от отворените данни.';

CREATE TABLE pub_tender.contract (
  id                         text NOT NULL,
  source                     text NOT NULL,
  source_day                 date NOT NULL,
  notice_id                  text,
  unp                        text,
  contract_number            text,
  lot_no                     integer,
  buyer_eik                  text,
  supplier_display           text,
  awarded_to_group           boolean,
  subject                    text,
  tender_name                text,
  procedure_type             text,
  cpv                        text,
  cpv_description            text,
  contract_type              text,
  contract_date              date,
  published_at               timestamp,
  effective_date             date,
  date_basis                 text,
  value_initial              numeric,
  value_current              numeric,
  value_current_currency     text,
  currency                   text,
  fx_rate                    numeric,
  value_initial_eur          numeric,
  value_current_eur          numeric,
  amount_eur                 numeric,
  value_flag                 text NOT NULL,
  date_flag                  text NOT NULL,
  estimated_value            numeric,
  estimate_currency          text,
  estimated_eur              numeric,
  estimate_ratio             numeric,
  offers_count               integer,
  sme_offers_count           integer,
  disqualified_offers_count  integer,
  is_eu_funded               boolean,
  european_program           text,
  is_framework               boolean NOT NULL,
  framework_contract         text,
  linked_tenders             text,
  direct_award_justification text,
  award_method               text,
  legal_basis                text,
  has_subcontractors         boolean,
  supplier_nuts              text,
  supplier_size              text,
  contract_period_days       integer,
  annex_count                integer NOT NULL,
  CONSTRAINT contract_pk PRIMARY KEY (id)
);
CREATE INDEX contract_buyer_eik_idx ON pub_tender.contract (buyer_eik);
CREATE INDEX contract_unp_idx ON pub_tender.contract (unp);
CREATE INDEX contract_effective_date_idx ON pub_tender.contract (effective_date);
COMMENT ON TABLE pub_tender.contract IS 'Договорите: възложител, изпълнител, дати, стойности в оригиналната валута и в евро, брой оферти.';

CREATE TABLE pub_tender.contract_supplier (
  contract_id text NOT NULL,
  position    integer NOT NULL,
  party_key   text NOT NULL,
  eik         text,
  name        text,
  joint       boolean NOT NULL,
  CONSTRAINT contract_supplier_pk PRIMARY KEY (contract_id, position)
);
ALTER TABLE pub_tender.contract_supplier ADD CONSTRAINT contract_supplier_contract_id_fk FOREIGN KEY (contract_id) REFERENCES pub_tender.contract (id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX contract_supplier_party_key_idx ON pub_tender.contract_supplier (party_key);
CREATE INDEX contract_supplier_eik9_idx ON pub_tender.contract_supplier (left(eik, 9));
COMMENT ON TABLE pub_tender.contract_supplier IS 'Изпълнителите по всеки договор; обединение е по един ред за всеки член.';

CREATE TABLE pub_tender.amendment (
  contract_id   text NOT NULL,
  published_at  timestamp,
  source_day    date,
  last_value    numeric,
  current_value numeric,
  difference    numeric,
  currency      text,
  reason        text,
  description   text,
  n             integer NOT NULL,
  CONSTRAINT amendment_pk PRIMARY KEY (contract_id, n)
);
ALTER TABLE pub_tender.amendment ADD CONSTRAINT amendment_contract_id_fk FOREIGN KEY (contract_id) REFERENCES pub_tender.contract (id) DEFERRABLE INITIALLY DEFERRED;
COMMENT ON TABLE pub_tender.amendment IS 'Анексите към договорите с новата стойност и причината.';

CREATE TABLE pub_tender.subcontract (
  contract_id text NOT NULL,
  party_key   text NOT NULL,
  eik         text,
  name        text,
  percent     numeric,
  amount      numeric,
  n           integer NOT NULL,
  CONSTRAINT subcontract_pk PRIMARY KEY (contract_id, n)
);
CREATE INDEX subcontract_party_key_idx ON pub_tender.subcontract (party_key);
COMMENT ON TABLE pub_tender.subcontract IS 'Подизпълнителите, посочени в договора.';

CREATE TABLE pub_tender.source_record (
  entity text NOT NULL,
  ref    text NOT NULL,
  day    text NOT NULL,
  kind   text NOT NULL,
  idx    integer NOT NULL,
  CONSTRAINT source_record_pk PRIMARY KEY (entity, ref, day, kind, idx)
);
COMMENT ON TABLE pub_tender.source_record IS 'Къде в суровите файлове е всеки публикуван запис на договор или поръчка.';

CREATE TABLE pub_tender.award (
  row_id                    integer NOT NULL,
  unp                       text,
  tender_id                 text,
  lot_no                    integer,
  notice_id                 text,
  notice_type               text,
  published_at              timestamp,
  source_day                date,
  offers_count              integer,
  sme_offers_count          integer,
  disqualified_offers_count integer,
  link_oj                   text,
  CONSTRAINT award_pk PRIMARY KEY (row_id)
);
CREATE INDEX award_unp_idx ON pub_tender.award (unp);
COMMENT ON TABLE pub_tender.award IS 'Обявления за възложена поръчка без възлагане: позиция, дата, брой оферти.';

CREATE TABLE pub_tender.notice (
  notice_id    text NOT NULL,
  unp          text,
  kind         text,
  notice_type  text,
  published_at timestamp,
  source_day   date,
  CONSTRAINT notice_pk PRIMARY KEY (notice_id)
);
CREATE INDEX notice_unp_idx ON pub_tender.notice (unp);
COMMENT ON TABLE pub_tender.notice IS 'Обявленията от отворените данни с номера и вида им.';

CREATE TABLE pub_tender.annex_orphan (
  row_id          integer NOT NULL,
  unp             text,
  contract_number text,
  lot_no          integer,
  published_at    timestamp,
  source_day      date,
  last_value      numeric,
  current_value   numeric,
  currency        text,
  reason          text,
  CONSTRAINT annex_orphan_pk PRIMARY KEY (row_id)
);
CREATE INDEX annex_orphan_unp_idx ON pub_tender.annex_orphan (unp);
COMMENT ON TABLE pub_tender.annex_orphan IS 'Анекси, чийто договор не може да се определи еднозначно.';

CREATE TABLE pub_tender.company (
  key            text NOT NULL,
  eik            text,
  name           text,
  legal_form     text,
  seat           text,
  registered_on  date,
  deed_status    text,
  capital_eur    numeric,
  tr_status      text,
  tr_fetched_at  timestamptz,
  is_public_body boolean,
  CONSTRAINT company_pk PRIMARY KEY (key)
);
CREATE INDEX company_eik_idx ON pub_tender.company (eik);
CREATE INDEX company_eik9_idx ON pub_tender.company (left(eik, 9));
COMMENT ON TABLE pub_tender.company IS 'Фирмите: изпълнители, участници в оферти и дружества от Търговския регистър. Едноличният търговец е с наименованието си от регистъра.';

CREATE TABLE pub_tender.company_stats (
  key                     text NOT NULL,
  contracts               bigint,
  joint_contracts         bigint,
  amount_eur              numeric,
  amount_eur_sole         numeric,
  contracts_without_value bigint,
  frameworks              bigint,
  buyers                  bigint,
  first_contract          date,
  last_contract           date,
  CONSTRAINT company_stats_pk PRIMARY KEY (key)
);
COMMENT ON TABLE pub_tender.company_stats IS 'Обобщение по фирма: брой договори, стойност, възложители, първи и последен договор.';

CREATE TABLE pub_tender.buyer_stats (
  eik                     text NOT NULL,
  contracts               bigint,
  amount_eur              numeric,
  contracts_without_value bigint,
  suppliers               bigint,
  first_contract          date,
  last_contract           date,
  tenders                 bigint,
  CONSTRAINT buyer_stats_pk PRIMARY KEY (eik)
);
COMMENT ON TABLE pub_tender.buyer_stats IS 'Обобщение по възложител: договори, стойност, изпълнители, поръчки.';

CREATE TABLE pub_tender.tender_stats (
  unp            text NOT NULL,
  contracts      bigint,
  amount_eur     numeric,
  first_contract date,
  single_bid     bigint,
  CONSTRAINT tender_stats_pk PRIMARY KEY (unp)
);
COMMENT ON TABLE pub_tender.tender_stats IS 'Обобщение по поръчка: договори, стойност, договори с една оферта.';

CREATE TABLE pub_tender.tag_def (
  code         text NOT NULL,
  kind         text NOT NULL,
  label        text NOT NULL,
  formula      text NOT NULL,
  window_years integer,
  min_n        integer,
  threshold    numeric,
  version      text NOT NULL,
  CONSTRAINT tag_def_pk PRIMARY KEY (code)
);
COMMENT ON TABLE pub_tender.tag_def IS 'Автоматичните отбелязвания: формула, прозорец, праг.';

CREATE TABLE pub_tender.tag (
  entity_type text NOT NULL,
  entity_id   text NOT NULL,
  code        text NOT NULL,
  reason      text NOT NULL,
  evidence    jsonb NOT NULL,
  CONSTRAINT tag_pk PRIMARY KEY (entity_type, entity_id, code)
);
ALTER TABLE pub_tender.tag ADD CONSTRAINT tag_code_fk FOREIGN KEY (code) REFERENCES pub_tender.tag_def (code) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX tag_code_idx ON pub_tender.tag (code);
COMMENT ON TABLE pub_tender.tag IS 'Отбелязванията по фирма с числата зад тях. Индикатор, не обвинение.';

CREATE TABLE pub_tender.form_type (
  form_type integer,
  name      text,
  seen      bigint,
  CONSTRAINT form_type_pk PRIMARY KEY (form_type)
);
COMMENT ON TABLE pub_tender.form_type IS 'Името на всеки вид обявление в ЦАИС ЕОП, научено от отворените данни.';

CREATE TABLE pub_tender.contract_lot (
  row_id      integer NOT NULL,
  contract_id text,
  unp         text,
  lot_no      integer,
  CONSTRAINT contract_lot_pk PRIMARY KEY (row_id)
);
CREATE INDEX contract_lot_unp_idx ON pub_tender.contract_lot (unp);
COMMENT ON TABLE pub_tender.contract_lot IS 'Позицията на всеки договор, както я номерира ЦАИС ЕОП.';

CREATE TABLE pub_tender.offer_lot (
  unp           text,
  lot_no        integer,
  title         text,
  estimated_eur numeric,
  status        text,
  CONSTRAINT offer_lot_pk PRIMARY KEY (unp, lot_no)
);
COMMENT ON TABLE pub_tender.offer_lot IS 'Позициите на поръчките, както ги номерира ЦАИС ЕОП, с прогнозната стойност и състоянието.';

CREATE TABLE pub_tender.offer (
  unp          text,
  tender_id    bigint,
  lot_no       integer,
  round        integer,
  offer_id     bigint,
  bidder_name  text,
  bidder_eik   text,
  consortium   jsonb,
  submitted_at timestamptz,
  price        numeric,
  price_opened boolean,
  currency     text,
  price_eur    numeric,
  company_key  text,
  won          boolean,
  fetched_at   timestamptz,
  CONSTRAINT offer_pk PRIMARY KEY (tender_id, lot_no, round, offer_id)
);
CREATE INDEX offer_unp_lot_no_idx ON pub_tender.offer (unp, lot_no);
CREATE INDEX offer_company_key_idx ON pub_tender.offer (company_key);
COMMENT ON TABLE pub_tender.offer IS 'Офертите по поръчка и позиция, с цената в евро, фирмата и дали е спечелила. Организацията, подала офертата от свой профил, не се публикува.';

CREATE TABLE pub_tender.payment (
  resource_uri         text,
  row_no               integer,
  settlement_date      date,
  receiver_name        text,
  is_person            boolean,
  receiver_iban        text,
  amount               numeric,
  currency             text,
  amount_eur           numeric,
  reason               text,
  reg_date             date,
  reg_no               text,
  pay_code             text,
  fin_code             text,
  fin_name             text,
  organization         text,
  primary_organization text,
  primary_org_code     text,
  company_key          text,
  buyer_eik            text,
  CONSTRAINT payment_pk PRIMARY KEY (resource_uri, row_no)
);
CREATE INDEX payment_company_key_idx ON pub_tender.payment (company_key);
CREATE INDEX payment_buyer_eik_idx ON pub_tender.payment (buyer_eik);
CREATE INDEX payment_settlement_date_idx ON pub_tender.payment (settlement_date);
CREATE INDEX payment_primary_org_code_idx ON pub_tender.payment (primary_org_code);
COMMENT ON TABLE pub_tender.payment IS 'Плащанията от бюджета (СЕБРА), съпоставени с фирма и възложител. За физическо лице източникът не дава име.';

CREATE TABLE pub_tender.node_contract (
  node        text,
  contract_id text,
  joint       boolean,
  CONSTRAINT node_contract_pk PRIMARY KEY (node, contract_id)
);
CREATE INDEX node_contract_contract_id_idx ON pub_tender.node_contract (contract_id);
COMMENT ON TABLE pub_tender.node_contract IS 'Договорите на всяко дружество като възел на мрежата.';

CREATE TABLE pub_tender.buyer_place (
  eik               text NOT NULL,
  municipality      text,
  basis             text,
  municipality_code text,
  CONSTRAINT buyer_place_pk PRIMARY KEY (eik)
);
ALTER TABLE pub_tender.buyer_place ADD CONSTRAINT buyer_place_municipality_code_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX buyer_place_municipality_idx ON pub_tender.buyer_place (municipality);
COMMENT ON TABLE pub_tender.buyer_place IS 'Възложителите, поставени в община: самата община или нейно звено, или по адрес.';

CREATE TABLE pub_tender.edge (
  id              bigint NOT NULL,
  holder          text,
  company         text,
  holder_kind     text,
  holder_name     pub.role_name,
  role            text,
  view            text,
  share           text,
  field_ident     text,
  entry_no        text,
  sub_uic         text,
  valid_from      date,
  valid_to        date,
  uncertain_after date,
  observed_at     timestamptz,
  in_branch       boolean,
  share_pct       numeric,
  CONSTRAINT edge_pk PRIMARY KEY (id)
);
CREATE INDEX edge_holder_idx ON pub_tender.edge (holder);
CREATE INDEX edge_company_idx ON pub_tender.edge (company);
COMMENT ON TABLE pub_tender.edge IS 'Връзките в регистъра, които се показват: всички между дружества и ролите на лица само в дружества с публични пари.';

CREATE TABLE pub_tender.node (
  id    text NOT NULL,
  kind  text,
  label pub.role_name,
  ref   text,
  CONSTRAINT node_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_tender.node IS 'Възлите на мрежата с името им: дружества и показваните лица. Знакът на лице важи до следващото изграждане.';

CREATE TABLE pub_tender.person (
  id            text NOT NULL,
  name          pub.role_name,
  name_key      pub.role_name,
  indent_type   text,
  search_status text,
  search_total  integer,
  searched_at   timestamptz,
  CONSTRAINT person_pk PRIMARY KEY (id)
);
CREATE INDEX person_name_key_idx ON pub_tender.person (name_key);
COMMENT ON TABLE pub_tender.person IS 'Лицата с роля в дружество с публични пари. id е случаен знак, нов при всяко изграждане: не е ключ на човека.';

CREATE TABLE pub_tender.person_stats (
  id         text NOT NULL,
  companies  bigint,
  active     bigint,
  first_role date,
  contracts  bigint,
  amount_eur numeric,
  CONSTRAINT person_stats_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_tender.person_stats IS 'Обобщение по показвано лице, само върху показваните му роли.';

CREATE TABLE pub_tender.search_item (
  kind   text NOT NULL,
  ref    text NOT NULL,
  label  pub.role_name NOT NULL,
  sub    text,
  key    pub.role_name NOT NULL,
  weight numeric NOT NULL,
  words  tsvector NOT NULL,
  CONSTRAINT search_item_pk PRIMARY KEY (kind, ref)
);
CREATE INDEX search_item_key_trgm_idx ON pub_tender.search_item USING gin (key public.gin_trgm_ops);
CREATE INDEX search_item_words_idx ON pub_tender.search_item USING gin (words);
COMMENT ON TABLE pub_tender.search_item IS 'Индексът за търсене по име, ЕИК и УНП върху възложители, фирми, поръчки и показваните лица.';

CREATE TABLE pub_tender.eopsvc_announcement (
  tender_id  bigint NOT NULL,
  id         bigint NOT NULL,
  created_at timestamptz,
  title      text NOT NULL,
  gone_at    timestamptz,
  CONSTRAINT eopsvc_announcement_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE pub_tender.eopsvc_announcement IS 'Съобщенията по поръчката в ЦАИС ЕОП (разяснения, протоколи, решения).';

CREATE TABLE pub_tender.eopsvc_detail (
  tender_id         bigint NOT NULL,
  offers_from       timestamptz,
  offers_until      timestamptz,
  opening_at        timestamptz,
  prices_opening_at timestamptz,
  parent_tender_id  bigint,
  linked            jsonb,
  fetched_at        timestamptz NOT NULL,
  CONSTRAINT eopsvc_detail_pk PRIMARY KEY (tender_id)
);
COMMENT ON TABLE pub_tender.eopsvc_detail IS 'Сроковете за оферти, отварянето им и свързаните поръчки от страницата в ЦАИС ЕОП.';

CREATE TABLE pub_tender.eopsvc_publication (
  tender_id    bigint NOT NULL,
  id           bigint NOT NULL,
  form_type    integer,
  ted_number   text,
  sent_at      timestamptz,
  published_at timestamptz,
  gone_at      timestamptz,
  CONSTRAINT eopsvc_publication_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE pub_tender.eopsvc_publication IS 'Обявленията и решенията по поръчката, както ги изброява страницата в ЦАИС ЕОП.';

CREATE TABLE pub_tender.eopsvc_appeal (
  tender_id          bigint NOT NULL,
  register_id        text NOT NULL,
  proceedings_number text,
  kind               text,
  subjects           text[],
  initiators         text[],
  defendants         text[],
  interim_measures   boolean,
  imposed_measures   integer,
  imposed_penalties  integer,
  status             text,
  filed_on           date,
  started_on         date,
  last_decision_on   date,
  closed_on          date,
  link               text,
  gone_at            timestamptz,
  CONSTRAINT eopsvc_appeal_pk PRIMARY KEY (tender_id, register_id)
);
COMMENT ON TABLE pub_tender.eopsvc_appeal IS 'Жалбите до КЗК по поръчката. Име на жалбоподател физическо лице не се пази.';

CREATE TABLE pub_tender.eopsvc_document (
  tender_id  bigint NOT NULL,
  id         bigint NOT NULL,
  name       text,
  size       bigint,
  created_at timestamptz,
  gone_at    timestamptz,
  CONSTRAINT eopsvc_document_pk PRIMARY KEY (tender_id, id)
);
COMMENT ON TABLE pub_tender.eopsvc_document IS 'Прикачените документи на поръчката: име, размер, дата.';

CREATE TABLE pub_tender.eopsvc_queue (
  tender_id  bigint NOT NULL,
  status     text NOT NULL,
  fetched_at timestamptz,
  CONSTRAINT eopsvc_queue_pk PRIMARY KEY (tender_id)
);
COMMENT ON TABLE pub_tender.eopsvc_queue IS 'Прочетена ли е страницата на поръчката в ЦАИС ЕОП и кога.';

CREATE TABLE pub_tender.eop_day (
  day       date NOT NULL,
  published boolean NOT NULL,
  CONSTRAINT eop_day_pk PRIMARY KEY (day)
);
COMMENT ON TABLE pub_tender.eop_day IS 'Дните на отворените данни на ЦАИС ЕОП и дали източникът ги е публикувал.';

CREATE TABLE pub_tender.eop_file (
  day    date NOT NULL,
  kind   text NOT NULL,
  key    text NOT NULL,
  sha256 text NOT NULL,
  CONSTRAINT eop_file_pk PRIMARY KEY (day, kind)
);
COMMENT ON TABLE pub_tender.eop_file IS 'Файловете на всеки ден от отворените данни с адреса и sha256 на пазеното копие.';

CREATE TABLE pub_tender.build_state (
  id           integer NOT NULL,
  at           timestamptz,
  eop_last_day date,
  tr_last_read timestamptz,
  CONSTRAINT build_state_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_tender.build_state IS 'Кога е последното изграждане, до кой ден на ЕОП стига и кога последно е четен Търговският регистър.';

CREATE TABLE pub_tender.tr_coverage (
  eik        text NOT NULL,
  status     text NOT NULL,
  done_at    timestamptz,
  fetched_at timestamptz,
  CONSTRAINT tr_coverage_pk PRIMARY KEY (eik)
);
COMMENT ON TABLE pub_tender.tr_coverage IS 'Докъде е стигнало четенето на партидата на всяко дружество в Търговския регистър.';

CREATE TABLE pub_tender.source_stat (
  name  text NOT NULL,
  value bigint,
  at    timestamptz,
  CONSTRAINT source_stat_pk PRIMARY KEY (name)
);
COMMENT ON TABLE pub_tender.source_stat IS 'Броячите на страницата „Източници“: колко партиди и търсения по име са прочетени.';

CREATE TABLE pub_tender.run_state (
  step        text NOT NULL,
  status      text NOT NULL,
  started_at  timestamptz NOT NULL,
  finished_at timestamptz,
  error       text,
  CONSTRAINT run_state_pk PRIMARY KEY (step)
);
COMMENT ON TABLE pub_tender.run_state IS 'Последното пускане на всяка стъпка на приемането и резултатът му. Без стъпката, която публикува, за да не се мени слоят от самото публикуване.';

CREATE TABLE pub_tender.article (
  id           bigint NOT NULL,
  entity_type  text NOT NULL,
  entity_id    text NOT NULL,
  url          text NOT NULL,
  title        text NOT NULL,
  source       text,
  published_at timestamptz,
  status       text NOT NULL,
  CONSTRAINT article_pk PRIMARY KEY (id)
);
CREATE INDEX article_entity_idx ON pub_tender.article (entity_type, entity_id);
COMMENT ON TABLE pub_tender.article IS 'Потвърдените статии за фирми. Статии за лица не се публикуват.';

CREATE TABLE pub_tender.editorial_tag (
  id          bigint NOT NULL,
  entity_type text NOT NULL,
  entity_id   text NOT NULL,
  code        text NOT NULL,
  note        text NOT NULL,
  removed_at  timestamptz,
  CONSTRAINT editorial_tag_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_tender.editorial_tag IS 'Действащите редакторски отбелязвания към фирма, възложител или договор, с основанието им.';

-- The municipalities as the pages of Тендер have always addressed them: by the ЕИК of the administration, with
-- the names of the oblast and the codes of the regions. A view over the shared serving tables, no copy.
CREATE VIEW pub_tender.municipality AS
SELECT m.eik AS id, m.name_bg, m.name_en, m.drawn_as_eik AS drawn_as, o.label_bg AS oblast, o.nuts3_code AS nuts3,
       n3.parent_code AS nuts2, n2.parent_code AS nuts1, m.municipality_code
FROM pub.municipality m
JOIN pub.oblast o USING (oblast_code)
JOIN pub.nuts n3 ON n3.nuts_code = o.nuts3_code
JOIN pub.nuts n2 ON n2.nuts_code = n3.parent_code
WHERE NOT m.is_gone;
COMMENT ON VIEW pub_tender.municipality IS 'Общините по ЕИК на администрацията им, с областта и районите: изглед върху общите pub.municipality, pub.oblast и pub.nuts.';

-- The web role reads the serving layer only.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'tender_web') THEN
    GRANT USAGE ON SCHEMA pub_tender TO tender_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_tender TO tender_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_tender GRANT SELECT ON TABLES TO tender_web;
  END IF;
END $$;

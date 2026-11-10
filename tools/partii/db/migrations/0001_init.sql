-- Партии и пари in the shared database: the landing zone partii_src and the clean tables and views partii.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops.
--
-- Every accepted answer of a table of ЕРИК is one load (ops.load) and its rows stay in partii_src under its load_id,
-- so "what did the register say on day D" is the newest accepted load not later than D. Nothing is overwritten.
-- No table holds the name of a natural person: a donor who is a person has no name (CHECK below), the persons checked
-- against their income are kept only as counts and sums, the candidates who financed a campaign only as rows of their
-- participant. The landing zone keeps the amounts exactly as the source gives them (up to 20 decimals); the clean
-- tables are numeric(20,2).
DO $$ BEGIN
  IF to_regnamespace('partii_src') IS NULL THEN CREATE SCHEMA partii_src; END IF;
  IF to_regnamespace('partii') IS NULL THEN CREATE SCHEMA partii; END IF;
END $$;
COMMENT ON SCHEMA partii_src IS 'Партии и пари, приемна зона: отговорите на регистъра ЕРИК на Сметната палата, ред по ред, за всяко прието четене.';
COMMENT ON SCHEMA partii IS 'Партии и пари: избори, участници, агенции и парите на участниците, с изгледи за всяко четене на регистъра.';

-- ---------------------------------------------------------------- the landing zone

CREATE TABLE partii_src.election_row (
  load_id       bigint NOT NULL,
  erik_id       integer NOT NULL,
  title         text NOT NULL,
  list_position integer NOT NULL,
  CONSTRAINT election_row_pk PRIMARY KEY (load_id, erik_id),
  CONSTRAINT election_row_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE partii_src.election_row IS 'Изборите в списъка на началната страница на ЕРИК при едно четене: номер, заглавие и място в списъка.';

CREATE TABLE partii_src.page_fact (
  load_id         bigint NOT NULL,
  erik_id         integer NOT NULL,
  listed_cik      integer,
  listed_rik      integer,
  listed_oik      integer,
  threshold_eur   numeric,
  threshold_shown numeric,
  CONSTRAINT page_fact_pk PRIMARY KEY (load_id),
  CONSTRAINT page_fact_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE partii_src.page_fact IS 'Какво казва страница на избор при едно четене: броят регистрирани участници по комисия или прагът за декларация в евро (точната стойност от скрипта и текстът).';

CREATE TABLE partii_src.answer (
  load_id       bigint NOT NULL,
  erik_id       integer NOT NULL,
  source        text NOT NULL,
  variant       text NOT NULL DEFAULT '',
  records_total integer NOT NULL,
  total_money   numeric,
  total_in_kind numeric,
  total_sum     numeric,
  checked       integer,
  compliant     integer,
  CONSTRAINT answer_pk PRIMARY KEY (load_id),
  CONSTRAINT answer_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT answer_source_ck CHECK (source IN ('donors', 'candidates', 'agencies', 'results', 'participants', 'report'))
);
CREATE INDEX answer_erik_idx ON partii_src.answer (erik_id, source, variant);
COMMENT ON TABLE partii_src.answer IS 'Собствените броячи и суми на източника за един отговор на таблица: recordsTotal, общо парични и непарични; за проверката на доходите и броят проверени лица и тези със съответствие.';

CREATE TABLE partii_src.participant_row (
  load_id         bigint NOT NULL,
  source_id       integer NOT NULL,
  erik_id         integer NOT NULL,
  reg_in          smallint NOT NULL,
  kind            text NOT NULL,
  name            text NOT NULL,
  registry_number text,
  sub_election    text,
  municipality    text,
  settlement      text,
  city_area       text,
  CONSTRAINT participant_row_pk PRIMARY KEY (load_id, source_id),
  CONSTRAINT participant_row_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT participant_row_reg_ck CHECK (reg_in IN (1, 2, 3)),
  CONSTRAINT participant_row_kind_ck CHECK (kind IN ('party', 'coalition', 'committee', 'local_coalition'))
);
COMMENT ON TABLE partii_src.participant_row IS 'Ред от списъка на участниците в избор по комисия (1 ЦИК, 2 РИК, 3 ОИК), както го дава ЕРИК.';

CREATE TABLE partii_src.donation (
  load_id         bigint NOT NULL,
  row_no          integer NOT NULL,
  erik_id         integer NOT NULL,
  row_key         text NOT NULL,
  ordinal         integer NOT NULL,
  reg_in          smallint NOT NULL,
  source_id       integer NOT NULL,
  recipient_kind  text NOT NULL,
  recipient_name  text NOT NULL,
  registry_number text,
  recipient_label text NOT NULL,
  donation_day    date,
  kind            text NOT NULL,
  stated_kind     text NOT NULL,
  money           numeric NOT NULL,
  in_kind         numeric NOT NULL,
  decl            text NOT NULL,
  donor_kind      text NOT NULL,
  donor_name      text,
  CONSTRAINT donation_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT donation_key_uq UNIQUE (load_id, row_key, ordinal),
  CONSTRAINT donation_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT donation_kind_ck CHECK (kind IN ('money', 'in_kind', 'both')),
  CONSTRAINT donation_stated_ck CHECK (stated_kind IN ('money', 'in_kind')),
  CONSTRAINT donation_amount_ck CHECK (money >= 0 AND in_kind >= 0),
  CONSTRAINT donation_column_ck CHECK ((kind = 'money' AND in_kind = 0) OR (kind = 'in_kind' AND money = 0) OR (kind = 'both' AND money > 0 AND in_kind > 0)),
  CONSTRAINT donation_decl_ck CHECK (decl IN ('published', 'processing', 'none')),
  CONSTRAINT donation_donor_ck CHECK (donor_kind IN ('person', 'sole_trader', 'legal')),
  CONSTRAINT donation_no_person_ck CHECK (donor_name IS NULL OR donor_kind = 'legal')
);
CREATE INDEX donation_recipient_idx ON partii_src.donation (erik_id, reg_in, source_id);
COMMENT ON TABLE partii_src.donation IS 'Едно дарение в едно четене на регистъра на дарителите. Дарителят е физическо лице и няма име; row_key и ordinal разпознават същия ред във всяко четене и не съдържат име. Сумите са в евро, както ги показва ЕРИК.';

CREATE TABLE partii_src.candidate_fund (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  row_key           text NOT NULL,
  ordinal           integer NOT NULL,
  participant_label text NOT NULL,
  role              text,
  fund_day          date,
  money             numeric NOT NULL,
  in_kind           numeric NOT NULL,
  decl              text NOT NULL,
  CONSTRAINT candidate_fund_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT candidate_fund_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT candidate_fund_decl_ck CHECK (decl IN ('published', 'processing', 'none'))
);
COMMENT ON TABLE partii_src.candidate_fund IS 'Средства, които кандидат или член на инициативен комитет е дал за собствената кампания, в едно четене. Без име: само участникът, ролята, денят и сумите в евро.';

CREATE TABLE partii_src.agency_use (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  row_key           text NOT NULL,
  ordinal           integer NOT NULL,
  participant_label text NOT NULL,
  agency_name       text NOT NULL,
  agency_eik        text,
  is_eik_valid      boolean,
  agency_kind       text,
  descr             text,
  CONSTRAINT agency_use_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT agency_use_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
CREATE INDEX agency_use_eik_idx ON partii_src.agency_use (agency_eik);
COMMENT ON TABLE partii_src.agency_use IS 'Рекламна, социологическа или PR агенция, обявена от участник, в едно четене. is_eik_valid: контролната цифра на ЕИК е вярна (NULL: източникът не дава ЕИК).';

CREATE TABLE partii_src.report_row (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  layout            text NOT NULL,
  participant_label text NOT NULL,
  label_no          integer NOT NULL,
  filled            integer NOT NULL,
  CONSTRAINT report_row_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT report_row_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT report_row_layout_ck CHECK (layout IN ('party', 'coalition', 'committee', 'local_coalition'))
);
COMMENT ON TABLE partii_src.report_row IS 'Участник в обобщената справка от отчетите след изборите (файл на Excel по вид участник), в едно четене. filled: брой попълнени клетки.';

CREATE TABLE partii_src.report_cell (
  load_id    bigint NOT NULL,
  row_no     integer NOT NULL,
  col        integer NOT NULL,
  amount_raw text,
  amount     numeric,
  CONSTRAINT report_cell_pk PRIMARY KEY (load_id, row_no, col),
  CONSTRAINT report_cell_row_fk FOREIGN KEY (load_id, row_no) REFERENCES partii_src.report_row (load_id, row_no)
);
COMMENT ON TABLE partii_src.report_cell IS 'Клетка от справката: сумата както е във файла (amount_raw; „-1“ е непопълнено поле) и като число във валутата на отчета (NULL, когато не е попълнено).';

-- ---------------------------------------------------------------- the clean tables

CREATE TABLE partii.election (
  erik_id                  integer NOT NULL,
  title                    text NOT NULL,
  kind                     text NOT NULL,
  election_day             date,
  day_source               text,
  list_position            integer NOT NULL,
  first_seen_at            timestamptz NOT NULL,
  last_seen_at             timestamptz NOT NULL,
  gone_at                  timestamptz,
  threshold_eur            numeric,
  threshold_shown          numeric(20,2),
  listed_cik               integer,
  listed_rik               integer,
  listed_oik               integer,
  report_currency          text,
  report_currency_evidence text,
  CONSTRAINT election_pk PRIMARY KEY (erik_id),
  CONSTRAINT election_kind_ck CHECK (kind IN ('president', 'parliament', 'european', 'local', 'local_partial', 'local_new', 'other')),
  CONSTRAINT election_day_source_ck CHECK (day_source IN ('title', 'ref')),
  CONSTRAINT election_report_currency_fk FOREIGN KEY (report_currency) REFERENCES core.currency (currency_code)
);
COMMENT ON TABLE partii.election IS 'Избор в регистъра ЕРИК.';
COMMENT ON COLUMN partii.election.erik_id IS 'Номерът на избора в ЕРИК (electionId).';
COMMENT ON COLUMN partii.election.title IS 'Заглавието от началната страница на ЕРИК.';
COMMENT ON COLUMN partii.election.kind IS 'Вид: president, parliament, european, local, local_partial, local_new, other.';
COMMENT ON COLUMN partii.election.election_day IS 'Изборният ден; NULL, когато не е известен.';
COMMENT ON COLUMN partii.election.day_source IS 'Откъде е денят: title (от заглавието) или ref (ръчен справочник db/ref/election_day.csv).';
COMMENT ON COLUMN partii.election.list_position IS 'Място в списъка на началната страница (1 е най-новият).';
COMMENT ON COLUMN partii.election.first_seen_at IS 'Първото четене, в което изборът е в списъка.';
COMMENT ON COLUMN partii.election.last_seen_at IS 'Последното прието четене, в което изборът е в списъка.';
COMMENT ON COLUMN partii.election.gone_at IS 'От кога изборът вече не е в регистъра; редовете му остават.';
COMMENT ON COLUMN partii.election.threshold_eur IS 'Прагът за декларация за произход, точната стойност от скрипта на страницата, в евро.';
COMMENT ON COLUMN partii.election.threshold_shown IS 'Прагът, както е написан в текста на страницата, в евро.';
COMMENT ON COLUMN partii.election.listed_cik IS 'Брой участници, регистрирани в ЦИК, по страницата на избора.';
COMMENT ON COLUMN partii.election.listed_rik IS 'Брой участници, регистрирани в РИК, по страницата на избора.';
COMMENT ON COLUMN partii.election.listed_oik IS 'Брой участници, регистрирани в ОИК, по страницата на избора.';
COMMENT ON COLUMN partii.election.report_currency IS 'Валутата на отчетите след изборите (BGN или EUR); NULL, когато не е установена.';
COMMENT ON COLUMN partii.election.report_currency_evidence IS 'Проверката на валутата по данните: колко участници съвпадат при лева и колко при евро.';

CREATE TABLE partii.participant (
  participant_id  bigint GENERATED ALWAYS AS IDENTITY,
  erik_id         integer NOT NULL,
  reg_in          smallint NOT NULL,
  source_id       integer NOT NULL,
  kind            text NOT NULL,
  name            text NOT NULL,
  name_norm       text NOT NULL,
  registry_number text,
  sub_election    text,
  municipality    text,
  settlement      text,
  city_area       text,
  label           text,
  is_listed       boolean NOT NULL DEFAULT false,
  first_seen_at   timestamptz NOT NULL,
  last_seen_at    timestamptz NOT NULL,
  CONSTRAINT participant_pk PRIMARY KEY (participant_id),
  CONSTRAINT participant_source_uq UNIQUE (erik_id, reg_in, source_id),
  CONSTRAINT participant_erik_id_fk FOREIGN KEY (erik_id) REFERENCES partii.election (erik_id),
  CONSTRAINT participant_reg_ck CHECK (reg_in IN (1, 2, 3)),
  CONSTRAINT participant_kind_ck CHECK (kind IN ('party', 'coalition', 'committee', 'local_coalition'))
);
CREATE INDEX participant_name_norm_idx ON partii.participant (name_norm);
CREATE INDEX participant_label_idx ON partii.participant (erik_id, label);
COMMENT ON TABLE partii.participant IS 'Участник в избор (партия, коалиция, инициативен комитет, местна коалиция), както е регистриран за този избор. Един и същ участник в различни избори е отделен ред: ЕРИК не дава общ номер.';
COMMENT ON COLUMN partii.participant.participant_id IS 'Наш пореден номер.';
COMMENT ON COLUMN partii.participant.erik_id IS 'Изборът.';
COMMENT ON COLUMN partii.participant.reg_in IS 'Комисията, в която е регистриран: 1 ЦИК, 2 РИК, 3 ОИК.';
COMMENT ON COLUMN partii.participant.source_id IS 'Номерът на участника в ЕРИК в рамките на комисията.';
COMMENT ON COLUMN partii.participant.kind IS 'Вид: party, coalition, committee, local_coalition.';
COMMENT ON COLUMN partii.participant.name IS 'Регистрираното име, както е в източника. Инициативният комитет носи името на кандидата си.';
COMMENT ON COLUMN partii.participant.name_norm IS 'Името с малки букви и без препинателни знаци, за търсене.';
COMMENT ON COLUMN partii.participant.registry_number IS 'Регистрационният номер в комисията.';
COMMENT ON COLUMN partii.participant.sub_election IS 'Местни избори: видът на избора (за кмет на кметство и т.н.), както го пише източникът.';
COMMENT ON COLUMN partii.participant.municipality IS 'Местни избори: общината, както я пише източникът.';
COMMENT ON COLUMN partii.participant.settlement IS 'Местни избори: кметството, както го пише източникът.';
COMMENT ON COLUMN partii.participant.city_area IS 'Местни избори: районът, както го пише източникът.';
COMMENT ON COLUMN partii.participant.label IS 'Текстът, с който другите таблици на ЕРИК назовават участника.';
COMMENT ON COLUMN partii.participant.is_listed IS 'Участникът е в списъка на участниците (а не само получател на дарение).';
COMMENT ON COLUMN partii.participant.first_seen_at IS 'Първото четене, в което участникът се среща.';
COMMENT ON COLUMN partii.participant.last_seen_at IS 'Последното четене, в което участникът се среща.';

CREATE TABLE partii.participant_label (
  erik_id        integer NOT NULL,
  label          text NOT NULL,
  participant_id bigint NOT NULL,
  CONSTRAINT participant_label_pk PRIMARY KEY (erik_id, label),
  CONSTRAINT participant_label_participant_id_fk FOREIGN KEY (participant_id) REFERENCES partii.participant (participant_id)
);
COMMENT ON TABLE partii.participant_label IS 'Текст, който назовава точно един участник в избора. Ред, чийто текст е на двама участници или на никого, не е тук и остава без връзка.';
COMMENT ON COLUMN partii.participant_label.erik_id IS 'Изборът.';
COMMENT ON COLUMN partii.participant_label.label IS 'Текстът, както е в редовете за кандидати и агенции.';
COMMENT ON COLUMN partii.participant_label.participant_id IS 'Единственият участник с този текст.';

CREATE TABLE partii.report_label (
  erik_id        integer NOT NULL,
  layout         text NOT NULL,
  label          text NOT NULL,
  participant_id bigint NOT NULL,
  CONSTRAINT report_label_pk PRIMARY KEY (erik_id, layout, label),
  CONSTRAINT report_label_participant_id_fk FOREIGN KEY (participant_id) REFERENCES partii.participant (participant_id)
);
COMMENT ON TABLE partii.report_label IS 'Име в справката от отчетите, което сочи точно един участник от същия вид в избора.';
COMMENT ON COLUMN partii.report_label.erik_id IS 'Изборът.';
COMMENT ON COLUMN partii.report_label.layout IS 'Видът на справката: party, coalition, committee, local_coalition.';
COMMENT ON COLUMN partii.report_label.label IS 'Името на реда в справката.';
COMMENT ON COLUMN partii.report_label.participant_id IS 'Единственият участник с това име.';

CREATE TABLE partii.report_column (
  layout      text NOT NULL,
  col         integer NOT NULL,
  code        text NOT NULL,
  label       text NOT NULL,
  section     text NOT NULL,
  depth       integer NOT NULL,
  parent_code text,
  CONSTRAINT report_column_pk PRIMARY KEY (layout, col),
  CONSTRAINT report_column_code_uq UNIQUE (layout, code)
);
COMMENT ON TABLE partii.report_column IS 'Редовете на формата на отчета за всеки вид участник, от заглавията на справката.';
COMMENT ON COLUMN partii.report_column.layout IS 'Видът на справката.';
COMMENT ON COLUMN partii.report_column.col IS 'Поредната колона във файла (без колоната с името).';
COMMENT ON COLUMN partii.report_column.code IS 'Код на реда: раздел и номер, например income.1.1 или expense.total.';
COMMENT ON COLUMN partii.report_column.label IS 'Заглавието на реда, както е във файла.';
COMMENT ON COLUMN partii.report_column.section IS 'Раздел: income, expense, in_kind, total.';
COMMENT ON COLUMN partii.report_column.depth IS 'Дълбочина в йерархията на формата (0 е общ ред).';
COMMENT ON COLUMN partii.report_column.parent_code IS 'Кодът на обобщаващия ред; NULL за ред от първо ниво.';

CREATE TABLE partii.agency (
  eik               text NOT NULL,
  name              text NOT NULL,
  name_count        integer NOT NULL,
  election_count    integer NOT NULL,
  participant_count integer NOT NULL,
  row_count         integer NOT NULL,
  org_eik           text,
  CONSTRAINT agency_pk PRIMARY KEY (eik),
  CONSTRAINT agency_org_eik_fk FOREIGN KEY (org_eik) REFERENCES core.org (eik)
);
COMMENT ON TABLE partii.agency IS 'Агенция, обявена от участници в избори, по ЕИК с вярна контролна цифра. Обявяването не показва платена сума.';
COMMENT ON COLUMN partii.agency.eik IS 'ЕИК, както е обявен, с вярна контролна цифра.';
COMMENT ON COLUMN partii.agency.name IS 'Най-честото изписване на името.';
COMMENT ON COLUMN partii.agency.name_count IS 'Брой различни изписвания на името.';
COMMENT ON COLUMN partii.agency.election_count IS 'В колко избора е обявена.';
COMMENT ON COLUMN partii.agency.participant_count IS 'От колко регистрации на участници е обявена.';
COMMENT ON COLUMN partii.agency.row_count IS 'Брой редове в регистъра.';
COMMENT ON COLUMN partii.agency.org_eik IS 'Същият ЕИК в общия регистър на организациите; NULL, когато организацията още не е там.';

CREATE TABLE partii.participant_money (
  participant_id         bigint NOT NULL,
  donation_count         integer NOT NULL,
  donated_money          numeric(20,2) NOT NULL,
  donated_in_kind        numeric(20,2) NOT NULL,
  over_threshold         integer NOT NULL,
  over_threshold_no_decl integer NOT NULL,
  candidate_rows         integer NOT NULL,
  candidate_money        numeric(20,2) NOT NULL,
  candidate_in_kind      numeric(20,2) NOT NULL,
  agency_count           integer NOT NULL,
  report_income          numeric(20,2),
  report_expense         numeric(20,2),
  report_total           numeric(20,2),
  report_currency        text,
  report_income_eur      numeric(20,2),
  report_expense_eur     numeric(20,2),
  report_total_eur       numeric(20,2),
  CONSTRAINT participant_money_pk PRIMARY KEY (participant_id),
  CONSTRAINT participant_money_participant_id_fk FOREIGN KEY (participant_id) REFERENCES partii.participant (participant_id),
  CONSTRAINT participant_money_report_currency_fk FOREIGN KEY (report_currency) REFERENCES core.currency (currency_code)
);
COMMENT ON TABLE partii.participant_money IS 'Какво казва всяка таблица на ЕРИК за участника в последното прието четене. Паричните и непаричните са отделни и не се събират.';
COMMENT ON COLUMN partii.participant_money.participant_id IS 'Участникът.';
COMMENT ON COLUMN partii.participant_money.donation_count IS 'Брой дарения (редове в регистъра на дарителите).';
COMMENT ON COLUMN partii.participant_money.donated_money IS 'Парични дарения, евро.';
COMMENT ON COLUMN partii.participant_money.donated_in_kind IS 'Стойност на непаричните дарения, евро.';
COMMENT ON COLUMN partii.participant_money.over_threshold IS 'Брой дарения от прага за декларация нагоре.';
COMMENT ON COLUMN partii.participant_money.over_threshold_no_decl IS 'От тях без декларация за произход в регистъра.';
COMMENT ON COLUMN partii.participant_money.candidate_rows IS 'Брой редове средства от кандидати и членове на инициативен комитет.';
COMMENT ON COLUMN partii.participant_money.candidate_money IS 'Парични средства от кандидати, евро.';
COMMENT ON COLUMN partii.participant_money.candidate_in_kind IS 'Непарични средства от кандидати, евро.';
COMMENT ON COLUMN partii.participant_money.agency_count IS 'Брой обявени агенции.';
COMMENT ON COLUMN partii.participant_money.report_income IS 'Общ размер на набраните средства по отчета, във валутата на отчета.';
COMMENT ON COLUMN partii.participant_money.report_expense IS 'Общ размер на разходите по отчета, във валутата на отчета.';
COMMENT ON COLUMN partii.participant_money.report_total IS 'Общ размер на финансирането по отчета, във валутата на отчета.';
COMMENT ON COLUMN partii.participant_money.report_currency IS 'Валутата на отчета; NULL, когато няма отчет или валутата не е установена.';
COMMENT ON COLUMN partii.participant_money.report_income_eur IS 'Набраните средства в евро по фиксирания курс.';
COMMENT ON COLUMN partii.participant_money.report_expense_eur IS 'Разходите в евро по фиксирания курс.';
COMMENT ON COLUMN partii.participant_money.report_total_eur IS 'Финансирането в евро по фиксирания курс.';

CREATE TABLE partii.observation (
  indicator_code text NOT NULL,
  erik_id        integer NOT NULL,
  participant_id bigint,
  period_kind    text NOT NULL,
  period_start   date,
  period_end     date,
  amount         numeric(20,2),
  unit_code      text,
  amount_source  numeric,
  unit_source    text,
  status         text NOT NULL,
  read_at        timestamptz NOT NULL,
  source_url     text NOT NULL,
  load_id        bigint NOT NULL,
  CONSTRAINT observation_uq UNIQUE NULLS NOT DISTINCT (indicator_code, erik_id, participant_id),
  CONSTRAINT observation_indicator_code_fk FOREIGN KEY (indicator_code) REFERENCES core.indicator (indicator_code),
  CONSTRAINT observation_erik_id_fk FOREIGN KEY (erik_id) REFERENCES partii.election (erik_id),
  CONSTRAINT observation_participant_id_fk FOREIGN KEY (participant_id) REFERENCES partii.participant (participant_id),
  CONSTRAINT observation_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT observation_status_ck CHECK (status IN ('final', 'provisional'))
);
COMMENT ON TABLE partii.observation IS 'Числата на изборите и участниците по общия договор за наблюдения: едно число на ред, с оригинала до него. Историята на числото е в четенията (partii.reading).';
COMMENT ON COLUMN partii.observation.indicator_code IS 'Показателят.';
COMMENT ON COLUMN partii.observation.erik_id IS 'Изборът, за който е числото.';
COMMENT ON COLUMN partii.observation.participant_id IS 'Участникът; NULL за число за целия избор.';
COMMENT ON COLUMN partii.observation.period_kind IS 'Вид на периода: election.';
COMMENT ON COLUMN partii.observation.period_start IS 'Изборният ден; NULL, когато не е известен.';
COMMENT ON COLUMN partii.observation.period_end IS 'Изборният ден; NULL, когато не е известен.';
COMMENT ON COLUMN partii.observation.amount IS 'Стойността в общата единица (евро или брой).';
COMMENT ON COLUMN partii.observation.unit_code IS 'Единицата на стойността.';
COMMENT ON COLUMN partii.observation.amount_source IS 'Стойността, както е в източника.';
COMMENT ON COLUMN partii.observation.unit_source IS 'Единицата в източника (EUR, BGN, count).';
COMMENT ON COLUMN partii.observation.status IS 'provisional, докато архивът чете избора всеки ден; иначе final.';
COMMENT ON COLUMN partii.observation.read_at IS 'Кога е четен отговорът, от който е числото.';
COMMENT ON COLUMN partii.observation.source_url IS 'Адресът на страницата в ЕРИК.';
COMMENT ON COLUMN partii.observation.load_id IS 'Четенето, от което е числото.';

CREATE TABLE partii.unmatched (
  kind      text NOT NULL,
  key       text NOT NULL,
  row_count integer NOT NULL,
  amount    numeric(20,2),
  reason    text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (kind, key)
);
COMMENT ON TABLE partii.unmatched IS 'Редове, които не са свързани с участник или с фирма, с причината. Нищо не се изхвърля.';
COMMENT ON COLUMN partii.unmatched.kind IS 'Вид: agency, participant:candidates, participant:agencies, participant:report.';
COMMENT ON COLUMN partii.unmatched.key IS 'Текстът от източника.';
COMMENT ON COLUMN partii.unmatched.row_count IS 'Брой редове с този текст.';
COMMENT ON COLUMN partii.unmatched.amount IS 'Сумата на редовете в евро, където има такава.';
COMMENT ON COLUMN partii.unmatched.reason IS 'Защо няма връзка.';

-- ---------------------------------------------------------------- the readings and the rows the pages show

-- one accepted answer of one table of one election; a held answer counts from its confirmation
CREATE VIEW partii.reading AS
SELECT l.load_id, a.erik_id, a.source AS tbl, a.variant, l.loaded_at AS read_at, l.status, l.is_current, f.sha256, f.path,
       a.records_total, a.total_money, a.total_in_kind, a.total_sum, a.checked, a.compliant
FROM ops.load l JOIN partii_src.answer a USING (load_id) LEFT JOIN ops.raw_file f USING (raw_file_id)
WHERE l.tool = 'partii' AND l.status IN ('loaded', 'confirmed');
COMMENT ON VIEW partii.reading IS 'Приетите четения на таблиците на ЕРИК: кога, с какъв файл и със собствените броячи и суми на източника.';

-- as of a moment: the newest reading of every table of every election accepted by then
CREATE FUNCTION partii.as_of(t timestamptz) RETURNS SETOF partii.reading LANGUAGE sql STABLE AS $$
  SELECT DISTINCT ON (erik_id, tbl, variant) * FROM partii.reading WHERE read_at <= t
  ORDER BY erik_id, tbl, variant, read_at DESC, load_id DESC $$;
COMMENT ON FUNCTION partii.as_of(timestamptz) IS 'Състоянието към момент: най-новото прието четене на всяка таблица на всеки избор до този момент.';

CREATE VIEW partii.donation AS
SELECT d.load_id, d.row_no, d.erik_id, d.row_key, d.ordinal, p.participant_id, d.reg_in, d.source_id, d.recipient_label, d.donation_day, d.kind,
       d.stated_kind, d.money, d.in_kind, d.decl, d.donor_kind, d.donor_name
FROM partii_src.donation d JOIN partii.participant p USING (erik_id, reg_in, source_id);
COMMENT ON VIEW partii.donation IS 'Даренията от всяко четене, с участника получател. Без име на дарител физическо лице.';

CREATE VIEW partii.candidate_fund AS
SELECT f.load_id, f.row_no, f.erik_id, f.row_key, f.ordinal, f.participant_label, x.participant_id, f.role, f.fund_day, f.money, f.in_kind, f.decl
FROM partii_src.candidate_fund f LEFT JOIN partii.participant_label x ON x.erik_id = f.erik_id AND x.label = f.participant_label;
COMMENT ON VIEW partii.candidate_fund IS 'Средствата на кандидати от всяко четене, с участника, когато текстът му е еднозначен.';

CREATE VIEW partii.agency_use AS
SELECT u.load_id, u.row_no, u.erik_id, u.participant_label, x.participant_id, u.agency_name, u.agency_eik, u.is_eik_valid, u.agency_kind, u.descr
FROM partii_src.agency_use u LEFT JOIN partii.participant_label x ON x.erik_id = u.erik_id AND x.label = u.participant_label;
COMMENT ON VIEW partii.agency_use IS 'Обявените агенции от всяко четене, с участника, когато текстът му е еднозначен.';

CREATE VIEW partii.report_row AS
SELECT r.load_id, r.row_no, r.erik_id, r.layout, r.participant_label, x.participant_id, r.filled
FROM partii_src.report_row r LEFT JOIN partii.report_label x ON x.erik_id = r.erik_id AND x.layout = r.layout AND x.label = r.participant_label AND r.label_no = 1;
COMMENT ON VIEW partii.report_row IS 'Участниците в справките от отчетите от всяко четене, с участника, когато името е еднозначно.';

CREATE VIEW partii.report_cell AS
SELECT load_id, row_no, col, amount_raw, amount FROM partii_src.report_cell;
COMMENT ON VIEW partii.report_cell IS 'Клетките на справките от отчетите от всяко четене.';

-- what the sources page says about the answers of the register (no personal data)
CREATE VIEW partii.source_state AS
SELECT l.load_id, l.source, l.ref, l.status, l.reason, l.is_current, l.loaded_at, l.rows
FROM ops.load l WHERE l.tool = 'partii';
COMMENT ON VIEW partii.source_state IS 'Всички отговори на източника с тяхното състояние: приет, същият, задържан, отхвърлен.';

CREATE VIEW partii.run_state AS
SELECT run_id, step, started_at, finished_at, status FROM ops.run WHERE tool = 'partii';
COMMENT ON VIEW partii.run_state IS 'Изпълненията на стъпките на инструмента.';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'partii_web') THEN
    GRANT USAGE ON SCHEMA partii TO partii_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA partii TO partii_web;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA partii TO partii_web;
  END IF;
END $$;

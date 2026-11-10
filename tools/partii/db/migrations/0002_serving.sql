-- Партии и пари, the serving layer: pub_partii is what the pages, the CSV and the JSON read, and nothing else.
-- It is written only by pipeline.publish (db/publish/*.sql), in one transaction, after every rule has passed, so a
-- page never sees half an update. It holds every accepted reading with its rows (the pages can ask for any day), and
-- no name of a natural person, as the tables it is filled from.
DO $$ BEGIN IF to_regnamespace('pub_partii') IS NULL THEN CREATE SCHEMA pub_partii; END IF; END $$;
COMMENT ON SCHEMA pub_partii IS 'Партии и пари, слой за показване: изборите, участниците, приетите четения на регистъра ЕРИК с редовете им и състоянието на източника. Оттук четат страниците.';

CREATE TABLE pub_partii.election (LIKE partii.election INCLUDING COMMENTS, CONSTRAINT election_pk PRIMARY KEY (erik_id));
COMMENT ON TABLE pub_partii.election IS 'Избор в регистъра ЕРИК.';
CREATE TABLE pub_partii.participant (LIKE partii.participant INCLUDING COMMENTS, CONSTRAINT participant_pk PRIMARY KEY (participant_id),
  CONSTRAINT participant_erik_id_fk FOREIGN KEY (erik_id) REFERENCES pub_partii.election (erik_id) DEFERRABLE INITIALLY DEFERRED);
CREATE INDEX participant_name_norm_idx ON pub_partii.participant (name_norm);
CREATE INDEX participant_source_idx ON pub_partii.participant (erik_id, reg_in, source_id);
COMMENT ON TABLE pub_partii.participant IS 'Участник в избор, както е регистриран за този избор.';
CREATE TABLE pub_partii.participant_money (LIKE partii.participant_money INCLUDING COMMENTS, CONSTRAINT participant_money_pk PRIMARY KEY (participant_id),
  CONSTRAINT participant_money_participant_id_fk FOREIGN KEY (participant_id) REFERENCES pub_partii.participant (participant_id) DEFERRABLE INITIALLY DEFERRED);
COMMENT ON TABLE pub_partii.participant_money IS 'Какво казва всяка таблица на ЕРИК за участника в последното прието четене.';
CREATE TABLE pub_partii.report_column (LIKE partii.report_column INCLUDING COMMENTS, CONSTRAINT report_column_pk PRIMARY KEY (layout, col));
COMMENT ON TABLE pub_partii.report_column IS 'Редовете на формата на отчета за всеки вид участник.';
CREATE TABLE pub_partii.unmatched (LIKE partii.unmatched INCLUDING COMMENTS, CONSTRAINT unmatched_pk PRIMARY KEY (kind, key));
COMMENT ON TABLE pub_partii.unmatched IS 'Редове, които не са свързани с участник или с фирма, с причината.';
CREATE TABLE pub_partii.agency (
  eik               text NOT NULL,
  name              text NOT NULL,
  name_count        integer NOT NULL,
  election_count    integer NOT NULL,
  participant_count integer NOT NULL,
  row_count         integer NOT NULL,
  CONSTRAINT agency_pk PRIMARY KEY (eik)
);
COMMENT ON TABLE pub_partii.agency IS 'Агенция, обявена от участници в избори, по ЕИК с вярна контролна цифра.';
COMMENT ON COLUMN pub_partii.agency.eik IS 'ЕИК с вярна контролна цифра.';
COMMENT ON COLUMN pub_partii.agency.name IS 'Най-честото изписване на името.';
COMMENT ON COLUMN pub_partii.agency.name_count IS 'Брой различни изписвания на името.';
COMMENT ON COLUMN pub_partii.agency.election_count IS 'В колко избора е обявена.';
COMMENT ON COLUMN pub_partii.agency.participant_count IS 'От колко регистрации на участници е обявена.';
COMMENT ON COLUMN pub_partii.agency.row_count IS 'Брой редове в регистъра.';

CREATE TABLE pub_partii.reading (
  load_id       bigint NOT NULL,
  erik_id       integer NOT NULL,
  tbl           text NOT NULL,
  variant       text NOT NULL,
  read_at       timestamptz NOT NULL,
  status        text NOT NULL,
  sha256        text,
  records_total integer NOT NULL,
  total_money   numeric,
  total_in_kind numeric,
  total_sum     numeric,
  checked       integer,
  compliant     integer,
  CONSTRAINT reading_pk PRIMARY KEY (load_id),
  CONSTRAINT reading_erik_id_fk FOREIGN KEY (erik_id) REFERENCES pub_partii.election (erik_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX reading_as_of_idx ON pub_partii.reading (erik_id, tbl, variant, read_at);
COMMENT ON TABLE pub_partii.reading IS 'Прието четене на таблица на ЕРИК за избор: кога, sha256 на файла и собствените броячи и суми на източника.';
COMMENT ON COLUMN pub_partii.reading.load_id IS 'Номерът на четенето.';
COMMENT ON COLUMN pub_partii.reading.erik_id IS 'Изборът.';
COMMENT ON COLUMN pub_partii.reading.tbl IS 'Таблицата: donors, candidates, agencies, results, participants, report.';
COMMENT ON COLUMN pub_partii.reading.variant IS 'За участници: комисията (1 ЦИК, 2 РИК, 3 ОИК); за справка: видът участник (0-3).';
COMMENT ON COLUMN pub_partii.reading.read_at IS 'Кога архивът е прочел отговора (за задържан отговор: кога е потвърден).';
COMMENT ON COLUMN pub_partii.reading.status IS 'loaded или confirmed (приет след задържане).';
COMMENT ON COLUMN pub_partii.reading.sha256 IS 'sha256 на архивирания файл.';
COMMENT ON COLUMN pub_partii.reading.records_total IS 'Броячът на източника (recordsTotal); за справка: участниците във файла.';
COMMENT ON COLUMN pub_partii.reading.total_money IS 'Общо парични по източника, евро.';
COMMENT ON COLUMN pub_partii.reading.total_in_kind IS 'Общо непарични по източника, евро.';
COMMENT ON COLUMN pub_partii.reading.total_sum IS 'Общо по източника (проверка на доходите), евро.';
COMMENT ON COLUMN pub_partii.reading.checked IS 'Проверка на доходите: брой проверени лица.';
COMMENT ON COLUMN pub_partii.reading.compliant IS 'Проверка на доходите: брой лица със съответствие.';

CREATE FUNCTION pub_partii.as_of(t timestamptz) RETURNS SETOF pub_partii.reading LANGUAGE sql STABLE AS $$
  SELECT DISTINCT ON (erik_id, tbl, variant) * FROM pub_partii.reading WHERE read_at <= t
  ORDER BY erik_id, tbl, variant, read_at DESC, load_id DESC $$;
COMMENT ON FUNCTION pub_partii.as_of(timestamptz) IS 'Състоянието към момент: най-новото прието четене на всяка таблица на всеки избор до този момент.';

CREATE TABLE pub_partii.donation (
  load_id         bigint NOT NULL,
  row_no          integer NOT NULL,
  erik_id         integer NOT NULL,
  row_key         text NOT NULL,
  ordinal         integer NOT NULL,
  participant_id  bigint NOT NULL,
  donation_day    date,
  kind            text NOT NULL,
  stated_kind     text NOT NULL,
  money           numeric NOT NULL,
  in_kind         numeric NOT NULL,
  decl            text NOT NULL,
  donor_kind      text NOT NULL,
  donor_name      text,
  CONSTRAINT donation_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT donation_load_id_fk FOREIGN KEY (load_id) REFERENCES pub_partii.reading (load_id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT donation_participant_id_fk FOREIGN KEY (participant_id) REFERENCES pub_partii.participant (participant_id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT donation_no_person_ck CHECK (donor_name IS NULL OR donor_kind = 'legal')
);
CREATE INDEX donation_participant_idx ON pub_partii.donation (participant_id, load_id);
COMMENT ON TABLE pub_partii.donation IS 'Едно дарение в едно прието четене. Дарителят е физическо лице и няма име. Сумите са в евро, както ги показва ЕРИК.';
COMMENT ON COLUMN pub_partii.donation.load_id IS 'Четенето.';
COMMENT ON COLUMN pub_partii.donation.row_no IS 'Поредният ред в отговора.';
COMMENT ON COLUMN pub_partii.donation.erik_id IS 'Изборът.';
COMMENT ON COLUMN pub_partii.donation.row_key IS 'Идентичност на реда между четенията (без име в нея).';
COMMENT ON COLUMN pub_partii.donation.ordinal IS 'Пореден номер сред напълно еднаквите редове.';
COMMENT ON COLUMN pub_partii.donation.participant_id IS 'Получателят.';
COMMENT ON COLUMN pub_partii.donation.donation_day IS 'Датата на дарението; NULL, когато източникът няма дата.';
COMMENT ON COLUMN pub_partii.donation.kind IS 'money, in_kind или both: по колоната, в която стои сумата.';
COMMENT ON COLUMN pub_partii.donation.stated_kind IS 'Видът, както го пише източникът.';
COMMENT ON COLUMN pub_partii.donation.money IS 'Размер на паричното дарение, евро.';
COMMENT ON COLUMN pub_partii.donation.in_kind IS 'Стойност на непаричното дарение, евро.';
COMMENT ON COLUMN pub_partii.donation.decl IS 'Декларация за произход: published, processing, none.';
COMMENT ON COLUMN pub_partii.donation.donor_kind IS 'person, sole_trader или legal.';
COMMENT ON COLUMN pub_partii.donation.donor_name IS 'Име само на юридическо лице; за хора винаги NULL.';

CREATE TABLE pub_partii.candidate_fund (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  participant_label text NOT NULL,
  participant_id    bigint,
  role              text,
  fund_day          date,
  money             numeric NOT NULL,
  in_kind           numeric NOT NULL,
  decl              text NOT NULL,
  CONSTRAINT candidate_fund_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT candidate_fund_load_id_fk FOREIGN KEY (load_id) REFERENCES pub_partii.reading (load_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX candidate_fund_participant_idx ON pub_partii.candidate_fund (participant_id, load_id);
COMMENT ON TABLE pub_partii.candidate_fund IS 'Средства от кандидат или член на инициативен комитет за собствената кампания, в едно прието четене. Без име.';
COMMENT ON COLUMN pub_partii.candidate_fund.load_id IS 'Четенето.';
COMMENT ON COLUMN pub_partii.candidate_fund.row_no IS 'Поредният ред в отговора.';
COMMENT ON COLUMN pub_partii.candidate_fund.erik_id IS 'Изборът.';
COMMENT ON COLUMN pub_partii.candidate_fund.participant_label IS 'Участникът, както го пише източникът.';
COMMENT ON COLUMN pub_partii.candidate_fund.participant_id IS 'Участникът, когато текстът му е еднозначен; иначе NULL.';
COMMENT ON COLUMN pub_partii.candidate_fund.role IS 'Кандидат или член на инициативен комитет.';
COMMENT ON COLUMN pub_partii.candidate_fund.fund_day IS 'Денят; NULL, когато източникът няма дата.';
COMMENT ON COLUMN pub_partii.candidate_fund.money IS 'Парични средства, евро.';
COMMENT ON COLUMN pub_partii.candidate_fund.in_kind IS 'Непарични средства, евро.';
COMMENT ON COLUMN pub_partii.candidate_fund.decl IS 'Декларация за произход: published, processing, none.';

CREATE TABLE pub_partii.agency_use (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  participant_label text NOT NULL,
  participant_id    bigint,
  agency_name       text NOT NULL,
  agency_eik        text,
  is_eik_valid      boolean,
  agency_kind       text,
  descr             text,
  CONSTRAINT agency_use_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT agency_use_load_id_fk FOREIGN KEY (load_id) REFERENCES pub_partii.reading (load_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX agency_use_eik_idx ON pub_partii.agency_use (agency_eik);
CREATE INDEX agency_use_participant_idx ON pub_partii.agency_use (participant_id, load_id);
COMMENT ON TABLE pub_partii.agency_use IS 'Агенция, обявена от участник, в едно прието четене.';
COMMENT ON COLUMN pub_partii.agency_use.load_id IS 'Четенето.';
COMMENT ON COLUMN pub_partii.agency_use.row_no IS 'Поредният ред в отговора.';
COMMENT ON COLUMN pub_partii.agency_use.erik_id IS 'Изборът.';
COMMENT ON COLUMN pub_partii.agency_use.participant_label IS 'Участникът, както го пише източникът.';
COMMENT ON COLUMN pub_partii.agency_use.participant_id IS 'Участникът, когато текстът му е еднозначен; иначе NULL.';
COMMENT ON COLUMN pub_partii.agency_use.agency_name IS 'Името на агенцията.';
COMMENT ON COLUMN pub_partii.agency_use.agency_eik IS 'ЕИК, както е обявен.';
COMMENT ON COLUMN pub_partii.agency_use.is_eik_valid IS 'Контролната цифра е вярна; NULL, когато няма ЕИК.';
COMMENT ON COLUMN pub_partii.agency_use.agency_kind IS 'Рекламна, социологическа или за връзки с обществеността.';
COMMENT ON COLUMN pub_partii.agency_use.descr IS 'Описанието на услугата, както е в източника.';

CREATE TABLE pub_partii.report_row (
  load_id           bigint NOT NULL,
  row_no            integer NOT NULL,
  erik_id           integer NOT NULL,
  layout            text NOT NULL,
  participant_label text NOT NULL,
  participant_id    bigint,
  filled            integer NOT NULL,
  CONSTRAINT report_row_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT report_row_load_id_fk FOREIGN KEY (load_id) REFERENCES pub_partii.reading (load_id) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_partii.report_row IS 'Участник в справката от отчетите след изборите, в едно прието четене.';
COMMENT ON COLUMN pub_partii.report_row.load_id IS 'Четенето.';
COMMENT ON COLUMN pub_partii.report_row.row_no IS 'Поредният ред във файла.';
COMMENT ON COLUMN pub_partii.report_row.erik_id IS 'Изборът.';
COMMENT ON COLUMN pub_partii.report_row.layout IS 'Видът на справката.';
COMMENT ON COLUMN pub_partii.report_row.participant_label IS 'Името на реда в справката.';
COMMENT ON COLUMN pub_partii.report_row.participant_id IS 'Участникът, когато името е еднозначно; иначе NULL.';
COMMENT ON COLUMN pub_partii.report_row.filled IS 'Брой попълнени клетки.';

CREATE TABLE pub_partii.report_cell (
  load_id    bigint NOT NULL,
  row_no     integer NOT NULL,
  col        integer NOT NULL,
  amount_raw text,
  amount     numeric,
  CONSTRAINT report_cell_pk PRIMARY KEY (load_id, row_no, col),
  CONSTRAINT report_cell_row_fk FOREIGN KEY (load_id, row_no) REFERENCES pub_partii.report_row (load_id, row_no) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_partii.report_cell IS 'Клетка от справката: както е във файла и като число във валутата на отчета.';
COMMENT ON COLUMN pub_partii.report_cell.load_id IS 'Четенето.';
COMMENT ON COLUMN pub_partii.report_cell.row_no IS 'Редът на участника.';
COMMENT ON COLUMN pub_partii.report_cell.col IS 'Колоната (ред на формата).';
COMMENT ON COLUMN pub_partii.report_cell.amount_raw IS 'Както е във файла; „-1“ е непопълнено поле.';
COMMENT ON COLUMN pub_partii.report_cell.amount IS 'Сумата; NULL, когато не е попълнено.';

CREATE TABLE pub_partii.source_state (
  load_id   bigint NOT NULL,
  source    text NOT NULL,
  ref       text NOT NULL,
  status    text NOT NULL,
  reason    text,
  loaded_at timestamptz NOT NULL,
  CONSTRAINT source_state_pk PRIMARY KEY (load_id)
);
COMMENT ON TABLE pub_partii.source_state IS 'Всеки отговор на източника и състоянието му: приет, същият, задържан, отхвърлен.';
COMMENT ON COLUMN pub_partii.source_state.load_id IS 'Номерът в дневника на приемането.';
COMMENT ON COLUMN pub_partii.source_state.source IS 'Таблицата на източника.';
COMMENT ON COLUMN pub_partii.source_state.ref IS 'Изборът (и вариантът).';
COMMENT ON COLUMN pub_partii.source_state.status IS 'loaded, confirmed, unchanged, held, rejected.';
COMMENT ON COLUMN pub_partii.source_state.reason IS 'Причината при задържане или отхвърляне.';
COMMENT ON COLUMN pub_partii.source_state.loaded_at IS 'Кога.';

CREATE TABLE pub_partii.health (
  kind     text NOT NULL,
  name     text NOT NULL,
  title_bg text NOT NULL,
  is_ok    boolean,
  since_at timestamptz,
  limit_h  integer,
  detail   text,
  CONSTRAINT health_pk PRIMARY KEY (kind, name)
);
COMMENT ON TABLE pub_partii.health IS 'От какво страницата „Източници“ смята проблемите: последното поглеждане на източника с прага му, задържаните отговори, правилата и последното пускане на всяка стъпка.';
COMMENT ON COLUMN pub_partii.health.kind IS 'source, hold, check или run.';
COMMENT ON COLUMN pub_partii.health.name IS 'Източникът, адресът, правилото или стъпката.';
COMMENT ON COLUMN pub_partii.health.title_bg IS 'Заглавие за показване.';
COMMENT ON COLUMN pub_partii.health.is_ok IS 'За правило и стъпка: минава ли; NULL, когато не е пускано.';
COMMENT ON COLUMN pub_partii.health.since_at IS 'За източник: последното успешно поглеждане; за задържане: от кога; за стъпка: началото на пускането.';
COMMENT ON COLUMN pub_partii.health.limit_h IS 'След колко часа от since_at това е проблем; NULL: не остарява.';
COMMENT ON COLUMN pub_partii.health.detail IS 'Очаквано и получено, причина на задържането, грешка на стъпката.';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'partii_web') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA partii FROM partii_web;
    REVOKE ALL ON SCHEMA partii FROM partii_web;
    GRANT USAGE ON SCHEMA pub_partii TO partii_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_partii TO partii_web;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pub_partii TO partii_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_partii GRANT SELECT ON TABLES TO partii_web;
  END IF;
END $$;

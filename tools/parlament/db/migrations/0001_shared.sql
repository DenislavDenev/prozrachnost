-- Парламент in the shared database: the state of every read in parlament_src and the clean tables in parlament.
-- The ledger (runs, raw files, holds, changes, checks) is the shared schema ops. The schema of the database of its own
-- (until 13.11.2026) is kept in db/legacy for reference: the tables keep their names and columns, so the parsers and the
-- builds write the same rows. vote (15,2 млн.) and speech are not partitioned (ARCHITECTURE.md, section 8); an index
-- that no query of the ingest side uses is not here (pg_stat_user_indexes of the old database, 13.11.2026).
DO $$ BEGIN
  IF to_regnamespace('parlament_src') IS NULL THEN CREATE SCHEMA parlament_src; END IF;
  IF to_regnamespace('parlament') IS NULL THEN CREATE SCHEMA parlament; END IF;
END $$;
COMMENT ON SCHEMA parlament_src IS 'Парламент, приемна зона: състоянието на всеки прочетен ресурс на parliament.bg (заседание, месец, профил, законопроект).';
COMMENT ON SCHEMA parlament IS 'Парламент: заседанията от 1879 г., поименните гласувания от 2009 г., депутатите, стенограмите и законопроектите.';

-- ---------------------------------------------------------------- landing zone

CREATE TABLE parlament_src.read_state (
  source      text NOT NULL,
  ref         text NOT NULL,
  last_read   timestamptz,
  last_ok     timestamptz,
  last_change timestamptz,
  status      text,
  error       text,
  rows        integer,
  CONSTRAINT read_state_pk PRIMARY KEY (source, ref),
  CONSTRAINT read_state_status_ck CHECK (status IN ('ok', 'held', 'invalid', 'no-files', 'no-votes', 'no-answer', 'empty'))
);
COMMENT ON TABLE parlament_src.read_state IS 'Последното четене на всеки ресурс: списъкът на месеца, заседанието, профилът, законопроектът. Суровите отговори са в ops.raw_file и ops.fetch.';
COMMENT ON COLUMN parlament_src.read_state.ref IS 'Ресурсът: month/2026-09, sten/11174, sten/11174/text, profile/5237, bill/167546, bills/2026-09, assembly/52, roster, absences, penalties.';
COMMENT ON COLUMN parlament_src.read_state.last_read IS 'Последният опит за четене.';
COMMENT ON COLUMN parlament_src.read_state.last_ok IS 'Последното успешно четене.';
COMMENT ON COLUMN parlament_src.read_state.last_change IS 'Последното четене, което промени пазеното.';
COMMENT ON COLUMN parlament_src.read_state.status IS 'ok; held: задържан до второ четене; invalid: файловете не се сбират или не се четат; no-files: няма четим файл; no-votes: заседание без гласуване; no-answer: източникът не отговаря; empty: празен отговор.';
COMMENT ON COLUMN parlament_src.read_state.error IS 'Защо не е прочетен, на български, както го пише парсерът.';
COMMENT ON COLUMN parlament_src.read_state.rows IS 'Колко реда е дал: заседания в месеца, гласове на заседанието, етапи на законопроекта.';

-- ---------------------------------------------------------------- the assemblies and the sittings

CREATE TABLE parlament.assembly (
  no     smallint NOT NULL,
  kind   text NOT NULL,
  name   text NOT NULL,
  start  date NOT NULL,
  "end"  date,
  api_id integer,
  CONSTRAINT assembly_pk PRIMARY KEY (no),
  CONSTRAINT assembly_api_id_uq UNIQUE (api_id)
);
COMMENT ON TABLE parlament.assembly IS 'Всяко Народно събрание от Учредителното (db/ref/assemblies.csv; от 39-о датите са от API-то).';
COMMENT ON COLUMN parlament.assembly.no IS 'Номерът на обикновеното събрание (1950-1990 продължават номерата: I НС от 1950 г. е 27-о); великото е 100 + номера, Учредителното 100.';
COMMENT ON COLUMN parlament.assembly.kind IS 'Вид: обикновено, велико, учредително.';
COMMENT ON COLUMN parlament.assembly.name IS 'Името, както го пише източникът.';
COMMENT ON COLUMN parlament.assembly.start IS 'Първият ден.';
COMMENT ON COLUMN parlament.assembly."end" IS 'Последният ден; NULL за сегашното.';
COMMENT ON COLUMN parlament.assembly.api_id IS 'Номерът на събранието в API-то на parliament.bg (от 39-о).';

CREATE TABLE parlament.sitting (
  id        integer NOT NULL,
  date      date NOT NULL,
  assembly  smallint,
  heading   text NOT NULL,
  gv        text,
  iv        text,
  gv_sha    text,
  iv_sha    text,
  note      text,
  video     text[],
  pdf       text,
  steno_sha text,
  CONSTRAINT sitting_pk PRIMARY KEY (id)
);
CREATE INDEX sitting_assembly_date_idx ON parlament.sitting (assembly, date);
COMMENT ON TABLE parlament.sitting IS 'Едно пленарно заседание (pl-sten/<id>), с файловете на гласуването, видеото и стенограмата.';
COMMENT ON COLUMN parlament.sitting.id IS 'Номерът на заседанието в parliament.bg.';
COMMENT ON COLUMN parlament.sitting.date IS 'Денят на заседанието.';
COMMENT ON COLUMN parlament.sitting.assembly IS 'Събранието; преди 2009 г. заглавието не го казва и то е по датата (последното започнало до деня).';
COMMENT ON COLUMN parlament.sitting.heading IS 'Заглавието на заседанието, както е в източника.';
COMMENT ON COLUMN parlament.sitting.gv IS 'Пътят на файла с гласуването по групи, от който са точките; NULL: не е публикуван или е само XLSX/PDF.';
COMMENT ON COLUMN parlament.sitting.iv IS 'Пътят на файла с поименното гласуване.';
COMMENT ON COLUMN parlament.sitting.gv_sha IS 'sha256 на прочетения файл по групи.';
COMMENT ON COLUMN parlament.sitting.iv_sha IS 'sha256 на прочетения поименен файл; NULL: заседанието няма записани гласове.';
COMMENT ON COLUMN parlament.sitting.note IS 'Какво казва страницата на гласуванията за файловете на източника (депутати с разместени знаци).';
COMMENT ON COLUMN parlament.sitting.video IS 'Частите на записа (от 2010 г.), на parliament.bg.';
COMMENT ON COLUMN parlament.sitting.pdf IS 'Сканираната стенограма (преди 1992 г.).';
COMMENT ON COLUMN parlament.sitting.steno_sha IS 'sha256 на текста на стенограмата, който пазим; NULL: още не е публикуван.';

CREATE TABLE parlament.item (
  sitting  integer NOT NULL,
  no       smallint NOT NULL,
  kind     text NOT NULL,
  at       timestamp NOT NULL,
  topic    text,
  yes      integer,
  no_      integer,
  abstain  integer,
  voted    integer,
  present  integer,
  listed   integer,
  mismatch text,
  CONSTRAINT item_pk PRIMARY KEY (sitting, no),
  CONSTRAINT item_sitting_fk FOREIGN KEY (sitting) REFERENCES parlament.sitting (id) ON DELETE CASCADE,
  CONSTRAINT item_kind_ck CHECK (kind IN ('registration', 'vote'))
);
COMMENT ON TABLE parlament.item IS 'Една регистрация или едно гласуване на заседание: резултатът на цялото събрание, от файла по групи.';
COMMENT ON COLUMN parlament.item.no IS 'Номерът на точката във файла по групи.';
COMMENT ON COLUMN parlament.item.kind IS 'registration или vote.';
COMMENT ON COLUMN parlament.item.at IS 'Часът, както го пише файлът: местно време в София, без часова зона.';
COMMENT ON COLUMN parlament.item.topic IS 'Темата на гласуването, както е във файла.';
COMMENT ON COLUMN parlament.item.yes IS 'Гласували за (гласуване).';
COMMENT ON COLUMN parlament.item.no_ IS 'Гласували против (гласуване).';
COMMENT ON COLUMN parlament.item.abstain IS 'Въздържали се (гласуване).';
COMMENT ON COLUMN parlament.item.voted IS 'Гласували общо (гласуване).';
COMMENT ON COLUMN parlament.item.present IS 'Регистрирани (регистрация).';
COMMENT ON COLUMN parlament.item.listed IS 'Депутати в списъка (регистрация).';
COMMENT ON COLUMN parlament.item.mismatch IS 'Където поименното гласуване, преброено по групи, се различава малко от файла по групи (ingest/parse.check).';

CREATE TABLE parlament.item_group (
  sitting integer NOT NULL,
  no      smallint NOT NULL,
  grp     text NOT NULL,
  yes     integer,
  no_     integer,
  abstain integer,
  voted   integer,
  present integer,
  listed  integer,
  CONSTRAINT item_group_pk PRIMARY KEY (sitting, no, grp),
  CONSTRAINT item_group_item_fk FOREIGN KEY (sitting, no) REFERENCES parlament.item (sitting, no) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.item_group IS 'Резултатът на една парламентарна група в една точка, от файла по групи.';
COMMENT ON COLUMN parlament.item_group.grp IS 'Кодът на групата (db/ref/groups.csv: преименуваната група в едно събрание е една).';
COMMENT ON COLUMN parlament.item_group.yes IS 'За.';
COMMENT ON COLUMN parlament.item_group.no_ IS 'Против.';
COMMENT ON COLUMN parlament.item_group.abstain IS 'Въздържали се.';
COMMENT ON COLUMN parlament.item_group.voted IS 'Гласували.';
COMMENT ON COLUMN parlament.item_group.present IS 'Регистрирани.';
COMMENT ON COLUMN parlament.item_group.listed IS 'В списъка.';

CREATE TABLE parlament.mp (
  assembly smallint NOT NULL,
  no       integer NOT NULL,
  name     core.person_name NOT NULL,
  profile  integer,
  CONSTRAINT mp_pk PRIMARY KEY (assembly, no)
);
COMMENT ON TABLE parlament.mp IS 'Депутат по номера в системата за гласуване, който е само за едно събрание.';
COMMENT ON COLUMN parlament.mp.no IS 'Номерът в системата за гласуване на това събрание.';
COMMENT ON COLUMN parlament.mp.name IS 'Името, както го пише поименното гласуване (народен представител: публична роля).';
COMMENT ON COLUMN parlament.mp.profile IS 'Профилът на Народното събрание с това пълно име, когато името е веднъж и в двата списъка на събранието.';

CREATE TABLE parlament.vote (
  sitting integer NOT NULL,
  item    smallint NOT NULL,
  mp      integer NOT NULL,
  grp     text NOT NULL,
  code    char(1) NOT NULL,
  CONSTRAINT vote_pk PRIMARY KEY (sitting, item, mp),
  CONSTRAINT vote_item_fk FOREIGN KEY (sitting, item) REFERENCES parlament.item (sitting, no) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.vote IS 'Поименното гласуване: гласът или регистрацията на един депутат в една точка.';
COMMENT ON COLUMN parlament.vote.mp IS 'Номерът на депутата в системата за гласуване (parlament.mp).';
COMMENT ON COLUMN parlament.vote.grp IS 'Групата му в тази точка.';
COMMENT ON COLUMN parlament.vote.code IS '+ за, - против, = въздържал се, 0 не гласувал; П регистриран, Д регистриран онлайн, О и Р не е регистриран.';

CREATE TABLE parlament.roster (
  assembly smallint NOT NULL,
  profile  integer NOT NULL,
  name     core.person_name NOT NULL,
  grp_name text,
  district text,
  since    date,
  CONSTRAINT roster_pk PRIMARY KEY (assembly, profile)
);
COMMENT ON TABLE parlament.roster IS 'Списъкът на депутатите на сегашното събрание (coll-list-ns): профил, група, избирателен район. Ред не се маха.';
COMMENT ON COLUMN parlament.roster.name IS 'Пълното име от списъка (народен представител: публична роля).';
COMMENT ON COLUMN parlament.roster.grp_name IS 'Пълното име на групата.';
COMMENT ON COLUMN parlament.roster.district IS 'Многомандатният избирателен район, както го пише източникът („23-СОФИЯ“).';
COMMENT ON COLUMN parlament.roster.since IS 'От кога е депутат в това събрание.';

CREATE TABLE parlament.line (
  sitting   integer NOT NULL,
  item      smallint NOT NULL,
  grp       text NOT NULL,
  line      char(1),
  voters    integer NOT NULL,
  with_line integer NOT NULL,
  CONSTRAINT line_pk PRIMARY KEY (sitting, item, grp)
);
COMMENT ON TABLE parlament.line IS 'Линията на групата в гласуване, изведена от parlament.vote (ingest/stats.py): страната на повечето ѝ гласували депутати.';
COMMENT ON COLUMN parlament.line.line IS '+ за, - не подкрепя (против или въздържал се); NULL при равенство.';
COMMENT ON COLUMN parlament.line.voters IS 'Гласувалите депутати на групата.';
COMMENT ON COLUMN parlament.line.with_line IS 'Гласувалите с линията.';

CREATE TABLE parlament.mp_stat (
  assembly     smallint NOT NULL,
  mp           integer NOT NULL,
  name         core.person_name NOT NULL,
  grp          text NOT NULL,
  votes        integer NOT NULL,
  voted        integer NOT NULL,
  yes          integer NOT NULL,
  no_          integer NOT NULL,
  abstain      integer NOT NULL,
  regs         integer NOT NULL,
  present      integer NOT NULL,
  with_line    integer NOT NULL,
  against_line integer NOT NULL,
  first        date NOT NULL,
  last         date NOT NULL,
  profile      integer,
  district     text,
  CONSTRAINT mp_stat_pk PRIMARY KEY (assembly, mp)
);
COMMENT ON TABLE parlament.mp_stat IS 'Депутат в едно събрание, изведено от parlament.vote: колко е гласувал, как, с линията и срещу нея, регистрации.';
COMMENT ON COLUMN parlament.mp_stat.name IS 'Името от поименното гласуване (народен представител: публична роля).';
COMMENT ON COLUMN parlament.mp_stat.grp IS 'Групата в последното му гласуване.';
COMMENT ON COLUMN parlament.mp_stat.votes IS 'Гласувания, в които е в списъка.';
COMMENT ON COLUMN parlament.mp_stat.voted IS 'Гласувал (за, против или въздържал се).';
COMMENT ON COLUMN parlament.mp_stat.yes IS 'За.';
COMMENT ON COLUMN parlament.mp_stat.no_ IS 'Против.';
COMMENT ON COLUMN parlament.mp_stat.abstain IS 'Въздържал се.';
COMMENT ON COLUMN parlament.mp_stat.regs IS 'Регистрации, в които е в списъка.';
COMMENT ON COLUMN parlament.mp_stat.present IS 'Регистриран (в залата или онлайн).';
COMMENT ON COLUMN parlament.mp_stat.with_line IS 'Гласувал с линията на групата си.';
COMMENT ON COLUMN parlament.mp_stat.against_line IS 'Гласувал срещу линията на групата си.';
COMMENT ON COLUMN parlament.mp_stat.first IS 'Първото заседание.';
COMMENT ON COLUMN parlament.mp_stat.last IS 'Последното заседание.';
COMMENT ON COLUMN parlament.mp_stat.profile IS 'Профилът на Народното събрание (parlament.mp.profile, иначе по сегашния списък със същото правило).';
COMMENT ON COLUMN parlament.mp_stat.district IS 'Избирателният район от профила.';

-- ---------------------------------------------------------------- the people of an assembly

CREATE TABLE parlament.body (
  id       integer NOT NULL,
  assembly smallint,
  kind     text,
  name     text NOT NULL,
  since    date,
  until    date,
  n_start  integer,
  n_end    integer,
  n_total  integer,
  grp      text,
  CONSTRAINT body_pk PRIMARY KEY (id)
);
COMMENT ON TABLE parlament.body IS 'Група, комисия, делегация или група за приятелство на събрание (archive/bg/<id> и членствата).';
COMMENT ON COLUMN parlament.body.kind IS 'Вид: събрание, група, комисия, временна комисия, подкомисия, делегация, група за приятелство, друго.';
COMMENT ON COLUMN parlament.body.name IS 'Името на органа.';
COMMENT ON COLUMN parlament.body.n_start IS 'Членове в началото.';
COMMENT ON COLUMN parlament.body.n_end IS 'Членове в края.';
COMMENT ON COLUMN parlament.body.n_total IS 'Членове общо.';
COMMENT ON COLUMN parlament.body.grp IS 'Кодът на групата, както го пише поименното гласуване (ingest/stats.link_bodies); само за групите от 07.2009.';

CREATE TABLE parlament.profile (
  id         integer NOT NULL,
  assembly   smallint NOT NULL,
  name       core.person_name NOT NULL,
  district   text,
  list       text,
  profession text,
  languages  text,
  person     integer,
  read_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT profile_pk PRIMARY KEY (id)
);
CREATE INDEX profile_assembly_name_idx ON parlament.profile (assembly, name);
COMMENT ON TABLE parlament.profile IS 'Профилът на депутат в едно събрание (mp-profile): номерът на Народното събрание. Без дата и място на раждане, телефон, имейл и снимка.';
COMMENT ON COLUMN parlament.profile.id IS 'Номерът на профила в parliament.bg.';
COMMENT ON COLUMN parlament.profile.name IS 'Пълното име от профила (народен представител: публична роля).';
COMMENT ON COLUMN parlament.profile.district IS 'Избирателният район.';
COMMENT ON COLUMN parlament.profile.list IS 'Листата, от която е избран.';
COMMENT ON COLUMN parlament.profile.profession IS 'Професията, както я пише профилът.';
COMMENT ON COLUMN parlament.profile.languages IS 'Езиците, както ги пише профилът.';
COMMENT ON COLUMN parlament.profile.person IS 'Най-малкият номер на профил на същия депутат: Народното събрание изброява предишните му събрания, а там профилът е този със същото пълно име, само ако името е веднъж (ingest/people.link).';
COMMENT ON COLUMN parlament.profile.read_at IS 'Кога е прочетен.';

CREATE TABLE parlament.profile_past (
  profile  integer NOT NULL,
  assembly smallint NOT NULL,
  CONSTRAINT profile_past_pk PRIMARY KEY (profile, assembly),
  CONSTRAINT profile_past_profile_fk FOREIGN KEY (profile) REFERENCES parlament.profile (id) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.profile_past IS 'Предишните събрания на депутата, както ги изброява профилът му.';

CREATE TABLE parlament.membership (
  id        integer NOT NULL,
  profile   integer NOT NULL,
  body      integer NOT NULL,
  body_name text NOT NULL,
  body_kind smallint,
  role      text,
  since     date,
  until     date,
  CONSTRAINT membership_pk PRIMARY KEY (id),
  CONSTRAINT membership_profile_fk FOREIGN KEY (profile) REFERENCES parlament.profile (id) ON DELETE CASCADE
);
CREATE INDEX membership_profile_idx ON parlament.membership (profile);
COMMENT ON TABLE parlament.membership IS 'Къде е бил депутатът и какъв, с датите: събранието, групи, комисии, делегации.';
COMMENT ON COLUMN parlament.membership.body IS 'Органът (parlament.body).';
COMMENT ON COLUMN parlament.membership.body_kind IS 'Видът на органа по номера на източника: 1 събрание, 2 група, 3 комисия, 4 временна комисия, 6 делегация, 9 подкомисия.';
COMMENT ON COLUMN parlament.membership.role IS 'Длъжността в органа (член, председател, заместник-председател).';

CREATE TABLE parlament.absence (
  id         integer NOT NULL,
  date       date NOT NULL,
  profile    integer NOT NULL,
  name       core.person_name NOT NULL,
  body       integer,
  body_name  text,
  kind       smallint,
  first_seen timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT absence_pk PRIMARY KEY (id)
);
COMMENT ON TABLE parlament.absence IS 'Официално отсъствие на депутат (mp-absense): Народното събрание показва само последните месеци, прочетеното се пази.';
COMMENT ON COLUMN parlament.absence.name IS 'Името на депутата (публична роля).';
COMMENT ON COLUMN parlament.absence.kind IS '1 пленарно заседание, 2 комисия.';
COMMENT ON COLUMN parlament.absence.first_seen IS 'Кога го видяхме за пръв път.';

CREATE TABLE parlament.penalty (
  id         integer NOT NULL,
  date       date NOT NULL,
  profile    integer NOT NULL,
  name       core.person_name NOT NULL,
  kind       text NOT NULL,
  note       text,
  by_name    core.person_name,
  what       text,
  first_seen timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT penalty_pk PRIMARY KEY (id)
);
COMMENT ON TABLE parlament.penalty IS 'Наказание от председателя на заседанието (mp-penalty), пазено като отсъствията.';
COMMENT ON COLUMN parlament.penalty.name IS 'Наказаният депутат (публична роля).';
COMMENT ON COLUMN parlament.penalty.by_name IS 'Председателстващият, който го е наложил (публична роля).';
COMMENT ON COLUMN parlament.penalty.kind IS 'Видът на наказанието.';
COMMENT ON COLUMN parlament.penalty.what IS 'За какво, както го пише източникът.';

CREATE TABLE parlament.speech (
  sitting integer NOT NULL,
  no      smallint NOT NULL,
  role    text,
  name    core.person_name,
  note    text,
  grp     text,
  profile integer,
  text    text NOT NULL,
  CONSTRAINT speech_pk PRIMARY KEY (sitting, no),
  CONSTRAINT speech_sitting_fk FOREIGN KEY (sitting) REFERENCES parlament.sitting (id) ON DELETE CASCADE,
  CONSTRAINT speech_profile_only_mp_ck CHECK (profile IS NULL OR role IS NULL OR role = ANY (ARRAY['председател', 'председателят', 'заместник-председател', 'заместник председател', 'докладчик', 'секретар']))
);
COMMENT ON TABLE parlament.speech IS 'Стенограмата, изказване по изказване (ingest/parse.speeches); № 0 е текстът преди първото изказване. Профил (ключ на депутат) има само изказване без роля или в роля на депутат (ingest/stats.MP_ROLES): министър, президент или гост няма ключ.';
COMMENT ON COLUMN parlament.speech.role IS 'Ролята, в която говори, както я пише стенограмата (председател, министър, докладчик ...).';
COMMENT ON COLUMN parlament.speech.name IS 'Името на изказалия се, както го пише стенограмата: депутат или лице с публична длъжност, на което е дадена думата в пленарна зала.';
COMMENT ON COLUMN parlament.speech.note IS 'Бележката до името (от място, реплика).';
COMMENT ON COLUMN parlament.speech.grp IS 'Групата, както я пише стенограмата.';
COMMENT ON COLUMN parlament.speech.profile IS 'Профилът на депутата, когато името е на един профил в събранието и ролята е на депутат (ingest/stats.link_speakers). Министър или гост няма профил.';
COMMENT ON COLUMN parlament.speech.text IS 'Текстът на изказването; абзаците са редове.';

-- ---------------------------------------------------------------- the bills

CREATE TABLE parlament.bill (
  id          integer NOT NULL,
  sign        text,
  date        date,
  title       text NOT NULL,
  final_title text,
  assembly    smallint,
  session     text,
  withdrawn   boolean NOT NULL DEFAULT false,
  adopted     date,
  dv_issue    text,
  dv_year     integer,
  government  boolean NOT NULL DEFAULT false,
  sha         text,
  read_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT bill_pk PRIMARY KEY (id)
);
CREATE INDEX bill_assembly_date_idx ON parlament.bill (assembly, date);
COMMENT ON TABLE parlament.bill IS 'Законопроект от 2001 г. (bill/<id>).';
COMMENT ON COLUMN parlament.bill.sign IS 'Сигнатурата.';
COMMENT ON COLUMN parlament.bill.date IS 'Денят на внасяне.';
COMMENT ON COLUMN parlament.bill.final_title IS 'Заглавието на приетия закон.';
COMMENT ON COLUMN parlament.bill.withdrawn IS 'Оттеглен.';
COMMENT ON COLUMN parlament.bill.adopted IS 'Денят на приемане; NULL: не е приет.';
COMMENT ON COLUMN parlament.bill.dv_issue IS 'Броят на Държавен вестник, в който е обнародван.';
COMMENT ON COLUMN parlament.bill.government IS 'Внесен от Министерския съвет.';
COMMENT ON COLUMN parlament.bill.sha IS 'sha256 на прочетения отговор.';

CREATE TABLE parlament.bill_sponsor (
  bill    integer NOT NULL,
  pos     smallint NOT NULL,
  profile integer,
  name    core.person_name NOT NULL,
  CONSTRAINT bill_sponsor_pk PRIMARY KEY (bill, pos),
  CONSTRAINT bill_sponsor_bill_fk FOREIGN KEY (bill) REFERENCES parlament.bill (id) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.bill_sponsor IS 'Вносител на законопроекта: депутат с профила си или Министерският съвет.';
COMMENT ON COLUMN parlament.bill_sponsor.name IS 'Името на вносителя, както го пише източникът (публична роля).';

CREATE TABLE parlament.bill_committee (
  bill      integer NOT NULL,
  committee integer NOT NULL,
  name      text NOT NULL,
  role      text,
  CONSTRAINT bill_committee_pk PRIMARY KEY (bill, committee),
  CONSTRAINT bill_committee_bill_fk FOREIGN KEY (bill) REFERENCES parlament.bill (id) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.bill_committee IS 'Комисия, на която е разпределен законопроектът.';
COMMENT ON COLUMN parlament.bill_committee.role IS 'водеща или подпомагаща.';

CREATE TABLE parlament.bill_step (
  bill           integer NOT NULL,
  id             integer NOT NULL,
  date           date,
  sitting        integer,
  committee      integer,
  committee_name text,
  what           text,
  stage          text,
  CONSTRAINT bill_step_pk PRIMARY KEY (bill, id),
  CONSTRAINT bill_step_bill_fk FOREIGN KEY (bill) REFERENCES parlament.bill (id) ON DELETE CASCADE
);
CREATE INDEX bill_step_sitting_idx ON parlament.bill_step (sitting);
CREATE INDEX bill_step_date_idx ON parlament.bill_step (date);
COMMENT ON TABLE parlament.bill_step IS 'Етап на законопроекта с датата си, в комисия или в зала.';
COMMENT ON COLUMN parlament.bill_step.sitting IS 'Заседанието на етап в зала, когато източникът го дава.';
COMMENT ON COLUMN parlament.bill_step.stage IS 'Етапът, както го пише източникът („зала първо гласуване“ ...).';

CREATE TABLE parlament.bill_item (
  bill    integer NOT NULL,
  sitting integer NOT NULL,
  item    smallint NOT NULL,
  reading smallint,
  CONSTRAINT bill_item_pk PRIMARY KEY (bill, sitting, item),
  CONSTRAINT bill_item_bill_fk FOREIGN KEY (bill) REFERENCES parlament.bill (id) ON DELETE CASCADE
);
COMMENT ON TABLE parlament.bill_item IS 'Гласуване в зала по законопроекта: намерено в заседанието на етап в зала по краткото заглавие (ingest/bills.link).';
COMMENT ON COLUMN parlament.bill_item.reading IS 'Четенето: 1, 2 или NULL.';

-- Парламент, the serving layer: what the pages read, and nothing else. It changes only by a publication
-- (pipeline.publish: one transaction with a version number, after every rule holds; db/publish/*.sql say what goes in).
-- The names of the MPs and of the officials who speak in the plenary are pub.role_name, by the decisions in
-- tools/danni/db/persons.toml. A person who is not an MP has no profile here, so no key.
-- The indexes are the ones the queries of the pages need (app/main.py); there are no foreign keys: the publication
-- checks every rule inside its transaction, and a check on 15 million votes at each publication is not worth it.
DO $$ BEGIN IF to_regnamespace('pub_parlament') IS NULL THEN CREATE SCHEMA pub_parlament; END IF; END $$;
COMMENT ON SCHEMA pub_parlament IS 'Парламент, слой за показване: заседанията, гласуванията, депутатите в публичната им роля, стенограмите, законопроектите и състоянието на четенето.';

CREATE TABLE pub_parlament.assembly (
  no     smallint NOT NULL,
  kind   text NOT NULL,
  name   text NOT NULL,
  start  date NOT NULL,
  "end"  date,
  api_id integer,
  CONSTRAINT assembly_pk PRIMARY KEY (no)
);
COMMENT ON TABLE pub_parlament.assembly IS 'Всяко Народно събрание от 1879 г.';

CREATE TABLE pub_parlament.sitting (
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
CREATE INDEX sitting_assembly_date_idx ON pub_parlament.sitting (assembly, date);
COMMENT ON TABLE pub_parlament.sitting IS 'Едно пленарно заседание с файловете на гласуването, видеото и стенограмата.';

CREATE TABLE pub_parlament.item (
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
  CONSTRAINT item_pk PRIMARY KEY (sitting, no)
);
COMMENT ON TABLE pub_parlament.item IS 'Регистрация или гласуване: резултатът на цялото събрание.';

CREATE TABLE pub_parlament.item_group (
  sitting integer NOT NULL,
  no      smallint NOT NULL,
  grp     text NOT NULL,
  yes     integer,
  no_     integer,
  abstain integer,
  voted   integer,
  present integer,
  listed  integer,
  CONSTRAINT item_group_pk PRIMARY KEY (sitting, no, grp)
);
COMMENT ON TABLE pub_parlament.item_group IS 'Резултатът на една група в една точка.';

CREATE TABLE pub_parlament.mp (
  assembly smallint NOT NULL,
  no       integer NOT NULL,
  name     pub.role_name NOT NULL,
  profile  integer,
  CONSTRAINT mp_pk PRIMARY KEY (assembly, no)
);
COMMENT ON TABLE pub_parlament.mp IS 'Депутат по номера в системата за гласуване на събранието, с името в публичната му роля.';

CREATE TABLE pub_parlament.vote (
  sitting integer NOT NULL,
  item    smallint NOT NULL,
  mp      integer NOT NULL,
  grp     text NOT NULL,
  code    char(1) NOT NULL,
  CONSTRAINT vote_pk PRIMARY KEY (sitting, item, mp)
);
CREATE INDEX vote_mp_sitting_idx ON pub_parlament.vote (mp, sitting);
COMMENT ON TABLE pub_parlament.vote IS 'Поименното гласуване: гласът на един депутат в една точка.';

CREATE TABLE pub_parlament.line (
  sitting   integer NOT NULL,
  item      smallint NOT NULL,
  grp       text NOT NULL,
  line      char(1),
  voters    integer NOT NULL,
  with_line integer NOT NULL,
  CONSTRAINT line_pk PRIMARY KEY (sitting, item, grp)
);
COMMENT ON TABLE pub_parlament.line IS 'Линията на групата в гласуване.';

CREATE TABLE pub_parlament.mp_stat (
  assembly     smallint NOT NULL,
  mp           integer NOT NULL,
  name         pub.role_name NOT NULL,
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
COMMENT ON TABLE pub_parlament.mp_stat IS 'Депутат в едно събрание: участие, гласове, с линията и срещу нея.';

CREATE TABLE pub_parlament.body (
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
COMMENT ON TABLE pub_parlament.body IS 'Група, комисия или делегация на събрание.';

CREATE TABLE pub_parlament.profile (
  id         integer NOT NULL,
  assembly   smallint NOT NULL,
  name       pub.role_name NOT NULL,
  district   text,
  list       text,
  profession text,
  languages  text,
  person     integer,
  CONSTRAINT profile_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_parlament.profile IS 'Профилът на депутат в едно събрание: номерът на Народното събрание и същият депутат в другите събрания.';

CREATE TABLE pub_parlament.membership (
  id        integer NOT NULL,
  profile   integer NOT NULL,
  body      integer NOT NULL,
  body_name text NOT NULL,
  body_kind smallint,
  role      text,
  since     date,
  until     date,
  CONSTRAINT membership_pk PRIMARY KEY (id)
);
CREATE INDEX membership_profile_idx ON pub_parlament.membership (profile);
COMMENT ON TABLE pub_parlament.membership IS 'Къде е бил депутатът и какъв, с датите.';

CREATE TABLE pub_parlament.absence (
  id        integer NOT NULL,
  date      date NOT NULL,
  profile   integer NOT NULL,
  name      pub.role_name NOT NULL,
  body      integer,
  body_name text,
  kind      smallint,
  CONSTRAINT absence_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_parlament.absence IS 'Официално отсъствие на депутат.';

CREATE TABLE pub_parlament.penalty (
  id      integer NOT NULL,
  date    date NOT NULL,
  profile integer NOT NULL,
  name    pub.role_name NOT NULL,
  kind    text NOT NULL,
  note    text,
  by_name pub.role_name,
  what    text,
  CONSTRAINT penalty_pk PRIMARY KEY (id)
);
COMMENT ON TABLE pub_parlament.penalty IS 'Наказание от председателя на заседанието.';

-- the search of the pages is to_tsvector('simple', text) @@ ...: an index on the expression, not a stored column, so
-- the publication does not compare a second copy of the text
CREATE TABLE pub_parlament.speech (
  sitting integer NOT NULL,
  no      smallint NOT NULL,
  role    text,
  name    pub.role_name,
  note    text,
  grp     text,
  profile integer,
  text    text NOT NULL,
  CONSTRAINT speech_pk PRIMARY KEY (sitting, no),
  CONSTRAINT speech_profile_only_mp_ck CHECK (profile IS NULL OR role IS NULL OR role = ANY (ARRAY['председател', 'председателят', 'заместник-председател', 'заместник председател', 'докладчик', 'секретар']))
);
CREATE INDEX speech_text_idx ON pub_parlament.speech USING gin (to_tsvector('simple', text));
CREATE INDEX speech_profile_idx ON pub_parlament.speech (profile);
COMMENT ON TABLE pub_parlament.speech IS 'Стенограмата, изказване по изказване. Профил има само изказване на депутат.';

CREATE TABLE pub_parlament.bill (
  id          integer NOT NULL,
  sign        text,
  date        date,
  title       text NOT NULL,
  final_title text,
  assembly    smallint,
  session     text,
  withdrawn   boolean NOT NULL,
  adopted     date,
  dv_issue    text,
  dv_year     integer,
  government  boolean NOT NULL,
  CONSTRAINT bill_pk PRIMARY KEY (id)
);
CREATE INDEX bill_assembly_date_idx ON pub_parlament.bill (assembly, date);
COMMENT ON TABLE pub_parlament.bill IS 'Законопроект от 2001 г.';

CREATE TABLE pub_parlament.bill_sponsor (
  bill    integer NOT NULL,
  pos     smallint NOT NULL,
  profile integer,
  name    pub.role_name NOT NULL,
  CONSTRAINT bill_sponsor_pk PRIMARY KEY (bill, pos)
);
COMMENT ON TABLE pub_parlament.bill_sponsor IS 'Вносител на законопроекта.';

CREATE TABLE pub_parlament.bill_committee (
  bill      integer NOT NULL,
  committee integer NOT NULL,
  name      text NOT NULL,
  role      text,
  CONSTRAINT bill_committee_pk PRIMARY KEY (bill, committee)
);
COMMENT ON TABLE pub_parlament.bill_committee IS 'Комисия на законопроекта.';

CREATE TABLE pub_parlament.bill_step (
  bill           integer NOT NULL,
  id             integer NOT NULL,
  date           date,
  sitting        integer,
  committee      integer,
  committee_name text,
  what           text,
  stage          text,
  CONSTRAINT bill_step_pk PRIMARY KEY (bill, id)
);
CREATE INDEX bill_step_date_idx ON pub_parlament.bill_step (date);
COMMENT ON TABLE pub_parlament.bill_step IS 'Етап на законопроекта.';

CREATE TABLE pub_parlament.bill_item (
  bill    integer NOT NULL,
  sitting integer NOT NULL,
  item    smallint NOT NULL,
  reading smallint,
  CONSTRAINT bill_item_pk PRIMARY KEY (bill, sitting, item)
);
CREATE INDEX bill_item_sitting_item_idx ON pub_parlament.bill_item (sitting, item);
COMMENT ON TABLE pub_parlament.bill_item IS 'Гласуване в зала по законопроекта.';

CREATE TABLE pub_parlament.read_state (
  source      text NOT NULL,
  ref         text NOT NULL,
  last_read   timestamptz,
  last_ok     timestamptz,
  last_change timestamptz,
  status      text,
  error       text,
  rows        integer,
  held_since  timestamptz,
  CONSTRAINT read_state_pk PRIMARY KEY (source, ref)
);
COMMENT ON TABLE pub_parlament.read_state IS 'Последното четене на всеки ресурс, за страницата на източниците и свежестта.';
COMMENT ON COLUMN pub_parlament.read_state.held_since IS 'От кога новото четене чака второ (ops.hold).';

-- The web role reads the serving layer only.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'parlament_web') THEN
    GRANT USAGE ON SCHEMA pub_parlament TO parlament_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_parlament TO parlament_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_parlament GRANT SELECT ON TABLES TO parlament_web;
  END IF;
END $$;

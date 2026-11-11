-- Образование in the shared database: the landing zone obrazovanie_src and the clean tables obrazovanie.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the places are core.*.
-- The schema of the old database of its own is kept in db/legacy for reference.
DO $$ BEGIN
  IF to_regnamespace('obrazovanie_src') IS NULL THEN CREATE SCHEMA obrazovanie_src; END IF;
  IF to_regnamespace('obrazovanie') IS NULL THEN CREATE SCHEMA obrazovanie; END IF;
END $$;
COMMENT ON SCHEMA obrazovanie_src IS 'Образование, приемна зона: редовете на ресурсите на МОН в data.egov.bg, както са в източника.';
COMMENT ON SCHEMA obrazovanie IS 'Образование: резултатите от НВО и ДЗИ по училище, официалните списъци и националните бройки, с ключове към core.';

-- ---------------------------------------------------------------- landing zone

CREATE TABLE obrazovanie_src.register (
  load_id    bigint NOT NULL,
  resource   text NOT NULL,
  school_year text NOT NULL,
  updated_at text NOT NULL,
  CONSTRAINT register_pk PRIMARY KEY (load_id),
  CONSTRAINT register_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE obrazovanie_src.register IS 'Един прочетен регистър на училищата и детските градини с кодове по НЕИСПУО.';
COMMENT ON COLUMN obrazovanie_src.register.updated_at IS 'Часът на последната промяна, както го пише каталогът на data.egov.bg (без часова зона).';

CREATE TABLE obrazovanie_src.register_row (
  load_id      bigint NOT NULL,
  neispuo      text NOT NULL,
  name         text NOT NULL,
  oblast       text NOT NULL,
  municipality text NOT NULL,
  town         text NOT NULL,
  CONSTRAINT register_row_pk PRIMARY KEY (load_id, neispuo),
  CONSTRAINT register_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.register (load_id),
  CONSTRAINT register_row_neispuo_ck CHECK (neispuo ~ '^[0-9]{6,7}$')
);
COMMENT ON TABLE obrazovanie_src.register_row IS 'Един ред на регистъра: код по НЕИСПУО, име и място, както са в източника.';

CREATE TABLE obrazovanie_src.nvo7 (
  load_id             bigint NOT NULL,
  school_year         text NOT NULL,
  exam_resource       text NOT NULL,
  exam_sha            text NOT NULL,
  exam_updated_at     text NOT NULL,
  register_resource   text NOT NULL,
  register_sha        text NOT NULL,
  register_updated_at text NOT NULL,
  register_load_id    bigint,
  register_rows       integer,
  verification        text NOT NULL,
  CONSTRAINT nvo7_pk PRIMARY KEY (load_id),
  CONSTRAINT nvo7_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT nvo7_register_fk FOREIGN KEY (register_load_id) REFERENCES obrazovanie_src.register (load_id),
  CONSTRAINT nvo7_verification_ck CHECK ((verification = 'code') = (register_load_id IS NOT NULL))
);
COMMENT ON TABLE obrazovanie_src.nvo7 IS 'Една прочетена година на НВО VII: изпитният ресурс и регистърът на същата година.';
COMMENT ON COLUMN obrazovanie_src.nvo7.register_load_id IS 'Прочетеният регистър с кодове. NULL: регистърът за годината няма кодове по НЕИСПУО (verification = no-code).';
COMMENT ON COLUMN obrazovanie_src.nvo7.register_rows IS 'Редовете на регистъра без кодове (проверен само по форма).';

CREATE TABLE obrazovanie_src.nvo7_row (
  load_id      bigint NOT NULL,
  neispuo      text NOT NULL,
  subject      text NOT NULL,
  school       text NOT NULL,
  oblast       text NOT NULL,
  municipality text NOT NULL,
  town         text NOT NULL,
  takers       integer,
  score        numeric,
  scale        text NOT NULL,
  CONSTRAINT nvo7_row_pk PRIMARY KEY (load_id, neispuo, subject),
  CONSTRAINT nvo7_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.nvo7 (load_id)
);
COMMENT ON TABLE obrazovanie_src.nvo7_row IS 'Един резултат от НВО VII: училище и предмет, както са в изпитния файл.';

CREATE TABLE obrazovanie_src.dzi (
  load_id            bigint NOT NULL,
  resource           text NOT NULL,
  school_year        text NOT NULL,
  session            text NOT NULL,
  kind               text NOT NULL,
  sha                text NOT NULL,
  updated_at         text NOT NULL,
  register_resource  text,
  register_sha       text,
  register_load_id   bigint,
  source_rows        integer NOT NULL,
  school_count       integer NOT NULL,
  aggregate_count    integer NOT NULL,
  missing_grade      integer NOT NULL,
  suppressed_takers  integer NOT NULL,
  source_placeholder integer NOT NULL,
  school_codes       text[] NOT NULL,
  CONSTRAINT dzi_pk PRIMARY KEY (load_id),
  CONSTRAINT dzi_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT dzi_register_fk FOREIGN KEY (register_load_id) REFERENCES obrazovanie_src.register (load_id),
  CONSTRAINT dzi_session_ck CHECK (session IN ('may', 'august')),
  CONSTRAINT dzi_kind_ck CHECK (kind IN ('mandatory', 'optional'))
);
COMMENT ON TABLE obrazovanie_src.dzi IS 'Един прочетен ресурс с резултатите от ДЗИ (матурите): година, сесия, вид и броячите на файла.';
COMMENT ON COLUMN obrazovanie_src.dzi.suppressed_takers IS 'Клетки, в които МОН е скрило броя явили се с „–“.';
COMMENT ON COLUMN obrazovanie_src.dzi.school_codes IS 'Кодовете по НЕИСПУО на всички училища във файла, и на тези без нито един резултат.';

CREATE TABLE obrazovanie_src.dzi_row (
  load_id      bigint NOT NULL,
  row_number   integer NOT NULL,
  subject      text NOT NULL,
  neispuo      text,
  school       text NOT NULL,
  oblast       text NOT NULL,
  municipality text NOT NULL,
  town         text NOT NULL,
  takers       integer,
  score        numeric,
  is_school    boolean NOT NULL,
  CONSTRAINT dzi_row_pk PRIMARY KEY (load_id, row_number, subject),
  CONSTRAINT dzi_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.dzi (load_id)
);
COMMENT ON TABLE obrazovanie_src.dzi_row IS 'Един резултат от ДЗИ: ред на файла и предмет, както са в източника. is_school = false: сборен ред на РУО.';

CREATE TABLE obrazovanie_src.nvo (
  load_id           bigint NOT NULL,
  resource          text NOT NULL,
  exam              text NOT NULL,
  school_year       text NOT NULL,
  sha               text NOT NULL,
  updated_at        text NOT NULL,
  register_resource text,
  register_sha      text,
  register_load_id  bigint,
  school_count      integer NOT NULL,
  subject_count     integer NOT NULL,
  CONSTRAINT nvo_pk PRIMARY KEY (load_id),
  CONSTRAINT nvo_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT nvo_register_fk FOREIGN KEY (register_load_id) REFERENCES obrazovanie_src.register (load_id),
  CONSTRAINT nvo_exam_ck CHECK (exam IN ('nvo4', 'nvo10'))
);
COMMENT ON TABLE obrazovanie_src.nvo IS 'Един прочетен ресурс на НВО IV или X клас.';

CREATE TABLE obrazovanie_src.nvo_row (
  load_id      bigint NOT NULL,
  neispuo      text NOT NULL,
  subject      text NOT NULL,
  school       text NOT NULL,
  oblast       text NOT NULL,
  municipality text NOT NULL,
  town         text NOT NULL,
  takers       integer,
  score        numeric,
  CONSTRAINT nvo_row_pk PRIMARY KEY (load_id, neispuo, subject),
  CONSTRAINT nvo_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.nvo (load_id)
);
COMMENT ON TABLE obrazovanie_src.nvo_row IS 'Един резултат от НВО IV или X: училище и предмет, както са в източника.';

CREATE TABLE obrazovanie_src.status (
  load_id     bigint NOT NULL,
  resource    text NOT NULL,
  kind        text NOT NULL,
  school_year text NOT NULL,
  sha         text NOT NULL,
  updated_at  text NOT NULL,
  CONSTRAINT status_pk PRIMARY KEY (load_id),
  CONSTRAINT status_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT status_kind_ck CHECK (kind IN ('protected', 'central'))
);
COMMENT ON TABLE obrazovanie_src.status IS 'Един прочетен официален списък на защитените или средищните училища.';

CREATE TABLE obrazovanie_src.status_row (
  load_id    bigint NOT NULL,
  row_number integer NOT NULL,
  neispuo    text NOT NULL,
  name       text NOT NULL,
  town       text NOT NULL,
  scope      text,
  is_school  boolean NOT NULL,
  CONSTRAINT status_row_pk PRIMARY KEY (load_id, row_number),
  CONSTRAINT status_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.status (load_id)
);
COMMENT ON TABLE obrazovanie_src.status_row IS 'Един ред на официалния списък, както е в източника.';
COMMENT ON COLUMN obrazovanie_src.status_row.name IS 'Името на училището или детската градина (не на човек).';

CREATE TABLE obrazovanie_src.context (
  load_id          bigint NOT NULL,
  school_year      text NOT NULL,
  pupils_resource  text NOT NULL,
  classes_resource text NOT NULL,
  pupils_sha       text NOT NULL,
  classes_sha      text NOT NULL,
  pupils_updated   text NOT NULL,
  classes_updated  text NOT NULL,
  CONSTRAINT context_pk PRIMARY KEY (load_id),
  CONSTRAINT context_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE obrazovanie_src.context IS 'Една прочетена национална справка: учениците по класове и групите и паралелките по вид училище.';

CREATE TABLE obrazovanie_src.context_row (
  load_id         bigint NOT NULL,
  kind            text NOT NULL,
  institutions    integer NOT NULL,
  preschool       integer NOT NULL,
  reported_pupils integer NOT NULL,
  reported_groups numeric NOT NULL,
  CONSTRAINT context_row_pk PRIMARY KEY (load_id, kind),
  CONSTRAINT context_row_load_fk FOREIGN KEY (load_id) REFERENCES obrazovanie_src.context (load_id)
);
COMMENT ON TABLE obrazovanie_src.context_row IS 'Един вид училище в националната справка.';

CREATE TABLE obrazovanie_src.context_grade (
  load_id  bigint NOT NULL,
  kind     text NOT NULL,
  grade    smallint NOT NULL,
  students integer NOT NULL,
  CONSTRAINT context_grade_pk PRIMARY KEY (load_id, kind, grade),
  CONSTRAINT context_grade_row_fk FOREIGN KEY (load_id, kind) REFERENCES obrazovanie_src.context_row (load_id, kind)
);
COMMENT ON TABLE obrazovanie_src.context_grade IS 'Учениците в един клас (1-12, 14 и 15 за специалните) за един вид училище.';

-- ---------------------------------------------------------------- matching to the shared places

-- The municipality of a school by the names MON writes. Exactly as the pages matched the reference list before:
-- the oblast and the municipality by their names, without case; "София-град" and "София-област" are the names
-- of the register for the two oblasts of Sofia. No match, no code: nothing is guessed.
CREATE FUNCTION obrazovanie.municipality_of(source_oblast text, source_municipality text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT m.municipality_code FROM core.municipality m JOIN core.oblast o USING (oblast_code)
  WHERE lower(o.label_bg) = CASE lower(btrim(source_oblast)) WHEN 'софия-област' THEN 'софия област'
                                                            WHEN 'софия-град' THEN 'софия (столица)'
                                                            ELSE lower(btrim(source_oblast)) END
    AND lower(m.name_bg) = lower(btrim(source_municipality)) AND m.gone_at IS NULL
$$;
COMMENT ON FUNCTION obrazovanie.municipality_of(text, text) IS 'Кодът на общината по имената на областта и общината в източника на МОН. NULL, ако няма съвпадение.';

-- The settlement of a school by its name: only when exactly one current settlement has that name in the
-- municipality (or in the oblast, when the source gives no municipality). Otherwise NULL.
-- The parameters are named apart from the columns: in an SQL function a column wins over a parameter of its name.
CREATE FUNCTION obrazovanie.ekatte_of(source_oblast text, in_municipality text, source_town text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT min(s.ekatte) FROM core.settlement s JOIN core.municipality m ON m.municipality_code = s.municipality_code
  JOIN core.oblast o ON o.oblast_code = m.oblast_code
  WHERE s.gone_at IS NULL
    AND lower(s.name_bg) = lower(regexp_replace(btrim(source_town), '^(гр|с)\.\s*', '', 'i'))
    AND CASE WHEN in_municipality IS NOT NULL THEN s.municipality_code = in_municipality
             ELSE lower(o.label_bg) = CASE lower(btrim(source_oblast)) WHEN 'софия-област' THEN 'софия област'
                                                                      WHEN 'софия-град' THEN 'софия (столица)'
                                                                      ELSE lower(btrim(source_oblast)) END END
  HAVING count(*) = 1
$$;
COMMENT ON FUNCTION obrazovanie.ekatte_of(text, text, text) IS 'ЕКАТТЕ на населеното място по името му в общината (или в областта, ако общината липсва), само при точно едно съвпадение. Иначе NULL.';

-- ---------------------------------------------------------------- clean tables

CREATE TABLE obrazovanie.nvo7_publication (
  school_year         text NOT NULL,
  exam_resource       text NOT NULL,
  register_resource   text NOT NULL,
  exam_updated_on     date NOT NULL,
  register_updated_on date NOT NULL,
  school_count        integer NOT NULL,
  matched_count       integer NOT NULL,
  unmatched           text[] NOT NULL,
  verification        text NOT NULL,
  load_id             bigint NOT NULL,
  CONSTRAINT nvo7_publication_pk PRIMARY KEY (school_year),
  CONSTRAINT nvo7_publication_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT nvo7_publication_year_ck CHECK (school_year ~ '^20[0-9]{2}/20[0-9]{2}$'),
  CONSTRAINT nvo7_publication_verification_ck CHECK (verification IN ('code', 'no-code')),
  CONSTRAINT nvo7_publication_counts_ck CHECK (0 <= matched_count AND matched_count <= school_count)
);
COMMENT ON TABLE obrazovanie.nvo7_publication IS 'Една учебна година на НВО VII: изпитният ресурс, регистърът и колко училища са съпоставени по код.';
COMMENT ON COLUMN obrazovanie.nvo7_publication.exam_updated_on IS 'Денят на последната промяна на изпитния ресурс по каталога на data.egov.bg.';
COMMENT ON COLUMN obrazovanie.nvo7_publication.unmatched IS 'Кодовете по НЕИСПУО от изпитния файл, които липсват в регистъра на годината.';
COMMENT ON COLUMN obrazovanie.nvo7_publication.verification IS 'code: сверено с регистър с кодове; no-code: регистърът на годината няма кодове и не се свързва нищо.';

CREATE TABLE obrazovanie.school (
  school_year       text NOT NULL,
  neispuo           text NOT NULL,
  name              text NOT NULL,
  oblast            text NOT NULL,
  municipality      text NOT NULL,
  town              text NOT NULL,
  matched           boolean NOT NULL,
  municipality_code text,
  ekatte            text,
  CONSTRAINT school_pk PRIMARY KEY (school_year, neispuo),
  CONSTRAINT school_year_fk FOREIGN KEY (school_year) REFERENCES obrazovanie.nvo7_publication (school_year),
  CONSTRAINT school_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT school_ekatte_fk FOREIGN KEY (ekatte) REFERENCES core.settlement (ekatte)
);
CREATE INDEX school_name_idx ON obrazovanie.school (school_year, name);
COMMENT ON TABLE obrazovanie.school IS 'Училище с резултат от НВО VII за една година: името и мястото от регистъра, ако кодът е в него, иначе от изпитния файл.';
COMMENT ON COLUMN obrazovanie.school.neispuo IS 'Кодът на училището по НЕИСПУО.';
COMMENT ON COLUMN obrazovanie.school.matched IS 'Кодът е намерен в регистъра на същата година.';
COMMENT ON COLUMN obrazovanie.school.municipality_code IS 'Общината по имената в източника; NULL без съвпадение (училищата в чужбина).';
COMMENT ON COLUMN obrazovanie.school.ekatte IS 'Населеното място само при едно съвпадение по име в общината. Иначе NULL: не се предполага.';

CREATE TABLE obrazovanie.exam_result (
  school_year text NOT NULL,
  neispuo     text NOT NULL,
  subject     text NOT NULL,
  takers      integer,
  score       numeric,
  scale       text NOT NULL,
  CONSTRAINT exam_result_pk PRIMARY KEY (school_year, neispuo, subject),
  CONSTRAINT exam_result_school_fk FOREIGN KEY (school_year, neispuo) REFERENCES obrazovanie.school (school_year, neispuo),
  CONSTRAINT exam_result_takers_ck CHECK (takers >= 0)
);
COMMENT ON TABLE obrazovanie.exam_result IS 'Резултатът на едно училище по един предмет от НВО VII.';
COMMENT ON COLUMN obrazovanie.exam_result.takers IS 'Явилите се. NULL: източникът не дава число.';
COMMENT ON COLUMN obrazovanie.exam_result.score IS 'Средният резултат в точки. NULL: източникът не дава число.';
COMMENT ON COLUMN obrazovanie.exam_result.scale IS 'Скалата на точките: points100 (от 0 до 100) или points65 (2017/2018, от 0 до 65).';

CREATE TABLE obrazovanie.dzi_publication (
  resource           text NOT NULL,
  school_year        text NOT NULL,
  session            text NOT NULL,
  kind               text NOT NULL,
  updated_on         date NOT NULL,
  register_resource  text,
  verification       text NOT NULL,
  source_rows        integer NOT NULL,
  school_count       integer NOT NULL,
  aggregate_count    integer NOT NULL,
  result_count       integer NOT NULL,
  matched_count      integer NOT NULL,
  unmatched          text[] NOT NULL,
  missing_grade      integer NOT NULL,
  suppressed_takers  integer NOT NULL,
  source_placeholder integer NOT NULL,
  load_id            bigint NOT NULL,
  CONSTRAINT dzi_publication_pk PRIMARY KEY (resource),
  CONSTRAINT dzi_publication_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT dzi_publication_session_ck CHECK (session IN ('may', 'august')),
  CONSTRAINT dzi_publication_kind_ck CHECK (kind IN ('mandatory', 'optional')),
  CONSTRAINT dzi_publication_verification_ck CHECK (verification IN ('code', 'no-code')),
  CONSTRAINT dzi_publication_rows_ck CHECK (source_rows = school_count + aggregate_count)
);
CREATE INDEX dzi_publication_year_idx ON obrazovanie.dzi_publication (school_year DESC, session, kind);
COMMENT ON TABLE obrazovanie.dzi_publication IS 'Един ресурс с резултатите от ДЗИ: година, сесия (май или август), вид (задължителни или по желание) и броячите.';
COMMENT ON COLUMN obrazovanie.dzi_publication.missing_grade IS 'Клетки с явили се, но без оценка.';
COMMENT ON COLUMN obrazovanie.dzi_publication.suppressed_takers IS 'Клетки, в които МОН е скрило броя явили се с „–“.';
COMMENT ON COLUMN obrazovanie.dzi_publication.source_placeholder IS 'Клетки със знак на източника вместо оценка, четени като празни.';

CREATE TABLE obrazovanie.dzi_result (
  resource          text NOT NULL,
  row_number        integer NOT NULL,
  subject           text NOT NULL,
  neispuo           text,
  school            text NOT NULL,
  oblast            text NOT NULL,
  municipality      text NOT NULL,
  town              text NOT NULL,
  takers            integer,
  score             numeric,
  is_school         boolean NOT NULL,
  matched           boolean,
  municipality_code text,
  ekatte            text,
  CONSTRAINT dzi_result_pk PRIMARY KEY (resource, row_number, subject),
  CONSTRAINT dzi_result_resource_fk FOREIGN KEY (resource) REFERENCES obrazovanie.dzi_publication (resource),
  CONSTRAINT dzi_result_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT dzi_result_ekatte_fk FOREIGN KEY (ekatte) REFERENCES core.settlement (ekatte),
  CONSTRAINT dzi_result_score_ck CHECK (score BETWEEN 2 AND 6)
);
CREATE INDEX dzi_result_school_idx ON obrazovanie.dzi_result (neispuo) WHERE neispuo IS NOT NULL;
COMMENT ON TABLE obrazovanie.dzi_result IS 'Резултатът на едно училище (или сборен ред на РУО) по един предмет от ДЗИ.';
COMMENT ON COLUMN obrazovanie.dzi_result.row_number IS 'Редът във файла на МОН.';
COMMENT ON COLUMN obrazovanie.dzi_result.score IS 'Средната оценка от 2 до 6. NULL: източникът не дава оценка.';
COMMENT ON COLUMN obrazovanie.dzi_result.matched IS 'Кодът е в регистъра на същата година. NULL: няма регистър с кодове или редът не е училище.';
COMMENT ON COLUMN obrazovanie.dzi_result.municipality_code IS 'Общината по имената в източника; NULL, ако файлът не дава община или няма съвпадение.';
COMMENT ON COLUMN obrazovanie.dzi_result.ekatte IS 'Населеното място само при едно съвпадение по име. Иначе NULL.';

CREATE TABLE obrazovanie.nvo_publication (
  resource          text NOT NULL,
  exam              text NOT NULL,
  school_year       text NOT NULL,
  updated_on        date NOT NULL,
  register_resource text,
  verification      text NOT NULL,
  school_count      integer NOT NULL,
  subject_count     integer NOT NULL,
  result_count      integer NOT NULL,
  matched_count     integer NOT NULL,
  unmatched         text[] NOT NULL,
  load_id           bigint NOT NULL,
  CONSTRAINT nvo_publication_pk PRIMARY KEY (resource),
  CONSTRAINT nvo_publication_exam_year_uq UNIQUE (exam, school_year),
  CONSTRAINT nvo_publication_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT nvo_publication_exam_ck CHECK (exam IN ('nvo4', 'nvo10')),
  CONSTRAINT nvo_publication_verification_ck CHECK (verification IN ('code', 'no-code'))
);
COMMENT ON TABLE obrazovanie.nvo_publication IS 'Една година на НВО IV или X клас.';

CREATE TABLE obrazovanie.nvo_result (
  resource          text NOT NULL,
  neispuo           text NOT NULL,
  subject           text NOT NULL,
  school            text NOT NULL,
  oblast            text NOT NULL,
  municipality      text NOT NULL,
  town              text NOT NULL,
  takers            integer,
  score             numeric,
  matched           boolean,
  municipality_code text,
  ekatte            text,
  CONSTRAINT nvo_result_pk PRIMARY KEY (resource, neispuo, subject),
  CONSTRAINT nvo_result_resource_fk FOREIGN KEY (resource) REFERENCES obrazovanie.nvo_publication (resource),
  CONSTRAINT nvo_result_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT nvo_result_ekatte_fk FOREIGN KEY (ekatte) REFERENCES core.settlement (ekatte)
);
CREATE INDEX nvo_result_school_idx ON obrazovanie.nvo_result (neispuo);
COMMENT ON TABLE obrazovanie.nvo_result IS 'Резултатът на едно училище по един предмет от НВО IV или X клас, в точки.';
COMMENT ON COLUMN obrazovanie.nvo_result.matched IS 'Кодът е в регистъра на същата година. NULL: няма регистър с кодове.';
COMMENT ON COLUMN obrazovanie.nvo_result.municipality_code IS 'Общината по имената в източника; NULL без съвпадение.';
COMMENT ON COLUMN obrazovanie.nvo_result.ekatte IS 'Населеното място само при едно съвпадение по име в общината. Иначе NULL.';

CREATE TABLE obrazovanie.status_publication (
  resource    text NOT NULL,
  kind        text NOT NULL,
  school_year text NOT NULL,
  updated_on  date NOT NULL,
  row_count   integer NOT NULL,
  school_rows integer NOT NULL,
  load_id     bigint NOT NULL,
  CONSTRAINT status_publication_pk PRIMARY KEY (resource),
  CONSTRAINT status_publication_kind_year_uq UNIQUE (kind, school_year),
  CONSTRAINT status_publication_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT status_publication_kind_ck CHECK (kind IN ('protected', 'central'))
);
COMMENT ON TABLE obrazovanie.status_publication IS 'Един официален списък на защитените (protected) или средищните (central) училища и детски градини за година.';

CREATE TABLE obrazovanie.status_row (
  resource   text NOT NULL,
  row_number integer NOT NULL,
  neispuo    text NOT NULL,
  name       text NOT NULL,
  town       text NOT NULL,
  scope      text,
  is_school  boolean NOT NULL,
  CONSTRAINT status_row_pk PRIMARY KEY (resource, row_number),
  CONSTRAINT status_row_resource_fk FOREIGN KEY (resource) REFERENCES obrazovanie.status_publication (resource)
);
CREATE INDEX status_row_school_idx ON obrazovanie.status_row (neispuo) WHERE is_school;
COMMENT ON TABLE obrazovanie.status_row IS 'Един ред на официален списък: училище или детска градина, място и за кого е статусът.';
COMMENT ON COLUMN obrazovanie.status_row.name IS 'Името на училището или детската градина (не на човек).';
COMMENT ON COLUMN obrazovanie.status_row.scope IS 'За кои деца или ученици е статусът, както го пише МОН.';

CREATE TABLE obrazovanie.context_publication (
  school_year         text NOT NULL,
  pupils_resource     text NOT NULL,
  classes_resource    text NOT NULL,
  pupils_updated_on   date NOT NULL,
  classes_updated_on  date NOT NULL,
  kind_count          integer NOT NULL,
  grade_1_12_students integer NOT NULL,
  reported_groups     numeric NOT NULL,
  load_id             bigint NOT NULL,
  CONSTRAINT context_publication_pk PRIMARY KEY (school_year),
  CONSTRAINT context_publication_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE obrazovanie.context_publication IS 'Националната справка за една учебна година: ученици по класове, групи и паралелки.';

CREATE TABLE obrazovanie.context_kind (
  school_year     text NOT NULL,
  kind            text NOT NULL,
  institutions    integer NOT NULL,
  preschool       integer NOT NULL,
  reported_pupils integer NOT NULL,
  reported_groups numeric NOT NULL,
  CONSTRAINT context_kind_pk PRIMARY KEY (school_year, kind),
  CONSTRAINT context_kind_year_fk FOREIGN KEY (school_year) REFERENCES obrazovanie.context_publication (school_year)
);
COMMENT ON TABLE obrazovanie.context_kind IS 'Един вид училище в националната справка: брой училища, деца в предучилищни групи, ученици и паралелки.';

CREATE TABLE obrazovanie.context_grade (
  school_year text NOT NULL,
  kind        text NOT NULL,
  grade       smallint NOT NULL,
  students    integer NOT NULL,
  CONSTRAINT context_grade_pk PRIMARY KEY (school_year, kind, grade),
  CONSTRAINT context_grade_kind_fk FOREIGN KEY (school_year, kind) REFERENCES obrazovanie.context_kind (school_year, kind),
  CONSTRAINT context_grade_ck CHECK (grade BETWEEN 1 AND 15 AND students >= 0)
);
COMMENT ON TABLE obrazovanie.context_grade IS 'Учениците в един клас за един вид училище; 14 и 15 са специалните паралелки.';

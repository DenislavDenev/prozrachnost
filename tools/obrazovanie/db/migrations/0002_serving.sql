-- The serving layer of Образование: the finished tables its pages read, and nothing else do they read.
-- It is filled only by a publication (db/publish/*.sql through pipeline.publish()), and it points only to the
-- shared serving tables pub.*, so it can be copied out with them and without the rest of the database.
DO $$ BEGIN IF to_regnamespace('pub_obrazovanie') IS NULL THEN CREATE SCHEMA pub_obrazovanie; END IF; END $$;
COMMENT ON SCHEMA pub_obrazovanie IS 'Образование, слой за показване: публикуваните резултати от НВО и ДЗИ, официалните списъци и националните бройки.';

CREATE TABLE pub_obrazovanie.nvo7_publication (
  school_year         text NOT NULL,
  exam_resource       text NOT NULL,
  register_resource   text NOT NULL,
  exam_updated_on     date NOT NULL,
  register_updated_on date NOT NULL,
  school_count        integer NOT NULL,
  matched_count       integer NOT NULL,
  unmatched           text[] NOT NULL,
  verification        text NOT NULL,
  CONSTRAINT nvo7_publication_pk PRIMARY KEY (school_year)
);
COMMENT ON TABLE pub_obrazovanie.nvo7_publication IS 'Публикуваните години на НВО VII: изпитният ресурс, регистърът и съпоставянето по код.';

CREATE TABLE pub_obrazovanie.school (
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
  CONSTRAINT school_year_fk FOREIGN KEY (school_year) REFERENCES pub_obrazovanie.nvo7_publication (school_year) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT school_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT school_ekatte_fk FOREIGN KEY (ekatte) REFERENCES pub.settlement (ekatte) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX school_neispuo_idx ON pub_obrazovanie.school (neispuo);
COMMENT ON TABLE pub_obrazovanie.school IS 'Училище с резултат от НВО VII за една година, с общината и населеното място, когато са еднозначни.';

CREATE TABLE pub_obrazovanie.exam_result (
  school_year text NOT NULL,
  neispuo     text NOT NULL,
  subject     text NOT NULL,
  takers      integer,
  score       numeric,
  scale       text NOT NULL,
  CONSTRAINT exam_result_pk PRIMARY KEY (school_year, neispuo, subject),
  CONSTRAINT exam_result_school_fk FOREIGN KEY (school_year, neispuo) REFERENCES pub_obrazovanie.school (school_year, neispuo) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_obrazovanie.exam_result IS 'Резултатът на едно училище по един предмет от НВО VII.';

CREATE TABLE pub_obrazovanie.dzi_publication (
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
  CONSTRAINT dzi_publication_pk PRIMARY KEY (resource)
);
COMMENT ON TABLE pub_obrazovanie.dzi_publication IS 'Публикуваните ресурси на ДЗИ: година, сесия, вид и броячите.';

CREATE TABLE pub_obrazovanie.dzi_result (
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
  CONSTRAINT dzi_result_resource_fk FOREIGN KEY (resource) REFERENCES pub_obrazovanie.dzi_publication (resource) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT dzi_result_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT dzi_result_ekatte_fk FOREIGN KEY (ekatte) REFERENCES pub.settlement (ekatte) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX dzi_result_school_idx ON pub_obrazovanie.dzi_result (neispuo) WHERE neispuo IS NOT NULL;
COMMENT ON TABLE pub_obrazovanie.dzi_result IS 'Резултатът на едно училище (или сборен ред на РУО) по един предмет от ДЗИ.';

CREATE TABLE pub_obrazovanie.nvo_publication (
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
  CONSTRAINT nvo_publication_pk PRIMARY KEY (resource)
);
COMMENT ON TABLE pub_obrazovanie.nvo_publication IS 'Публикуваните години на НВО IV и X клас.';

CREATE TABLE pub_obrazovanie.nvo_result (
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
  CONSTRAINT nvo_result_resource_fk FOREIGN KEY (resource) REFERENCES pub_obrazovanie.nvo_publication (resource) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT nvo_result_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT nvo_result_ekatte_fk FOREIGN KEY (ekatte) REFERENCES pub.settlement (ekatte) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX nvo_result_school_idx ON pub_obrazovanie.nvo_result (neispuo);
COMMENT ON TABLE pub_obrazovanie.nvo_result IS 'Резултатът на едно училище по един предмет от НВО IV или X клас.';

CREATE TABLE pub_obrazovanie.status_publication (
  resource    text NOT NULL,
  kind        text NOT NULL,
  school_year text NOT NULL,
  updated_on  date NOT NULL,
  row_count   integer NOT NULL,
  school_rows integer NOT NULL,
  CONSTRAINT status_publication_pk PRIMARY KEY (resource)
);
COMMENT ON TABLE pub_obrazovanie.status_publication IS 'Публикуваните официални списъци на защитените и средищните училища.';

CREATE TABLE pub_obrazovanie.status_row (
  resource   text NOT NULL,
  row_number integer NOT NULL,
  neispuo    text NOT NULL,
  name       text NOT NULL,
  town       text NOT NULL,
  scope      text,
  is_school  boolean NOT NULL,
  CONSTRAINT status_row_pk PRIMARY KEY (resource, row_number),
  CONSTRAINT status_row_resource_fk FOREIGN KEY (resource) REFERENCES pub_obrazovanie.status_publication (resource) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX status_row_school_idx ON pub_obrazovanie.status_row (neispuo) WHERE is_school;
COMMENT ON TABLE pub_obrazovanie.status_row IS 'Един ред на официален списък: училище или детска градина, място и за кого е статусът.';

CREATE TABLE pub_obrazovanie.context_publication (
  school_year         text NOT NULL,
  pupils_resource     text NOT NULL,
  classes_resource    text NOT NULL,
  pupils_updated_on   date NOT NULL,
  classes_updated_on  date NOT NULL,
  kind_count          integer NOT NULL,
  grade_1_12_students integer NOT NULL,
  reported_groups     numeric NOT NULL,
  CONSTRAINT context_publication_pk PRIMARY KEY (school_year)
);
COMMENT ON TABLE pub_obrazovanie.context_publication IS 'Публикуваните национални справки: ученици по класове, групи и паралелки.';

CREATE TABLE pub_obrazovanie.context_kind (
  school_year     text NOT NULL,
  kind            text NOT NULL,
  institutions    integer NOT NULL,
  preschool       integer NOT NULL,
  reported_pupils integer NOT NULL,
  reported_groups numeric NOT NULL,
  CONSTRAINT context_kind_pk PRIMARY KEY (school_year, kind),
  CONSTRAINT context_kind_year_fk FOREIGN KEY (school_year) REFERENCES pub_obrazovanie.context_publication (school_year) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_obrazovanie.context_kind IS 'Един вид училище в националната справка.';

CREATE TABLE pub_obrazovanie.context_grade (
  school_year text NOT NULL,
  kind        text NOT NULL,
  grade       smallint NOT NULL,
  students    integer NOT NULL,
  CONSTRAINT context_grade_pk PRIMARY KEY (school_year, kind, grade),
  CONSTRAINT context_grade_kind_fk FOREIGN KEY (school_year, kind) REFERENCES pub_obrazovanie.context_kind (school_year, kind) DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE pub_obrazovanie.context_grade IS 'Учениците в един клас за един вид училище.';

-- The web role reads the serving layer only.
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'obrazovanie_web') THEN
    GRANT USAGE ON SCHEMA pub_obrazovanie TO obrazovanie_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_obrazovanie TO obrazovanie_web;
    ALTER DEFAULT PRIVILEGES IN SCHEMA pub_obrazovanie GRANT SELECT ON TABLES TO obrazovanie_web;
  END IF;
END $$;

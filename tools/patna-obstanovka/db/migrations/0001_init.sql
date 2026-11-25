-- Пътна обстановка in the shared database: the landing zone patna_obstanovka_src and the clean tables patna_obstanovka.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the oblasts and municipalities are
-- core.*. Until 24.11.2026 the tool had its own SQLite file (road.db).
--
-- Every source is read whole: the landing zone keeps only the current answer of each (policy `latest`). The traffic,
-- the weather and the restrictions are what the source says now, so the clean side shows them as views of the
-- current answer. The road sections and the crashes keep their history: a changed or vanished row gets superseded_at.
DO $$ BEGIN
  IF to_regnamespace('patna_obstanovka_src') IS NULL THEN CREATE SCHEMA patna_obstanovka_src; END IF;
  IF to_regnamespace('patna_obstanovka') IS NULL THEN CREATE SCHEMA patna_obstanovka; END IF;
END $$;
COMMENT ON SCHEMA patna_obstanovka_src IS 'Пътна обстановка, приемна зона: последният приет отговор на всеки източник, ред по ред.';
COMMENT ON SCHEMA patna_obstanovka IS 'Пътна обстановка: трафик, метеостанции, ограничения, оценени пътни участъци и катастрофи, с ключове към core.';

-- ---------------------------------------------------------------- БГТОЛ
CREATE TABLE patna_obstanovka_src.toll_answer (
  load_id  bigint NOT NULL,
  kind     text NOT NULL,
  sha256   text NOT NULL,
  skipped  integer NOT NULL,
  CONSTRAINT toll_answer_pk PRIMARY KEY (load_id),
  CONSTRAINT toll_answer_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT toll_answer_kind_ck CHECK (kind IN ('traffic', 'weather'))
);
COMMENT ON TABLE patna_obstanovka_src.toll_answer IS 'Един приет отговор на БГТОЛ: отпечатък на измерванията и колко станции са без координати в България.';

CREATE TABLE patna_obstanovka_src.traffic_reading (
  load_id  bigint NOT NULL,
  row_no   integer NOT NULL,
  scp      text NOT NULL,
  name     text,
  lat      double precision NOT NULL,
  lon      double precision NOT NULL,
  count15  integer,
  count60  integer,
  speed15  double precision,
  observed timestamptz,
  CONSTRAINT traffic_reading_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT traffic_reading_scp_uq UNIQUE (load_id, scp),
  CONSTRAINT traffic_reading_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.traffic_reading IS 'Броячите на БГТОЛ: една посока на една точка (SCP), в реда на източника.';
COMMENT ON COLUMN patna_obstanovka_src.traffic_reading.scp IS 'Кодът на точката и посоката. Последната цифра различава нарастващ (1) и намаляващ (2) километър.';
COMMENT ON COLUMN patna_obstanovka_src.traffic_reading.count15 IS 'Превозни средства за последните 15 минути (count15min).';
COMMENT ON COLUMN patna_obstanovka_src.traffic_reading.count60 IS 'Превозни средства за последния час (count1Hour).';
COMMENT ON COLUMN patna_obstanovka_src.traffic_reading.speed15 IS 'average_speed_15min, както е в източника. Единицата не е потвърдена и страниците не я показват.';
COMMENT ON COLUMN patna_obstanovka_src.traffic_reading.observed IS 'Часът на измерването. БГТОЛ го пише без зона и го описва като UTC.';

CREATE TABLE patna_obstanovka_src.weather_reading (
  load_id  bigint NOT NULL,
  row_no   integer NOT NULL,
  scp      text NOT NULL,
  name     text,
  lat      double precision NOT NULL,
  lon      double precision NOT NULL,
  air      double precision,
  surface  double precision,
  humidity double precision,
  wind     double precision,
  observed timestamptz,
  CONSTRAINT weather_reading_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT weather_reading_scp_uq UNIQUE (load_id, scp),
  CONSTRAINT weather_reading_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.weather_reading IS 'Метеостанциите на БГТОЛ, в реда на източника.';
COMMENT ON COLUMN patna_obstanovka_src.weather_reading.air IS 'Температура на въздуха, °C.';
COMMENT ON COLUMN patna_obstanovka_src.weather_reading.surface IS 'Температура на настилката, °C.';
COMMENT ON COLUMN patna_obstanovka_src.weather_reading.humidity IS 'Относителна влажност, %.';
COMMENT ON COLUMN patna_obstanovka_src.weather_reading.wind IS 'Скорост на вятъра, км/ч.';

-- ---------------------------------------------------------------- АПИ ЛИМА
CREATE TABLE patna_obstanovka_src.lima_file (
  load_id bigint NOT NULL,
  kind    text NOT NULL,
  xml_url text NOT NULL,
  CONSTRAINT lima_file_pk PRIMARY KEY (load_id),
  CONSTRAINT lima_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.lima_file IS 'Файлът DATEX II на деня, към който сочи каталогът на ЛИМА, и видът на съобщенията в него.';

CREATE TABLE patna_obstanovka_src.lima_event (
  load_id     bigint NOT NULL,
  row_no      integer NOT NULL,
  record_id   text NOT NULL,
  title       text NOT NULL,
  detail      text NOT NULL,
  lat         double precision NOT NULL,
  lon         double precision NOT NULL,
  valid_from  text,
  valid_until text,
  CONSTRAINT lima_event_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT lima_event_record_uq UNIQUE (load_id, record_id),
  CONSTRAINT lima_event_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.lima_event IS 'Съобщенията с точка от файла на ЛИМА: първата публикувана координата и периодът на валидност, както са в източника.';

-- ---------------------------------------------------------------- ДАБДП (ArcGIS)
CREATE TABLE patna_obstanovka_src.risk_layer (
  load_id  bigint NOT NULL,
  layer    smallint NOT NULL,
  features integer NOT NULL,
  counted  integer NOT NULL,
  CONSTRAINT risk_layer_pk PRIMARY KEY (load_id, layer),
  CONSTRAINT risk_layer_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT risk_layer_count_ck CHECK (features = counted)
);
COMMENT ON TABLE patna_obstanovka_src.risk_layer IS 'Сверката на всеки слой на ArcGIS: прочетени обекти и броят, който казва самият източник (returnCountOnly).';

CREATE TABLE patna_obstanovka_src.risk_section (
  load_id    bigint NOT NULL,
  row_no     integer NOT NULL,
  layer      smallint NOT NULL,
  fid        integer NOT NULL,
  road       text NOT NULL,
  risk_level text NOT NULL,
  condition  text NOT NULL,
  reference  text NOT NULL,
  district   text NOT NULL,
  minlat     double precision NOT NULL,
  maxlat     double precision NOT NULL,
  minlon     double precision NOT NULL,
  maxlon     double precision NOT NULL,
  geometry   text NOT NULL,
  length_km  double precision,
  CONSTRAINT risk_section_pk PRIMARY KEY (load_id, layer, fid),
  CONSTRAINT risk_section_row_uq UNIQUE (load_id, row_no),
  CONSTRAINT risk_section_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.risk_section IS 'Оценените участъци на ДАБДП (6 слоя), в реда на четене. Оценка с референция 2023, не текущо състояние.';
COMMENT ON COLUMN patna_obstanovka_src.risk_section.road IS 'National_r, иначе Section_ID, иначе „Пътен участък“.';
COMMENT ON COLUMN patna_obstanovka_src.risk_section.district IS 'District: кодът на областта по ЕКАТТЕ, както е в източника.';
COMMENT ON COLUMN patna_obstanovka_src.risk_section.geometry IS 'Линиите на участъка (paths на ArcGIS, EPSG:4326) като JSON.';
COMMENT ON COLUMN patna_obstanovka_src.risk_section.length_km IS 'L__km: публикуваната дължина на участъка.';

-- ---------------------------------------------------------------- МВР
CREATE TABLE patna_obstanovka_src.crash_file (
  load_id  bigint NOT NULL,
  csv_rows integer NOT NULL,
  skipped  integer NOT NULL,
  CONSTRAINT crash_file_pk PRIMARY KEY (load_id),
  CONSTRAINT crash_file_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.crash_file IS 'Един приет ptp.zip: редовете на CSV и колко от тях са без координати или дата.';

CREATE TABLE patna_obstanovka_src.crash (
  load_id      bigint NOT NULL,
  row_no       integer NOT NULL,
  crash_at     text NOT NULL,
  lat          double precision NOT NULL,
  lon          double precision NOT NULL,
  crash_type   text NOT NULL,
  killed       integer NOT NULL,
  injured      integer NOT NULL,
  region       text,
  municipality text,
  CONSTRAINT crash_src_pk PRIMARY KEY (load_id, row_no),
  CONSTRAINT crash_src_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE patna_obstanovka_src.crash IS 'Катастрофите от ptp.zip на МВР с координати и дата.';
COMMENT ON COLUMN patna_obstanovka_src.crash.row_no IS 'Мястото на реда във файла, като се броят всички редове. Архивът няма друг номер.';
COMMENT ON COLUMN patna_obstanovka_src.crash.crash_at IS 'crashdatetime, както е в източника (местно време).';
COMMENT ON COLUMN patna_obstanovka_src.crash.region IS 'Областта, както я пише МВР (СОФИЯ е столицата, СОФИЙСКА е областта).';
COMMENT ON COLUMN patna_obstanovka_src.crash.municipality IS 'Общината, както я пише МВР.';

-- ---------------------------------------------------------------- clean: the current answers
CREATE VIEW patna_obstanovka.traffic AS
SELECT t.row_no, t.scp, t.name, t.lat, t.lon, t.count15, t.count60, t.speed15, t.observed, t.load_id
FROM patna_obstanovka_src.traffic_reading t JOIN ops.load l USING (load_id)
WHERE l.tool = 'patna-obstanovka' AND l.source = 'traffic' AND l.is_current;
COMMENT ON VIEW patna_obstanovka.traffic IS 'Измереният трафик сега: действащият отговор на БГТОЛ.';

CREATE VIEW patna_obstanovka.weather AS
SELECT w.row_no, w.scp, w.name, w.lat, w.lon, w.air, w.surface, w.humidity, w.wind, w.observed, w.load_id
FROM patna_obstanovka_src.weather_reading w JOIN ops.load l USING (load_id)
WHERE l.tool = 'patna-obstanovka' AND l.source = 'weather' AND l.is_current;
COMMENT ON VIEW patna_obstanovka.weather IS 'Метеостанциите сега: действащият отговор на БГТОЛ.';

CREATE VIEW patna_obstanovka.event AS
SELECT l.source, f.kind, f.xml_url, e.row_no, e.record_id, e.title, e.detail, e.lat, e.lon, e.valid_from, e.valid_until, e.load_id
FROM patna_obstanovka_src.lima_event e JOIN patna_obstanovka_src.lima_file f USING (load_id) JOIN ops.load l USING (load_id)
WHERE l.tool = 'patna-obstanovka' AND l.is_current;
COMMENT ON VIEW patna_obstanovka.event IS 'Ограниченията и инцидентите сега: действащите файлове на ЛИМА.';

-- ---------------------------------------------------------------- clean: sections and crashes, with their history
CREATE TABLE patna_obstanovka.risk_section (
  risk_section_id bigint GENERATED ALWAYS AS IDENTITY,
  section_id      text NOT NULL,
  layer           smallint NOT NULL,
  fid             integer NOT NULL,
  position        integer NOT NULL,
  road            text NOT NULL,
  risk_level      text NOT NULL,
  condition       text NOT NULL,
  reference       text NOT NULL,
  district        text NOT NULL,
  oblast_code     text,
  minlat          double precision NOT NULL,
  maxlat          double precision NOT NULL,
  minlon          double precision NOT NULL,
  maxlon          double precision NOT NULL,
  geometry        text NOT NULL,
  length_km       double precision,
  load_id         bigint NOT NULL,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  superseded_at   timestamptz,
  CONSTRAINT risk_section_pk PRIMARY KEY (risk_section_id),
  CONSTRAINT risk_section_oblast_fk FOREIGN KEY (oblast_code) REFERENCES core.oblast (oblast_code),
  CONSTRAINT risk_section_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT risk_section_id_ck CHECK (section_id = layer || ':' || fid)
);
CREATE UNIQUE INDEX risk_section_current_uq ON patna_obstanovka.risk_section (section_id) WHERE superseded_at IS NULL;
CREATE INDEX risk_section_load_idx ON patna_obstanovka.risk_section (load_id);
COMMENT ON TABLE patna_obstanovka.risk_section IS 'Оценените участъци на ДАБДП. Променен или изчезнал участък получава superseded_at; действащите са без него.';
COMMENT ON COLUMN patna_obstanovka.risk_section.section_id IS 'слой:FID, номерът, който страниците и API-то винаги са ползвали.';
COMMENT ON COLUMN patna_obstanovka.risk_section.position IS 'Редът на участъка при последното четене (слой, после страница на ArcGIS). Само за подредба.';
COMMENT ON COLUMN patna_obstanovka.risk_section.oblast_code IS 'Областта по ЕКАТТЕ, когато District е неин код. Иначе NULL: не се предполага.';

CREATE TABLE patna_obstanovka.crash (
  crash_id          bigint GENERATED ALWAYS AS IDENTITY,
  crash_key         text NOT NULL,
  row_no            integer NOT NULL,
  crash_at          text NOT NULL,
  lat               double precision NOT NULL,
  lon               double precision NOT NULL,
  crash_type        text NOT NULL,
  killed            integer NOT NULL,
  injured           integer NOT NULL,
  municipality_code text,
  load_id           bigint NOT NULL,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  superseded_at     timestamptz,
  CONSTRAINT crash_pk PRIMARY KEY (crash_id),
  CONSTRAINT crash_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT crash_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT crash_numbers_ck CHECK (killed >= 0 AND injured >= 0)
);
CREATE UNIQUE INDEX crash_current_uq ON patna_obstanovka.crash (crash_key) WHERE superseded_at IS NULL;
CREATE INDEX crash_row_idx ON patna_obstanovka.crash (row_no) WHERE superseded_at IS NULL;
CREATE INDEX crash_load_idx ON patna_obstanovka.crash (load_id);
COMMENT ON TABLE patna_obstanovka.crash IS 'Катастрофите от архива на МВР. Архивът няма номер на запис: ключът е часът, координатите и пореден номер при еднакви. Променен или изчезнал запис получава superseded_at.';
COMMENT ON COLUMN patna_obstanovka.crash.crash_key IS 'час|ширина|дължина|пореден номер сред записите със същите три.';
COMMENT ON COLUMN patna_obstanovka.crash.row_no IS 'Мястото на реда в последния приет файл: номерът, който страниците показват. Следва файла, без да прави нова версия.';
COMMENT ON COLUMN patna_obstanovka.crash.municipality_code IS 'Общината по ЕКАТТЕ при точно едно съвпадение на областта и името от МВР. Иначе NULL: не се предполага.';

-- ---------------------------------------------------------------- clean: derived
CREATE TABLE patna_obstanovka.station_bearing (
  scp         text NOT NULL,
  lat         double precision NOT NULL,
  lon         double precision NOT NULL,
  bearing     double precision,
  method      text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT station_bearing_pk PRIMARY KEY (scp),
  CONSTRAINT station_bearing_method_ck CHECK (method IN ('risk', 'osm', 'none', 'road.db')),
  CONSTRAINT station_bearing_value_ck CHECK (bearing IS NULL OR (bearing >= 0 AND bearing < 360))
);
COMMENT ON TABLE patna_obstanovka.station_bearing IS 'Посоката на стрелката на всяка точка на БГТОЛ: по най-близкия оценен участък до 500 м, иначе по път от OpenStreetMap до 300 м. NULL: не е потвърдена.';
COMMENT ON COLUMN patna_obstanovka.station_bearing.method IS 'risk: по оценен участък; osm: по OpenStreetMap; none: няма път наблизо; road.db: пренесена от старата база на 24.11.2026.';

CREATE TABLE patna_obstanovka.crash_match (
  load_id    bigint NOT NULL,
  crash_key  text NOT NULL,
  section_id text NOT NULL,
  distance_m double precision NOT NULL,
  CONSTRAINT crash_match_pk PRIMARY KEY (load_id, crash_key),
  CONSTRAINT crash_match_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT crash_match_distance_ck CHECK (distance_m >= 0 AND distance_m <= 75)
);
COMMENT ON TABLE patna_obstanovka.crash_match IS 'Катастрофа към оценен участък: най-близкият до 75 м, ако следващият не е по-близо от 15 м по-далеч (кръстовища и успоредни платна не се свързват).';

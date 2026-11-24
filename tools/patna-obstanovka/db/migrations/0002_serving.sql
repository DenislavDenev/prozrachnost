-- The serving layer of Пътна обстановка: the finished tables the map reads, and nothing else does it read. It is
-- filled only by a publication (db/publish/*.sql through pipeline.publish()) and points nowhere outside itself.
DO $$ BEGIN IF to_regnamespace('pub_patna_obstanovka') IS NULL THEN CREATE SCHEMA pub_patna_obstanovka; END IF; END $$;
COMMENT ON SCHEMA pub_patna_obstanovka IS 'Пътна обстановка, слой за показване: каквото картата и API-то показват.';

-- the state of every source in the words of /api/status: the last good read, its rows, and the error of the last
-- read when it failed (the last good data stays shown)
CREATE VIEW patna_obstanovka.source_state AS
WITH reads AS (
  SELECT source, load_id, loaded_at, status, reason, ref, is_current FROM ops.load WHERE tool = 'patna-obstanovka'
), last AS (
  SELECT DISTINCT ON (source) source, status, reason, ref FROM reads ORDER BY source, loaded_at DESC, load_id DESC
), ok AS (
  SELECT source, max(loaded_at) AS last_ok FROM reads WHERE status IN ('loaded', 'confirmed', 'unchanged') GROUP BY source
), counted AS (
  SELECT load_id, count(*)::int AS n FROM patna_obstanovka_src.traffic_reading GROUP BY load_id
  UNION ALL SELECT load_id, count(*)::int FROM patna_obstanovka_src.weather_reading GROUP BY load_id
  UNION ALL SELECT f.load_id, count(e.row_no)::int FROM patna_obstanovka_src.lima_file f
            LEFT JOIN patna_obstanovka_src.lima_event e USING (load_id) GROUP BY f.load_id
  UNION ALL SELECT load_id, count(*)::int FROM patna_obstanovka_src.risk_section GROUP BY load_id
  UNION ALL SELECT load_id, count(*)::int FROM patna_obstanovka_src.crash GROUP BY load_id
  UNION ALL SELECT load_id, count(*)::int FROM patna_obstanovka.crash_match GROUP BY load_id
), cur AS (
  SELECT r.source, r.load_id, c.n, f.xml_url FROM reads r LEFT JOIN counted c USING (load_id)
  LEFT JOIN patna_obstanovka_src.lima_file f USING (load_id) WHERE r.is_current
)
SELECT s.source AS key, s.title_bg AS label,
       CASE WHEN last.status = 'rejected' THEN last.ref ELSE COALESCE(cur.xml_url, last.ref, s.url) END AS url,
       to_char(ok.last_ok AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"') AS updated,
       COALESCE(cur.n, 0) AS count,
       CASE WHEN last.status = 'rejected' THEN last.reason
            WHEN last.status = 'held' THEN 'задържан до второ четене: ' || last.reason END AS error
FROM ops.source s JOIN last USING (source) LEFT JOIN ok USING (source) LEFT JOIN cur USING (source)
WHERE s.tool = 'patna-obstanovka';
COMMENT ON VIEW patna_obstanovka.source_state IS 'Състоянието на всеки източник, както го показва /api/status: последното успешно четене, редовете му и грешката на последното четене, ако е неуспешно.';

CREATE TABLE pub_patna_obstanovka.traffic (
  row_no   integer NOT NULL,
  scp      text NOT NULL,
  name     text,
  lat      double precision NOT NULL,
  lon      double precision NOT NULL,
  count15  integer,
  count60  integer,
  speed15  double precision,
  observed text,
  bearing  double precision,
  CONSTRAINT traffic_pk PRIMARY KEY (row_no),
  CONSTRAINT traffic_scp_uq UNIQUE (scp)
);
COMMENT ON TABLE pub_patna_obstanovka.traffic IS 'Измереният трафик по посока на точка на БГТОЛ, със стрелка, когато посоката е потвърдена.';

CREATE TABLE pub_patna_obstanovka.weather (
  row_no   integer NOT NULL,
  scp      text NOT NULL,
  name     text,
  lat      double precision NOT NULL,
  lon      double precision NOT NULL,
  air      double precision,
  surface  double precision,
  humidity double precision,
  wind     double precision,
  observed text,
  CONSTRAINT weather_pk PRIMARY KEY (row_no),
  CONSTRAINT weather_scp_uq UNIQUE (scp)
);
COMMENT ON TABLE pub_patna_obstanovka.weather IS 'Метеостанциите на БГТОЛ: въздух, настилка, влажност и вятър.';

CREATE TABLE pub_patna_obstanovka.event (
  position    integer NOT NULL,
  id          text NOT NULL,
  kind        text NOT NULL,
  title       text NOT NULL,
  detail      text NOT NULL,
  lat         double precision NOT NULL,
  lon         double precision NOT NULL,
  valid_from  text,
  valid_until text,
  source_url  text NOT NULL,
  CONSTRAINT event_pk PRIMARY KEY (position)
);
COMMENT ON TABLE pub_patna_obstanovka.event IS 'Затворени пътища, затворени ленти и инциденти от ЛИМА, с първата публикувана точка.';

CREATE TABLE pub_patna_obstanovka.risk_section (
  id         text NOT NULL,
  position   integer NOT NULL,
  layer      smallint NOT NULL,
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
  CONSTRAINT risk_section_pk PRIMARY KEY (id),
  CONSTRAINT risk_section_position_uq UNIQUE (position)
);
COMMENT ON TABLE pub_patna_obstanovka.risk_section IS 'Оценените участъци на ДАБДП (референция 2023) с линиите им.';

CREATE TABLE pub_patna_obstanovka.crash (
  id          text NOT NULL,
  row_no      integer NOT NULL,
  date        text NOT NULL,
  lat         double precision NOT NULL,
  lon         double precision NOT NULL,
  description text NOT NULL,
  killed      integer NOT NULL,
  injured     integer NOT NULL,
  CONSTRAINT crash_pk PRIMARY KEY (id),
  CONSTRAINT crash_row_uq UNIQUE (row_no)
);
CREATE INDEX crash_place_idx ON pub_patna_obstanovka.crash (lat, lon, date);
CREATE INDEX crash_date_idx ON pub_patna_obstanovka.crash (date);
COMMENT ON TABLE pub_patna_obstanovka.crash IS 'Катастрофите от архива на МВР с координати и дата. id е мястото на реда в архива.';

CREATE TABLE pub_patna_obstanovka.crash_match (
  crash_id   text NOT NULL,
  risk_id    text NOT NULL,
  distance_m double precision NOT NULL,
  CONSTRAINT crash_match_pk PRIMARY KEY (crash_id),
  CONSTRAINT crash_match_crash_fk FOREIGN KEY (crash_id) REFERENCES pub_patna_obstanovka.crash (id) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT crash_match_risk_fk FOREIGN KEY (risk_id) REFERENCES pub_patna_obstanovka.risk_section (id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX crash_match_risk_idx ON pub_patna_obstanovka.crash_match (risk_id);
COMMENT ON TABLE pub_patna_obstanovka.crash_match IS 'Катастрофа към най-близкия оценен участък до 75 м, без кръстовища и успоредни платна.';

CREATE TABLE pub_patna_obstanovka.source_state (
  key     text NOT NULL,
  label   text NOT NULL,
  url     text,
  updated text,
  count   integer NOT NULL,
  error   text,
  CONSTRAINT source_state_pk PRIMARY KEY (key)
);
COMMENT ON TABLE pub_patna_obstanovka.source_state IS 'Последното успешно четене на всеки източник, броят на редовете му и грешката на последното четене, ако е неуспешно.';

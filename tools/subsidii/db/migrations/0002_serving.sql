-- The serving layer of Субсидии и помощи: what the pages, the CSV and the JSON read, and nothing else. Written only by
-- pipeline.publish() from the SELECTs in db/publish/; foreign keys point only inside pub*.
--
-- Names of people (PERSONS.md, decision 3, the same as for donors): a natural person or a sole trader whose row has more
-- than the threshold of the source itself (1 250 euro, Regulation (EU) 2021/2116, art. 98) is a row with a masked name,
-- "Иван ***", until two years after the end of the year; one with the threshold or less is only in the sums
-- (below_threshold, municipality_fy, summary, concentration). Legal entities are shown with their full name. There is no
-- key of a person here: beneficiary_id stays on the ingest side.
DO $$ BEGIN IF to_regnamespace('pub_subsidii') IS NULL THEN CREATE SCHEMA pub_subsidii; END IF; END $$;
COMMENT ON SCHEMA pub_subsidii IS 'Субсидии и помощи, слой за показване: каквото четат страниците; сменя се само с публикуване.';

CREATE TABLE pub_subsidii.fiscal_year (
  fy integer NOT NULL, starts date NOT NULL, ends date NOT NULL, label_bg text NOT NULL, currency text, eur_per_unit numeric(20,12),
  unit_evidence text, in_form boolean NOT NULL, gone_at date, first_snap date, last_snap date, names_until date NOT NULL,
  name_threshold_eur numeric NOT NULL,
  CONSTRAINT fiscal_year_pk PRIMARY KEY (fy)
);
COMMENT ON TABLE pub_subsidii.fiscal_year IS 'Финансовите години: етикет, валута, курс към евро, първи и последен прочит, срок и праг за имената.';

CREATE TABLE pub_subsidii.snapshot (
  fy integer NOT NULL, day date NOT NULL, status text NOT NULL, sha256 text NOT NULL, path text NOT NULL, bytes bigint NOT NULL, encoding text,
  rows_file integer, holders integer, payments integer, total_efgz numeric(18,2), total_ezfrs numeric(18,2), total_nb numeric(18,2),
  total_all numeric(18,2), paid_efgz numeric(18,2), paid_ezfrs numeric(18,2), paid_nb numeric(18,2), block_diffs integer, negatives integer,
  rows_added integer, rows_removed integer, note text, built_at timestamptz NOT NULL, build_secs numeric,
  CONSTRAINT snapshot_pk PRIMARY KEY (fy, day)
);
COMMENT ON TABLE pub_subsidii.snapshot IS 'Всеки файл на архива: проверката му и какво е записано от него.';

CREATE TABLE pub_subsidii.build (
  fy integer NOT NULL, snap_day date NOT NULL, finished timestamptz, ok boolean, rows jsonb, checks jsonb,
  CONSTRAINT build_pk PRIMARY KEY (fy, snap_day)
);
COMMENT ON TABLE pub_subsidii.build IS 'Изграждането на сборовете на всеки прочит; страниците показват само прочитите с ok.';

CREATE TABLE pub_subsidii.measure (
  measure_id integer NOT NULL, code text NOT NULL, name text NOT NULL, objective text NOT NULL, family text NOT NULL, family_bg text NOT NULL,
  CONSTRAINT measure_pk PRIMARY KEY (measure_id)
);
COMMENT ON TABLE pub_subsidii.measure IS 'Мерките с групата им.';

CREATE TABLE pub_subsidii.org (
  org_id text NOT NULL, name_norm text NOT NULL, name_bg text NOT NULL,
  CONSTRAINT org_pk PRIMARY KEY (org_id)
);
COMMENT ON TABLE pub_subsidii.org IS 'Юридическите лица по нормализирано наименование (не по ЕИК).';

CREATE TABLE pub_subsidii.recipient (
  holder_id bigint NOT NULL, fy integer NOT NULL, from_day date NOT NULL, to_day date, kind text NOT NULL, org_id text,
  municipality_code text, oblast_raw text NOT NULL, obshtina_raw text NOT NULL,
  org_name text, person_masked pub.masked_name,
  efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), ezfrs_nb numeric(16,2), total numeric(16,2),
  CONSTRAINT recipient_pk PRIMARY KEY (holder_id),
  CONSTRAINT recipient_org_fk FOREIGN KEY (org_id) REFERENCES pub_subsidii.org (org_id) DEFERRABLE,
  CONSTRAINT recipient_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE,
  CONSTRAINT recipient_kind_ck CHECK (kind IN ('legal', 'sole_trader', 'natural')),
  CONSTRAINT recipient_names_ck CHECK ((kind = 'legal' AND person_masked IS NULL) OR (kind <> 'legal' AND org_name IS NULL AND org_id IS NULL))
);
CREATE INDEX recipient_span ON pub_subsidii.recipient (fy, from_day, to_day);
CREATE INDEX recipient_org ON pub_subsidii.recipient (org_id) WHERE org_id IS NOT NULL;
COMMENT ON TABLE pub_subsidii.recipient IS 'Редовете ОБЩО за дните от from_day до to_day: юридическо лице с наименованието си, физическо лице и ЕТ над прага с маска до срока; под прага ги няма (само в сборовете).';
COMMENT ON COLUMN pub_subsidii.recipient.org_name IS 'Наименованието на юридическото лице; NULL за хора.';
COMMENT ON COLUMN pub_subsidii.recipient.person_masked IS 'Маската на физическо лице или ЕТ („Иван ***“); NULL след срока.';

CREATE TABLE pub_subsidii.below_threshold (
  fy integer NOT NULL, snap_day date NOT NULL, municipality_code text NOT NULL, kind text NOT NULL,
  recipients integer NOT NULL, efgz numeric(18,2), ezfrs numeric(18,2), nb numeric(18,2), total numeric(18,2),
  CONSTRAINT below_threshold_pk PRIMARY KEY (fy, snap_day, municipality_code, kind),
  CONSTRAINT below_threshold_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE
);
COMMENT ON TABLE pub_subsidii.below_threshold IS 'Физическите лица и ЕТ с прага или по-малко: само брой и сума по община, за всеки прочит.';

CREATE TABLE pub_subsidii.municipality_fy (
  fy integer NOT NULL, snap_day date NOT NULL, municipality_code text NOT NULL, kind text NOT NULL, recipients integer NOT NULL,
  efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), total numeric(16,2),
  CONSTRAINT municipality_fy_pk PRIMARY KEY (fy, snap_day, municipality_code, kind),
  CONSTRAINT municipality_fy_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE
);
COMMENT ON TABLE pub_subsidii.municipality_fy IS 'Сборовете по община и вид получател, за всеки прочит.';

CREATE TABLE pub_subsidii.payment_agg (
  fy integer NOT NULL, snap_day date NOT NULL, municipality_code text NOT NULL, measure_id integer NOT NULL, kind text NOT NULL,
  payments integer NOT NULL, efgz numeric(16,2), ezfrs numeric(16,2), nb numeric(16,2), total numeric(16,2),
  CONSTRAINT payment_agg_pk PRIMARY KEY (fy, snap_day, municipality_code, measure_id, kind),
  CONSTRAINT payment_agg_municipality_fk FOREIGN KEY (municipality_code) REFERENCES pub.municipality (municipality_code) DEFERRABLE,
  CONSTRAINT payment_agg_measure_fk FOREIGN KEY (measure_id) REFERENCES pub_subsidii.measure (measure_id) DEFERRABLE
);
CREATE INDEX payment_agg_measure ON pub_subsidii.payment_agg (fy, snap_day, measure_id);
COMMENT ON TABLE pub_subsidii.payment_agg IS 'Плащанията по община, мярка и вид получател, за всеки прочит.';

CREATE TABLE pub_subsidii.summary (
  fy integer NOT NULL, snap_day date NOT NULL, scope text NOT NULL, recipients integer NOT NULL, payments integer NOT NULL,
  efgz numeric(18,2), ezfrs numeric(18,2), nb numeric(18,2), total numeric(18,2), top1_n integer, top1_total numeric(18,2),
  CONSTRAINT summary_pk PRIMARY KEY (fy, snap_day, scope)
);
COMMENT ON TABLE pub_subsidii.summary IS 'Сборовете по вид получател за всеки прочит.';

CREATE TABLE pub_subsidii.concentration (
  fy integer NOT NULL, snap_day date NOT NULL, place_level text NOT NULL, place text NOT NULL, kind text NOT NULL,
  n integer NOT NULL, total numeric(18,2), top_n integer NOT NULL, top_total numeric(18,2),
  CONSTRAINT concentration_pk PRIMARY KEY (fy, snap_day, place_level, place, kind),
  CONSTRAINT concentration_level_ck CHECK (place_level IN ('bg', 'oblast', 'municipality'))
);
COMMENT ON TABLE pub_subsidii.concentration IS 'Дялът на най-горния 1% от редовете ОБЩО (не от хората) в страната, областта и общината, по вид получател, смятан от всички редове, и тези под прага.';

CREATE TABLE pub_subsidii.org_measure (
  org_id text NOT NULL, fy integer NOT NULL, snap_day date NOT NULL, measure_id integer NOT NULL,
  payments integer NOT NULL, efgz numeric(18,2), ezfrs numeric(18,2), nb numeric(18,2),
  CONSTRAINT org_measure_pk PRIMARY KEY (org_id, fy, snap_day, measure_id),
  CONSTRAINT org_measure_org_fk FOREIGN KEY (org_id) REFERENCES pub_subsidii.org (org_id) DEFERRABLE,
  CONSTRAINT org_measure_measure_fk FOREIGN KEY (measure_id) REFERENCES pub_subsidii.measure (measure_id) DEFERRABLE
);
COMMENT ON TABLE pub_subsidii.org_measure IS 'Плащанията на юридическо лице по мярка, за всеки прочит.';

CREATE TABLE pub_subsidii.block_diff (
  fy integer NOT NULL, day date NOT NULL, line_no integer NOT NULL, fund text NOT NULL, stated numeric(16,2) NOT NULL, summed numeric(16,2) NOT NULL,
  kind text NOT NULL, name_export text NOT NULL, oblast text NOT NULL, obshtina text NOT NULL,
  CONSTRAINT block_diff_pk PRIMARY KEY (fy, day, line_no, fund)
);
COMMENT ON TABLE pub_subsidii.block_diff IS 'Несъответствията на източника; хората са „физическо лице“.';

CREATE TABLE pub_subsidii.unmatched (
  kind text NOT NULL, key text NOT NULL, n integer NOT NULL, amount numeric(18,2), reason text NOT NULL,
  CONSTRAINT unmatched_pk PRIMARY KEY (kind, key)
);
COMMENT ON TABLE pub_subsidii.unmatched IS 'Общините на справката, които справочникът не знае.';

CREATE TABLE pub_subsidii.change (
  change_id bigint NOT NULL, detected_at timestamptz NOT NULL, ref text NOT NULL, field text, old text, new text, cause text NOT NULL,
  CONSTRAINT change_pk PRIMARY KEY (change_id)
);
COMMENT ON TABLE pub_subsidii.change IS 'Последните промени в дневника на приемането на ДФЗ (по година, без имена).';

CREATE TABLE pub_subsidii.health (
  kind text NOT NULL, name text NOT NULL, title_bg text NOT NULL, is_ok boolean, since_at timestamptz, limit_h integer, detail text,
  CONSTRAINT health_pk PRIMARY KEY (kind, name)
);
COMMENT ON TABLE pub_subsidii.health IS 'От какво страницата „Източници“ смята проблемите: архивът, задържаните файлове, правилата, последното пускане на всяка стъпка.';

DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'subsidii_web') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA subsidii, subsidii_src FROM subsidii_web;
    REVOKE ALL ON SCHEMA subsidii, subsidii_src FROM subsidii_web;
    GRANT USAGE ON SCHEMA pub_subsidii TO subsidii_web;
    GRANT SELECT ON ALL TABLES IN SCHEMA pub_subsidii TO subsidii_web;
  END IF;
END $$;

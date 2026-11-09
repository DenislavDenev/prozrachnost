-- The shared dimensions and registers. Only the role that owns this schema writes here.
DO $$ BEGIN IF to_regnamespace('core') IS NULL THEN CREATE SCHEMA core; END IF; END $$;
COMMENT ON SCHEMA core IS 'Общите измерения и регистри на всички инструменти.';

-- ЕИК / БУЛСТАТ: 9 or 13 digits with the official control digits.
CREATE FUNCTION core.eik_valid(e text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE d int[]; s int;
BEGIN
  IF e !~ '^([0-9]{9}|[0-9]{13})$' OR e ~ '^0+$' THEN RETURN false; END IF;
  d := string_to_array(e, NULL)::int[];
  s := (d[1] + 2*d[2] + 3*d[3] + 4*d[4] + 5*d[5] + 6*d[6] + 7*d[7] + 8*d[8]) % 11;
  IF s = 10 THEN
    s := (3*d[1] + 4*d[2] + 5*d[3] + 6*d[4] + 7*d[5] + 8*d[6] + 9*d[7] + 10*d[8]) % 11;
    IF s = 10 THEN s := 0; END IF;
  END IF;
  IF s <> d[9] THEN RETURN false; END IF;
  IF length(e) = 9 THEN RETURN true; END IF;
  s := (2*d[9] + 7*d[10] + 3*d[11] + 5*d[12]) % 11;
  IF s = 10 THEN
    s := (4*d[9] + 9*d[10] + 5*d[11] + 7*d[12]) % 11;
    IF s = 10 THEN s := 0; END IF;
  END IF;
  RETURN s = d[13];
END $$;
COMMENT ON FUNCTION core.eik_valid(text) IS 'Вярно, когато ЕИК/БУЛСТАТ е от 9 или 13 цифри с вярна контролна цифра.';

CREATE TABLE core.nuts (
  nuts_code   text NOT NULL,
  level       smallint NOT NULL,
  name_bg     text NOT NULL,
  name_en     text,
  parent_code text,
  CONSTRAINT nuts_pk PRIMARY KEY (nuts_code),
  CONSTRAINT nuts_parent_fk FOREIGN KEY (parent_code) REFERENCES core.nuts (nuts_code),
  CONSTRAINT nuts_level_ck CHECK (level BETWEEN 0 AND 3 AND length(nuts_code) = level + 2),
  CONSTRAINT nuts_parent_ck CHECK ((level = 0) = (parent_code IS NULL))
);
COMMENT ON TABLE core.nuts IS 'Статистическите райони по NUTS: държава (0), макрорайон (1), район (2), област (3).';

CREATE TABLE core.oblast (
  oblast_code text NOT NULL,
  name_bg     text NOT NULL,
  name_en     text,
  nuts3_code  text NOT NULL,
  CONSTRAINT oblast_pk PRIMARY KEY (oblast_code),
  CONSTRAINT oblast_nuts3_uq UNIQUE (nuts3_code),
  CONSTRAINT oblast_nuts3_fk FOREIGN KEY (nuts3_code) REFERENCES core.nuts (nuts_code),
  CONSTRAINT oblast_code_ck CHECK (oblast_code ~ '^[A-Z]{3}$')
);
COMMENT ON TABLE core.oblast IS 'Областите с кода им по НСИ (ЕКАТТЕ) и по NUTS 3.';

CREATE TABLE core.municipality (
  municipality_code text NOT NULL,
  oblast_code       text NOT NULL,
  name_bg           text NOT NULL,
  name_en           text,
  eik               text NOT NULL,
  drawn_as_eik      text NOT NULL,
  center_ekatte     text,
  gone_at           timestamptz,
  CONSTRAINT municipality_pk PRIMARY KEY (municipality_code),
  CONSTRAINT municipality_eik_uq UNIQUE (eik),
  CONSTRAINT municipality_oblast_fk FOREIGN KEY (oblast_code) REFERENCES core.oblast (oblast_code),
  CONSTRAINT municipality_code_ck CHECK (municipality_code ~ '^[A-Z]{3}[0-9]{2}$' AND left(municipality_code, 3) = oblast_code),
  CONSTRAINT municipality_eik_ck CHECK (core.eik_valid(eik))
);
CREATE INDEX municipality_oblast_idx ON core.municipality (oblast_code);
COMMENT ON TABLE core.municipality IS 'Общините. Ключът е кодът на територията по НСИ; ЕИК е на общинската администрация.';
COMMENT ON COLUMN core.municipality.eik IS 'ЕИК/БУЛСТАТ на общинската администрация. С него страниците адресират общината.';
COMMENT ON COLUMN core.municipality.drawn_as_eik IS 'Общината, чийто контур се ползва на картата (същата, освен когато контурите са по-стари от общината).';

CREATE TABLE core.settlement (
  ekatte            text NOT NULL,
  kind_prefix       text NOT NULL,
  name_bg           text NOT NULL,
  name_en           text,
  municipality_code text NOT NULL,
  kmetstvo_code     text,
  settlement_kind   smallint,
  category          smallint,
  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  gone_at           timestamptz,
  CONSTRAINT settlement_pk PRIMARY KEY (ekatte),
  CONSTRAINT settlement_municipality_fk FOREIGN KEY (municipality_code) REFERENCES core.municipality (municipality_code),
  CONSTRAINT settlement_ekatte_ck CHECK (ekatte ~ '^[0-9]{5}$')
);
CREATE INDEX settlement_municipality_idx ON core.settlement (municipality_code);
COMMENT ON TABLE core.settlement IS 'Населените места по ЕКАТТЕ. Закрито място остава с gone_at.';
COMMENT ON COLUMN core.settlement.kind_prefix IS 'Вид пред името, както е в ЕКАТТЕ: гр., с., ман.';

CREATE TABLE core.org (
  eik             text NOT NULL,
  name            text NOT NULL,
  legal_form      text,
  seat            text,
  registered_on   date,
  register_status text,
  is_public_body  boolean NOT NULL DEFAULT false,
  is_buyer        boolean NOT NULL DEFAULT false,
  is_person_named boolean NOT NULL DEFAULT false,
  source          text NOT NULL,
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  gone_at         timestamptz,
  CONSTRAINT org_pk PRIMARY KEY (eik),
  CONSTRAINT org_eik_ck CHECK (core.eik_valid(eik))
);
COMMENT ON TABLE core.org IS 'Юридическите лица и институциите по ЕИК. Физически лица не влизат.';
COMMENT ON COLUMN core.org.is_person_named IS 'Едноличен търговец: името на субекта съдържа име на човек и не се показва в публичния API.';
COMMENT ON COLUMN core.org.source IS 'Откъде е редът: tr (намерен в Търговския регистър), eop (срещнат само в данните за поръчките).';

CREATE TABLE core.currency (
  currency_code text NOT NULL,
  name_bg       text NOT NULL,
  fixed_per_eur numeric,
  fixed_from    date,
  CONSTRAINT currency_pk PRIMARY KEY (currency_code),
  CONSTRAINT currency_code_ck CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT currency_fixed_ck CHECK ((fixed_per_eur IS NULL) = (fixed_from IS NULL))
);
COMMENT ON TABLE core.currency IS 'Валутите по ISO 4217. Валута с фиксиран курс към еврото го носи тук.';

CREATE TABLE core.fx_rate (
  currency_code text NOT NULL,
  day           date NOT NULL,
  per_eur       numeric NOT NULL,
  source        text NOT NULL,
  CONSTRAINT fx_rate_pk PRIMARY KEY (currency_code, day),
  CONSTRAINT fx_rate_currency_fk FOREIGN KEY (currency_code) REFERENCES core.currency (currency_code),
  CONSTRAINT fx_rate_per_eur_ck CHECK (per_eur > 0)
);
COMMENT ON TABLE core.fx_rate IS 'Дневни курсове: колко единици от валутата са едно евро.';

-- NULL when there is no rate for that currency and day: the amount is never guessed.
CREATE FUNCTION core.to_eur(amount numeric, currency text, day date) RETURNS numeric
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT round(CASE
    WHEN amount IS NULL OR currency IS NULL THEN NULL
    WHEN currency = 'EUR' THEN amount
    ELSE amount / COALESCE(
      (SELECT c.fixed_per_eur FROM core.currency c WHERE c.currency_code = currency AND day >= c.fixed_from),
      (SELECT f.per_eur FROM core.fx_rate f WHERE f.currency_code = currency AND f.day = to_eur.day))
  END, 2)
$$;
COMMENT ON FUNCTION core.to_eur(numeric, text, date) IS 'Сумата в евро по фиксирания или дневния курс. NULL, когато курс за този ден няма.';

CREATE TABLE core.unit (
  unit_code text NOT NULL,
  name_bg   text NOT NULL,
  CONSTRAINT unit_pk PRIMARY KEY (unit_code)
);
COMMENT ON TABLE core.unit IS 'Мерните единици на показателите.';

CREATE TABLE core.period_kind (
  period_kind text NOT NULL,
  name_bg     text NOT NULL,
  CONSTRAINT period_kind_pk PRIMARY KEY (period_kind)
);
COMMENT ON TABLE core.period_kind IS 'Видовете период. Натрупано от началото на годината (ytd) е отделен вид.';

CREATE TABLE core.dataset (
  dataset            text NOT NULL,
  tool               text NOT NULL,
  title_bg           text NOT NULL,
  description_bg     text,
  source_org         text NOT NULL,
  source_url         text,
  channel            text NOT NULL,
  licence            text NOT NULL,
  licence_url        text,
  licence_checked_on date,
  is_distributable   boolean NOT NULL DEFAULT false,
  personal_data      text NOT NULL DEFAULT 'none',
  cadence            text,
  CONSTRAINT dataset_pk PRIMARY KEY (dataset),
  CONSTRAINT dataset_channel_ck CHECK (channel IN ('api', 'file', 'document', 'site-json', 'html', 'derived')),
  CONSTRAINT dataset_personal_ck CHECK (personal_data IN ('none', 'public-role', 'names')),
  CONSTRAINT dataset_distributable_ck CHECK (NOT is_distributable OR licence_checked_on IS NOT NULL)
);
COMMENT ON TABLE core.dataset IS 'Регистър на наборите: източник, канал, лиценз и дали може да се разпространява.';
COMMENT ON COLUMN core.dataset.is_distributable IS 'Вярно само след проверен лиценз, който позволява разпространение. Иначе наборът не стига до API-то.';
COMMENT ON COLUMN core.dataset.personal_data IS 'none: няма; public-role: хора в публична роля; names: имена на физически лица (не влиза в API-то).';

CREATE TABLE core.dataset_table (
  schema_name text NOT NULL,
  table_name  text NOT NULL,
  dataset     text NOT NULL,
  CONSTRAINT dataset_table_pk PRIMARY KEY (schema_name, table_name),
  CONSTRAINT dataset_table_dataset_fk FOREIGN KEY (dataset) REFERENCES core.dataset (dataset)
);
COMMENT ON TABLE core.dataset_table IS 'Коя таблица към кой набор принадлежи. По това се проверява лицензът на изгледите за API-то.';

CREATE TABLE core.indicator (
  indicator_code text NOT NULL,
  dataset        text NOT NULL,
  title_bg       text NOT NULL,
  unit_code      text NOT NULL,
  kind           text NOT NULL,
  definition_bg  text,
  official_code  text,
  CONSTRAINT indicator_pk PRIMARY KEY (indicator_code),
  CONSTRAINT indicator_dataset_fk FOREIGN KEY (dataset) REFERENCES core.dataset (dataset),
  CONSTRAINT indicator_unit_fk FOREIGN KEY (unit_code) REFERENCES core.unit (unit_code),
  CONSTRAINT indicator_kind_ck CHECK (kind IN ('flow', 'stock', 'rate', 'index', 'count'))
);
COMMENT ON TABLE core.indicator IS 'Регистър на показателите с единица и вид (поток, наличност, дял, индекс, брой).';

CREATE TABLE core.xwalk (
  kind        text NOT NULL,
  a_source    text NOT NULL,
  a_key       text NOT NULL,
  b_source    text NOT NULL,
  b_key       text NOT NULL,
  method      text NOT NULL,
  evidence    text NOT NULL,
  valid_from  date NOT NULL DEFAULT '-infinity',
  valid_to    date,
  verified_on date NOT NULL,
  verified_by text NOT NULL,
  CONSTRAINT xwalk_pk PRIMARY KEY (kind, a_source, a_key, b_source, b_key, valid_from),
  CONSTRAINT xwalk_method_ck CHECK (method IN ('identifier', 'official-list', 'manual', 'candidate')),
  CONSTRAINT xwalk_person_ck CHECK (kind <> 'person' OR method IN ('identifier', 'official-list'))
);
COMMENT ON TABLE core.xwalk IS 'Съответствия между източници без общ идентификатор, с метод и доказателство.';
COMMENT ON COLUMN core.xwalk.method IS 'identifier, official-list, manual или candidate. candidate не е връзка и не се публикува. За хора само първите две.';

ALTER TABLE ops.source ADD CONSTRAINT source_dataset_fk FOREIGN KEY (dataset) REFERENCES core.dataset (dataset);
ALTER TABLE ops.check_rule ADD CONSTRAINT check_rule_dataset_fk FOREIGN KEY (dataset) REFERENCES core.dataset (dataset);

-- The documentation of every column comes from the catalogue, so it cannot fall behind the real tables.
CREATE VIEW core.column_doc AS
SELECT n.nspname AS schema_name, c.relname AS table_name, a.attnum AS position, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type, NOT a.attnotnull AS is_nullable,
       col_description(c.oid, a.attnum) AS description_bg, obj_description(c.oid, 'pg_class') AS table_description_bg,
       t.dataset
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN core.dataset_table t ON t.schema_name = n.nspname AND t.table_name = c.relname
WHERE c.relkind IN ('r', 'p', 'v', 'm') AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema';
COMMENT ON VIEW core.column_doc IS 'Описание на всяка колона от каталога на PostgreSQL (COMMENT ON), с набора на таблицата.';

INSERT INTO core.currency (currency_code, name_bg, fixed_per_eur, fixed_from) VALUES
  ('EUR', 'евро', 1, '-infinity'),
  ('BGN', 'лев', 1.95583, '1999-07-05');
COMMENT ON COLUMN core.currency.fixed_from IS 'От кой ден важи фиксираният курс. За лева: деноминацията от 05.07.1999.';

INSERT INTO core.period_kind (period_kind, name_bg) VALUES
  ('day', 'ден'), ('week', 'седмица'), ('month', 'месец'), ('quarter', 'тримесечие'), ('half', 'полугодие'),
  ('year', 'година'), ('ytd', 'натрупано от началото на годината'), ('fiscal-year', 'финансова година'),
  ('school-year', 'учебна година');

INSERT INTO core.unit (unit_code, name_bg) VALUES
  ('person', 'лица'), ('count', 'брой'), ('EUR', 'евро'), ('BGN', 'лева'), ('share', 'дял'), ('index', 'индекс');

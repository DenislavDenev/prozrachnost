-- Пазар in the shared database: the landing zone pazar_src and the clean tables pazar. The shared ledger (runs, raw
-- files, loads, holds, changes, checks) is in ops, the places and the organisations in core. The tables keep the shape
-- they had in the tool's own database (silver and gold) so that the move can be proved row by row.
-- Money in the spans is an integer in 1e-4 of the unit the chain submitted (BGN before its switch to the euro, EUR
-- after); the currency of a chain's day is in pazar.chain_day.currency, found from the data (docs/methodology.md).
DO $$ BEGIN
  IF to_regnamespace('pazar_src') IS NULL THEN CREATE SCHEMA pazar_src; END IF;
  IF to_regnamespace('pazar') IS NULL THEN CREATE SCHEMA pazar; END IF;
END $$;
COMMENT ON SCHEMA pazar_src IS 'Пазар, приемна зона: файловете на веригите в дневния ZIP на „Колко струва“, невалидните им редове, редовете на деня до изграждането, бюлетинът за горивата.';
COMMENT ON SCHEMA pazar IS 'Пазар, чисти таблици: вериги, обекти, продукти, цените като интервали, дневните наблюдения по категория и място.';

-- ---------------------------------------------------------------- landing

CREATE TABLE pazar_src.file (
  load_id    bigint NOT NULL REFERENCES ops.load,
  member     text NOT NULL,
  chain_name text,
  eik        text,
  eik_valid  boolean NOT NULL,
  crc32      bigint NOT NULL,
  bytes      bigint NOT NULL,
  delimiter  text,
  bom        boolean,
  encoding   text,
  truncated  boolean NOT NULL,
  records    integer NOT NULL,
  blank      integer NOT NULL,
  valid      integer NOT NULL,
  dups       integer NOT NULL,
  bad        integer NOT NULL,
  error      text,
  copy_of    text,
  CONSTRAINT file_pk PRIMARY KEY (load_id, member)
);
COMMENT ON TABLE pazar_src.file IS 'Един CSV файл на верига в дневния ZIP, както е подаден: името му, контролната сума в ZIP-а и какво е прочетено от него.';
COMMENT ON COLUMN pazar_src.file.member IS 'Името на файла в ZIP-а: „<Верига (Фирма)>_<ЕИК>.csv“.';
COMMENT ON COLUMN pazar_src.file.eik IS 'ЕИК от името на файла (без „BG“), като текст: водещите нули остават.';
COMMENT ON COLUMN pazar_src.file.crc32 IS 'CRC32 на файла, както го пази ZIP-ът. С размера казва дали файлът е същият като при предишно четене.';
COMMENT ON COLUMN pazar_src.file.truncated IS 'Файлът е отрязан (порталът реже на 20 MiB): последният му запис не се приема.';
COMMENT ON COLUMN pazar_src.file.records IS 'Записи с данни, без заглавието и празните редове.';
COMMENT ON COLUMN pazar_src.file.dups IS 'Записи, еднакви с по-ранен валиден запис за същия обект и код.';
COMMENT ON COLUMN pazar_src.file.error IS 'Целият файл не се чете (заглавие, име): записите му са невалидни редове с причина `file`.';
COMMENT ON COLUMN pazar_src.file.copy_of IS 'ЕИК на файла, чиито редове този повтаря точно, или `group`. Копието не влиза в числата.';

CREATE TABLE pazar_src.bad_row (
  load_id bigint NOT NULL REFERENCES ops.load,
  member  text NOT NULL,
  line_no integer NOT NULL,
  reason  text NOT NULL,
  raw     text NOT NULL
);
CREATE INDEX bad_row_load ON pazar_src.bad_row (load_id, member);
COMMENT ON TABLE pazar_src.bad_row IS 'Невалиден запис от файл на верига, както е подаден, с причината. Не влиза в цените, пази се.';

-- the rows of one day while it is built: emptied in the same transaction (landing policy `transient`)
CREATE UNLOGGED TABLE pazar_src.day_row (
  load_id    bigint NOT NULL,
  chain_id   integer NOT NULL,
  store_id   integer NOT NULL,
  product_id integer NOT NULL,
  retail     integer NOT NULL,
  promo      integer,
  category   integer,
  flags      smallint NOT NULL
);
COMMENT ON TABLE pazar_src.day_row IS 'Валидните редове на един ден, докато денят се строи. Изпразва се в същата транзакция: цените остават в интервалите.';

CREATE TABLE pazar_src.fuel_week (
  load_id  bigint NOT NULL REFERENCES ops.load,
  week     date NOT NULL,
  geo      text NOT NULL,
  fuel     text NOT NULL,
  with_tax numeric(12,4),
  wo_tax   numeric(12,4),
  CONSTRAINT fuel_week_pk PRIMARY KEY (load_id, week, geo, fuel),
  CONSTRAINT fuel_week_geo_ck CHECK (geo IN ('BG', 'EU', 'EUR')),
  CONSTRAINT fuel_week_fuel_ck CHECK (fuel IN ('euro95', 'diesel', 'LPG'))
);
COMMENT ON TABLE pazar_src.fuel_week IS 'Седмичният бюлетин за горивата на Европейската комисия, както е подаден: евро за 1000 литра, с и без данъци.';
COMMENT ON COLUMN pazar_src.fuel_week.week IS 'Понеделникът на седмицата на цените.';
COMMENT ON COLUMN pazar_src.fuel_week.geo IS 'BG, средното за ЕС (EU) или за еврозоната (EUR).';

-- ---------------------------------------------------------------- clean

CREATE TABLE pazar.chain (
  chain_id       integer GENERATED ALWAYS AS IDENTITY,
  eik            text NOT NULL,
  eik_valid      boolean NOT NULL,
  name           text NOT NULL,
  first_day      date NOT NULL,
  national_price boolean NOT NULL DEFAULT false,
  org_eik        text REFERENCES core.org (eik),
  CONSTRAINT chain_pk PRIMARY KEY (chain_id),
  CONSTRAINT chain_eik_uq UNIQUE (eik),
  CONSTRAINT chain_eik_ck CHECK (eik ~ '^[0-9]{9}([0-9]{4})?$')
);
COMMENT ON TABLE pazar.chain IS 'Търговска верига, както я назовава името на файла ѝ.';
COMMENT ON COLUMN pazar.chain.eik IS 'ЕИК от името на файла, като текст.';
COMMENT ON COLUMN pazar.chain.eik_valid IS 'Контролната цифра на ЕИК е вярна.';
COMMENT ON COLUMN pazar.chain.name IS '„<Верига> (<Фирма>)“ от името на последния прочетен файл.';
COMMENT ON COLUMN pazar.chain.first_day IS 'Първият ден, в който веригата има файл.';
COMMENT ON COLUMN pazar.chain.national_price IS 'Единственият обект на веригата е онлайн магазин за цялата страна: никога не е магазин на картата.';
COMMENT ON COLUMN pazar.chain.org_eik IS 'Същият ЕИК в общия регистър на организациите, когато го има там.';

CREATE TABLE pazar.store (
  store_id          integer GENERATED ALWAYS AS IDENTITY,
  chain_id          integer NOT NULL REFERENCES pazar.chain,
  place_raw         text NOT NULL,
  ekatte            text,
  district          text,
  name              text NOT NULL,
  first_day         date NOT NULL,
  settlement_ekatte text REFERENCES core.settlement (ekatte),
  CONSTRAINT store_pk PRIMARY KEY (store_id),
  CONSTRAINT store_uq UNIQUE (chain_id, place_raw, name)
);
COMMENT ON TABLE pazar.store IS 'Търговски обект на верига: населеното място и името с адреса, както са подадени.';
COMMENT ON COLUMN pazar.store.place_raw IS '„Населено място“, както е подадено.';
COMMENT ON COLUMN pazar.store.ekatte IS 'Пет цифри, когато мястото е код по ЕКАТТЕ (файловете губят водещите нули).';
COMMENT ON COLUMN pazar.store.district IS 'Районът (68134-02), когато файлът дава район на София, Пловдив или Варна.';
COMMENT ON COLUMN pazar.store.name IS '„Търговски обект“, както е подаден: име и адрес.';
COMMENT ON COLUMN pazar.store.settlement_ekatte IS 'Населеното място в общия регистър, когато кодът е в него; иначе празно.';

CREATE TABLE pazar.product (
  product_id integer GENERATED ALWAYS AS IDENTITY,
  chain_id   integer NOT NULL REFERENCES pazar.chain,
  code       text NOT NULL,
  name       text NOT NULL,
  category   integer,
  first_day  date NOT NULL,
  CONSTRAINT product_pk PRIMARY KEY (product_id),
  CONSTRAINT product_uq UNIQUE (chain_id, code)
);
COMMENT ON TABLE pazar.product IS 'Продукт на верига по нейния собствен код. Еднакъв код в две вериги не е един продукт.';
COMMENT ON COLUMN pazar.product.code IS '„Код на продукта“ като текст: „001314“ не е „1314“.';
COMMENT ON COLUMN pazar.product.name IS 'Последното прочетено име.';
COMMENT ON COLUMN pazar.product.category IS 'Последната прочетена категория.';

CREATE TABLE pazar.day (
  day          date NOT NULL,
  status       text NOT NULL,
  load_id      bigint REFERENCES ops.load,
  zip_sha256   text NOT NULL,
  zip_path     text NOT NULL,
  zip_bytes    bigint NOT NULL,
  files        integer,
  chains       integer,
  stores       integer,
  records      integer,
  valid        integer,
  dups         integer,
  bad          integer,
  blank        integer,
  spans_opened integer,
  spans_closed integer,
  anomalies    jsonb NOT NULL DEFAULT '{}',
  note         text,
  built_at     timestamptz NOT NULL DEFAULT now(),
  build_secs   numeric,
  CONSTRAINT day_pk PRIMARY KEY (day),
  CONSTRAINT day_status_ck CHECK (status IN ('built', 'held', 'invalid'))
);
COMMENT ON TABLE pazar.day IS 'Един ден на „Колко струва“: построен, задържан до второ четене или невалиден, с броя на записите.';
COMMENT ON COLUMN pazar.day.load_id IS 'Приемането на дневния ZIP, от което е построен денят.';
COMMENT ON COLUMN pazar.day.anomalies IS 'Отбелязаните аномалии, причините за невалидните редове, копията и смените на валутата в деня.';

CREATE TABLE pazar.chain_day (
  day          date NOT NULL,
  chain_id     integer NOT NULL REFERENCES pazar.chain,
  file_name    text NOT NULL,
  delimiter    text,
  bom          boolean,
  encoding     text,
  truncated    boolean NOT NULL DEFAULT false,
  records      integer NOT NULL,
  valid        integer NOT NULL,
  dups         integer NOT NULL,
  bad          integer NOT NULL,
  stores       integer NOT NULL,
  filed        boolean NOT NULL,
  error        text,
  copy_of      text,
  currency     text NOT NULL REFERENCES core.currency (currency_code),
  matched      integer,
  switch_share numeric,
  CONSTRAINT chain_day_pk PRIMARY KEY (day, chain_id),
  CONSTRAINT chain_day_currency_ck CHECK (currency IN ('BGN', 'EUR'))
);
CREATE INDEX chain_day_chain ON pazar.chain_day (chain_id, day);
COMMENT ON TABLE pazar.chain_day IS 'Файлът на една верига в един ден. Ден без файл на веригата не е „0 цени“: интервалите ѝ не се затварят.';
COMMENT ON COLUMN pazar.chain_day.filed IS 'Във файла има прочетени цени (не е нечетим или празен).';
COMMENT ON COLUMN pazar.chain_day.currency IS 'Валутата на цените на веригата в деня, открита от данните: BGN или EUR.';
COMMENT ON COLUMN pazar.chain_day.matched IS 'Редове с по-ранна цена, по които е проверена смяната на валутата.';
COMMENT ON COLUMN pazar.chain_day.switch_share IS 'Делът на тези редове, сменени точно по курса 1,95583, когато валутата се е сменила.';

CREATE TABLE pazar.price_span (
  store_id   integer NOT NULL,
  product_id integer NOT NULL,
  from_day   date NOT NULL,
  to_day     date,
  retail     integer NOT NULL,
  promo      integer,
  category   integer,
  flags      smallint NOT NULL
) PARTITION BY RANGE (from_day);
-- a span never crosses a month: on the 1st the standing prices are written again, so a day is read from one partition
CREATE INDEX price_span_open ON pazar.price_span (store_id, product_id) WHERE to_day IS NULL;
CREATE INDEX price_span_product ON pazar.price_span (product_id, from_day);
CREATE INDEX price_span_from_brin ON pazar.price_span USING brin (from_day);
COMMENT ON TABLE pazar.price_span IS 'Цена на продукт в обект като интервал: един ред, докато цената е същата. Интервал не минава през месец; дял на месец.';
COMMENT ON COLUMN pazar.price_span.to_day IS 'Последният ден с тази цена. Празно: още е цената на последния построен ден от месеца.';
COMMENT ON COLUMN pazar.price_span.retail IS 'Цената на дребно в 1/10 000 от валутата на веригата за деня (pazar.chain_day.currency).';
COMMENT ON COLUMN pazar.price_span.promo IS 'Цената в промоция, в същата единица; празно, когато няма промоция.';
COMMENT ON COLUMN pazar.price_span.flags IS 'Отбелязаните аномалии като битове (ingest/parse.py): 1 над 5 000, 2 промоцията не е под цената, 4 категория извън списъка, 8 две цени, 16 скок.';

CREATE FUNCTION pazar.ensure_month(d date) RETURNS void LANGUAGE plpgsql AS $$
DECLARE s date := date_trunc('month', d)::date;
BEGIN
  EXECUTE format('CREATE TABLE IF NOT EXISTS pazar.price_span_%s PARTITION OF pazar.price_span FOR VALUES FROM (%L) TO (%L)',
                 to_char(s, 'YYYYMM'), s, (s + interval '1 month')::date);
  EXECUTE format('CREATE TABLE IF NOT EXISTS pazar.category_day_%s PARTITION OF pazar.category_day FOR VALUES FROM (%L) TO (%L)',
                 to_char(s, 'YYYYMM'), s, (s + interval '1 month')::date);
END $$;
COMMENT ON FUNCTION pazar.ensure_month(date) IS 'Дяловете на месеца на деня в pazar.price_span и pazar.category_day.';

CREATE TABLE pazar.category (
  code             smallint NOT NULL,
  name_bg          text NOT NULL,
  group_bg         text NOT NULL,
  source_url       text NOT NULL,
  group_source_url text NOT NULL,
  CONSTRAINT category_pk PRIMARY KEY (code),
  CONSTRAINT category_code_ck CHECK (code BETWEEN 1 AND 101)
);
COMMENT ON TABLE pazar.category IS 'Официалният списък на 101-те категории на КЗП (чл. 55б ЗВЕРБ) с групите на портала; от db/ref/kzp_categories.csv.';

-- one row per day, category and scope: the whole country, a chain (key = ЕИК), an oblast (key = NUTS 3 code) or a
-- municipality (key = ЕИК of the municipality). Only rows that are not marked anomalies and are in the list.
CREATE TABLE pazar.category_day (
  day                  date NOT NULL,
  scope                text NOT NULL,
  key                  text NOT NULL,
  category             smallint NOT NULL,
  n_prices             integer NOT NULL,
  n_stores             integer NOT NULL,
  n_chains             integer NOT NULL,
  min_eur              numeric(12,4) NOT NULL,
  median_eur           numeric(12,4) NOT NULL,
  max_eur              numeric(12,4) NOT NULL,
  promo_prices         integer NOT NULL,
  promo_share          numeric(7,6) NOT NULL,
  median_effective_eur numeric(12,4) NOT NULL,
  matched              integer NOT NULL DEFAULT 0,
  changed              integer NOT NULL DEFAULT 0,
  sum_ln               numeric(16,8) NOT NULL DEFAULT 0,
  CONSTRAINT category_day_pk PRIMARY KEY (day, scope, key, category),
  CONSTRAINT category_day_scope_ck CHECK (scope IN ('country', 'chain', 'oblast', 'municipality'))
) PARTITION BY RANGE (day);
CREATE INDEX category_day_lookup ON pazar.category_day (scope, key, category, day);
COMMENT ON TABLE pazar.category_day IS 'Дневно наблюдение по категория и място (страна, верига, област, община), в евро, без аномалиите и копията.';
COMMENT ON COLUMN pazar.category_day.median_effective_eur IS 'Медианата на цената, която се плаща: промоционалната, когато има истинска промоция, иначе цената на дребно.';
COMMENT ON COLUMN pazar.category_day.matched IS 'Цени на същия обект и продукт при предишното подаване на веригата (до 7 дни назад): основата на индекса.';
COMMENT ON COLUMN pazar.category_day.changed IS 'От тях с променена цена.';
COMMENT ON COLUMN pazar.category_day.sum_ln IS 'Сборът на логаритмите на отношението на цените (индекс на Джевънс).';

CREATE TABLE pazar.municipality_day (
  day             date NOT NULL,
  municipality_id text NOT NULL REFERENCES core.municipality (eik),
  n_stores        integer NOT NULL,
  n_chains        integer NOT NULL,
  CONSTRAINT municipality_day_pk PRIMARY KEY (day, municipality_id)
);
COMMENT ON TABLE pazar.municipality_day IS 'Обекти и вериги с цени в общината в деня. Картата показва община само с поне 3 обекта от 2 вериги.';
COMMENT ON COLUMN pazar.municipality_day.municipality_id IS 'ЕИК на общината.';

CREATE TABLE pazar.promo_day (
  day          date NOT NULL,
  chain_eik    text NOT NULL REFERENCES pazar.chain (eik),
  n_promos     integer NOT NULL,
  n_real       integer NOT NULL,
  n_none       integer NOT NULL,
  n_raised     integer NOT NULL,
  n_nohistory  integer NOT NULL,
  avg_declared numeric(7,5),
  avg_real     numeric(7,5),
  window_days  integer NOT NULL,
  CONSTRAINT promo_day_pk PRIMARY KEY (day, chain_eik)
);
COMMENT ON TABLE pazar.promo_day IS 'Промоциите на верига в деня срещу собствените цени на обекта в 30-те дни преди това: истински отстъпки и „промоции“ без разлика.';

CREATE TABLE pazar.aggregate_day (
  day           date NOT NULL,
  built_at      timestamptz NOT NULL DEFAULT now(),
  rows          integer NOT NULL,
  secs          numeric,
  silver_sha256 text NOT NULL,
  CONSTRAINT aggregate_day_pk PRIMARY KEY (day)
);
COMMENT ON TABLE pazar.aggregate_day IS 'Денят, за който са изградени дневните наблюдения, и ZIP-ът, от който е построен.';

-- the stores with their place in the shared register: the municipality and the oblast of the daily observations
CREATE VIEW pazar.store_place AS
SELECT s.store_id, s.chain_id, c.eik AS chain_eik, c.national_price, m.eik AS municipality_id, o.nuts3_code AS nuts3
FROM pazar.store s
JOIN pazar.chain c USING (chain_id)
LEFT JOIN core.settlement p ON p.ekatte = s.settlement_ekatte
LEFT JOIN core.municipality m ON m.municipality_code = p.municipality_code
LEFT JOIN core.oblast o ON o.oblast_code = m.oblast_code;
COMMENT ON VIEW pazar.store_place IS 'Обектът с общината (ЕИК) и областта (NUTS 3) на населеното си място от общия регистър.';

-- the weekly bulletin as currently accepted, per litre
CREATE VIEW pazar.fuel_week AS
SELECT f.week, f.geo, f.fuel, round(f.with_tax / 1000, 4) AS with_tax_eur_l, round(f.wo_tax / 1000, 4) AS wo_tax_eur_l, r.sha256 AS source_sha256
FROM pazar_src.fuel_week f
JOIN ops.load l ON l.load_id = f.load_id AND l.is_current
JOIN ops.raw_file r ON r.raw_file_id = l.raw_file_id;
COMMENT ON VIEW pazar.fuel_week IS 'Цените на горивата от действащото приемане на бюлетина, в евро за литър.';

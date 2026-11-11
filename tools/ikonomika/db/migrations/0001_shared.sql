-- Икономика in the shared database: the landing zone ikonomika_src and the clean tables ikonomika.
-- The ledger (runs, files, loads, holds, changes, checks) is the shared schema ops; the indicators, the places of
-- Bulgaria and the period kinds are core.*. The schema of the old database of its own is kept in db/legacy.
--
-- The old database held every number in one long table live.series keyed by (indicator, dims jsonb, geo, time);
-- its primary key was larger than the table. Here a series (indicator, dimensions, place) is a row of its own with
-- a small number, and a fact is (series, period). Nothing of the old indexes is copied: only what the queries use.
DO $$ BEGIN
  IF to_regnamespace('ikonomika_src') IS NULL THEN CREATE SCHEMA ikonomika_src; END IF;
  IF to_regnamespace('ikonomika') IS NULL THEN CREATE SCHEMA ikonomika; END IF;
END $$;
COMMENT ON SCHEMA ikonomika_src IS 'Икономика, приемна зона: отговорите на Eurostat, ЕЦБ, МФ и БНБ, ред по ред, както са прочетени.';
COMMENT ON SCHEMA ikonomika IS 'Икономика: сериите на показателите и фактите по период, с ключове към core.';

-- ---------------------------------------------------------------- landing zone
CREATE TABLE ikonomika_src.observation (
  load_id   bigint NOT NULL,
  indicator text NOT NULL,
  dims      jsonb NOT NULL,
  geo       text NOT NULL,
  period    text NOT NULL,
  value     numeric,
  flag      text,
  CONSTRAINT observation_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
-- no key over (load_id, indicator, dims, geo, period): the parser refuses a cell twice and pipeline.load() keys the
-- rows of a load, and an index over jsonb would be larger than the table (it was in the old database)
CREATE INDEX observation_load_idx ON ikonomika_src.observation (load_id);
COMMENT ON TABLE ikonomika_src.observation IS 'Едно число от един отговор на източника: показател, измерения без мястото и периода (dims), място (geo), период, както го пише източникът, стойност и флаг. NULL е „няма данни“, никога 0.';

CREATE TABLE ikonomika_src.label (
  load_id   bigint NOT NULL,
  indicator text NOT NULL,
  dim       text NOT NULL,
  code      text NOT NULL,
  label     text NOT NULL,
  CONSTRAINT label_pk PRIMARY KEY (load_id, dim, code),
  CONSTRAINT label_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE ikonomika_src.label IS 'Имената на категориите, както ги дава източникът в същия отговор (Eurostat ги пише на английски, френски и немски).';

CREATE TABLE ikonomika_src.ref_label (
  load_id bigint NOT NULL,
  dim     text NOT NULL,
  code    text NOT NULL,
  label   text NOT NULL,
  CONSTRAINT ref_label_pk PRIMARY KEY (load_id, dim, code),
  CONSTRAINT ref_label_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id)
);
COMMENT ON TABLE ikonomika_src.ref_label IS 'db/ref/labels.csv: българските имена на категориите и валутите, с източника им в README.';

-- ---------------------------------------------------------------- clean tables
-- year 2025, quarter 2025-Q2, half 2025-S1, month 2026-08, day 2026-09-25; anything else is NULL and stops the build
CREATE FUNCTION ikonomika.period_of(p text, OUT period_kind text, OUT period_start date, OUT period_end date)
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT k, s, (s + i)::date - 1
  FROM (SELECT CASE WHEN p ~ '^\d{4}$' THEN 'year' WHEN p ~ '^\d{4}-Q[1-4]$' THEN 'quarter'
                    WHEN p ~ '^\d{4}-S[12]$' THEN 'half' WHEN p ~ '^\d{4}-(0[1-9]|1[0-2])$' THEN 'month'
                    WHEN p ~ '^\d{4}-\d{2}-\d{2}$' THEN 'day' END AS k) a
  CROSS JOIN LATERAL (SELECT CASE k WHEN 'year' THEN make_date(left(p, 4)::int, 1, 1)
                                    WHEN 'quarter' THEN make_date(left(p, 4)::int, 3 * right(p, 1)::int - 2, 1)
                                    WHEN 'half' THEN make_date(left(p, 4)::int, 6 * right(p, 1)::int - 5, 1)
                                    WHEN 'month' THEN (p || '-01')::date
                                    WHEN 'day' THEN p::date END AS s,
                             CASE k WHEN 'year' THEN interval '1 year' WHEN 'quarter' THEN interval '3 months'
                                    WHEN 'half' THEN interval '6 months' WHEN 'month' THEN interval '1 month'
                                    WHEN 'day' THEN interval '1 day' END AS i) b
$$;
COMMENT ON FUNCTION ikonomika.period_of(text) IS 'Видът, началото и краят (включително) на период, записан като в източника: 2025, 2025-Q2, 2025-S1, 2026-08, 2026-09-25.';

CREATE TABLE ikonomika.series (
  series_id      bigint GENERATED ALWAYS AS IDENTITY,
  indicator_code text NOT NULL,
  dims           jsonb NOT NULL,
  geo            text NOT NULL,
  nuts_code      text,
  CONSTRAINT series_pk PRIMARY KEY (series_id),
  CONSTRAINT series_uq UNIQUE (indicator_code, geo, dims),
  CONSTRAINT series_indicator_fk FOREIGN KEY (indicator_code) REFERENCES core.indicator (indicator_code),
  CONSTRAINT series_nuts_fk FOREIGN KEY (nuts_code) REFERENCES core.nuts (nuts_code),
  CONSTRAINT series_nuts_ck CHECK (nuts_code IS NULL OR nuts_code = geo)
);
COMMENT ON TABLE ikonomika.series IS 'Една серия: показател, комбинация от измеренията на източника и място. Числата ѝ по период са в ikonomika.observation.';
COMMENT ON COLUMN ikonomika.series.series_id IS 'Наш пореден номер на серията.';
COMMENT ON COLUMN ikonomika.series.indicator_code IS 'Показателят в core.indicator: ikonomika.<id>, където id е редът в db/ref/indicators.csv или показател на ЕЦБ, МФ или БНБ.';
COMMENT ON COLUMN ikonomika.series.dims IS 'Измеренията на източника без мястото и периода, с кодовете му: {"unit": "RCH_A", "coicop18": "CP01"}. Единицата на числото е тук (unit, currency, indic).';
COMMENT ON COLUMN ikonomika.series.geo IS 'Мястото с кода на източника: NUTS за България и Европа (BG, BG3, BG31, BG311, DE21), държава (DE), съвкупност (EU27_2020, EA).';
COMMENT ON COLUMN ikonomika.series.nuts_code IS 'Мястото в core.nuts, когато е в България и кодът съществува там; иначе NULL. Чуждите места остават само с geo.';

CREATE TABLE ikonomika.observation (
  series_id     bigint NOT NULL,
  period        text NOT NULL,
  period_kind   text NOT NULL,
  period_start  date NOT NULL,
  period_end    date NOT NULL,
  value         numeric,
  flag          text,
  load_id       bigint NOT NULL,
  first_seen_at timestamptz NOT NULL,
  superseded_at timestamptz,
  CONSTRAINT observation_pk PRIMARY KEY (series_id, period, first_seen_at),
  CONSTRAINT observation_series_fk FOREIGN KEY (series_id) REFERENCES ikonomika.series (series_id),
  CONSTRAINT observation_period_kind_fk FOREIGN KEY (period_kind) REFERENCES core.period_kind (period_kind),
  CONSTRAINT observation_load_fk FOREIGN KEY (load_id) REFERENCES ops.load (load_id),
  CONSTRAINT observation_period_ck CHECK (period_start <= period_end)
);
CREATE UNIQUE INDEX observation_current_uq ON ikonomika.observation (series_id, period) WHERE superseded_at IS NULL;
COMMENT ON TABLE ikonomika.observation IS 'Числото на една серия за един период. Поправка от източника не презаписва: старият ред получава superseded_at, новият се добавя.';
COMMENT ON COLUMN ikonomika.observation.series_id IS 'Серията в ikonomika.series.';
COMMENT ON COLUMN ikonomika.observation.period IS 'Периодът, както го пише източникът: 2025, 2025-Q2, 2025-S1, 2026-08, 2026-09-25.';
COMMENT ON COLUMN ikonomika.observation.period_kind IS 'Видът на периода: year, quarter, half, month, day (core.period_kind).';
COMMENT ON COLUMN ikonomika.observation.period_start IS 'Първият ден на периода.';
COMMENT ON COLUMN ikonomika.observation.period_end IS 'Последният ден на периода, включително.';
COMMENT ON COLUMN ikonomika.observation.value IS 'Числото на източника в неговата единица (виж dims на серията), без превръщане. NULL е „няма данни“: източникът има флаг без число.';
COMMENT ON COLUMN ikonomika.observation.flag IS 'Флагът на източника: p предварителни, e оценка, b прекъсване на реда, c поверително, f прогноза (МФ) и други.';
COMMENT ON COLUMN ikonomika.observation.load_id IS 'Зареждането (ops.load), което е донесло това число.';
COMMENT ON COLUMN ikonomika.observation.first_seen_at IS 'Кога е прието зареждането, което е донесло числото.';
COMMENT ON COLUMN ikonomika.observation.superseded_at IS 'Кога източникът го е поправил или махнал. NULL: действащото число.';

CREATE TABLE ikonomika.dim_label (
  indicator_code text NOT NULL,
  dim            text NOT NULL,
  code           text NOT NULL,
  label          text NOT NULL,
  CONSTRAINT dim_label_pk PRIMARY KEY (indicator_code, dim, code),
  CONSTRAINT dim_label_indicator_fk FOREIGN KEY (indicator_code) REFERENCES core.indicator (indicator_code)
);
COMMENT ON TABLE ikonomika.dim_label IS 'Името на категория, както го дава източникът на показателя. Българските имена са в db/ref/labels.csv и имат предимство на страниците.';
COMMENT ON COLUMN ikonomika.dim_label.indicator_code IS 'Показателят в core.indicator.';
COMMENT ON COLUMN ikonomika.dim_label.dim IS 'Измерението на източника (coicop18, na_item, vintage, series, flag).';
COMMENT ON COLUMN ikonomika.dim_label.code IS 'Кодът на категорията.';
COMMENT ON COLUMN ikonomika.dim_label.label IS 'Името на категорията от последния прочетен отговор.';

CREATE TABLE ikonomika.source_state (
  source          text NOT NULL,
  ref             text NOT NULL,
  last_read       timestamptz,
  last_ok         timestamptz,
  last_change     timestamptz,
  last_new_period timestamptz,
  status          text,
  error           text,
  rows            integer,
  updated         text,
  label           text,
  CONSTRAINT source_state_pk PRIMARY KEY (source, ref),
  CONSTRAINT source_state_status_ck CHECK (status IN ('ok', 'held', 'invalid', 'gone', 'error'))
);
COMMENT ON TABLE ikonomika.source_state IS 'Последното четене на всеки показател (и на всеки прозорец на БНБ): кога, с какъв резултат, кога е дошъл нов период. По него страницата „Източници“ и проверката на свежестта казват кое закъснява.';
COMMENT ON COLUMN ikonomika.source_state.source IS 'Източникът: eurostat, ecb, mf, bnb.';
COMMENT ON COLUMN ikonomika.source_state.ref IS 'Показателят, а за БНБ прозорецът (начало и валути) или fx за сверката на целия прочит.';
COMMENT ON COLUMN ikonomika.source_state.last_read IS 'Последното четене, успешно или не.';
COMMENT ON COLUMN ikonomika.source_state.last_ok IS 'Последното успешно четене.';
COMMENT ON COLUMN ikonomika.source_state.last_change IS 'Последното четене, което е сменило числа.';
COMMENT ON COLUMN ikonomika.source_state.last_new_period IS 'Кога е дошъл последният нов период.';
COMMENT ON COLUMN ikonomika.source_state.status IS 'ok, held (задържан до второ четене), invalid (невалиден отговор), gone (липсва при източника), error (грешка при четене).';
COMMENT ON COLUMN ikonomika.source_state.error IS 'Текстът на грешката.';
COMMENT ON COLUMN ikonomika.source_state.rows IS 'Колко реда има в последния приет отговор.';
COMMENT ON COLUMN ikonomika.source_state.updated IS 'Кога източникът казва, че е обновил набора (Eurostat: updated).';
COMMENT ON COLUMN ikonomika.source_state.label IS 'Името на набора при източника.';

-- the exchange rates of the BNB as core.fx_rate wants them: units of the currency for 1 euro on a day.
-- From 2026 the BNB publishes the ECB reference rates (currency for 1 euro): taken as they are (source bnb).
-- Until 2025 it published leva for a number of units and no rate of the euro (the lev was tied to it): from the
-- denomination of 05.07.1999 (core.currency.fixed_from of BGN) they are turned into euro with the fixed rate
-- 1.95583, source bnb-bgn. Before it the rates are in old leva and are not turned (docs/methodology.md 5).
-- A currency printed for two numbers of units on the same day (CZK for 10 and for 100 in 2000-2006) gives the one
-- for more units, which has more digits.
CREATE VIEW ikonomika.fx_rate AS
WITH r AS (
  SELECT DISTINCT ON (substr(s.indicator_code, 11), s.dims->>'code', o.period)
         substr(s.indicator_code, 11) AS ind, s.dims->>'code' AS code, COALESCE((s.dims->>'units')::numeric, 1) AS units,
         o.period::date AS day, o.value
  FROM ikonomika.observation o JOIN ikonomika.series s USING (series_id)
  WHERE s.indicator_code IN ('ikonomika.fx_bgn', 'ikonomika.fx_eur') AND o.superseded_at IS NULL AND o.value > 0
  ORDER BY substr(s.indicator_code, 11), s.dims->>'code', o.period, COALESCE((s.dims->>'units')::numeric, 1) DESC)
SELECT code AS currency_code, day, value AS per_eur, 'bnb'::text AS source
FROM r WHERE ind = 'fx_eur' AND code NOT IN ('EUR', 'BGN')
UNION ALL
SELECT x.code, x.day, round(c.fixed_per_eur * x.units / x.value, 10), 'bnb-bgn'
FROM r x JOIN core.currency c ON c.currency_code = 'BGN'
WHERE x.ind = 'fx_bgn' AND x.code NOT IN ('EUR', 'BGN') AND x.day >= c.fixed_from;
COMMENT ON VIEW ikonomika.fx_rate IS 'Курсовете на БНБ като единици валута за 1 евро: от 2026 г. референтните на ЕЦБ, както ги публикува БНБ (bnb); от деноминацията на 05.07.1999 до 2025 г. курсът в левове, превърнат с фиксирания курс 1,95583 (bnb-bgn). По-старите курсове са в стари левове и не се превръщат. От тук основата ги пренася в core.fx_rate.';

CREATE VIEW ikonomika.fx_currency AS
SELECT c.currency_code, COALESCE(l.label, c.currency_code) AS name_bg
FROM (SELECT DISTINCT currency_code FROM ikonomika.fx_rate) c
LEFT JOIN (SELECT r.code, r.label FROM ikonomika_src.ref_label r JOIN ops.load l ON l.load_id = r.load_id AND l.is_current
           WHERE r.dim = 'code') l ON l.code = c.currency_code;
COMMENT ON VIEW ikonomika.fx_currency IS 'Валутите с курс от БНБ и българското им име от db/ref/labels.csv.';

-- the foundation (role danni) copies the rates into core.fx_rate with core.take_fx_rates(); it reads only these views
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'danni') THEN
    GRANT USAGE ON SCHEMA ikonomika TO danni;
    GRANT SELECT ON ikonomika.fx_rate, ikonomika.fx_currency TO danni;
  END IF;
END $$;

-- The shared operational ledger: every tool writes its runs, files, loads, holds, changes and checks here.
-- ops.schema_migration is created by pipeline.migrate() before the first file, so it is not here.
CREATE TABLE ops.source (
  tool        text NOT NULL,
  source      text NOT NULL,
  title_bg    text NOT NULL,
  url         text,
  channel     text NOT NULL,
  dataset     text,
  cadence     text,
  stale_after interval,
  landing     text NOT NULL DEFAULT 'versions',
  CONSTRAINT source_pk PRIMARY KEY (tool, source),
  CONSTRAINT source_channel_ck CHECK (channel IN ('api', 'file', 'document', 'site-json', 'html', 'derived')),
  CONSTRAINT source_landing_ck CHECK (landing IN ('versions', 'latest', 'transient'))
);
COMMENT ON TABLE ops.source IS 'Източниците на всеки инструмент: канал, ритъм, праг за остаряване и политика на приемната зона.';
COMMENT ON COLUMN ops.source.stale_after IS 'След колко време без успешно четене източникът е остарял. NULL значи, че свежестта не се следи.';

CREATE TABLE ops.run (
  run_id      bigint GENERATED ALWAYS AS IDENTITY,
  tool        text NOT NULL,
  step        text NOT NULL,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status      text NOT NULL DEFAULT 'running',
  code_sha    text,
  params      jsonb NOT NULL DEFAULT '{}',
  stats       jsonb NOT NULL DEFAULT '{}',
  error       text,
  CONSTRAINT run_pk PRIMARY KEY (run_id),
  CONSTRAINT run_status_ck CHECK (status IN ('running', 'ok', 'failed', 'skipped'))
);
CREATE INDEX run_tool_step_started_idx ON ops.run (tool, step, started_at DESC);
COMMENT ON TABLE ops.run IS 'Дневник на пусканията: една стъпка на един инструмент, от начало до край.';

CREATE TABLE ops.raw_file (
  raw_file_id   bigint GENERATED ALWAYS AS IDENTITY,
  sha256        text NOT NULL,
  bytes         bigint NOT NULL,
  path          text NOT NULL,
  media_type    text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT raw_file_pk PRIMARY KEY (raw_file_id),
  CONSTRAINT raw_file_sha256_uq UNIQUE (sha256),
  CONSTRAINT raw_file_sha256_ck CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT raw_file_bytes_ck CHECK (bytes >= 0)
);
COMMENT ON TABLE ops.raw_file IS 'Регистър на суровите файлове. Един ред за едно съдържание (sha256), независимо колко пъти е свалено.';

CREATE TABLE ops.fetch (
  fetch_id    bigint GENERATED ALWAYS AS IDENTITY,
  run_id      bigint,
  tool        text NOT NULL,
  source      text NOT NULL,
  ref         text NOT NULL,
  raw_file_id bigint NOT NULL,
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  http_status integer,
  CONSTRAINT fetch_pk PRIMARY KEY (fetch_id),
  CONSTRAINT fetch_run_fk FOREIGN KEY (run_id) REFERENCES ops.run (run_id),
  CONSTRAINT fetch_source_fk FOREIGN KEY (tool, source) REFERENCES ops.source (tool, source),
  CONSTRAINT fetch_raw_file_fk FOREIGN KEY (raw_file_id) REFERENCES ops.raw_file (raw_file_id)
);
CREATE INDEX fetch_source_ref_idx ON ops.fetch (tool, source, ref, fetched_at DESC);
CREATE INDEX fetch_raw_file_idx ON ops.fetch (raw_file_id);
COMMENT ON TABLE ops.fetch IS 'Всяко сваляне от източник: кой адрес, кога и кой файл е дошъл.';
COMMENT ON COLUMN ops.fetch.ref IS 'Адресът или ключът на ресурса в източника.';

CREATE TABLE ops.load (
  load_id        bigint GENERATED ALWAYS AS IDENTITY,
  run_id         bigint,
  tool           text NOT NULL,
  source         text NOT NULL,
  ref            text NOT NULL,
  raw_file_id    bigint,
  content_sha256 text,
  rows           integer,
  status         text NOT NULL,
  reason         text,
  is_current     boolean NOT NULL DEFAULT false,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT load_pk PRIMARY KEY (load_id),
  CONSTRAINT load_run_fk FOREIGN KEY (run_id) REFERENCES ops.run (run_id),
  CONSTRAINT load_source_fk FOREIGN KEY (tool, source) REFERENCES ops.source (tool, source),
  CONSTRAINT load_raw_file_fk FOREIGN KEY (raw_file_id) REFERENCES ops.raw_file (raw_file_id),
  CONSTRAINT load_status_ck CHECK (status IN ('loaded', 'confirmed', 'unchanged', 'held', 'rejected')),
  CONSTRAINT load_current_ck CHECK (NOT is_current OR status IN ('loaded', 'confirmed')),
  CONSTRAINT load_content_ck CHECK (status = 'rejected' OR (content_sha256 IS NOT NULL AND rows IS NOT NULL))
);
CREATE UNIQUE INDEX load_current_uq ON ops.load (tool, source, ref) WHERE is_current;
CREATE INDEX load_source_ref_idx ON ops.load (tool, source, ref, loaded_at DESC);
COMMENT ON TABLE ops.load IS 'Зареждане (партида): един ресурс, прочетен веднъж. Редовете в приемната зона носят load_id.';
COMMENT ON COLUMN ops.load.content_sha256 IS 'Хеш на прочетените редове, не на файла. Еднакъв хеш значи същото съдържание.';
COMMENT ON COLUMN ops.load.status IS 'loaded: прието; confirmed: прието след задържане; unchanged: същото като действащото; held: задържано; rejected: отхвърлено.';
COMMENT ON COLUMN ops.load.is_current IS 'Действащата версия на ресурса. Най-много една за (tool, source, ref).';

CREATE TABLE ops.hold (
  tool           text NOT NULL,
  source         text NOT NULL,
  ref            text NOT NULL,
  content_sha256 text NOT NULL,
  rows           integer,
  reason         text NOT NULL,
  first_at       timestamptz NOT NULL,
  CONSTRAINT hold_pk PRIMARY KEY (tool, source, ref),
  CONSTRAINT hold_source_fk FOREIGN KEY (tool, source) REFERENCES ops.source (tool, source)
);
COMMENT ON TABLE ops.hold IS 'Задържан отговор: по-малък от действащия. Приема се при второ четене със същия хеш поне ден по-късно.';

CREATE TABLE ops.change_log (
  change_id   bigint GENERATED ALWAYS AS IDENTITY,
  detected_at timestamptz NOT NULL DEFAULT now(),
  tool        text NOT NULL,
  source      text NOT NULL,
  ref         text NOT NULL,
  field       text,
  old         text,
  new         text,
  cause       text NOT NULL,
  run_id      bigint,
  CONSTRAINT change_log_pk PRIMARY KEY (change_id),
  CONSTRAINT change_log_cause_ck CHECK (cause IN ('new-record', 'rewritten', 'removed', 'held', 'confirmed', 'invalid', 'gone', 'erased'))
);
CREATE INDEX change_log_ref_idx ON ops.change_log (tool, source, ref);
CREATE INDEX change_log_detected_idx ON ops.change_log (tool, detected_at);
COMMENT ON TABLE ops.change_log IS 'Всяка разлика между две четения, поле по поле.';

CREATE TABLE ops.check_rule (
  tool      text NOT NULL,
  name      text NOT NULL,
  title_bg  text NOT NULL,
  kind      text NOT NULL,
  sql       text,
  tolerance numeric NOT NULL DEFAULT 0,
  gating    boolean NOT NULL DEFAULT true,
  dataset   text,
  is_active boolean NOT NULL DEFAULT true,
  CONSTRAINT check_rule_pk PRIMARY KEY (tool, name),
  CONSTRAINT check_rule_kind_ck CHECK (kind IN ('reconcile', 'unique', 'not_null', 'fk', 'unmatched', 'drop', 'parity', 'parser', 'licence')),
  CONSTRAINT check_rule_tolerance_ck CHECK (tolerance >= 0)
);
COMMENT ON TABLE ops.check_rule IS 'Правилата за качество като данни. Заявката връща един ред: очаквано и получено.';
COMMENT ON COLUMN ops.check_rule.sql IS 'NULL за правило, което се смята в парсера и се записва с record_check().';
COMMENT ON COLUMN ops.check_rule.gating IS 'Вярно: неуспехът е проблем и спира публикуването.';

CREATE TABLE ops.check_result (
  result_id  bigint GENERATED ALWAYS AS IDENTITY,
  run_id     bigint,
  tool       text NOT NULL,
  name       text NOT NULL,
  checked_at timestamptz NOT NULL DEFAULT now(),
  expected   numeric,
  actual     numeric,
  ok         boolean NOT NULL,
  detail     text,
  CONSTRAINT check_result_pk PRIMARY KEY (result_id),
  CONSTRAINT check_result_run_fk FOREIGN KEY (run_id) REFERENCES ops.run (run_id),
  CONSTRAINT check_result_rule_fk FOREIGN KEY (tool, name) REFERENCES ops.check_rule (tool, name)
);
CREATE INDEX check_result_rule_idx ON ops.check_result (tool, name, checked_at DESC);
COMMENT ON TABLE ops.check_result IS 'Резултатът от всяко изпълнение на правило.';

-- The newest result of every active rule.
CREATE VIEW ops.check_latest AS
SELECT r.tool, r.name, r.title_bg, r.kind, r.gating, r.tolerance, x.checked_at, x.expected, x.actual, x.ok, x.detail
FROM ops.check_rule r
LEFT JOIN LATERAL (SELECT * FROM ops.check_result c WHERE c.tool = r.tool AND c.name = r.name
                   ORDER BY c.checked_at DESC, c.result_id DESC LIMIT 1) x ON true
WHERE r.is_active;
COMMENT ON VIEW ops.check_latest IS 'Последният резултат на всяко действащо правило.';

-- One row per source: the last successful read and whether it is stale.
CREATE VIEW ops.freshness AS
SELECT s.tool, s.source, s.title_bg, s.stale_after, l.last_ok_at, l.last_read_at, l.last_status,
       CASE WHEN s.stale_after IS NULL THEN false
            WHEN l.last_ok_at IS NULL THEN true
            ELSE now() - l.last_ok_at > s.stale_after END AS is_stale
FROM ops.source s
LEFT JOIN LATERAL (
  SELECT max(loaded_at) FILTER (WHERE status IN ('loaded', 'confirmed', 'unchanged')) AS last_ok_at,
         max(loaded_at) AS last_read_at,
         (array_agg(status ORDER BY loaded_at DESC, load_id DESC))[1] AS last_status
  FROM ops.load x WHERE x.tool = s.tool AND x.source = s.source) l ON true;
COMMENT ON VIEW ops.freshness IS 'Свежест по източник: последно успешно четене и дали е минал прагът.';

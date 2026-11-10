"""The shared ingest module of Прозрачност.

The original is tools/danni/pipeline.py. Every tool carries an unchanged copy as ingest/pipeline.py and CI compares
the copies, so a tool still deploys from its own folder. It writes the shared ledger in schema `ops`:

    run()            one step of one tool, under an advisory lock          ops.run
    save_raw()       the answer of the source, stored once by sha256       ops.raw_file, ops.fetch
    load()           one resource into the landing zone, as one batch      ops.load, ops.hold, ops.change_log
    run_checks()     the quality rules, which are rows, not code           ops.check_rule, ops.check_result
    publish()        the serving layer, one transaction with a version     pub.publication, pub.*, pub_<tool>.*
    freshness()      the list of problems, empty when all is well          ops.freshness
    migrate()        ordered SQL files, each applied once                  ops.schema_migration
"""
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import tomllib
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

HOLD = dt.timedelta(days=1)


class ShapeError(ValueError):
    """The answer of the source does not have the expected shape. Nothing is written."""


class Busy(RuntimeError):
    """Another run of the same step holds the lock; reported as skipped."""


class CorruptRaw(RuntimeError):
    """A stored raw file no longer matches its registered size or sha256."""


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def connect(dsn, **kw):
    return psycopg.connect(dsn, autocommit=True, **kw)


def is_test_db(conn):
    return 'test' in conn.execute('SELECT current_database()').fetchone()[0]


def require_test_db(conn):
    """Destructive test helpers call this first."""
    if not is_test_db(conn):
        raise RuntimeError('destructive operation outside a test database')


def _lock_key(*parts):
    return int.from_bytes(hashlib.sha256('/'.join(parts).encode()).digest()[:8], 'big', signed=True)


def code_sha(root):
    """The deployed commit: deploy writes it to <root>/.sha."""
    f = Path(root) / '.sha'
    return f.read_text().strip() if f.exists() else os.environ.get('PZ_CODE_SHA')


# ---------------------------------------------------------------- migrations and registers

def _file_sha(text):
    return hashlib.sha256(text.replace('\r\n', '\n').encode()).hexdigest()


def migrate(conn, tool, root):
    """Apply <root>/db/migrations/*.sql in order, each in its own transaction, then sync the registers.

    A file that was applied and later edited stops the migration: the fix is a new file.
    """
    root = Path(root)
    conn.execute('SELECT pg_advisory_lock(%s)', (_lock_key('pz-migrate'),))
    try:
        if conn.execute("SELECT to_regclass('ops.schema_migration')").fetchone()[0] is None:
            with conn.transaction():
                conn.execute("DO $$ BEGIN IF to_regnamespace('ops') IS NULL THEN CREATE SCHEMA ops; END IF; END $$")
                conn.execute('''CREATE TABLE ops.schema_migration (
                    tool text NOT NULL, name text NOT NULL, sha256 text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now(),
                    CONSTRAINT schema_migration_pk PRIMARY KEY (tool, name))''')
        done = dict(conn.execute('SELECT name, sha256 FROM ops.schema_migration WHERE tool = %s', (tool,)))
        applied = []
        for f in sorted((root / 'db' / 'migrations').glob('*.sql')):
            text = f.read_text(encoding='utf-8')
            sha = _file_sha(text)
            if f.name in done:
                if done[f.name] != sha:
                    raise RuntimeError(f'migration {tool}/{f.name} was edited after it was applied')
                continue
            with conn.transaction():
                conn.execute(text)
                conn.execute('INSERT INTO ops.schema_migration (tool, name, sha256) VALUES (%s, %s, %s)',
                             (tool, f.name, sha))
            applied.append(f.name)
        sync_registry(conn, tool, root)
        return applied
    finally:
        conn.execute('SELECT pg_advisory_unlock(%s)', (_lock_key('pz-migrate'),))


def require_core(conn, prefix):
    """A tool's migration step calls this: the foundation it depends on must be there."""
    if conn.execute("SELECT to_regclass('ops.schema_migration')").fetchone()[0] is None or not conn.execute(
            "SELECT 1 FROM ops.schema_migration WHERE tool = 'danni' AND name LIKE %s", (prefix + '%',)).fetchone():
        raise RuntimeError(f'the foundation migration {prefix} of danni is not applied in this database')


def _toml(path):
    return tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def sync_registry(conn, tool, root):
    """db/datasets.toml (datasets, their tables, sources, indicators) and db/checks.toml into the registers.

    Whether a dataset may be distributed is not set here: only danni sets it, from its licence list.
    """
    root = Path(root)
    reg, checks = _toml(root / 'db' / 'datasets.toml'), _toml(root / 'db' / 'checks.toml')
    with conn.transaction():
        for d in reg.get('dataset', []):
            conn.execute('''INSERT INTO core.dataset (dataset, tool, title_bg, description_bg, source_org, source_url,
                  channel, licence, licence_url, personal_data, cadence)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (dataset) DO UPDATE SET tool=EXCLUDED.tool, title_bg=EXCLUDED.title_bg,
                  description_bg=EXCLUDED.description_bg, source_org=EXCLUDED.source_org, source_url=EXCLUDED.source_url,
                  channel=EXCLUDED.channel, licence=EXCLUDED.licence, licence_url=EXCLUDED.licence_url,
                  personal_data=EXCLUDED.personal_data, cadence=EXCLUDED.cadence''',
                         (d['dataset'], tool, d['title_bg'], d.get('description_bg'), d['source_org'], d.get('source_url'),
                          d['channel'], d['licence'], d.get('licence_url'), d.get('personal_data', 'none'), d.get('cadence')))
            for t in d.get('tables', []):
                schema, table = t.split('.')
                conn.execute('''INSERT INTO core.dataset_table (schema_name, table_name, dataset) VALUES (%s,%s,%s)
                    ON CONFLICT (schema_name, table_name) DO UPDATE SET dataset = EXCLUDED.dataset''',
                             (schema, table, d['dataset']))
        for u in reg.get('unit', []):
            conn.execute('''INSERT INTO core.unit (unit_code, name_bg) VALUES (%s,%s)
                ON CONFLICT (unit_code) DO UPDATE SET name_bg = EXCLUDED.name_bg''', (u['unit_code'], u['name_bg']))
        for i in reg.get('indicator', []):
            conn.execute('''INSERT INTO core.indicator (indicator_code, dataset, title_bg, unit_code, kind,
                  definition_bg, official_code) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (indicator_code) DO UPDATE SET dataset=EXCLUDED.dataset, title_bg=EXCLUDED.title_bg,
                  unit_code=EXCLUDED.unit_code, kind=EXCLUDED.kind, definition_bg=EXCLUDED.definition_bg,
                  official_code=EXCLUDED.official_code''',
                         (i['indicator_code'], i['dataset'], i['title_bg'], i['unit_code'], i['kind'],
                          i.get('definition_bg'), i.get('official_code')))
        for s in reg.get('source', []):
            conn.execute('''INSERT INTO ops.source (tool, source, title_bg, url, channel, dataset, cadence, stale_after, landing)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::interval,%s)
                ON CONFLICT (tool, source) DO UPDATE SET title_bg=EXCLUDED.title_bg, url=EXCLUDED.url,
                  channel=EXCLUDED.channel, dataset=EXCLUDED.dataset, cadence=EXCLUDED.cadence,
                  stale_after=EXCLUDED.stale_after, landing=EXCLUDED.landing''',
                         (tool, s['source'], s['title_bg'], s.get('url'), s['channel'], s.get('dataset'), s.get('cadence'),
                          s.get('stale_after'), s.get('landing', 'versions')))
        names = []
        for c in checks.get('check', []):
            names.append(c['name'])
            conn.execute('''INSERT INTO ops.check_rule (tool, name, title_bg, kind, sql, tolerance, gating, dataset, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true)
                ON CONFLICT (tool, name) DO UPDATE SET title_bg=EXCLUDED.title_bg, kind=EXCLUDED.kind, sql=EXCLUDED.sql,
                  tolerance=EXCLUDED.tolerance, gating=EXCLUDED.gating, dataset=EXCLUDED.dataset, is_active=true''',
                         (tool, c['name'], c['title_bg'], c['kind'], c.get('sql'), c.get('tolerance', 0),
                          c.get('gating', True), c.get('dataset')))
        if 'check' in checks:   # a rule that left the file is switched off, its results stay
            conn.execute('UPDATE ops.check_rule SET is_active = false WHERE tool = %s AND NOT (name = ANY(%s))',
                         (tool, names))


# ---------------------------------------------------------------- runs

@dataclasses.dataclass
class Run:
    conn: psycopg.Connection
    tool: str
    step: str
    run_id: int
    stats: dict = dataclasses.field(default_factory=dict)

    def count(self, key, n=1):
        self.stats[key] = self.stats.get(key, 0) + n


@contextlib.contextmanager
def run(conn, tool, step, params=None, sha=None):
    """One step of one tool. A second run of the same step at the same time is recorded as skipped and raises Busy."""
    key = _lock_key('pz-run', tool, step)
    params = Jsonb(params or {})
    if not conn.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()[0]:
        conn.execute("INSERT INTO ops.run (tool, step, status, finished_at, code_sha, params) VALUES (%s,%s,'skipped',now(),%s,%s)",
                     (tool, step, sha, params))
        raise Busy(f"another '{step}' run of {tool} is going")
    try:
        run_id = conn.execute('INSERT INTO ops.run (tool, step, code_sha, params) VALUES (%s,%s,%s,%s) RETURNING run_id',
                              (tool, step, sha, params)).fetchone()[0]
        r = Run(conn, tool, step, run_id)
        try:
            yield r
        except BaseException as e:
            conn.execute("UPDATE ops.run SET status='failed', finished_at=now(), stats=%s, error=%s WHERE run_id=%s",
                         (Jsonb(json.loads(json.dumps(r.stats, default=str))), repr(e)[:4000], run_id))
            raise
        conn.execute("UPDATE ops.run SET status='ok', finished_at=now(), stats=%s WHERE run_id=%s",
                     (Jsonb(json.loads(json.dumps(r.stats, default=str))), run_id))
    finally:
        conn.execute('SELECT pg_advisory_unlock(%s)', (key,))


# ---------------------------------------------------------------- raw files

@dataclasses.dataclass(frozen=True)
class RawFile:
    raw_file_id: int
    sha256: str
    path: Path


def _register(conn, sha, size, path, media_type):
    row = conn.execute('''INSERT INTO ops.raw_file (sha256, bytes, path, media_type) VALUES (%s,%s,%s,%s)
        ON CONFLICT (sha256) DO NOTHING RETURNING raw_file_id''', (sha, size, str(path), media_type)).fetchone()
    if row:
        return row[0]
    rid, known = conn.execute('SELECT raw_file_id, bytes FROM ops.raw_file WHERE sha256 = %s', (sha,)).fetchone()
    if known != size:
        raise CorruptRaw(f'{sha}: registered with {known} bytes, now {size}')
    return rid


def _fetch(conn, r, source, ref, rid, fetched_at, http_status):
    conn.execute('''INSERT INTO ops.fetch (run_id, tool, source, ref, raw_file_id, fetched_at, http_status)
        VALUES (%s,%s,%s,%s,%s,COALESCE(%s, now()),%s)''', (r.run_id, r.tool, source, ref, rid, fetched_at, http_status))


def save_raw(conn, r, source, ref, raw, raw_dir, name='', media_type=None, http_status=None, fetched_at=None):
    """Store the answer under its sha256 and record the fetch. The same content is stored once."""
    sha = hashlib.sha256(raw).hexdigest()
    safe = ''.join(c if c.isalnum() or c in '.-_' else '_' for c in name)[-80:]
    path = Path(raw_dir) / sha[:2] / (sha + ('-' + safe if safe else ''))
    if path.exists():
        if path.stat().st_size != len(raw) or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise CorruptRaw(f'{path} does not match its name')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.part')
        tmp.write_bytes(raw)
        tmp.replace(path)
    rid = _register(conn, sha, len(raw), path, media_type)
    _fetch(conn, r, source, ref, rid, fetched_at, http_status)
    return RawFile(rid, sha, path)


def register_raw(conn, r, source, ref, path, media_type=None, fetched_at=None, expect_sha=None):
    """Register a file that is already on disk (the archive of Наблюдател, an older raw folder) without copying it."""
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expect_sha and sha != expect_sha:
        raise CorruptRaw(f'{path}: sha256 {sha}, expected {expect_sha}')
    rid = _register(conn, sha, len(raw), path, media_type)
    _fetch(conn, r, source, ref, rid, fetched_at, None)
    return RawFile(rid, sha, path), raw


def read_raw(conn, raw_file_id):
    """The bytes of a registered file, checked against the register before use."""
    sha, size, path = conn.execute('SELECT sha256, bytes, path FROM ops.raw_file WHERE raw_file_id = %s',
                                   (raw_file_id,)).fetchone()
    raw = Path(path).read_bytes()
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
        raise CorruptRaw(f'{path} does not match the register')
    return raw


# ---------------------------------------------------------------- loads

@dataclasses.dataclass(frozen=True)
class Loaded:
    status: str            # loaded | confirmed | unchanged | held
    load_id: int | None    # the load that is current after this call (None: held and nothing was current)
    previous: int | None   # the load it replaced, when status is loaded or confirmed


def content_hash(rows):
    """A hash of the parsed rows, not of the file: a file that differs only in a timestamp is the same content."""
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                                     default=str).encode()).hexdigest()


def log_change(conn, r, source, ref, field, old, new, cause, at=None):
    conn.execute('''INSERT INTO ops.change_log (detected_at, tool, source, ref, field, old, new, cause, run_id)
        VALUES (COALESCE(%s, now()),%s,%s,%s,%s,%s,%s,%s,%s)''',
                 (at, r.tool, source, ref, field, None if old is None else str(old)[:2000],
                  None if new is None else str(new)[:2000], cause, r.run_id))


def diff(conn, r, source, ref, old, new, at=None):
    """Field by field difference of two versions of a resource, keyed rows on both sides. Returns the number logged."""
    n = 0
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        n += 1
        if before is None or after is None:
            log_change(conn, r, source, ref, key, None if before is None else json.dumps(before, ensure_ascii=False, default=str),
                       None if after is None else json.dumps(after, ensure_ascii=False, default=str),
                       'new-record' if before is None else 'removed', at)
        else:
            for field in sorted(before.keys() | after.keys()):
                if before.get(field) != after.get(field):
                    log_change(conn, r, source, ref, f'{key}/{field}', before.get(field), after.get(field), 'rewritten', at)
    return n


def load(conn, r, source, ref, rows, write, *, raw_file_id=None, current=None, now=None, hold=HOLD):
    """Load one resource into the landing zone as one batch, in one transaction.

    rows     {key: row}: the parsed rows of the resource, each under a key that is stable between reads
    write    write(conn, load_id, rows): inserts the rows into the tool's landing tables
    current  current(conn, load_id) -> {key: row} of the version that is current now. With it a read that lacks
             keys of the current version is held and every difference is logged field by field. Without it
             (large resources) a read with fewer rows is held and nothing is logged per field.

    The same content as the current version writes nothing (unchanged). A smaller read replaces the current
    version only when a second read with the same content comes at least `hold` later (confirmed).
    """
    if not rows:
        raise ShapeError('Празен отговор')
    now = now or utcnow()
    sha, n = content_hash(rows), len(rows)

    def ledger(status, reason=None, is_current=False):
        return conn.execute('''INSERT INTO ops.load (run_id, tool, source, ref, raw_file_id, content_sha256, rows, status,
              reason, is_current, loaded_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING load_id''',
                            (r.run_id, r.tool, source, ref, raw_file_id, sha, n, status, reason, is_current, now)).fetchone()[0]

    key = (r.tool, source, ref)
    with conn.transaction():
        cur = conn.execute('''SELECT load_id, content_sha256, rows FROM ops.load
            WHERE tool = %s AND source = %s AND ref = %s AND is_current FOR UPDATE''', key).fetchone()
        if cur and cur[1] == sha:
            ledger('unchanged')
            conn.execute('DELETE FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s', key)
            r.count('unchanged')
            return Loaded('unchanged', cur[0], None)
        old = current(conn, cur[0]) if cur and current else None
        status = 'loaded'
        if cur:
            missing = len(old.keys() - rows.keys()) if old is not None else max(cur[2] - n, 0)
            if missing:
                reason = f'липсват {missing} от {cur[2]} реда'
                held = conn.execute('SELECT content_sha256, first_at FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s',
                                    key).fetchone()
                if not held or held[0] != sha:
                    conn.execute('''INSERT INTO ops.hold (tool, source, ref, content_sha256, rows, reason, first_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tool, source, ref) DO UPDATE SET
                          content_sha256=EXCLUDED.content_sha256, rows=EXCLUDED.rows, reason=EXCLUDED.reason,
                          first_at=EXCLUDED.first_at''', (*key, sha, n, reason, now))
                    log_change(conn, r, source, ref, 'rows', cur[2], n, 'held', now)
                if not held or held[0] != sha or now - held[1] < hold:
                    ledger('held', reason)
                    r.count('held')
                    return Loaded('held', cur[0], None)
                log_change(conn, r, source, ref, 'rows', cur[2], n, 'confirmed', now)
                status = 'confirmed'
            if old is not None:
                diff(conn, r, source, ref, old, rows, now)
            conn.execute('UPDATE ops.load SET is_current = false WHERE load_id = %s', (cur[0],))
        else:
            # the first version of a resource is one line, not one per row: the log records changes
            log_change(conn, r, source, ref, 'rows', None, n, 'new-record', now)
        load_id = ledger(status, is_current=True)
        write(conn, load_id, rows)
        conn.execute('DELETE FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s', key)
    r.count(status)
    return Loaded(status, load_id, cur[0] if cur else None)


def reject(conn, r, source, ref, reason, raw_file_id=None):
    """The answer is not valid (ShapeError and the like): recorded, nothing replaced."""
    conn.execute('''INSERT INTO ops.load (run_id, tool, source, ref, raw_file_id, status, reason)
        VALUES (%s,%s,%s,%s,%s,'rejected',%s)''', (r.run_id, r.tool, source, ref, raw_file_id, str(reason)[:2000]))
    log_change(conn, r, source, ref, None, None, str(reason)[:2000], 'invalid')
    r.count('rejected')


# ---------------------------------------------------------------- checks

def record_check(conn, r, name, expected, actual, detail=None):
    """Record the result of a rule that the code computed (a parser reconciliation). The rule must be registered."""
    rule = conn.execute('SELECT tolerance FROM ops.check_rule WHERE tool = %s AND name = %s', (r.tool, name)).fetchone()
    if not rule:
        raise KeyError(f'check {r.tool}/{name} is not in db/checks.toml')
    ok = expected is not None and actual is not None and abs(actual - expected) <= rule[0]
    conn.execute('''INSERT INTO ops.check_result (run_id, tool, name, expected, actual, ok, detail)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', (r.run_id, r.tool, name, expected, actual, ok, detail))
    return ok


def run_checks(conn, r, kinds=None):
    """Run every active rule of the tool that has SQL. Returns the problems of the gating rules that failed.
    Rules of kind `publish` are left to publish(): they judge the new serving content inside its transaction."""
    problems = []
    rules = conn.execute('''SELECT name, title_bg, sql, tolerance, gating FROM ops.check_rule
        WHERE tool = %s AND is_active AND sql IS NOT NULL AND kind <> 'publish'
          AND (%s::text[] IS NULL OR kind = ANY(%s)) ORDER BY name''',
                         (r.tool, kinds, kinds)).fetchall()
    for name, title, sql, tolerance, gating in rules:
        expected = actual = detail = None
        try:
            with conn.transaction():
                expected, actual = conn.execute(sql).fetchone()
        except Exception as e:   # a rule that cannot run has failed
            detail = repr(e)[:2000]
        ok = expected is not None and actual is not None and abs(actual - expected) <= tolerance
        conn.execute('''INSERT INTO ops.check_result (run_id, tool, name, expected, actual, ok, detail)
            VALUES (%s,%s,%s,%s,%s,%s,%s)''', (r.run_id, r.tool, name, expected, actual, ok, detail))
        r.count('checks_ok' if ok else 'checks_failed')
        if not ok and gating:
            problems.append(f'{title}: очаквано {expected}, получено {actual}' + (f' ({detail})' if detail else ''))
    return problems


# ---------------------------------------------------------------- publication

@dataclasses.dataclass(frozen=True)
class Published:
    publication_id: int | None   # the new version; None when nothing changed or the publication was refused
    changes: dict                # {table: {'added': n, 'removed': n}}
    problems: list               # why it was refused; empty otherwise


class _Refused(Exception):
    pass


def publish(conn, r, root):
    """Bring the serving layer of a tool up to its clean tables, as one transaction with a version number.

    <root>/db/publish/NN_<schema>.<table>.sql each hold one SELECT that yields the rows of a serving table
    (schema pub or pub_<tool>) from the ingest side. The table is brought to exactly those rows: what is not
    in the SELECT is removed, what is new is added, an unchanged row is not touched.

    The transaction commits only when the serving layer breaks none of its guards (core.serving_violation) and
    every rule of kind `publish` of the tool holds against the new content. Otherwise everything rolls back and
    readers keep the version they had. Readers never see a mix: they see the old version or the new one.
    A publication that changes nothing gets no number, so the version moves only when the content does.
    """
    files = sorted((Path(root) / 'db' / 'publish').glob('*.sql'))
    rules = conn.execute('''SELECT name, title_bg, sql, tolerance FROM ops.check_rule
        WHERE tool = %s AND is_active AND kind = 'publish' AND sql IS NOT NULL ORDER BY name''', (r.tool,)).fetchall()
    changes, results, problems, publication_id = {}, [], [], None
    try:
        with conn.transaction():
            # one publication at a time for the whole database: the numbers commit in their own order
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (_lock_key('pz-publish'),))
            conn.execute('SET CONSTRAINTS ALL DEFERRED')
            for f in files:
                target = f.stem.split('_', 1)[1]
                schema, table = target.split('.')
                if schema != 'pub' and not schema.startswith('pub_'):
                    raise RuntimeError(f'{f.name}: {schema} is not a serving schema')
                key = [x[0] for x in conn.execute('''SELECT a.attname FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = %s::regclass AND i.indisprimary''', (target,))]
                if not key:
                    raise RuntimeError(f'{target} has no primary key')
                conn.execute(f'CREATE TEMP TABLE pz_publish (LIKE {target}) ON COMMIT DROP')
                conn.execute(f'INSERT INTO pz_publish {f.read_text(encoding="utf-8")}')
                removed = conn.execute(
                    f'DELETE FROM {target} t USING (SELECT * FROM {target} EXCEPT SELECT * FROM pz_publish) d WHERE '
                    + ' AND '.join(f't.{c} = d.{c}' for c in key)).rowcount
                added = conn.execute(f'INSERT INTO {target} SELECT * FROM pz_publish EXCEPT SELECT * FROM {target}').rowcount
                conn.execute('DROP TABLE pz_publish')
                if removed or added:
                    changes[target] = {'added': added, 'removed': removed}
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')   # a broken link inside the layer fails here, not at commit
            for relation, reason in conn.execute('SELECT relation, reason FROM core.serving_violation ORDER BY 1'):
                problems.append(f'{relation}: {reason}')
            for name, title, sql, tolerance in rules:
                expected, actual = conn.execute(sql).fetchone()
                ok = expected is not None and actual is not None and abs(actual - expected) <= tolerance
                results.append((name, expected, actual, ok))
                if not ok:
                    problems.append(f'{title}: очаквано {expected}, получено {actual}')
            if problems:
                raise _Refused()
            if changes:
                publication_id = conn.execute('''INSERT INTO pub.publication (tool, code_sha, run_id, changes)
                    VALUES (%s, (SELECT code_sha FROM ops.run WHERE run_id = %s), %s, %s) RETURNING publication_id''',
                                              (r.tool, r.run_id, r.run_id, Jsonb(changes))).fetchone()[0]
    except _Refused:
        changes = {}
    except psycopg.Error as e:   # a broken link, a duplicate key, a rule that cannot run: nothing is published
        changes, problems = {}, [f'публикуването е отказано: {str(e).splitlines()[0]}']
    for name, expected, actual, ok in results:   # written outside the transaction, so a refusal leaves its evidence
        conn.execute('''INSERT INTO ops.check_result (run_id, tool, name, expected, actual, ok)
            VALUES (%s,%s,%s,%s,%s,%s)''', (r.run_id, r.tool, name, expected, actual, ok))
    r.stats['publication'] = publication_id
    if problems:
        r.stats['publication_refused'] = True
    return Published(publication_id, changes, problems)


# ---------------------------------------------------------------- freshness and the report

def freshness(conn, tool):
    """The problems of a tool, empty when all is well: stale sources, holds over a day, failed gating checks,
    and steps whose last run failed."""
    problems = []
    for title, last_ok in conn.execute('SELECT title_bg, last_ok_at FROM ops.freshness WHERE tool = %s AND is_stale ORDER BY source', (tool,)):
        problems.append(f'{title}: ' + ('няма успешно четене' if last_ok is None else f'последно успешно четене {last_ok:%d.%m.%Y %H:%M} UTC'))
    for source, ref, reason, first_at in conn.execute('''SELECT source, ref, reason, first_at FROM ops.hold
            WHERE tool = %s AND first_at < now() - interval '1 day' ORDER BY first_at''', (tool,)):
        problems.append(f'{source} {ref}: задържан от {first_at:%d.%m.%Y} ({reason})')
    for title, expected, actual in conn.execute('''SELECT title_bg, expected, actual FROM ops.check_latest
            WHERE tool = %s AND gating AND ok = false ORDER BY name''', (tool,)):
        problems.append(f'{title}: очаквано {expected}, получено {actual}')
    for step, error in conn.execute('''SELECT DISTINCT ON (step) step, CASE WHEN status = 'failed' THEN COALESCE(error, 'грешка') END
            FROM ops.run WHERE tool = %s AND status <> 'skipped' ORDER BY step, run_id DESC''', (tool,)):
        if error:
            problems.append(f'стъпка {step}: {error[:300]}')
    return problems


def report(result):
    """Print the JSON report on the last line and return the exit code: 1 when there are problems.
    The n8n line and the Discord card read exactly this."""
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 1 if result.get('problems') else 0

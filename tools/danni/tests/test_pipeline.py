import datetime as dt
import hashlib

import psycopg
import pytest

import pipeline
from pipeline import ShapeError

# three days back, so that the hold tests can move forward in time without reaching the future
NOW = pipeline.utcnow().replace(microsecond=0) - dt.timedelta(days=3)
ROWS = {'a': {'name': 'А', 'n': 1}, 'b': {'name': 'Б', 'n': 2}, 'c': {'name': 'В', 'n': 3}}


@pytest.fixture
def demo(conn):
    """A tool `demo` with one source and one landing table."""
    conn.execute('CREATE SCHEMA demo_src')
    conn.execute('''CREATE TABLE demo_src.item (load_id bigint NOT NULL REFERENCES ops.load, key text NOT NULL,
        name text, n integer, PRIMARY KEY (load_id, key))''')
    conn.execute("INSERT INTO ops.source (tool, source, title_bg, channel, stale_after) VALUES ('demo','list','Списък','file','2 days')")
    return conn


def write(conn, load_id, rows):
    for k, r in rows.items():
        conn.execute('INSERT INTO demo_src.item VALUES (%s,%s,%s,%s)', (load_id, k, r['name'], r['n']))


def current(conn, load_id):
    return {k: {'name': name, 'n': n} for k, name, n in
            conn.execute('SELECT key, name, n FROM demo_src.item WHERE load_id = %s', (load_id,))}


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def load(conn, rows, now=None, **kw):
    with pipeline.run(conn, 'demo', 'list') as r:
        return pipeline.load(conn, r, 'list', 'x', rows, write, current=current, now=now, **kw)


def test_migrate_runs_once_and_refuses_an_edited_file(conn, tmp_path):
    (tmp_path / 'db' / 'migrations').mkdir(parents=True)
    f = tmp_path / 'db' / 'migrations' / '0001_a.sql'
    f.write_text('CREATE SCHEMA demo; CREATE TABLE demo.t (id int);', encoding='utf-8')
    assert pipeline.migrate(conn, 'demo', tmp_path) == ['0001_a.sql']
    assert pipeline.migrate(conn, 'demo', tmp_path) == []
    f.write_text('CREATE SCHEMA demo; CREATE TABLE demo.t (id bigint);', encoding='utf-8')
    with pytest.raises(RuntimeError, match='edited'):
        pipeline.migrate(conn, 'demo', tmp_path)
    # a failing file leaves nothing behind: its statements and its ledger row roll back together
    (tmp_path / 'db' / 'migrations' / '0001_a.sql').write_text('CREATE SCHEMA demo; CREATE TABLE demo.t (id int);', encoding='utf-8')
    (tmp_path / 'db' / 'migrations' / '0002_b.sql').write_text('CREATE TABLE demo.u (id int); SELECT 1/0;', encoding='utf-8')
    with pytest.raises(psycopg.Error):
        pipeline.migrate(conn, 'demo', tmp_path)
    assert one(conn, "SELECT to_regclass('demo.u')") is None
    assert one(conn, "SELECT count(*) FROM ops.schema_migration WHERE tool = 'demo'") == 1


def test_require_core(conn):
    pipeline.require_core(conn, '0003')
    with pytest.raises(RuntimeError):
        pipeline.require_core(conn, '9999')


def test_run_is_recorded_and_a_second_one_is_skipped(demo, dsn):
    with pipeline.run(demo, 'demo', 'list', params={'x': 1}) as r:
        r.count('rows', 3)
        with pipeline.connect(dsn) as other, pytest.raises(pipeline.Busy):
            with pipeline.run(other, 'demo', 'list'):
                pass
    with pytest.raises(ZeroDivisionError):
        with pipeline.run(demo, 'demo', 'list'):
            1 / 0
    rows = demo.execute("SELECT status, stats, error FROM ops.run WHERE tool = 'demo' ORDER BY run_id").fetchall()
    assert [x[0] for x in rows] == ['ok', 'skipped', 'failed']
    assert {'rows': 3} in [x[1] for x in rows] and any('ZeroDivisionError' in (x[2] or '') for x in rows)
    assert pipeline.freshness(demo, 'demo')[-1].startswith('стъпка list')


def test_raw_is_stored_once_and_a_changed_file_is_refused(demo, tmp_path):
    with pipeline.run(demo, 'demo', 'list') as r:
        a = pipeline.save_raw(demo, r, 'list', 'http://x/1', b'abc', tmp_path, name='a b.txt')
        b = pipeline.save_raw(demo, r, 'list', 'http://x/2', b'abc', tmp_path, name='a b.txt')
        assert a == b and a.sha256 == hashlib.sha256(b'abc').hexdigest() and a.path.read_bytes() == b'abc'
        assert one(demo, 'SELECT count(*) FROM ops.raw_file') == 1 and one(demo, 'SELECT count(*) FROM ops.fetch') == 2
        assert pipeline.read_raw(demo, a.raw_file_id) == b'abc'
        a.path.write_bytes(b'abd')
        with pytest.raises(pipeline.CorruptRaw):
            pipeline.save_raw(demo, r, 'list', 'http://x/1', b'abc', tmp_path, name='a b.txt')
        with pytest.raises(pipeline.CorruptRaw):
            pipeline.read_raw(demo, a.raw_file_id)
        other = tmp_path / 'archive.bin'
        other.write_bytes(b'xyz')
        f, raw = pipeline.register_raw(demo, r, 'list', 'archive', other)
        assert raw == b'xyz' and f.path == other
        with pytest.raises(pipeline.CorruptRaw):
            pipeline.register_raw(demo, r, 'list', 'archive', other, expect_sha='0' * 64)


def test_second_load_of_the_same_content_changes_nothing(demo):
    first = load(demo, ROWS)
    assert first.status == 'loaded' and one(demo, 'SELECT count(*) FROM demo_src.item') == 3
    log = one(demo, 'SELECT count(*) FROM ops.change_log')
    again = load(demo, dict(reversed(ROWS.items())))   # the order of the rows is not content
    assert again.status == 'unchanged' and again.load_id == first.load_id
    assert one(demo, 'SELECT count(*) FROM demo_src.item') == 3
    assert one(demo, 'SELECT count(*) FROM ops.change_log') == log == 1   # the first version is one line
    assert one(demo, "SELECT count(*) FROM ops.load WHERE is_current") == 1


def test_changes_are_logged_field_by_field(demo):
    first = load(demo, ROWS)
    new = {**ROWS, 'b': {'name': 'Б', 'n': 20}, 'd': {'name': 'Г', 'n': 4}}
    second = load(demo, new)
    assert second.status == 'loaded' and second.previous == first.load_id
    log = demo.execute("SELECT field, old, new, cause FROM ops.change_log WHERE cause <> 'new-record' OR field = 'd' ORDER BY change_id").fetchall()
    assert ('b/n', '2', '20', 'rewritten') in log and any(x[0] == 'd' and x[3] == 'new-record' for x in log)
    assert current(demo, second.load_id) == new and current(demo, first.load_id) == ROWS   # both versions are kept


def test_smaller_answer_is_held_until_the_same_comes_a_day_later(demo):
    load(demo, ROWS, NOW)
    small = {k: ROWS[k] for k in 'ab'}
    other = {k: ROWS[k] for k in 'ac'}
    assert load(demo, small, NOW).status == 'held'
    assert one(demo, "SELECT count(*) FROM demo_src.item i JOIN ops.load l USING (load_id) WHERE l.is_current") == 3
    assert load(demo, small, NOW + dt.timedelta(hours=23)).status == 'held'
    assert load(demo, other, NOW + dt.timedelta(days=1)).status == 'held'           # a different answer starts over
    assert load(demo, other, NOW + dt.timedelta(days=1, hours=23)).status == 'held'
    done = load(demo, other, NOW + dt.timedelta(days=2))
    assert done.status == 'confirmed' and current(demo, done.load_id) == other
    causes = [x[0] for x in demo.execute('SELECT cause FROM ops.change_log ORDER BY change_id')]
    assert causes == ['new-record', 'held', 'held', 'confirmed', 'removed']
    assert one(demo, 'SELECT count(*) FROM ops.hold') == 0
    assert pipeline.freshness(demo, 'demo') == []


def test_a_hold_over_a_day_is_a_problem_and_the_full_answer_clears_it(demo):
    load(demo, ROWS)
    load(demo, {'a': ROWS['a']})
    demo.execute("UPDATE ops.hold SET first_at = now() - interval '2 days'")
    assert any('задържан' in p for p in pipeline.freshness(demo, 'demo'))
    assert load(demo, ROWS).status == 'unchanged'
    assert one(demo, 'SELECT count(*) FROM ops.hold') == 0


def test_without_current_a_shorter_answer_is_held_by_count(demo):
    with pipeline.run(demo, 'demo', 'list') as r:
        assert pipeline.load(demo, r, 'list', 'x', ROWS, write, now=NOW).status == 'loaded'
        assert pipeline.load(demo, r, 'list', 'x', {'a': ROWS['a']}, write, now=NOW).status == 'held'
        same_size = {**ROWS, 'c': {'name': 'В', 'n': 30}}
        assert pipeline.load(demo, r, 'list', 'x', same_size, write, now=NOW).status == 'loaded'
        with pytest.raises(ShapeError):
            pipeline.load(demo, r, 'list', 'x', {}, write)


def test_a_failed_write_leaves_the_current_version(demo):
    first = load(demo, ROWS)

    def broken(conn, load_id, rows):
        write(conn, load_id, rows)
        raise RuntimeError('disk')
    with pytest.raises(RuntimeError):
        with pipeline.run(demo, 'demo', 'list') as r:
            pipeline.load(demo, r, 'list', 'x', {**ROWS, 'd': {'name': 'Г', 'n': 4}}, broken, current=current)
    assert one(demo, 'SELECT load_id FROM ops.load WHERE is_current') == first.load_id
    assert one(demo, 'SELECT count(*) FROM demo_src.item') == 3
    assert one(demo, "SELECT count(*) FROM ops.change_log") == 1


def test_reject_is_recorded(demo):
    with pipeline.run(demo, 'demo', 'list') as r:
        pipeline.reject(demo, r, 'list', 'x', ShapeError('HTML вместо данни'))
    assert demo.execute('SELECT status, reason FROM ops.load').fetchall() == [('rejected', 'HTML вместо данни')]
    assert one(demo, 'SELECT cause FROM ops.change_log') == 'invalid'


def test_checks_are_rows_with_a_tolerance(demo):
    demo.execute('''INSERT INTO ops.check_rule (tool, name, title_bg, kind, sql, tolerance, gating) VALUES
        ('demo','exact','Точно','reconcile','SELECT 3, count(*) FROM demo_src.item',0,true),
        ('demo','near','С допуск','reconcile','SELECT 5, count(*) FROM demo_src.item',2,true),
        ('demo','soft','Само бележка','reconcile','SELECT 9, count(*) FROM demo_src.item',0,false),
        ('demo','broken','Счупено правило','reconcile','SELECT 1 FROM no_such_table',0,true),
        ('demo','coded','От кода','parser',NULL,1,true)''')
    load(demo, ROWS)
    with pipeline.run(demo, 'demo', 'check') as r:
        assert pipeline.run_checks(demo, r) == ['Счупено правило: очаквано None, получено None ' + '(' + one(demo, "SELECT detail FROM ops.check_result WHERE name='broken'") + ')']
        assert pipeline.record_check(demo, r, 'coded', 10, 11) is True
        assert pipeline.record_check(demo, r, 'coded', 10, 12) is False
        with pytest.raises(KeyError):
            pipeline.record_check(demo, r, 'missing', 1, 1)
    got = dict(demo.execute('SELECT name, ok FROM ops.check_latest WHERE tool = %s', ('demo',)).fetchall())
    assert got == {'exact': True, 'near': True, 'soft': False, 'broken': False, 'coded': False}
    problems = pipeline.freshness(demo, 'demo')
    assert len(problems) == 2 and all('Счупено' in p or 'От кода' in p for p in problems)   # the soft rule is not a problem
    demo.execute("UPDATE ops.check_rule SET sql = 'SELECT 4, count(*) FROM demo_src.item' WHERE name = 'exact'")
    with pipeline.run(demo, 'demo', 'check') as r:
        assert 'Точно: очаквано 4, получено 3' in pipeline.run_checks(demo, r)


def test_stale_source_is_a_problem(demo):
    assert pipeline.freshness(demo, 'demo') == ['Списък: няма успешно четене']
    load(demo, ROWS)
    assert pipeline.freshness(demo, 'demo') == []
    demo.execute("UPDATE ops.load SET loaded_at = now() - interval '3 days'")
    assert pipeline.freshness(demo, 'demo')[0].startswith('Списък: последно успешно четене')


def test_registry_sync_and_only_the_licence_list_opens_a_dataset(demo, tmp_path):
    (tmp_path / 'db').mkdir()
    (tmp_path / 'db' / 'datasets.toml').write_text('''
[[dataset]]
dataset = "demo.list"
title_bg = "Списък"
source_org = "Източник"
channel = "file"
licence = "CC0"
tables = ["demo_src.item"]
[[source]]
source = "list"
title_bg = "Списък 2"
channel = "api"
dataset = "demo.list"
stale_after = "1 day"
[[indicator]]
indicator_code = "demo.n"
dataset = "demo.list"
title_bg = "Брой"
unit_code = "count"
kind = "count"
''', encoding='utf-8')
    (tmp_path / 'db' / 'checks.toml').write_text('''
[[check]]
name = "a"
title_bg = "А"
kind = "unique"
sql = "SELECT 0, 0"
''', encoding='utf-8')
    demo.execute("INSERT INTO ops.check_rule (tool, name, title_bg, kind) VALUES ('demo','old','Старо','parser')")
    pipeline.sync_registry(demo, 'demo', tmp_path)
    pipeline.sync_registry(demo, 'demo', tmp_path)
    assert demo.execute("SELECT title_bg, channel, stale_after FROM ops.source WHERE tool='demo'").fetchone() == ('Списък 2', 'api', dt.timedelta(days=1))
    assert dict(demo.execute("SELECT name, is_active FROM ops.check_rule WHERE tool='demo'").fetchall()) == {'a': True, 'old': False}
    assert one(demo, "SELECT is_distributable FROM core.dataset WHERE dataset='demo.list'") is False
    assert one(demo, "SELECT dataset FROM core.column_doc WHERE schema_name='demo_src' AND column_name='n'") == 'demo.list'
    # the api guard: a view over a dataset that may not be distributed is a violation, also through another view
    demo.execute('CREATE VIEW core.demo_mid AS SELECT key, n FROM demo_src.item')
    demo.execute('CREATE VIEW api_v1.demo AS SELECT * FROM core.demo_mid')
    assert demo.execute('SELECT api_view, table_name, reason FROM core.api_licence_violation').fetchall() == [
        ('api_v1.demo', 'item', 'наборът няма право на разпространение')]
    with pipeline.run(demo, 'danni', 'check') as r:
        assert len(pipeline.run_checks(demo, r, kinds=['licence'])) == 1
    demo.execute("UPDATE core.dataset SET is_distributable = true, licence_checked_on = '2026-11-10' WHERE dataset='demo.list'")
    assert one(demo, 'SELECT count(*) FROM core.api_licence_violation') == 0
    demo.execute("UPDATE core.dataset SET personal_data = 'names' WHERE dataset='demo.list'")
    assert one(demo, 'SELECT reason FROM core.api_licence_violation') == 'наборът съдържа имена на физически лица'
    demo.execute("DELETE FROM core.dataset_table WHERE table_name = 'item'")
    assert one(demo, 'SELECT reason FROM core.api_licence_violation') == 'таблицата не е записана към набор'
    demo.execute('DROP VIEW api_v1.demo; CREATE TABLE api_v1.leak (x int)')
    assert one(demo, 'SELECT reason FROM core.api_licence_violation') == 'в схемата за API-то има таблица, а не изглед'
    with pytest.raises(psycopg.errors.CheckViolation):   # distributable needs the date of the check
        demo.execute("UPDATE core.dataset SET is_distributable = true, licence_checked_on = NULL WHERE dataset = 'danni.ekatte'")


def test_a_tool_role_cannot_open_a_dataset(conn):
    if not one(conn, "SELECT count(*) FROM pg_roles WHERE rolname = 'pz_ingest'"):
        pytest.skip('the group roles are not in this cluster')
    q = "SELECT has_column_privilege('pz_ingest', 'core.dataset', %s, 'UPDATE')"
    assert one(conn, q, 'title_bg') is True
    assert one(conn, q, 'is_distributable') is False and one(conn, q, 'licence_checked_on') is False
    assert one(conn, "SELECT has_table_privilege('pz_ingest', 'ops.change_log', 'DELETE')") is False
    assert one(conn, "SELECT has_table_privilege('pz_ingest', 'core.org', 'INSERT')") is False
    assert one(conn, "SELECT has_table_privilege('pz_ingest', 'ops.load', 'INSERT')") is True


def test_to_eur_never_guesses(conn):
    q = "SELECT core.to_eur(%s::numeric, %s, %s)"
    assert str(one(conn, q, 195.583, 'BGN', dt.date(2020, 1, 1))) == '100.00'
    assert one(conn, q, 195.583, 'BGN', dt.date(1998, 1, 1)) is None      # before the denomination
    assert str(one(conn, q, 7, 'EUR', dt.date(1990, 1, 1))) == '7.00'
    assert one(conn, q, 10, 'USD', dt.date(2020, 1, 1)) is None            # no rate, no value
    conn.execute("INSERT INTO core.currency (currency_code, name_bg) VALUES ('USD', 'щатски долар')")
    conn.execute("INSERT INTO core.fx_rate VALUES ('USD', '2020-01-01', 1.25, 'test')")
    assert str(one(conn, q, 10, 'USD', dt.date(2020, 1, 1))) == '8.00'
    assert one(conn, q, 10, 'USD', dt.date(2020, 1, 2)) is None
    assert one(conn, q, None, 'EUR', dt.date(2020, 1, 1)) is None


def test_postgres_17_facts_the_design_relies_on(conn):
    """What docs/data/ARCHITECTURE.md states about partitioned tables, checked on the real server."""
    conn.execute('CREATE SCHEMA demo')
    conn.execute('CREATE TABLE demo.day (day date NOT NULL, k int NOT NULL, PRIMARY KEY (day, k)) PARTITION BY RANGE (day)')
    conn.execute("CREATE TABLE demo.day_2026 PARTITION OF demo.day FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')")
    with pytest.raises(psycopg.errors.FeatureNotSupported):   # a key of a partitioned table must hold the partition column
        conn.execute('CREATE TABLE demo.bad (day date NOT NULL, k int PRIMARY KEY) PARTITION BY RANGE (day)')
    # a foreign key may point to a partitioned table, and a partitioned table may point out
    conn.execute('CREATE TABLE demo.child (day date, k int, FOREIGN KEY (day, k) REFERENCES demo.day (day, k))')
    conn.execute('ALTER TABLE demo.day ADD COLUMN m text REFERENCES core.municipality (municipality_code)')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("INSERT INTO demo.child VALUES ('2026-05-05', 1)")
    plan = '\n'.join(x[0] for x in conn.execute("EXPLAIN SELECT * FROM demo.day WHERE day = '2025-05-05'"))
    assert 'day_2026' not in plan   # pruned at plan time

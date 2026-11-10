import copy
import datetime as dt
import hashlib
import tempfile

import pytest

from ingest import pipeline, reference, run, store
from ingest.parse import ShapeError, parse
from .test_sources import FIX, NOW, sample

DETAILED = 'https://www.grao.bg/tna/t41nm-15-09-2026_2.txt'
MONTHLY = 'https://www.grao.bg/tna/t41ob-15-09-2026_1.txt'
T0 = pipeline.utcnow().replace(microsecond=0) - dt.timedelta(days=3)


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def monthly():
    return (FIX / 'grao/t41ob-15-09-2026_1.txt').read_bytes()


def data(conn, raw=None):
    return reference.enrich(parse(raw or sample(), NOW), reference.load(conn))


def put(conn, d, url=DETAILED, now=None):
    with pipeline.run(conn, 'naselenie', 'test') as r:
        return pipeline.load(conn, r, 'grao', url, store.rows(d), store.writer(d, now), current=store.current, now=now)


def test_reference_never_guesses_ambiguous_names(conn):
    d = data(conn)
    assert d['unmatched'] == 25
    assert d['places'][0]['ekatte'] == '02676' and d['places'][0]['code'] == 'BLG01'
    ambiguous = [r for r in d['places'] if r['name'] == 'С.ВЪЛЧОВЦИ' and r['municipality'] == 'ЕЛЕНА']
    assert len(ambiguous) == 2 and all(r['ekatte'] is None for r in ambiguous)
    assert len({m['code'] for m in d['municipalities']}) == 265


def test_unknown_municipality_stops_before_anything_is_written(conn):
    raw = sample().replace('БАНСКО'.encode('cp1251'), 'БАНСКА'.encode('cp1251'))
    with pipeline.run(conn, 'naselenie', 'test') as r, pytest.raises(ShapeError, match='Несъпоставена община'):
        run.load_table(conn, r, reference.load(conn), DETAILED, raw, None, NOW)
    assert one(conn, 'SELECT count(*) FROM naselenie.population') == 0 == one(conn, "SELECT count(*) FROM ops.load WHERE tool = 'naselenie'")


def test_table_lands_and_builds_with_the_numbers_of_the_source(conn):
    with pipeline.run(conn, 'naselenie', 'test') as r:
        done = run.load_table(conn, r, reference.load(conn), DETAILED, sample(), None, NOW)
        assert done.status == 'loaded'
        log = one(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'naselenie'")
        again = run.load_table(conn, r, reference.load(conn), DETAILED, sample(), None, NOW)
        assert again.status == 'unchanged' and again.load_id == done.load_id
    assert one(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'naselenie'") == log == 1
    assert conn.execute('SELECT as_of, table_kind, url FROM naselenie.snapshot').fetchall() == [(dt.date(2026, 9, 15), 'detailed', DETAILED)]
    assert conn.execute('''SELECT count(*), sum(current), count(*) FILTER (WHERE superseded_at IS NULL)
        FROM naselenie.population''').fetchone() == (265, 7393257, 265)
    assert conn.execute('SELECT count(*), count(ekatte) FROM naselenie.settlement_population').fetchone() == (5138, 5113)
    # ГРАО 15.09.2026, the first row of the first table: гр. Банско
    assert conn.execute('''SELECT s.source_name, s.ekatte, s.permanent, s.current, s.both_here, m.name_bg, m.eik
        FROM naselenie.settlement_population s JOIN core.municipality m USING (municipality_code)
        WHERE s.row_no = 0''').fetchone() == ('ГР.БАНСКО', '02676', 9943, 9190, 7745, 'Банско', '000024663')
    assert one(conn, "SELECT current FROM naselenie.population WHERE municipality_code = 'BLG01'") == 12795
    assert conn.execute('''SELECT row_kind, count(*) FROM naselenie_src.grao_row GROUP BY 1 ORDER BY 1''').fetchall()[:2] == [
        ('municipality', 265), ('settlement', 5138)]
    assert store.current(conn, done.load_id) == store.rows(data(conn))   # the landing zone gives the same rows back
    with pipeline.run(conn, 'naselenie', 'check') as r:
        problems = pipeline.run_checks(conn, r)
    assert len(problems) == 1 and problems[0].startswith('Последната месечна таблица')   # the fixture is from September


def test_two_tables_of_one_date_stay_separate(conn):
    put(conn, data(conn))
    put(conn, data(conn, monthly()), MONTHLY)
    assert conn.execute('''SELECT table_kind, current FROM naselenie.population p JOIN core.municipality m USING (municipality_code)
        WHERE m.name_bg = 'Бургас' ORDER BY 1''').fetchall() == [('detailed', 218576), ('monthly', 218590)]
    assert one(conn, 'SELECT count(*) FROM naselenie.population') == 530
    assert one(conn, 'SELECT count(*) FROM naselenie.snapshot') == 2


def test_hold_requires_the_same_answer_a_day_later(conn):
    d = data(conn)
    put(conn, d, now=T0)
    small = copy.deepcopy(d)
    gone = small['places'].pop()
    other = copy.deepcopy(d)
    other['places'].pop(-2)
    assert put(conn, small, now=T0).status == 'held'
    assert put(conn, small, now=T0 + dt.timedelta(hours=23)).status == 'held'
    assert put(conn, other, now=T0 + dt.timedelta(days=1)).status == 'held'
    assert one(conn, 'SELECT count(*) FROM naselenie.settlement_population WHERE superseded_at IS NULL') == 5138
    assert put(conn, other, now=T0 + dt.timedelta(days=2)).status == 'confirmed'
    assert conn.execute('''SELECT count(*), count(*) FILTER (WHERE superseded_at IS NULL) FROM naselenie.settlement_population''').fetchone() == (5138, 5137)
    assert gone['name'] in [x[0] for x in conn.execute('SELECT source_name FROM naselenie.settlement_population WHERE superseded_at IS NULL ORDER BY row_no DESC LIMIT 1')]


def test_a_corrected_number_keeps_the_earlier_one(conn):
    d = data(conn)
    first = put(conn, d, now=T0)
    fixed = copy.deepcopy(d)
    fixed['municipalities'][0]['current'] += 5
    second = put(conn, fixed, now=T0 + dt.timedelta(days=1))
    assert second.status == 'loaded'
    rows = conn.execute('''SELECT current, load_id, superseded_at FROM naselenie.population
        WHERE municipality_code = 'BLG01' ORDER BY population_id''').fetchall()
    assert rows == [(12795, first.load_id, T0 + dt.timedelta(days=1)), (12800, second.load_id, None)]
    assert one(conn, 'SELECT count(*) FROM naselenie.population') == 266   # only the changed row has a second version
    # what we knew on a day: before the correction and after it
    q = '''SELECT current FROM naselenie.population WHERE municipality_code = 'BLG01' AND first_seen_at <= %s
           AND (superseded_at IS NULL OR superseded_at > %s)'''
    assert one(conn, q, T0, T0) == 12795 and one(conn, q, T0 + dt.timedelta(days=2), T0 + dt.timedelta(days=2)) == 12800
    assert conn.execute("SELECT field, old, new, cause FROM ops.change_log WHERE cause = 'rewritten'").fetchall() == [
        ('m/БЛАГОЕВГРАД/БАНСКО/current', '12795', '12800', 'rewritten')]


def test_too_many_unmatched_rows_refuse_a_recent_table(conn):
    conn.execute("UPDATE core.settlement SET gone_at = now() WHERE municipality_code = 'BLG01'")
    with pipeline.run(conn, 'naselenie', 'test') as r:
        with pytest.raises(ShapeError, match='несъпоставени'):
            run.load_table(conn, r, reference.load(conn), DETAILED, sample(), None, NOW)
        # an old table is taken with its names: the current EKATTE cannot judge the past
        assert run.load_table(conn, r, reference.load(conn), DETAILED, sample(), None, NOW + dt.timedelta(days=200)).status == 'loaded'


def test_rebuild_follows_the_places_of_core(conn):
    put_raw(conn, DETAILED, sample())
    conn.execute("UPDATE core.settlement SET gone_at = now() WHERE ekatte = '02676'")
    with pipeline.run(conn, 'naselenie', 'build') as r:
        run.rebuild(conn, r)
    assert conn.execute('''SELECT ekatte, superseded_at IS NULL FROM naselenie.settlement_population
        WHERE source_name = 'ГР.БАНСКО' ORDER BY settlement_population_id''').fetchall() == [('02676', False), (None, True)]


def put_raw(conn, url, raw):
    with pipeline.run(conn, 'naselenie', 'test') as r:
        file = pipeline.save_raw(conn, r, 'grao', url, raw, tempfile.mkdtemp(), name=url.rsplit('/', 1)[-1])
        return run.load_table(conn, r, reference.load(conn), url, raw, file.raw_file_id, NOW)


def fake_get(pages):
    def get(url):
        if url not in pages:
            raise RuntimeError('giving up on ' + url)
        return pages[url]
    return get


def test_refresh_reads_the_catalogue_and_one_bad_table_does_not_stop_the_rest(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'RAW', tmp_path)
    index = f'<html><a href="{DETAILED}">a</a><a href="{MONTHLY}">b</a></html>'.encode('cp1251')
    pages = {run.INDEX: index, DETAILED: sample(), MONTHLY: monthly()}
    with pipeline.run(conn, 'naselenie', 'grao') as r:
        assert run.refresh(conn, r, get=fake_get(pages), today=NOW) == []
        assert r.stats['loaded'] == 3   # the catalogue and two tables
    assert one(conn, 'SELECT count(*) FROM naselenie_src.grao_catalog') == 2
    assert one(conn, 'SELECT count(*) FROM naselenie.snapshot') == 2
    with pipeline.run(conn, 'naselenie', 'check') as r:
        pipeline.run_checks(conn, r)
    problems = pipeline.freshness(conn, 'naselenie')   # the sources were just read; only the age of the fixture is a problem
    assert len(problems) == 1 and problems[0].startswith('Последната месечна таблица')
    pages[MONTHLY] = b'<html>503</html>'
    del pages[DETAILED]
    with pipeline.run(conn, 'naselenie', 'grao') as r:
        problems = run.refresh(conn, r, get=fake_get(pages), today=NOW)
    assert len(problems) == 2 and 'Не е таблица на ГРАО' in problems[1] and 'giving up' in problems[0]
    assert one(conn, 'SELECT count(*) FROM naselenie.population WHERE superseded_at IS NULL') == 530   # what was published stays
    assert sorted(x[0] for x in conn.execute("SELECT status FROM naselenie.source_state")) == ['invalid', 'invalid', 'ok']
    with pipeline.run(conn, 'naselenie', 'grao') as r, pytest.raises(ShapeError):
        run.refresh(conn, r, get=fake_get({run.INDEX: b'<html>error</html>'}), today=NOW)


OLD_SCHEMA = '''
CREATE SCHEMA live; CREATE SCHEMA old_ops;
CREATE TABLE live.source (url text PRIMARY KEY, sha text NOT NULL, as_of date, kind text NOT NULL, payload jsonb NOT NULL,
  published_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE old_ops.raw_file (id bigserial PRIMARY KEY, url text NOT NULL, sha text NOT NULL, path text NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now(), UNIQUE(url, sha));
CREATE TABLE old_ops.change_log (id bigserial PRIMARY KEY, detected_at timestamptz NOT NULL DEFAULT now(), source text NOT NULL,
  ref text NOT NULL, field text, old text, new text, cause text NOT NULL);
'''


def old_payload(conn, raw):
    """The JSON document the old database kept for a table: the parsed rows with the ЕИК of the municipality as code."""
    d = data(conn, raw)
    eik = dict(conn.execute('SELECT municipality_code, eik FROM core.municipality'))
    for r in d['municipalities'] + d['places']:
        r['code'] = eik[r['code']]
    return d


def test_replay_from_raw_equals_the_old_database_and_parity_sees_a_difference(conn, dsn, tmp_path):
    from psycopg.types.json import Jsonb
    conn.execute(OLD_SCHEMA)
    for url, raw in ((DETAILED, sample()), (MONTHLY, monthly())):
        path = tmp_path / url.rsplit('/', 1)[-1]
        path.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        d = old_payload(conn, raw)
        conn.execute("INSERT INTO live.source (url, sha, as_of, kind, payload, published_at) VALUES (%s,%s,%s,%s,%s,'2026-09-29 13:00+00')",
                     (url, sha, d['date'], d['kind'], Jsonb(d)))
        conn.execute('INSERT INTO old_ops.raw_file (url, sha, path) VALUES (%s,%s,%s)', (url, sha, str(path)))
    conn.execute("INSERT INTO old_ops.change_log (source, ref, field, old, new, cause) VALUES ('grao','x','rows','5','4','held'), ('grao','x','k',NULL,'{}','new-record')")
    with pipeline.run(conn, 'naselenie', 'replay') as r:
        assert run.replay(conn, r, old_dsn=dsn, old_ops='old_ops') == [] and r.stats['files'] == 2
        assert run.replay(conn, r, old_dsn=dsn, old_ops='old_ops') == [] and r.stats['unchanged'] == 2   # a second replay writes nothing
    assert one(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'naselenie' AND cause = 'held'") == 1
    assert one(conn, 'SELECT min(first_seen_at) FROM naselenie.population') == dt.datetime(2026, 9, 29, 13, tzinfo=dt.timezone.utc)
    with pipeline.run(conn, 'naselenie', 'parity') as r:
        run.parity(conn, r, old_dsn=dsn)
        assert r.stats['old'] == r.stats['new'] and r.stats['rows_differ'] == 0 == r.stats['ekatte_differ']
        assert r.stats['new']['m_rows'] == 530 and r.stats['new']['s_rows'] == 5138 and r.stats['new']['m_cur'] == 7393257 + sum(x['current'] for x in parse(monthly(), NOW)['municipalities'])
    assert one(conn, "SELECT bool_and(ok) FROM ops.check_latest WHERE tool = 'naselenie' AND kind = 'parity'") is True
    # one number off by one in the new tables: the sums and the row comparison both say so
    conn.execute("UPDATE naselenie.population SET current = current + 1 WHERE municipality_code = 'BLG01' AND table_kind = 'monthly'")
    with pipeline.run(conn, 'naselenie', 'parity') as r:
        run.parity(conn, r, old_dsn=dsn)
    bad = [x[0] for x in conn.execute("SELECT name FROM ops.check_latest WHERE tool = 'naselenie' AND kind = 'parity' AND NOT ok ORDER BY 1")]
    assert bad == ['parity_municipality_current', 'parity_rows_differ']
    # a file of the old database that no longer matches its sha256 is refused
    conn.execute("UPDATE live.source SET sha = repeat('0', 64) WHERE url = %s", (MONTHLY,))
    conn.execute("UPDATE old_ops.raw_file SET sha = repeat('0', 64) WHERE url = %s", (MONTHLY,))
    with pytest.raises(pipeline.CorruptRaw), pipeline.run(conn, 'naselenie', 'replay') as r:
        run.replay(conn, r, old_dsn=dsn, old_ops='old_ops')


def publish(conn):
    with pipeline.run(conn, 'naselenie', 'check') as r:
        return pipeline.publish(conn, r, run.ROOT)


def test_serving_layer_follows_only_through_a_publication(conn):
    put(conn, data(conn), now=T0)
    assert one(conn, 'SELECT count(*) FROM pub_naselenie.population') == 0   # loaded and clean, but not published
    first = publish(conn)
    assert first.problems == [] and first.publication_id is not None
    assert first.changes['pub_naselenie.population'] == {'added': 265, 'removed': 0}
    assert first.changes['pub_naselenie.settlement_population'] == {'added': 5138, 'removed': 0}
    assert conn.execute('''SELECT source_municipality, source_name, ekatte, current FROM pub_naselenie.settlement_population
        WHERE row_no = 0''').fetchone() == ('БАНСКО', 'ГР.БАНСКО', '02676', 9190)
    assert publish(conn) == pipeline.Published(None, {}, [])   # nothing new: the version stays
    # a corrected number: exactly one row leaves the serving layer and one enters it, under a new version
    fixed = copy.deepcopy(data(conn))
    fixed['municipalities'][0]['current'] += 5
    put(conn, fixed, now=T0 + dt.timedelta(days=1))
    assert one(conn, "SELECT current FROM pub_naselenie.population WHERE municipality_code = 'BLG01'") == 12795
    second = publish(conn)
    assert second.publication_id == first.publication_id + 1
    assert second.changes['pub_naselenie.population'] == {'added': 1, 'removed': 1}
    assert 'pub_naselenie.settlement_population' not in second.changes   # an untouched table is not rewritten
    assert one(conn, "SELECT current FROM pub_naselenie.population WHERE municipality_code = 'BLG01'") == 12800
    assert one(conn, 'SELECT count(*) FROM pub_naselenie.population') == 265   # only what is current is published


def test_a_publication_that_breaks_a_rule_changes_nothing(conn):
    put(conn, data(conn), now=T0)
    first = publish(conn)
    conn.execute("UPDATE naselenie.population SET current = current + 1 WHERE municipality_code = 'BLG01'")
    conn.execute("UPDATE ops.check_rule SET sql = 'SELECT 7393257, sum(current) FROM pub_naselenie.population' WHERE tool = 'naselenie' AND name = 'published_population_current'")
    refused = publish(conn)
    assert refused.publication_id is None and refused.changes == {}
    assert refused.problems == ['Публикуваният сбор по настоящ адрес е сборът в чистата таблица: очаквано 7393257, получено 7393258']
    assert one(conn, "SELECT current FROM pub_naselenie.population WHERE municipality_code = 'BLG01'") == 12795
    assert one(conn, "SELECT max(publication_id) FROM pub.publication WHERE tool = 'naselenie'") == first.publication_id
    assert conn.execute("SELECT ok FROM ops.check_latest WHERE tool = 'naselenie' AND name = 'published_population_current'").fetchone() == (False,)
    # a row that points to a settlement the serving layer does not have: the publication is refused whole
    conn.execute("UPDATE ops.check_rule SET is_active = false WHERE tool = 'naselenie' AND name = 'published_population_current'")
    conn.execute("INSERT INTO core.settlement (ekatte, kind_prefix, name_bg, municipality_code) VALUES ('99999', 'с.', 'Непубликувано', 'BLG01')")
    conn.execute("UPDATE naselenie.settlement_population SET ekatte = '99999' WHERE row_no = 0")
    try:
        broken = publish(conn)
        assert broken.publication_id is None and 'публикуването е отказано' in broken.problems[0]
        assert one(conn, 'SELECT ekatte FROM pub_naselenie.settlement_population WHERE row_no = 0') == '02676'
        assert one(conn, "SELECT current FROM pub_naselenie.population WHERE municipality_code = 'BLG01'") == 12795
    finally:
        conn.execute("UPDATE naselenie.settlement_population SET ekatte = '02676' WHERE row_no = 0")
        conn.execute("DELETE FROM core.settlement WHERE ekatte = '99999'")


def test_serving_layer_stands_alone(conn):
    """Nothing in the serving layer of this tool points outside the serving layer, and all of it may be shown."""
    put(conn, data(conn))
    assert publish(conn).problems == []
    assert one(conn, 'SELECT count(*) FROM core.serving_violation') == 0
    outside = conn.execute('''SELECT k.conrelid::regclass::text, k.confrelid::regclass::text FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class f ON f.oid = k.confrelid JOIN pg_namespace fn ON fn.oid = f.relnamespace
        WHERE k.contype = 'f' AND n.nspname = 'pub_naselenie' AND fn.nspname NOT IN ('pub', 'pub_naselenie')''').fetchall()
    assert outside == []
    assert one(conn, "SELECT count(*) FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE k.contype = 'f' AND n.nspname = 'pub_naselenie'") == 4

"""Бюджет in the shared database: a resource through pipeline.load() into the landing zone and the clean tables, the
history of a corrected table, the hold, the publication, the pages read from the serving layer, and the move from the
old database (replay and parity)."""
import datetime as dt
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from ingest import pipeline, run, store
from ingest.parse import ShapeError, parse, table

F = Path(__file__).parent / 'fixtures'
MANIFEST = json.loads((F / 'egov/manifest.json').read_text(encoding='utf-8'))
EGOV = 'https://data.egov.bg/data/resourceView/'


def meta(name, version='1'):
    m = MANIFEST[name]
    return dict(uri=m['ref'], name=m['name'], version=version, updated_at='2026-09-30 17:14:06')


def load(conn, r, name, raw=None, version='1', now=None):
    raw = raw if raw is not None else (F / 'egov' / name).read_bytes()
    file = pipeline.save_raw(conn, r, MANIFEST[name]['kind'], MANIFEST[name]['ref'], raw, run.RAW, name=name)
    return run.load_resource(conn, r, MANIFEST[name]['kind'], meta(name, version), raw, file.raw_file_id, now=now).status


def current(conn, table, where='true'):
    return conn.execute(f'SELECT count(*) FROM byudzhet.{table} WHERE superseded_at IS NULL AND {where}').fetchone()[0]


def test_load_unchanged_corrected_and_invalid(conn):
    with pipeline.run(conn, 'byudzhet', 'test') as r:
        assert load(conn, r, 'debt-latest.json') == 'loaded'
        assert current(conn, 'municipal_value') == 265 and current(conn, 'report') == 1
        assert conn.execute('SELECT count(*) FROM byudzhet_src.egov_row').fetchone()[0] == len(table((F / 'egov/debt-latest.json').read_bytes()))
        assert load(conn, r, 'debt-latest.json', version='2') == 'unchanged'
        assert conn.execute('SELECT count(*) FROM byudzhet.municipal_value').fetchone()[0] == 265
        # МФ corrects one municipality: the period gets a new version, the old rows stay with superseded_at
        x = json.loads((F / 'egov/debt-latest.json').read_bytes())
        x['data'][1][2] = '216000'
        assert load(conn, r, 'debt-latest.json', json.dumps(x).encode(), '3') == 'loaded'
        assert current(conn, 'municipal_value') == 265
        assert conn.execute('SELECT count(*) FROM byudzhet.municipal_value').fetchone()[0] == 530
        assert conn.execute("SELECT original FROM byudzhet.municipal_value WHERE superseded_at IS NULL AND municipality_code = "
                            "(SELECT municipality_code FROM core.municipality WHERE eik = '000024663')").fetchone()[0] == Decimal('216000')
        assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = 'byudzhet' AND cause = 'rewritten'").fetchone()[0] == 1
        with pytest.raises(ShapeError):
            load(conn, r, 'debt-latest.json', b'<html>denied', '4')
        assert current(conn, 'municipal_value', "original = 216000") == 1


def test_amounts_are_unfolded_rounded_and_in_euro(conn):
    with pipeline.run(conn, 'byudzhet', 'test') as r:
        load(conn, r, 'state-historical.json')   # 2021, in millions of leva
        load(conn, r, 'debt-latest.json')        # 2026, in euro
    lo, amount, eur, unit = conn.execute('''SELECT actual_original, actual_amount, actual_eur, source_unit FROM byudzhet.state_line
        WHERE row_key = 'income' AND superseded_at IS NULL''').fetchone()
    assert unit == 'млн. BGN' and amount == round(lo * 1000000, 2) and eur == round(amount / Decimal('1.95583'), 2)
    assert conn.execute("SELECT DISTINCT currency FROM byudzhet.municipal_value").fetchone()[0] == 'EUR'
    with pipeline.run(conn, 'byudzhet', 'check') as r:
        assert pipeline.run_checks(conn, r) == []


def test_a_shorter_or_emptier_read_waits_a_day(conn):
    now = dt.datetime.now(dt.timezone.utc)
    raw = (F / 'egov/state-latest.json').read_bytes()
    x = json.loads(raw)
    x['data'][4][1:] = ['', '', '']
    new = json.dumps(x).encode()
    with pipeline.run(conn, 'byudzhet', 'test') as r:
        load(conn, r, 'state-latest.json', raw, now=now)
        assert load(conn, r, 'state-latest.json', new, '2', now=now) == 'held'
        assert load(conn, r, 'state-latest.json', new, '2', now=now + dt.timedelta(hours=23)) == 'held'
        assert load(conn, r, 'state-latest.json', new, '2', now=now + dt.timedelta(days=1)) == 'confirmed'
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = 'byudzhet' AND cause = 'confirmed'").fetchone()[0] == 1


def population(conn):
    """The registrations of 15.09.2026 as Население publishes them (pub_naselenie), for the denominator."""
    d = json.loads((F / 'population.json').read_text(encoding='utf-8'))
    d['period'] = '2026-06-15'   # a date before the quarter of the fixtures, so the quarter has a denominator
    conn.execute("INSERT INTO pub_naselenie.snapshot VALUES (%s, 'monthly', 'https://www.grao.bg/tna/t41ob-15-09-2026_2.txt')", (d['period'],))
    for x in d['rows']:
        conn.execute('''INSERT INTO pub_naselenie.population SELECT %s, 'monthly', municipality_code, %s, %s, 0
            FROM pub.municipality WHERE eik = %s''', (d['period'], x['permanent'], x['current'], x['code']))
    return d


def publish_all(conn):
    with pipeline.run(conn, 'byudzhet', 'test') as r:
        for name in MANIFEST:
            if 'invalid' not in name:
                load(conn, r, name)
        assert pipeline.run_checks(conn, r) == []
        published = pipeline.publish(conn, r, run.ROOT)
    assert published.problems == [] and published.publication_id
    return published


@pytest.fixture
def site(conn, dsn, monkeypatch):
    from app import queries
    from app.main import app
    monkeypatch.setattr(queries, 'DSN', dsn)
    queries._cache.clear()
    return TestClient(app)


def test_pages_read_the_serving_layer_with_its_version(conn, site):
    pop = population(conn)
    published = publish_all(conn)
    assert conn.execute('SELECT count(*) FROM pub_byudzhet.municipal_value').fetchone()[0] == current(conn, 'municipal_value')
    r = site.get('/api/obshtini.json?y=2026-06-30')
    assert r.status_code == 200 and r.headers['X-Publication'] == str(published.publication_id)
    rows = {x['code']: x for x in r.json()['rows']}
    want = parse((F / 'egov/debt-latest.json').read_bytes(), 'debt', '2026-06-30')
    first = want['rows'][0]
    assert rows[first['code']]['debt'] == float(first['values']['debt']['eur'])
    perm = {x['code']: x['current'] for x in pop['rows']}
    assert rows[first['code']]['population'] == perm[first['code']]
    for url in ('/', '/karta', '/darzhaven', '/kfp', '/obshtini', '/obshtini/000024663', '/sources', '/how', '/healthz'):
        page = site.get(url)
        assert page.status_code == 200, url
        assert page.headers['X-Publication'] == str(published.publication_id)
    assert 'data.egov.bg/data/resourceView/' in site.get('/sources').text
    # a publication that changes nothing gets no number; the pages keep the one they show
    with pipeline.run(conn, 'byudzhet', 'check') as r:
        assert pipeline.publish(conn, r, run.ROOT).publication_id is None


def test_the_pages_read_nothing_but_the_serving_layer():
    code = (run.ROOT / 'app' / 'queries.py').read_text(encoding='utf-8') + (run.ROOT / 'app' / 'main.py').read_text(encoding='utf-8')
    schemas = set(re.findall(r'\b(?:FROM|JOIN)\s+([a-z_]+)\.', code))
    assert schemas <= {'pub', 'pub_byudzhet', 'pub_naselenie'}, schemas
    assert "load_reference(c,'pub')" in code


def old_database(conn, tmp_path):
    """The old database of Бюджет in schemas old_src, old_ops and live of the test database, as its store wrote it:
    the raw files under their sha256, the resources and the documents."""
    conn.execute('CREATE SCHEMA old_src; CREATE SCHEMA old_ops; CREATE SCHEMA live')
    conn.execute('''CREATE TABLE old_src.resource (ref text PRIMARY KEY, kind text, name text, period date, version text, sha256 text,
        read_at timestamptz, status text, error text, metadata jsonb)''')
    conn.execute('CREATE TABLE old_ops.raw_file (source text, ref text, path text, sha256 text, fetched_at timestamptz, bytes bigint)')
    conn.execute('CREATE TABLE old_ops.change_log (id bigserial, detected_at timestamptz, source text, ref text, field text, old text, new text, cause text)')
    conn.execute('CREATE TABLE live.snapshot (ref text PRIMARY KEY, kind text, period date, payload jsonb)')
    # the KFP table of November 2025 whose balance does not hold in the source (db/ref/source_gaps.json)
    files = {**MANIFEST, 'kfp-invalid-balance.json': dict(kind='kfp', name='2025-11-30', ref='f4fca4e3-a1e4-4f83-be11-776286dd8666')}
    for name, m in files.items():
        raw = (F / 'egov' / name).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        path = tmp_path / sha
        path.write_bytes(raw)
        conn.execute("INSERT INTO old_ops.raw_file VALUES (%s, %s, %s, %s, now() - interval '3 days', %s)", (m['kind'], m['ref'], str(path), sha, len(raw)))
        try:
            data = parse(raw, m['kind'], m['name'])
        except ShapeError as e:
            conn.execute("INSERT INTO old_src.resource VALUES (%s, %s, %s, NULL, '1', %s, now(), 'невалиден отговор', %s, %s)",
                         (m['ref'], m['kind'], m['name'], sha, str(e), Jsonb(dict(uri=m['ref'], name=m['name'], version='1'))))
            continue
        data.update(url=EGOV + m['ref'], resource=m['ref'], version='1')
        for x in data['rows']:
            x.pop('municipality_code', None)   # the old parser did not know the code by НСИ
        conn.execute("INSERT INTO old_src.resource VALUES (%s, %s, %s, %s, '1', %s, now(), 'наред', NULL, %s)",
                     (m['ref'], m['kind'], m['name'], data['period'], sha, Jsonb(dict(uri=m['ref'], name=m['name'], version='1'))))
        conn.execute('INSERT INTO live.snapshot VALUES (%s, %s, %s, %s)', (m['ref'], m['kind'], data['period'], Jsonb(data)))
    conn.execute("INSERT INTO old_ops.change_log (detected_at, source, ref, field, old, new, cause) VALUES (now(), 'debt', 'x', NULL, NULL, 'bad', 'invalid')")


def test_replay_and_parity_against_the_old_database(conn, dsn, tmp_path):
    from ingest import parse as p
    old_database(conn, tmp_path)
    p.use(p.REF)
    with pipeline.run(conn, 'byudzhet', 'replay') as r:
        problems = run.replay(conn, r, dsn, old=('old_src', 'old_ops'))
        assert pipeline.run_checks(conn, r) == []
        assert pipeline.publish(conn, r, run.ROOT).problems == []
    # the invalid KFP table of November 2025 is a known gap of the source: refused, not a problem
    assert problems == []
    assert conn.execute("SELECT count(*) FROM ops.load WHERE tool = 'byudzhet' AND status = 'rejected'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = 'byudzhet' AND cause = 'invalid' AND ref = 'x'").fetchone()[0] == 1
    with pipeline.run(conn, 'byudzhet', 'parity') as r:
        run.parity(conn, r, dsn, old_live='live')
    got = dict(conn.execute("SELECT name, actual FROM ops.check_latest WHERE tool = 'byudzhet' AND kind = 'parity'").fetchall())
    want = dict(conn.execute("SELECT name, expected FROM ops.check_latest WHERE tool = 'byudzhet' AND kind = 'parity'").fetchall())
    detail = conn.execute("SELECT detail FROM ops.check_latest WHERE tool = 'byudzhet' AND name = 'parity_rows_differ'").fetchone()[0]
    assert got['parity_rows_differ'] == 0, detail
    for k in ('parity_reports', 'parity_rows', 'parity_original_sum', 'parity_eur_sum'):
        assert got[k] == want[k] and got[k] > 0, k
    # one number changed in the shared database is one report that differs
    conn.execute('UPDATE pub_byudzhet.reserve_line SET original = original + 1 WHERE row_no = 0 AND as_of = (SELECT max(as_of) FROM pub_byudzhet.reserve_line)')
    with pipeline.run(conn, 'byudzhet', 'parity') as r:
        run.parity(conn, r, dsn, old_live='live')
    assert conn.execute("SELECT actual FROM ops.check_latest WHERE tool = 'byudzhet' AND name = 'parity_rows_differ'").fetchone()[0] == 1

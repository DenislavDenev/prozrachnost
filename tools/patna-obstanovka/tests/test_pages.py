"""The map and its API as rendered answers: they read only the serving layer, say their version, and answer what the
SQLite version answered for the same data."""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, queries
from ingest import run

ROOT = Path(__file__).resolve().parent.parent


def test_pages_read_only_the_serving_layer():
    """The web role has no right to anything else; the source of the queries says so too."""
    text = (ROOT / 'app' / 'queries.py').read_text(encoding='utf-8')
    schemas = set(re.findall(r'\b(pub_patna_obstanovka|patna_obstanovka_src|patna_obstanovka|ops|core|pub)\.', text.replace('{P}', 'pub_patna_obstanovka')))
    assert schemas <= {'pub_patna_obstanovka', 'pub'} and 'pub_patna_obstanovka' in schemas
    assert 'import psycopg' not in (ROOT / 'app' / 'main.py').read_text(encoding='utf-8')


def test_feedback_last_in_header_and_support_last_in_footer():
    html = main.home()
    assert '{{' not in html
    assert html.split('</header>')[0].endswith('</script></div>')
    assert html.split('</footer>')[0].endswith('href="http://localhost:8001/podkrepi">Подкрепи проекта</a>')


def test_road_conditions_sum_as_sqlite_does():
    """The lengths are summed as SQLite 3.43+ sums real numbers, so the totals of the map stay what they were."""
    values = [1e16, 1.0, -1e16, 0.1, 0.2, 0.3, 12.345, 7.0005]
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE t(x REAL)')
    db.executemany('INSERT INTO t VALUES(?)', [(v,) for v in values])
    if sqlite3.sqlite_version_info < (3, 43):
        pytest.skip('SQLite before 3.43 sums plainly')
    assert main.sqlite_sum(values) == db.execute('SELECT sum(x) FROM t').fetchone()[0]
    assert main.sqlite_sum(values) != sum(values)


@pytest.fixture
def client(conn, dsn, monkeypatch):
    monkeypatch.setattr(queries, 'DSN', dsn)
    main._risk.clear()
    return TestClient(main.app)


def test_answers_after_the_copy_are_the_answers_of_road_db(client, conn, road_db):
    assert client.get('/healthz').json() == {'status': 'ok', 'sources': 0}
    assert client.get('/api/status').json() == []          # nothing read yet: nothing shown, not zeros
    run.perform(conn, 'copy', old_db=road_db)
    db = sqlite3.connect(road_db)
    db.row_factory = sqlite3.Row

    def old(sql, *args):
        return [dict(r) for r in db.execute(sql, args)]
    for url, sql in (('/api/status', 'SELECT * FROM sources ORDER BY key'), ('/api/traffic', 'SELECT * FROM traffic'),
                     ('/api/weather', 'SELECT * FROM weather'), ('/api/events', 'SELECT * FROM events')):
        r = client.get(url)
        assert r.content == json.dumps(old(sql), ensure_ascii=False, separators=(',', ':')).encode(), url
        assert int(r.headers['X-Publication']) >= 1
    assert client.get('/healthz').json() == {'status': 'ok', 'sources': 5}
    features = client.get('/api/risk/all').json()['features']
    assert [f['properties']['id'] for f in features] == ['5:1', '0:7']
    assert features[0]['geometry']['coordinates'] == [[[23.5, 42.4], [23.5, 42.6]]]
    points = client.get('/api/crashes/points?zoom=14&period=all&south=42.4&north=42.6&west=23.4&east=23.6').json()
    assert points == old('SELECT * FROM crashes ORDER BY date DESC, lat, lon, rowid')
    summary = client.get('/api/crashes/summary?zoom=7&period=all&south=42.4&north=42.6&west=23.4&east=23.6').json()
    assert summary['total'] == 3 and sum(f['properties']['fatal'] for f in summary['features']) == 1
    sections = client.get('/api/dangerous-sections?period=all').json()
    assert sections['matching'] == {'matched': 1, 'total': 3, 'percent': 33.3}
    assert sections['features'][0]['properties'] == {'id': '5:1', 'road': 'I-1', 'risk_level': 'HIGH', 'condition': 'GOOD',
                                                     'reference': '2023', 'length_km': 10.0, 'crashes': 1, 'fatal': 1,
                                                     'injury': 0, 'killed': 1, 'injured': 2}
    assert client.get('/api/road-conditions').json() == {'reference': '2023', 'total_km': 10.0,
                                                          'conditions': [{'condition': 'GOOD', 'km': 10.0, 'sections': 1}]}
    assert client.get('/api/crashes?since=2026-09-27').json() == old("SELECT * FROM crashes WHERE date >= '2026-09-27'")
    assert client.get('/api/crashes/summary?period=7').status_code == 422

import pytest
from fastapi.testclient import TestClient

from app import main, queries
from .test_sources import sample
from .test_store import DETAILED, MONTHLY, monthly, publish, put_raw


@pytest.fixture
def client(conn, dsn, monkeypatch):
    put_raw(conn, DETAILED, sample())
    put_raw(conn, MONTHLY, monthly())
    assert publish(conn).problems == []
    monkeypatch.setattr(queries, 'DSN', dsn)
    return TestClient(main.app)


def code_of(name):
    with queries.snapshot() as c:
        return next(k for k, v in queries.reference(c)[0].items() if v['name_bg'] == name)


def test_pages_and_shared_header(client):
    for url in ['/', '/karta', '/obshtini', '/obshtini/' + code_of('Банско'), '/sources', '/how']:
        r = client.get(url)
        assert r.status_code == 200, url
        assert 'Обратна връзка' in r.text and 'Подкрепи проекта' in r.text
        assert '—' not in r.text
    assert client.get('/obshtini/000000000').status_code == 404
    assert client.get('/healthz').json()['ok'] is True


def test_every_answer_names_its_publication_and_changes_only_with_one(client, conn):
    first = client.get('/api/karta.json')
    version = int(first.headers['X-Publication'])
    assert version == conn.execute("SELECT max(publication_id) FROM pub.publication").fetchone()[0]
    assert first.headers['ETag'] == f'W/"pub-{version}"' and first.headers['Last-Modified'].endswith('GMT')
    for url in ['/', '/karta', '/obshtini', '/export.csv', '/history.csv', '/api/series.json', '/sources', '/healthz']:
        assert client.get(url).headers['X-Publication'] == str(version), url
    assert 'X-Publication' not in client.get('/static/app.css').headers   # a static file is not of any publication
    # new data is loaded and clean, but the pages do not move until it is published
    conn.execute("UPDATE naselenie.population SET current = current + 1000 WHERE table_kind = 'monthly'")
    again = client.get('/api/karta.json')
    assert again.headers['X-Publication'] == str(version) and again.json() == first.json()
    assert publish(conn).publication_id == version + 1
    after = client.get('/api/karta.json')
    assert after.headers['X-Publication'] == str(version + 1) and after.json()['bg'] == first.json()['bg'] + 265000


def test_pages_read_only_the_serving_layer():
    """The source of the queries names no schema but the serving ones: the web role has no right to any other."""
    import re
    from pathlib import Path
    text = (Path(queries.__file__).parent / 'queries.py').read_text(encoding='utf-8')
    schemas = set(re.findall(r'(?:FROM|JOIN)\s+\(?([a-z_0-9]+)\.', text))
    assert schemas == {'pub', 'pub_naselenie'}
    assert 'execute(' not in (Path(queries.__file__).parent / 'main.py').read_text(encoding='utf-8')   # every query is in queries.py


def test_map_levels_reconcile_and_dates(client):
    totals = []
    for level, n in (('obshtini', 265), ('oblasti', 28), ('rayoni', 6), ('makrorayoni', 2)):
        d = client.get('/api/karta.json', params={'l': level}).json()
        assert len(d['items']) == n and all(r['v'] is not None for r in d['items'])
        totals.append(sum(r['v'] for r in d['items']))
    assert len(set(totals)) == 1 and totals[0] == d['bg']
    d = client.get('/api/karta.json', params={'l': 'oblasti'}).json()
    assert {'София област', 'София (столица)'} <= {r['name'] for r in d['items']}   # the two Sofias keep their names apart
    assert client.get('/api/karta.json?y=1900-01-01').status_code == 404
    assert client.get('/api/karta.json?m=other').status_code == 400


def test_burgas_difference_visible(client):
    code = code_of('Бургас')
    r = client.get('/obshtini/' + code)
    assert '14 регистрации' in r.text and 'по-малко' in r.text
    assert '218 590' in r.text
    assert client.get('/export.csv?code=' + code).text.count('\n') > 5
    s = client.get(f'/api/obshtini/{code}.json').json()
    assert s['series'][1]['points'] == [['2026-09-15', 218590]] and s['sources'][0]['kind'] == 'municipalities'


def test_csv_sources_and_no_data(client, conn):
    for url in ['/export.csv', '/history.csv', '/sources.csv', '/unmatched.csv']:
        assert client.get(url).status_code == 200
    assert client.get('/unmatched.csv').text.count('\n') == 26   # the header and the 25 rows without an unambiguous code
    assert 'ЕЛЕНА,С.ВЪЛЧОВЦИ' in client.get('/unmatched.csv').text
    labels = {r['label'] for r in client.get('/sources.json').json()}
    assert labels == {'ГРАО', 'ЕКАТТЕ'}
    conn.execute('TRUNCATE pub_naselenie.snapshot, pub_naselenie.population, pub_naselenie.settlement_population')   # an empty serving layer
    assert client.get('/').status_code == 200
    d = client.get('/api/karta.json').json()
    assert d['bg'] is None and len(d['items']) == 265 and all(r['v'] is None for r in d['items'])   # no data is not zero
    assert client.get('/api/series.json').json()['series'][0]['points'] == []

import pytest
from fastapi.testclient import TestClient

from app import main, queries
from .test_sources import sample
from .test_store import DETAILED, MONTHLY, monthly, put_raw


@pytest.fixture
def client(conn, dsn, monkeypatch):
    put_raw(conn, DETAILED, sample())
    put_raw(conn, MONTHLY, monthly())
    monkeypatch.setattr(queries, 'DSN', dsn)
    queries._cache.clear()
    return TestClient(main.app)


def code_of(name):
    return next(k for k, v in queries.reference()[0].items() if v['name_bg'] == name)


def test_pages_and_shared_header(client):
    for url in ['/', '/karta', '/obshtini', '/obshtini/' + code_of('Банско'), '/sources', '/how']:
        r = client.get(url)
        assert r.status_code == 200, url
        assert 'Обратна връзка' in r.text and 'Подкрепи проекта' in r.text
        assert '—' not in r.text
    assert client.get('/obshtini/000000000').status_code == 404
    assert client.get('/healthz').json() == {'ok': True}


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
    conn.execute('TRUNCATE naselenie.population, naselenie.settlement_population, naselenie_src.grao_row, naselenie_src.grao_file')
    assert client.get('/').status_code == 200
    d = client.get('/api/karta.json').json()
    assert d['bg'] is None and len(d['items']) == 265 and all(r['v'] is None for r in d['items'])   # no data is not zero
    assert client.get('/api/series.json').json()['series'][0]['points'] == []

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from ingest import store
from ingest.parse import parse
from ingest.reference import ekatte,enrich
from .test_sources import FIX,NOW,sample
from .test_store import conn


@pytest.fixture
def client(conn,monkeypatch):
    ref=ekatte((FIX/'ekatte.zip').read_bytes())
    d=enrich(parse(sample(),NOW),ref)
    store.apply(conn,'https://www.grao.bg/tna/t41nm-15-09-2026_2.txt','a',d)
    d=enrich(parse((FIX/'grao/t41ob-15-09-2026_1.txt').read_bytes(),NOW),ref)
    store.apply(conn,'https://www.grao.bg/tna/t41ob-15-09-2026_1.txt','b',d)
    monkeypatch.setattr(main,'DSN',os.environ['NASELENIE_TEST_DSN'])
    main._cache.clear()
    return TestClient(main.app)


def test_pages_and_shared_header(client):
    code=next(iter(main.REF))
    for url in ['/','/karta','/obshtini','/obshtini/'+code,'/sources','/how']:
        r=client.get(url)
        assert r.status_code==200,url
        assert 'Обратна връзка' in r.text and 'Подкрепи проекта' in r.text
        assert '\u2014' not in r.text


def test_map_levels_reconcile_and_dates(client):
    totals=[]
    for level in main.NUTS:
        d=client.get('/api/karta.json',params={'l':level}).json()
        totals.append(sum(r['v'] for r in d['items'] if r['v'] is not None))
    assert len(set(totals))==1
    assert client.get('/api/karta.json?y=1900-01-01').status_code==404
    assert client.get('/api/karta.json?m=other').status_code==400


def test_burgas_difference_visible(client):
    code=next(k for k,v in main.REF.items() if v['name_bg']=='Бургас')
    r=client.get('/obshtini/'+code)
    assert '14 регистрации' in r.text and 'по-малко' in r.text
    assert '218 590' in r.text
    assert client.get('/export.csv?code='+code).status_code==200


def test_csv_and_no_data(client,conn):
    for url in ['/export.csv','/history.csv','/sources.csv','/unmatched.csv']:
        assert client.get(url).status_code==200
    conn.execute('TRUNCATE live.source');main._cache.clear()
    assert client.get('/').status_code==200
    assert client.get('/api/karta.json').json()['bg'] is None

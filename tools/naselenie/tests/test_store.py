import copy
import datetime as dt
import os

import psycopg
import pytest

from ingest import db, store
from ingest.parse import parse, ShapeError
from .test_sources import sample, NOW


@pytest.fixture
def conn(tmp_path,monkeypatch):
    dsn=os.environ.get('NASELENIE_TEST_DSN')
    if not dsn: pytest.skip('NASELENIE_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname','')
    monkeypatch.setattr(store,'RAW',tmp_path)
    with psycopg.connect(dsn,autocommit=True) as c:
        db.migrate(c)
        c.execute('TRUNCATE live.source,ops.held,ops.change_log,ops.source_state,ops.raw_file RESTART IDENTITY')
        yield c


def data():
    d=parse(sample(),NOW)
    for m in d['municipalities']:m['code']=m['oblast']+'/'+m['municipality']
    for m in d['places']:m['code']=m['oblast']+'/'+m['municipality']
    return d


def test_idempotent_and_raw(conn):
    d=data();sha=store.save_raw(conn,'https://source/test.txt',sample())
    store.save_raw(conn,'https://source/test.txt',sample())
    assert conn.execute('SELECT count(*) FROM ops.raw_file').fetchone()[0]==1
    assert store.apply(conn,'test',sha,d)=='stored'
    n=conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]
    assert store.apply(conn,'test',sha,d)=='unchanged'
    assert conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]==n


def test_hold_requires_same_answer_after_day(conn):
    d=data();now=dt.datetime(2026,9,29,tzinfo=dt.timezone.utc)
    store.apply(conn,'test','a',d,now)
    small=copy.deepcopy(d);small['places'].pop()
    assert store.apply(conn,'test','b',small,now)=='held'
    assert store.apply(conn,'test','b',small,now+dt.timedelta(hours=23))=='held'
    assert store.apply(conn,'test','c',small,now+dt.timedelta(days=1))=='held'
    assert store.apply(conn,'test','c',small,now+dt.timedelta(days=2))=='stored'


def test_different_source_families_preserved(conn):
    d=data();store.apply(conn,'detailed','a',d)
    bad=copy.deepcopy(d);bad['kind']='municipalities';bad['places']=[];bad['municipalities'][0]['current']+=1
    store.apply(conn,'monthly','b',bad)
    assert conn.execute('SELECT count(*) FROM live.source').fetchone()[0]==2
    assert conn.execute("SELECT payload FROM live.source WHERE url='detailed'").fetchone()[0]==d

import datetime as dt,json,os
from pathlib import Path
import psycopg,pytest
from ingest import db,store
from ingest.parse import ShapeError
F=Path(__file__).parent/'fixtures/egov'
@pytest.fixture
def conn(tmp_path,monkeypatch):
 dsn=os.environ.get('BYUDZHET_TEST_DSN')
 if not dsn:pytest.skip('BYUDZHET_TEST_DSN required')
 assert 'test' in dsn
 monkeypatch.setattr(store,'DATA',tmp_path)
 c=psycopg.connect(dsn,autocommit=True);db.migrate(c)
 c.execute('TRUNCATE live.snapshot,stage.snapshot,src.resource,ops.held,ops.raw_file,ops.change_log,ops.job_run RESTART IDENTITY')
 yield c
 c.close()
def test_store_idempotent_rewritten_invalid(conn):
 raw=(F/'debt-latest.json').read_bytes()
 assert store.put(conn,'debt','r','2026-06-30','1',raw)=='stored'
 n=conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]
 assert store.put(conn,'debt','r','2026-06-30','1',raw)=='unchanged'
 assert conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]==n
 x=json.loads(raw);x['data'][1][2]='216000'
 assert store.put(conn,'debt','r','2026-06-30','2',json.dumps(x).encode())=='stored'
 assert conn.execute("SELECT count(*) FROM ops.change_log WHERE cause='rewritten'").fetchone()[0]>0
 with pytest.raises(ShapeError):store.put(conn,'debt','r','2026-06-30','3',b'<html>denied')
 assert conn.execute('SELECT payload FROM live.snapshot').fetchone()[0]['rows'][0]['values']['debt']['original']=='216000'
def test_store_held_and_confirmed(conn):
 raw=(F/'state-latest.json').read_bytes();now=dt.datetime.now(dt.timezone.utc)
 store.put(conn,'state','r','31.08.2026','1',raw,now=now)
 x=json.loads(raw);x['data'][4][1:]=['','',''];new=json.dumps(x).encode()
 assert store.put(conn,'state','r','31.08.2026','2',new,now=now)=='held'
 assert store.put(conn,'state','r','31.08.2026','2',new,now=now+dt.timedelta(hours=23))=='held'
 assert store.put(conn,'state','r','31.08.2026','2',new,now=now+dt.timedelta(days=1))=='stored'
 assert conn.execute("SELECT count(*) FROM ops.change_log WHERE cause='confirmed'").fetchone()[0]>0

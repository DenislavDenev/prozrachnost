import datetime as dt
import os
from pathlib import Path
import psycopg
import pytest
from ingest import db, parse, store

@pytest.fixture
def conn(tmp_path, monkeypatch):
    dsn = os.environ.get('KONTROL_TEST_DSN')
    if not dsn:
        pytest.skip('KONTROL_TEST_DSN required')
    # Both DSN naming and database isolation verified before any destructive test.
    c = psycopg.connect(dsn, autocommit=True)
    name = c.execute('SELECT current_database()').fetchone()[0]
    assert name == 'kontrol_test', 'Only dedicated kontrol_test is allowed'
    db.migrate(c)
    c.execute('TRUNCATE live.snapshot,stage.snapshot,live.document,src.resource,ops.held,ops.raw_file,ops.version,ops.document_version,ops.change_log,ops.job_run RESTART IDENTITY')
    monkeypatch.setattr(store, 'DATA', tmp_path)
    yield c
    c.close()

def rows():
    return parse.nao((Path(__file__).parent / 'fixtures/nao.html').read_bytes(), 'nao', 'https://www.bulnao.government.bg/')

def manifest(rs):
    return dict(complete=True, occurrence_count=len(rs), source_count=len(rs), pages=1, scope='Test fixture only', answer_sha256=store.digest(store.encode(rs)))

def test_idempotency_and_changes(conn):
    rs = rows()
    assert store.publish(conn, 'nao', rs, manifest(rs)) == 'stored'
    n = conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]
    assert store.publish(conn, 'nao', rs, manifest(rs)) == 'unchanged'
    assert conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0] == n
    changed = [dict(r) for r in rs]
    changed[0]['title'] += ' (обновено)'
    assert store.publish(conn, 'nao', changed, manifest(changed)) == 'stored'
    assert conn.execute('SELECT count(*) FROM ops.version').fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE cause='rewritten'").fetchone()[0] == 1

def test_reconciliation_blocks_without_replacing(conn):
    rs = rows()
    store.publish(conn, 'nao', rs, manifest(rs))
    sha = conn.execute('SELECT sha256 FROM live.snapshot').fetchone()[0]
    with pytest.raises(parse.ShapeError):
        store.publish(conn, 'nao', rs[:1], dict(manifest(rs[:1]), source_count=3))
    with pytest.raises(parse.ShapeError):
        store.publish(conn, 'nao', rs, dict(manifest(rs), complete=False))
    assert conn.execute('SELECT sha256 FROM live.snapshot').fetchone()[0] == sha

def test_hold_confirm_and_never_delete_archive(conn):
    rs = rows()
    now = dt.datetime.now(dt.timezone.utc)
    store.publish(conn, 'nao', rs, manifest(rs), now)
    assert store.publish(conn, 'nao', rs[:1], manifest(rs[:1]), now) == 'held'
    assert store.publish(conn, 'nao', rs[:1], manifest(rs[:1]), now + dt.timedelta(hours=23)) == 'held'
    assert store.publish(conn, 'nao', rs[:2], manifest(rs[:2]), now + dt.timedelta(days=1)) == 'held'
    assert store.publish(conn, 'nao', rs[:2], manifest(rs[:2]), now + dt.timedelta(days=2)) == 'stored'
    assert conn.execute('SELECT count(*) FROM ops.version').fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE cause='confirmed'").fetchone()[0] > 0

def test_deterministic_database_rebuild(conn):
    rs = rows()
    store.publish(conn, 'nao', rs, manifest(rs))
    before = conn.execute('SELECT sha256 FROM live.snapshot').fetchone()[0]
    conn.execute('TRUNCATE live.snapshot,stage.snapshot')
    store.publish(conn, 'nao', list(reversed(rs)), manifest(rs))
    assert conn.execute('SELECT sha256 FROM live.snapshot').fetchone()[0] == before


def test_document_bytes_versions_and_provenance(conn):
    from ingest.documents import save_document
    r=rows()[0]
    first=b'%PDF-original'; second=b'%PDF-changed'
    def metadata(raw):
        return dict(document_sha256=store.digest(raw),text_available=False,excerpt=None)
    save_document(conn,r,first,metadata(first),fetch_url='https://official.example/postback')
    save_document(conn,r,first,metadata(first),fetch_url='https://official.example/postback')
    assert conn.execute('SELECT count(*) FROM ops.document_version').fetchone()[0]==1
    save_document(conn,r,second,metadata(second),fetch_url='https://official.example/postback')
    assert conn.execute('SELECT count(*) FROM ops.document_version').fetchone()[0]==2
    assert conn.execute('SELECT payload FROM live.document').fetchone()[0]['document_sha256']==store.digest(second)
    assert conn.execute('SELECT count(*) FROM ops.raw_file').fetchone()[0]==2
    assert conn.execute('SELECT url FROM ops.raw_file LIMIT 1').fetchone()[0]=='https://official.example/postback'

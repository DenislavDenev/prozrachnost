import pytest
from ingest import documents,store,progress,run,parse

def metadata(conn):
    for source in ['appk','ncr']:store.state(conn,source,'details','ok',rows=1)
    reports=[dict(id=str(year),appk_id='105',kind=kind,year=year,documents=[dict(id=str(year),url='https://example.invalid/'+str(year),title='Original')]) for year,kind in [(2024,'annual'),(2025,'annual'),(2026,'quarterly')]]
    store.apply(conn,'appk','reports:105',reports,3)

def test_selects_only_actual_latest_annual_year(conn):
    metadata(conn)
    rows=documents.selected(conn,'appk')
    assert len(rows)==1 and rows[0]['year']==2025
    assert rows[0]['id']=='105:2025:2025'
    store.state(conn,'appk','details','partial',rows=1)
    with pytest.raises(parse.ShapeError,match='complete'):documents.selected(conn,'appk')

def test_attachment_original_versions_do_not_rewrite_report(conn,tmp_path,monkeypatch):
    metadata(conn);monkeypatch.setattr(progress,'DATA',tmp_path);monkeypatch.setattr(store,'DATA',tmp_path)
    class Client:
        raws={};body=b'%PDF-1.7\noriginal\n%%EOF'
        def get(self,url,data=None):return self.body
    client=Client();before=conn.execute('SELECT count(*) FROM ops.version').fetchone()[0]
    documents.archive(conn,client,run.archive)
    documents.archive(conn,client,run.archive)
    assert conn.execute('SELECT count(*) FROM ops.document_file').fetchone()[0]==1
    client.body=b'%PDF-1.7\nchanged\n%%EOF';documents.archive(conn,client,run.archive)
    assert conn.execute('SELECT count(*) FROM ops.document_file').fetchone()[0]==2
    assert conn.execute('SELECT count(*) FROM ops.version').fetchone()[0]==before
    assert conn.execute('SELECT count(*) FROM ops.raw_file').fetchone()[0]==2
    assert conn.execute("SELECT count(*) FROM ops.raw_read WHERE mode='live' AND method='GET'").fetchone()[0]==3
    documents.archive(conn,client,run.archive,resume=True)
    assert conn.execute("SELECT count(*) FROM ops.raw_read WHERE mode='archive_reuse'").fetchone()[0]==1
    with pytest.raises(parse.ShapeError):documents.media_type(b'<html>Error</html>')

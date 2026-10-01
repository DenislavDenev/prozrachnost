import pytest
from ingest import store,parse

def test_hold_identical_day_and_idempotent(conn):
    rows=[dict(id='1',name='a'),dict(id='2',name='a')]
    assert store.apply(conn,'appk','catalogue',rows,2)=='stored'
    n=conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]
    assert store.apply(conn,'appk','catalogue',rows,2)=='unchanged'
    assert conn.execute('SELECT count(*) FROM ops.change_log').fetchone()[0]==n
    assert store.apply(conn,'appk','catalogue',rows[:1],1)=='held'
    assert store.apply(conn,'appk','catalogue',rows[:1],1)=='held'
    assert conn.execute('SELECT count(*) FROM live.record WHERE gone_at IS NULL').fetchone()[0]==2
    conn.execute("UPDATE ops.held SET first_at=now()-interval '2 days'")
    changed=[dict(id='1',name='b')]
    assert store.apply(conn,'appk','catalogue',changed,1)=='held'
    conn.execute("UPDATE ops.held SET first_at=now()-interval '2 days'")
    assert store.apply(conn,'appk','catalogue',changed,1)=='stored'
    assert conn.execute('SELECT count(*) FROM live.record').fetchone()[0]==2
    assert conn.execute('SELECT count(*) FROM live.record WHERE gone_at IS NULL').fetchone()[0]==1

def test_schema_and_reconciliation_keep_live(conn):
    store.apply(conn,'appk','catalogue',[dict(id='1',name='safe')],1)
    with pytest.raises(parse.ShapeError):store.apply(conn,'appk','catalogue',[],1)
    assert conn.execute('SELECT payload FROM live.record').fetchone()[0]['name']=='safe'
    with pytest.raises(parse.ShapeError):parse.companies(b'<title>500 error</title>')
    assert conn.execute('SELECT count(*) FROM live.record').fetchone()[0]==1

def test_board_loss_versions_and_context(conn):
    board=[dict(name='Same Name',country='БЪЛГАРИЯ')]
    for enterprise in ['105','51']:
        store.apply(conn,'appk','profile:'+enterprise,[dict(id=enterprise,board=board)],1)
    assert conn.execute('SELECT count(*) FROM live.record').fetchone()[0]==2
    assert store.apply(conn,'appk','profile:105',[dict(id='105',board=[])],1)=='held'
    conn.execute("UPDATE ops.held SET first_at=now()-interval '2 days'")
    assert store.apply(conn,'appk','profile:105',[dict(id='105',board=[])],1)=='stored'
    assert conn.execute('SELECT count(*) FROM ops.version').fetchone()[0]==3
    assert conn.execute("SELECT payload->'board' FROM live.record WHERE scope='profile:51'").fetchone()[0]==board

def test_report_revisions_preserve_history(conn):
    rows=[dict(id='9848',year=2025,kind='annual',unit='BGN',documents=[dict(id='doc',title='old')])]
    store.apply(conn,'appk','reports:105',rows,1)
    updated=[dict(id='9848',year=2025,kind='annual',unit='BGN',documents=[dict(id='doc',title='revised')])]
    assert store.apply(conn,'appk','reports:105',updated,1)=='held'
    conn.execute("UPDATE ops.held SET first_at=now()-interval '2 days'")
    store.apply(conn,'appk','reports:105',updated,1)
    assert conn.execute('SELECT count(*) FROM ops.version').fetchone()[0]==2
    assert conn.execute('SELECT count(*) FROM live.record').fetchone()[0]==1

def test_scalar_identity_loss_is_held(conn):
    store.apply(conn,'appk','profile:105',[dict(id='105',eik='831646048',share_pct='100')],1)
    assert store.apply(conn,'appk','profile:105',[dict(id='105',eik=None,share_pct=None)],1)=='held'
    assert conn.execute('SELECT payload FROM live.record').fetchone()[0]['eik']=='831646048'

def test_raw_corruption_never_overwrites(conn,tmp_path,monkeypatch):
    monkeypatch.setattr(store,'DATA',tmp_path)
    sha=store.save_raw(conn,'appk','https://example.invalid/source',b'original')
    path=next(tmp_path.rglob(sha));path.write_bytes(b'corrupt')
    with pytest.raises(parse.ShapeError):store.save_raw(conn,'appk','https://example.invalid/source',b'original')
    assert path.read_bytes()==b'corrupt'

def test_freshness_rejects_catalogue_only(conn):
    from ingest.checks import freshness,summary
    for source in ['appk','ncr']:store.apply(conn,source,'catalogue',[dict(id='1')],1)
    problems=freshness(conn)
    assert any(p.startswith('appk: няма завършено') for p in problems)
    assert any(p.startswith('ncr: няма завършено') for p in problems)
    for source in ['appk','ncr']:store.state(conn,source,'details','ok',rows=1)
    assert freshness(conn)==[]
    result=summary(conn)
    assert result['counts']['Предприятия']==1 and result['counts']['Концесии, уникални партиди']==1
    store.state(conn,'ncr','details','partial','still incomplete',rows=0)
    assert any('partial' in p for p in freshness(conn))

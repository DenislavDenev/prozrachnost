import json
import pytest
from ingest import progress,parse

def test_checkpoint_resume_keeps_original_queue(tmp_path,monkeypatch):
    monkeypatch.setattr(progress,'DATA',tmp_path)
    first=progress.Queue('appk','details',[dict(id='1'),dict(id='2')]);first.advance()
    resumed=progress.Queue('appk','details',[dict(id='changed')],True)
    assert resumed.done==1 and resumed.rows==[dict(id='1'),dict(id='2')]
    resumed.advance();resumed.complete()
    assert not progress.pending('appk','details')
    assert len(list(tmp_path.rglob('*.completed-*.json')))==1

def test_corrupt_checkpoint_never_skips_source(tmp_path,monkeypatch):
    monkeypatch.setattr(progress,'DATA',tmp_path)
    queue=progress.Queue('ncr','details',[dict(id='1')]);data=json.loads(queue.path.read_text());data['done']=2;queue.path.write_text(json.dumps(data))
    with pytest.raises(parse.ShapeError,match='corrupt'):progress.Queue('ncr','details',[],True)

def test_deadline_does_not_start_request_that_crosses_budget(monkeypatch):
    monkeypatch.setattr(progress.time,'monotonic',lambda:100)
    deadline=progress.Deadline(30)
    with pytest.raises(progress.BudgetReached):deadline.require(90)
    deadline.require(10)

def test_real_job_budget_is_pending_without_full_success(conn,tmp_path,monkeypatch,capsys):
    import os,sys,psycopg
    from ingest import run,db,store
    monkeypatch.setattr(progress,'DATA',tmp_path)
    monkeypatch.setattr(db,'connect',lambda **kw:psycopg.connect(os.environ['SOBSTVENOST_TEST_DSN'],**kw))
    store.apply(conn,'appk','catalogue',[dict(id='105',source_url='https://example.invalid/profile')],1)
    monkeypatch.setattr(sys,'argv',['run','--step','appk-details','--budget-seconds','1'])
    assert run.main()==0
    assert json.loads(capsys.readouterr().out)['pending'] is True
    assert conn.execute("SELECT status,last_success FROM ops.source_state WHERE scope='details'").fetchone()==('partial',None)
    assert conn.execute('SELECT status,error FROM ops.job_run').fetchone()==('ok',None)
    assert progress.pending('appk','details')

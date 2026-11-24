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

def test_real_job_budget_is_pending_without_full_success(conn, dsn, monkeypatch, capsys):
    from ingest import pipeline, run, store
    from . import seed
    monkeypatch.setattr(run, 'DSN', dsn)
    rows, manifest = seed.enterprises()
    with pipeline.run(conn, 'sobstvenost', 'appk') as r:
        store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest)
    # one second is less than any request needs: nothing is read, the pass waits for the next run
    assert run.main(['--step', 'appk-details', '--budget-seconds', '1']) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['pending'] is True
    assert conn.execute("SELECT status, error FROM ops.run WHERE tool = 'sobstvenost' AND step = 'appk-details'").fetchone() == ('ok', None)
    assert progress.pending('appk', 'details')
    assert conn.execute("SELECT count(*) FROM ops.load WHERE tool = 'sobstvenost' AND source = 'appk-full'").fetchone()[0] == 0
    assert any('профили: partial' in p for p in run.freshness(conn))


def test_safe_budget_never_runs_into_the_day_windows():
    import datetime as dt
    from ingest.run import safe_budget
    at = lambda h, m: dt.datetime(2026, 11, 29, h, m, tzinfo=dt.timezone.utc)
    assert safe_budget(19800, at(0, 15)) == 19800
    assert safe_budget(None, at(5, 0)) == 45 * 60
    assert safe_budget(19800, at(6, 0)) == 0 and safe_budget(19800, at(18, 0)) == 0
    assert safe_budget(19800, at(22, 30)) == 19800

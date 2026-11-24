"""The move from the old database of its own: replay from its raw files, then parity, against the old schemas built in
the test database (live, stage and old_ops, as db/legacy made them and the old store filled them)."""
import datetime as dt
import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from ingest import move, parse, pipeline, run, store

from . import seed

T1 = dt.datetime(2026, 10, 1, 22, 6, 40, 50098, tzinfo=dt.timezone.utc)
T2 = T1 + dt.timedelta(minutes=18)
T3 = T1 + dt.timedelta(minutes=30)
COMPANIES = parse.APPK + '/Public/Public/Companies'
SEARCH = parse.NCR + '/Concessions/Search'


def read_appk(fetch):
    rows = parse.companies(fetch('appk', COMPANIES))[0]
    m, mm = seed.ministries()
    return rows, dict(pages=1, source_count=sum(r['status'] == 'Активно' for r in rows)), m, mm


def read_ncr(fetch):
    rows = parse.concessions(fetch('ncr', SEARCH))[0]
    return rows, dict(pages=1, source_count=len(rows))


def old_database(conn, tmp_path):
    """One read of each catalogue, enterprise 105 and one concession, as the old store wrote them, with their raw files;
    and a later answer for the profile of 105 that no stored read used."""
    for sql in sorted((run.ROOT / 'db' / 'legacy').glob('*.sql')):
        conn.execute(sql.read_text(encoding='utf-8').replace('ops.', 'old_ops.').replace('SCHEMA IF NOT EXISTS ops', 'SCHEMA IF NOT EXISTS old_ops'))
    folder = tmp_path / 'old'
    folder.mkdir()

    def raw(source, url, data, at):
        sha = hashlib.sha256(data).hexdigest()
        path = folder / sha
        path.write_bytes(data)
        conn.execute('INSERT INTO old_ops.raw_file (source, url, path, sha256, fetched_at, bytes) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (source, url, str(path), sha, at, len(data)))

    def stored(source, scope, rows, at):
        for row in rows:
            conn.execute('INSERT INTO live.record (source, scope, ref, payload, first_seen, last_seen) VALUES (%s,%s,%s,%s,%s,%s)',
                         (source, scope, str(row['id']), Jsonb(row), at, at))
            conn.execute('INSERT INTO old_ops.version (source, scope, ref, sha256, payload, observed_at) VALUES (%s,%s,%s,%s,%s,%s)',
                         (source, scope, str(row['id']), store.version(row), Jsonb(row), at))
    pages = seed.answers()
    raw('appk', COMPANIES, (seed.P / 'appk-companies-1.html').read_bytes(), T1 - dt.timedelta(seconds=30))
    raw('ncr', SEARCH, (seed.P / 'ncr-search-1.html').read_bytes(), T1 - dt.timedelta(seconds=20))
    for url, data in pages.items():
        raw('ncr' if url.startswith(parse.NCR) else 'appk', url, data, T2 - dt.timedelta(minutes=1))
    later = pages[seed.PROFILE].replace('Иван Атанасов Кунев'.encode(), 'Друго Име Някакво'.encode())
    raw('appk', seed.PROFILE, later, T2 + dt.timedelta(hours=1))
    rows, manifest, ministries, _ = read_appk(seed.fetch_from({COMPANIES: (seed.P / 'appk-companies-1.html').read_bytes()}))
    concessions = read_ncr(seed.fetch_from({SEARCH: (seed.P / 'ncr-search-1.html').read_bytes()}))[0]
    stored('appk', 'catalogue', rows, T1)
    stored('appk', 'ministries', ministries, T1)
    stored('ncr', 'catalogue', concessions, T1)
    profile, reports = run.read_enterprise(seed.fetch_from(pages), next(x for x in rows if x['id'] == '105'))
    stored('appk', 'profile:105', [profile], T2)
    stored('appk', 'reports:105', reports, T2)
    concession = run.read_concession(seed.fetch_from(pages), next(x for x in concessions if x['id'] == seed.CONCESSION))
    stored('ncr', 'profile:' + seed.CONCESSION, [concession], T3)
    for source, scope, status, last_read, last_success, n in [
            ('appk', 'catalogue', 'ok', T1, T1, len(rows)), ('appk', 'ministries', 'ok', T1, T1, len(ministries)),
            ('ncr', 'catalogue', 'ok', T1, T1, len(concessions)),
            ('appk', 'details', 'ok', T2 + dt.timedelta(seconds=5), T2 - dt.timedelta(seconds=5), 1),
            ('ncr', 'details', 'partial', T3, None, 1)]:
        conn.execute('INSERT INTO old_ops.source_state (source, scope, status, last_read, last_success, rows) VALUES (%s,%s,%s,%s,%s,%s)',
                     (source, scope, status, last_read, last_success, n))
    return rows, profile


def parity_results(conn):
    got = dict(conn.execute("SELECT name, actual FROM ops.check_latest WHERE tool = 'sobstvenost' AND kind = 'parity'").fetchall())
    want = dict(conn.execute("SELECT name, expected FROM ops.check_latest WHERE tool = 'sobstvenost' AND kind = 'parity'").fetchall())
    return got, want


def test_replay_and_parity_against_the_old_database(conn, dsn, tmp_path, monkeypatch):
    old_database(conn, tmp_path)
    monkeypatch.setattr(move, 'read_appk_catalogue', read_appk)
    monkeypatch.setattr(move, 'read_ncr_catalogue', read_ncr)
    with pipeline.run(conn, 'sobstvenost', 'replay') as r:
        assert move.replay(conn, r, dsn, ops='old_ops') == []
        assert run.finish(conn, r) == []
    # each read at the time the old database stored it, from the answer archived before that time
    assert conn.execute("SELECT loaded_at FROM ops.load WHERE tool = 'sobstvenost' AND source = 'appk-details'").fetchone()[0] == T2
    assert conn.execute("SELECT count(*) FROM sobstvenost.board_entry WHERE member_name = 'Иван Атанасов Кунев'").fetchone()[0] == 1
    with pipeline.run(conn, 'sobstvenost', 'parity') as r:
        move.parity(conn, r, dsn, ops='old_ops')
    got, want = parity_results(conn)
    assert got['parity_records_differ'] == 0
    for k in ('parity_records', 'parity_versions', 'parity_sources'):
        assert got[k] == want[k] and got[k] > 0, (k, got[k], want[k])
    assert got['parity_documents'] == want['parity_documents'] == 0
    assert want['parity_records'] == 10 + 2 + 20 + 1 + 16 + 1
    # one record kept differently is one record that differs
    conn.execute("""UPDATE sobstvenost_src.enterprise SET row_sha256 = 'x' WHERE appk_id = '105'
        AND load_id = (SELECT load_id FROM ops.load WHERE tool = 'sobstvenost' AND source = 'appk' AND ref = 'catalogue' AND is_current)""")
    with pipeline.run(conn, 'sobstvenost', 'parity') as r:
        move.parity(conn, r, dsn, ops='old_ops')
    assert parity_results(conn)[0]['parity_records_differ'] == 1


def test_replay_without_an_archived_answer_is_refused_and_said(conn, dsn, tmp_path, monkeypatch):
    old_database(conn, tmp_path)
    monkeypatch.setattr(move, 'read_appk_catalogue', read_appk)
    monkeypatch.setattr(move, 'read_ncr_catalogue', read_ncr)
    conn.execute("DELETE FROM old_ops.raw_file WHERE url LIKE '%%AssignedConcession%%'")
    with pipeline.run(conn, 'sobstvenost', 'replay') as r:
        problems = move.replay(conn, r, dsn, ops='old_ops')
    assert len(problems) == 1 and 'Няма архивиран отговор' in problems[0]
    assert conn.execute("SELECT status FROM ops.load WHERE tool = 'sobstvenost' AND source = 'ncr-details'").fetchone()[0] == 'rejected'


def test_the_legacy_schema_is_kept_for_the_move():
    assert {p.name for p in (Path(run.ROOT) / 'db' / 'legacy').glob('*.sql')} == {'0001_initial.sql', '0002_documents.sql', '0003_read_proof.sql'}

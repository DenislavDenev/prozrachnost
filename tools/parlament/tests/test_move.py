"""The move from the database of its own: replay copies it from one snapshot and builds what is built, parity finds any
difference. The old database is made here in schemas live and old_ops of the test database, by the old migrations
(db/legacy) and filled with what the real fixtures load; then the tables of Парламент are emptied and replayed from it.

Runs only when PARLAMENT_TEST_DSN points at a disposable database (it is wiped).
"""
import os
from pathlib import Path

import pytest

DSN = os.environ.get("PARLAMENT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PARLAMENT_TEST_DSN not set")

LEGACY = Path(__file__).resolve().parent.parent / "db" / "legacy"


def old_database(conn):
    """The old schemas, as the old migrations made them, with the rows of the tables here and of the ledger."""
    from ingest import move
    for f in sorted(LEGACY.glob("*.sql")):
        sql = f.read_text(encoding="utf-8").replace("SCHEMA IF NOT EXISTS ops", "SCHEMA IF NOT EXISTS old_ops").replace("ops.", "old_ops.")
        conn.execute(sql)
    for table, cols in move.TABLES + move.BUILT:
        conn.execute(f"INSERT INTO live.{table} ({cols}) SELECT {cols} FROM parlament.{table}")
    conn.execute(f"INSERT INTO old_ops.source_state ({move.STATE}) SELECT {move.STATE} FROM parlament_src.read_state")
    conn.execute("""INSERT INTO old_ops.raw_file (source, ref, path, sha256, bytes, fetched_at)
                    SELECT f.source, f.ref, x.path, x.sha256, x.bytes, f.fetched_at FROM ops.fetch f JOIN ops.raw_file x USING (raw_file_id)
                    WHERE f.tool = 'parlament' ORDER BY f.fetch_id""")
    conn.execute("""INSERT INTO old_ops.change_log (detected_at, source, ref, field, old, new, cause)
                    SELECT detected_at, source, ref, field, old, new, cause FROM ops.change_log WHERE tool = 'parlament' ORDER BY change_id""")
    conn.execute("""INSERT INTO old_ops.job_run (step, started_at, finished_at, status, code_sha, inputs, stats, error)
                    VALUES ('sittings', now() - interval '1 day', now() - interval '1 day', 'ok', 'abc', '{}', '{}', NULL),
                           ('people', now() - interval '2 days', NULL, 'running', NULL, '{"all": true}', '{}', NULL)""")
    conn.execute("INSERT INTO old_ops.held (source, ref, sha256, rows) VALUES ('parliament', 'sten/11174', 'a:b', 3)")


def empty(conn):
    from tests.conftest import CLEAN, SERVING
    conn.execute("TRUNCATE " + ", ".join([f"parlament.{t}" for t in CLEAN] + ["parlament_src.read_state"]
                                         + [f"pub_parlament.{t}" for t in SERVING]))
    for table in ("change_log", "hold", "fetch"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = 'parlament'")


def test_the_old_database_is_replayed_built_and_proved_equal(conn):
    from ingest import db, move, run
    from tests.test_store import FX, Source, everyone, run as read
    src = Source()
    src.files["pl-sten/11159"] = (FX / "sten-310726-text.json").read_bytes()
    read(conn, src)
    everyone(conn, src)
    old_database(conn)
    before = {t: conn.execute(f"SELECT count(*) FROM live.{t}").fetchone()[0] for t, _ in move.TABLES + move.BUILT}
    empty(conn)
    with db.job("replay", conn=conn) as (_, st):
        move.replay(conn, db.current(), old_dsn=DSN, old_ops="old_ops")
        assert run.publish(conn) == []
    assert {t: st["rows"][t] for t, _ in move.TABLES} == {t: before[t] for t, _ in move.TABLES} and st["rows"]["vote"] == 4080
    assert st["runs"] == 2 and st["held"] == 1 and st["raw_missing"] == 0 and st["raw_files"] > 0
    # the lines and the statistics are not copied: they are built from the votes, and come out the same
    assert st["built"] == {"line": before["line"], "mp_stat": before["mp_stat"]} and before["line"] > 0
    with db.job("parity", conn=conn) as (_, st):
        move.parity(conn, db.current(), old_dsn=DSN, old_ops="old_ops")
    assert st["differ"] == 0, st["detail"]
    assert all(a == b for a, b in st["rows"].values()) and st["rows"]["vote"] == [4080, 4080]
    ok = dict(conn.execute("""SELECT name, ok FROM ops.check_result WHERE tool = 'parlament' AND name LIKE 'parity%'"""))
    assert ok["parity_differ"] and ok["parity_pub_speech_rows"] and len(ok) == 55 - 9
    # a second replay is refused: the move is done once
    with db.job("replay", conn=conn):
        with pytest.raises(RuntimeError, match="веднъж"):
            move.replay(conn, db.current(), old_dsn=DSN, old_ops="old_ops")


def test_parity_finds_a_changed_vote_a_missing_speech_and_a_changed_result(conn):
    from ingest import db, move, run
    from tests.test_store import FX, Source, everyone, run as read
    src = Source()
    src.files["pl-sten/11159"] = (FX / "sten-310726-text.json").read_bytes()
    read(conn, src)
    everyone(conn, src)
    old_database(conn)
    empty(conn)
    with db.job("replay", conn=conn):
        move.replay(conn, db.current(), old_dsn=DSN, old_ops="old_ops")
        assert run.publish(conn) == []
    conn.execute("UPDATE live.vote SET code = '-' WHERE sitting = 11174 AND item = 2 AND mp = 3839")
    conn.execute("DELETE FROM pub_parlament.speech WHERE sitting = 11159 AND no = 3")
    conn.execute("UPDATE parlament.item SET yes = yes + 1 WHERE sitting = 11174 AND no = 2")
    with db.job("parity", conn=conn) as (_, st):
        move.parity(conn, db.current(), old_dsn=DSN, old_ops="old_ops")
    found = " | ".join(st["detail"])
    assert "vote: 4080 / 4080 реда, съдържанието различно" in found and "pub_vote:" in found
    assert "pub_speech:" in found and "speeches по събрание" in found
    assert "item:" in found and "pub_item:" not in found
    assert not dict(conn.execute("SELECT name, ok FROM ops.check_result WHERE tool = 'parlament' AND name = 'parity_differ'"))["parity_differ"]


def test_the_step_parity_says_its_differences_and_run_main_reports_them(conn, monkeypatch, capsys):
    """The step through its command line: a difference is a problem, and the exit code is 1."""
    from ingest import db, move, run
    from tests.test_store import Source, run as read
    read(conn, Source())
    old_database(conn)
    empty(conn)
    with db.job("replay", conn=conn):
        move.replay(conn, db.current(), old_dsn=DSN, old_ops="old_ops")
        assert run.publish(conn) == []
    monkeypatch.setattr(move, "OLD_DSN", DSN)
    real = move.parity
    monkeypatch.setattr(move, "parity", lambda c, r: real(c, r, old_dsn=DSN, old_ops="old_ops"))
    assert run.main(["--step", "parity"]) == 0
    conn.execute("UPDATE live.sitting SET heading = 'друго' WHERE id = 11174")
    assert run.main(["--step", "parity"]) == 1
    assert "сверката: sitting:" in capsys.readouterr().out

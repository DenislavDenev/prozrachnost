"""The archive in the shared register: every line of index.jsonl is a fetch, every file a raw file, every run a row."""
import json
import tomllib
from datetime import datetime, timedelta, timezone

import pytest

from archive import register
from archive import run as runmod
from archive import sources
from archive.core import Bad, Store
from ingest import pipeline


def put(store, source, key, data, ext="json"):
    assert store.put(source, key, data, ext)
    store.save()


def fetches(conn):
    return conn.execute('''SELECT f.source, f.ref, r.sha256, r.bytes, r.path FROM ops.fetch f JOIN ops.raw_file r
        USING (raw_file_id) WHERE f.tool = 'nablyudatel' ORDER BY f.fetch_id''').fetchall()


def ledger(conn, step="replay"):
    return pipeline.run(conn, register.TOOL, step)


def test_every_source_the_archive_writes_is_registered():
    reg = tomllib.loads((register.ROOT / "db" / "datasets.toml").read_text(encoding="utf-8"))
    names = {s["source"] for s in reg["source"]}
    assert names == (set(sources.SOURCES) - {"urls"}) | set(sources.URLS)


def test_every_line_is_registered_once_with_its_file(conn, tmp_path):
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    put(st, "erik", "page-1", b'{"a": 2}')
    put(st, "kolkostruva", "2026-11-10", b'{"a": 1}')   # the same answer under another key: one file, two fetches
    with ledger(conn) as r:
        assert register.sync(conn, r, tmp_path) == 3
        assert register.sync(conn, r, tmp_path) == 0
    rows = fetches(conn)
    assert [(s, ref) for s, ref, *_ in rows] == [("erik", "page-1"), ("erik", "page-1"), ("kolkostruva", "2026-11-10")]
    assert rows[0][2] == rows[2][2] and rows[0][3] == 8
    assert conn.execute("SELECT count(*) FROM ops.raw_file WHERE sha256 = %s", (rows[0][2],)).fetchone()[0] == 1
    lines = register.index_lines(tmp_path)
    times = [t for (t,) in conn.execute("SELECT fetched_at FROM ops.fetch WHERE tool = 'nablyudatel' ORDER BY fetch_id")]
    assert [register.stamp(t) for t in times] == [e["at"] for e in lines]
    assert register.compare(conn, tmp_path) == {"lines": 3, "matched": 3, "missing": 0, "extra": 0, "size_differs": 0}


def test_a_file_that_is_not_its_line_stops_the_sync(conn, tmp_path):
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    f = tmp_path / register.index_lines(tmp_path)[0]["file"]
    f.write_bytes(b'{"b": 1}')   # the same size, other bytes
    with pytest.raises(pipeline.CorruptRaw), ledger(conn) as r:
        register.sync(conn, r, tmp_path)
    f.write_bytes(b'{"a": 10}')
    with pytest.raises(pipeline.CorruptRaw), ledger(conn) as r:
        register.sync(conn, r, tmp_path)
    assert fetches(conn) == []


def test_a_line_still_being_written_waits_and_a_broken_one_stops(conn, tmp_path):
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    index = tmp_path / "index.jsonl"
    full = index.read_text(encoding="utf-8")
    index.write_text(full[:-20], encoding="utf-8")
    with ledger(conn) as r:
        assert register.sync(conn, r, tmp_path) == 0
    index.write_text(full, encoding="utf-8")
    with ledger(conn) as r:
        assert register.sync(conn, r, tmp_path) == 1
    index.write_text("not json\n" + full, encoding="utf-8")
    with pytest.raises(register.BadIndex):
        register.index_lines(tmp_path)


def test_a_line_of_an_unknown_source_is_not_registered(conn, tmp_path):
    put(Store(tmp_path), "nowhere", "k", b'{"a": 1}')
    with pytest.raises(Exception, match="fetch_source_fk"), ledger(conn) as r:
        register.sync(conn, r, tmp_path)


def test_a_run_is_a_row_and_its_new_lines_are_registered(conn, tmp_path, monkeypatch):
    def good(st, http, full, deadline):
        st.put("erik", "page-1", b'{"a": 1}', "json")
        return {"pages": 1}

    def broken(st, http, full, deadline):
        raise Bad("the source is down")
    monkeypatch.setitem(sources.SOURCES, "erik", {**sources.SOURCES["erik"], "fn": good})
    monkeypatch.setitem(sources.SOURCES, "dfz", {**sources.SOURCES["dfz"], "fn": broken})
    st = Store(tmp_path)
    assert runmod.run(st, ["erik", "dfz"], conn=conn) == 1
    runs = conn.execute("SELECT step, status, stats, error FROM ops.run WHERE tool = 'nablyudatel' ORDER BY run_id").fetchall()
    assert [(s, x) for s, x, *_ in runs] == [("erik", "ok"), ("dfz", "failed")]
    assert runs[0][2]["registered"] == 1
    assert runs[1][3] == "Bad: the source is down" == st.all_states()["dfz"]["last_error"]
    assert len(fetches(conn)) == 1
    states = register.run_states(conn, sources.SOURCES)
    assert set(states) == {"erik", "dfz"} and "last_error_at" in states["dfz"] and "last_ok" in states["erik"]
    files = st.all_states()
    assert runmod.freshness(states) == runmod.freshness({n: files.get(n, {}) for n in sources.SOURCES})


def test_without_the_database_the_answer_is_kept_and_registered_later(conn, dsn, tmp_path, monkeypatch, capsys):
    def good(st, http, full, deadline):
        st.put("erik", "page-1", b'{"a": 1}', "json")
        return {}
    monkeypatch.setitem(sources.SOURCES, "erik", {**sources.SOURCES["erik"], "fn": good})
    monkeypatch.setenv("ARHIV_DIR", str(tmp_path))
    monkeypatch.setenv("NABLYUDATEL_DSN", "dbname=nablyudatel_test_missing host=/nonexistent")
    assert runmod.main(["erik"]) == 1
    assert len(register.index_lines(tmp_path)) == 1 and fetches(conn) == []
    assert "register" in capsys.readouterr().out
    monkeypatch.setenv("NABLYUDATEL_DSN", dsn)
    runmod.main(["freshness"])   # the daily check registers what the runs could not
    assert len(fetches(conn)) == 1


def test_the_state_files_become_runs_once(conn, tmp_path):
    st = Store(tmp_path)
    (tmp_path / "state" / "erik.json").write_text(json.dumps(
        {"seen": {}, "last_ok": "2026-11-11T02:08:58Z", "last": {"pages": 3},
         "last_error": "Blocked: refused", "last_error_at": "2026-10-04T02:41:19Z"}), encoding="utf-8")
    (tmp_path / "state" / "dfz.json").write_text(json.dumps(
        {"seen": {}, "last_ok": "2026-11-02T00:31:00Z", "last_error": "Bad: not the CSV", "last_error_at": "2026-11-09T00:30:05Z"}),
        encoding="utf-8")
    assert register.import_states(conn, tmp_path, sources.SOURCES) == 4
    assert register.import_states(conn, tmp_path, sources.SOURCES) == 0
    at = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    files = st.all_states()
    assert register.run_states(conn, sources.SOURCES) == {
        n: {k: v for k, v in files[n].items() if k in ("last_ok", "last_error", "last_error_at")} for n in ("erik", "dfz")}
    assert runmod.freshness(register.run_states(conn, sources.SOURCES), at) == runmod.freshness(files, at)
    assert any(p.startswith("dfz") and "not the CSV" in p for p in runmod.freshness(files, at))


def test_parity_holds_after_the_replay_and_names_every_difference(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("ARHIV_DIR", str(tmp_path))
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    put(st, "erik", "page-1", b'{"a": 2}')
    put(st, "ekatte", "latest", b"PK", ext="zip")
    assert runmod.main(["replay"]) == 0
    assert runmod.main(["parity"]) == 0
    latest = dict(conn.execute("SELECT name, ok FROM ops.check_latest WHERE tool = 'nablyudatel'").fetchall())
    assert latest == {"index_in_register": True, "register_in_index": True, "state_in_register": True, "freshness_same": True}
    conn.execute("DELETE FROM ops.fetch WHERE fetch_id = (SELECT max(fetch_id) FROM ops.fetch WHERE tool = 'nablyudatel')")
    ok, out = runmod.parity(conn, st)
    assert not ok and out["missing"] == 1 and out["state"] == {"keys": 2, "same": 1}
    with ledger(conn) as r:
        register.sync(conn, r, tmp_path)
        rid = conn.execute("SELECT raw_file_id FROM ops.raw_file ORDER BY raw_file_id LIMIT 1").fetchone()[0]
        conn.execute("""INSERT INTO ops.fetch (run_id, tool, source, ref, raw_file_id, fetched_at)
            VALUES (%s, 'nablyudatel', 'erik', 'x', %s, '2026-01-01T00:00:00Z')""",
                     (r.run_id, rid))
    ok, out = runmod.parity(conn, st)
    assert not ok and out["extra"] == 1 and out["missing"] == 0
    problems = runmod.check_freshness(conn, st)
    assert any("ops.fetch" in p for p in problems) and any("index.jsonl" in p and "регистъра" in p for p in problems)


def test_freshness_registers_what_is_missing_and_ignores_what_is_being_written(conn, tmp_path):
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    for name in sources.SOURCES:   # every source ran well just now
        with pipeline.run(conn, register.TOOL, name):
            pass
    assert runmod.check_freshness(conn, st) == []
    assert len(fetches(conn)) == 1
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(register.STAMP)
    assert register.compare(conn, tmp_path, before="2000-01-01T00:00:00Z")["lines"] == 0
    assert register.compare(conn, tmp_path, before=later)["matched"] == 1
    empty = Store(tmp_path / "elsewhere")
    assert "no index.jsonl" in runmod.check_freshness(conn, empty)[0]


def test_a_late_or_failed_source_is_a_problem_from_its_runs(conn):
    for name in sources.SOURCES:
        with pipeline.run(conn, register.TOOL, name):
            pass
    assert runmod.freshness(register.run_states(conn, sources.SOURCES)) == []
    with pytest.raises(runmod.Failed), pipeline.run(conn, register.TOOL, "egov"):
        raise runmod.Failed("Blocked: 403")
    later = datetime.now(timezone.utc) + timedelta(hours=40)
    problems = runmod.freshness(register.run_states(conn, sources.SOURCES), later)
    assert any(p.startswith("egov") and "403" in p for p in problems)
    assert any(p.startswith("kolkostruva") and "h ago" in p for p in problems)
    assert not any(p.startswith("dfz") for p in problems)   # weekly


def test_the_move_takes_the_sha_of_the_line_and_checks_the_size(conn, tmp_path):
    st = Store(tmp_path)
    put(st, "erik", "page-1", b'{"a": 1}')
    f = tmp_path / register.index_lines(tmp_path)[0]["file"]
    f.write_bytes(b'{"a": 10}')
    with pytest.raises(pipeline.CorruptRaw), ledger(conn) as r:
        register.sync(conn, r, tmp_path, rehash=False)
    f.write_bytes(b'{"a": 1}')
    with ledger(conn) as r:
        assert register.sync(conn, r, tmp_path, rehash=False) == 1
    assert fetches(conn)[0][2] == register.index_lines(tmp_path)[0]["sha256"]


def test_lines_are_registered_in_batches_and_a_bad_batch_leaves_the_good_ones(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(register, "BATCH", 2)
    st = Store(tmp_path)
    for i in range(5):
        put(st, "erik", f"page-{i}", b'{"a": %d}' % i)
    (tmp_path / register.index_lines(tmp_path)[4]["file"]).write_bytes(b"{}")
    with pytest.raises(pipeline.CorruptRaw), ledger(conn) as r:
        register.sync(conn, r, tmp_path, rehash=False)
    assert len(fetches(conn)) == 4

"""The build against a scratch database: silver, the hold rule, the change log, the totals, gold and its checks, freshness.

Runs only when ZAKONI_TEST_DSN points at a disposable database (it is wiped):
    ZAKONI_TEST_DSN=dbname=zakoni_test pytest tests/test_store.py
"""
import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

DSN = os.environ.get("ZAKONI_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="ZAKONI_TEST_DSN not set")

FX = Path(__file__).parent / "fixtures" / "egov"
SET = "18da0fff-79b2-45c6-a9af-1509df96261b"
PRIS, ARCH, CONS = "057c5c06-a11e-4e2d-8862-189c4d06e7b7", "e01f99a7-e1e0-4b5e-be8d-d5141a41e0f2", "b600c109-9d30-4ba0-8463-9ed88a89f745"
T0 = dt.datetime(2026, 10, 2, 5, 0, tzinfo=dt.timezone.utc)


def put(root, res, data: bytes, version="7"):
    d = root / "egov" / SET / res
    for old in d.glob("*.json") if d.is_dir() else []:
        old.unlink()
    d.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(data).hexdigest()
    (d / f"{version}.{sha[:12]}.json").write_bytes(data)


@pytest.fixture
def arch(tmp_path, monkeypatch):
    from ingest import archive
    root = tmp_path / "arhiv"
    for f in FX.glob("*.json"):
        if f.name == "_list.json":
            (root / "egov" / SET / "_list").mkdir(parents=True, exist_ok=True)
            (root / "egov" / SET / "_list" / f"2026-10-02.{hashlib.sha256(f.read_bytes()).hexdigest()[:12]}.json").write_bytes(f.read_bytes())
        else:
            put(root, f.stem, f.read_bytes())
    (root / "state").mkdir(parents=True)
    set_state(root, T0)
    monkeypatch.setattr(archive, "SET_DIR", root / "egov" / SET)
    monkeypatch.setattr(archive, "STATE_FILE", root / "state" / "egov.json")
    return root


def set_state(root, when):
    (root / "state" / "egov.json").write_text(json.dumps({"last_ok": when.strftime("%Y-%m-%dT%H:%M:%SZ")}))


@pytest.fixture
def conn(tmp_path):
    from ingest import db
    with db.connect(autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS gold, silver, ref, ops CASCADE; DROP TABLE IF EXISTS public.schema_migrations")
        db.migrate(c)
        yield c


def build(conn, now=T0, force=False, fresh=True):
    from ingest import archive, gold, load
    if fresh:     # the archive reads the source every day: its clock moves with ours
        archive.STATE_FILE.write_text(json.dumps({"last_ok": now.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    st = {}
    run = conn.execute("INSERT INTO ops.job_run(step) VALUES ('build') RETURNING id").fetchone()[0]
    problems = load.build(conn, st, run, now=now, force=force)
    return problems, st, run


def count(conn, sql):
    return conn.execute(sql).fetchone()[0]


def edit(root, res, fn):
    """Rewrite one fixture answer with fn(records) -> records, keeping the legend."""
    p = next((root / "egov" / SET / res).glob("*.json"))
    d = json.loads(p.read_bytes())
    d["data"] = fn(d["data"])
    put(root, res, json.dumps(d, ensure_ascii=True, separators=(",", ":")).encode())


def test_the_first_build_reads_every_resource_and_the_counts_agree(conn, arch):
    problems, st, run = build(conn)
    assert problems == []
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'archive'") == 18
    assert count(conn, "SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL") == 30       # 31 records, one number twice
    assert count(conn, "SELECT count(*) FROM silver.strategy_doc WHERE valid_to IS NULL") == 21
    states = dict(conn.execute("SELECT ref, status FROM ops.source_state").fetchall())
    assert len(states) == 14 and set(states.values()) <= {"наред", "същият файл като Стратегически документи, стандартна справка"}
    assert count(conn, "SELECT count(*) FROM ops.reconciliation WHERE kind = 'block' AND NOT ok") == 0
    assert count(conn, "SELECT count(*) FROM ops.reconciliation WHERE name LIKE 'file-records-vs-silver:%'") == 5
    assert count(conn, "SELECT count(*) FROM ops.change_log") == 0         # a first read is not a change
    assert count(conn, "SELECT count(*) FROM ops.raw_file") == 14


def test_the_legislative_initiatives_report_is_the_strategic_documents_report(conn, arch):
    build(conn)
    s = conn.execute("SELECT status FROM ops.source_state WHERE ref = '8b718708-ab20-4c25-8d6f-133f0d5a5ce3'").fetchone()[0]
    assert s.startswith("същият файл като")
    # if the source ever gives it its own data, it is said so, not silently treated as the other report
    edit(arch, "8b718708-ab20-4c25-8d6f-133f0d5a5ce3", lambda d: d[:5])
    build(conn)
    assert conn.execute("SELECT status FROM ops.source_state WHERE ref = '8b718708-ab20-4c25-8d6f-133f0d5a5ce3'").fetchone()[0] == "собствени данни, не се зареждат"


def test_the_same_answer_again_changes_nothing_and_logs_nothing(conn, arch):
    from ingest import gold
    build(conn)
    gold.build(conn, {}, 1)
    h1 = gold_hash(conn)
    problems, st, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    gold.build(conn, {}, 2)
    assert problems == [] and count(conn, "SELECT count(*) FROM ops.change_log") == 0
    assert count(conn, "SELECT count(*) FROM silver.pris_act") == 53           # no new versions
    assert gold_hash(conn) == h1                                                # determinism: same input, same gold
    problems, st, _ = build(conn, now=T0 + dt.timedelta(hours=2), force=True)   # parsed again: still nothing
    assert count(conn, "SELECT count(*) FROM ops.change_log") == 0 and count(conn, "SELECT count(*) FROM silver.pris_act") == 53


def gold_hash(conn):
    h = hashlib.sha256()
    for t, order in (("act", "pris_id"), ("consultation", "reg_num"), ("strategy_doc", "doc_key"), ("impact_contract", "ic_key"), ("act_relation", "pris_id, ord")):
        for r in conn.execute(f"SELECT x::text FROM gold.{t} x ORDER BY {order}"):
            h.update(r[0].encode())
    return h.hexdigest()


def test_a_changed_act_is_a_new_version_and_not_a_new_act(conn, arch):
    build(conn)

    def change(d):
        for r in d[1:]:
            if r["pris_id"] == 171000:
                r["doc_about"] = "&lt;p&gt;ПОПРАВЕНО ЗАГЛАВИЕ&lt;/p&gt;"
        return d

    edit(arch, PRIS, change)
    build(conn, now=T0 + dt.timedelta(days=1))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE pris_id = 171000") == 2
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE pris_id = 171000 AND valid_to IS NULL") == 1
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35
    log = conn.execute("SELECT ref, field, new, cause FROM ops.change_log WHERE field = 'about'").fetchall()
    assert log == [("pris_act/171000", "about", "ПОПРАВЕНО ЗАГЛАВИЕ", "rewritten")]
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE cause = 'new-record'") == 0


def test_a_new_record_is_logged_and_its_children_are_not_logged_one_by_one(conn, arch):
    build(conn)

    def add(d):
        r = json.loads(json.dumps(d[1]))
        r.update(pris_id=999001, doc_num="1", tags=["един", "два"], related=None)
        return d + [r]

    edit(arch, PRIS, add)
    build(conn, now=T0 + dt.timedelta(days=1))
    log = conn.execute("SELECT ref, cause FROM ops.change_log").fetchall()
    assert log == [("pris_act/999001", "new-record")]
    assert count(conn, "SELECT count(*) FROM silver.pris_tag WHERE pris_id = 999001 AND valid_to IS NULL") == 2


def test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees(conn, arch):
    build(conn)
    edit(arch, PRIS, lambda d: [x for x in d if not (isinstance(x["pris_id"], int) and x["pris_id"] == 171000)])
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35     # nothing replaced
    assert count(conn, "SELECT count(*) FROM ops.held") == 1
    assert [r[0] for r in conn.execute("SELECT cause FROM ops.change_log")] == ["held"]
    assert conn.execute("SELECT status FROM ops.source_state WHERE ref = %s", (PRIS,)).fetchone()[0] == "held"
    # the same answer again but only hours later: still held, not confirmed
    build(conn, now=T0 + dt.timedelta(hours=5))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35
    # a day later the same file: confirmed; the record is closed, never deleted
    build(conn, now=T0 + dt.timedelta(days=1, hours=2))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 34
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE pris_id = 171000") == 1            # still there, with an end
    causes = [r[0] for r in conn.execute("SELECT cause FROM ops.change_log ORDER BY id")]
    assert causes[0] == "held" and "confirmed" in causes and "removed" in causes
    assert count(conn, "SELECT count(*) FROM ops.held") == 0


def test_a_different_second_answer_does_not_confirm_the_hold(conn, arch):
    build(conn)
    edit(arch, PRIS, lambda d: [x for x in d if x["pris_id"] != 171000])
    build(conn, now=T0 + dt.timedelta(hours=1))
    edit(arch, PRIS, lambda d: [x for x in d if x["pris_id"] not in (171000, 170993)])
    build(conn, now=T0 + dt.timedelta(days=2))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE cause = 'confirmed'") == 0


def test_a_record_that_comes_back_is_a_current_record_again(conn, arch):
    build(conn)
    keep = (arch / "egov" / SET / PRIS).glob("*.json").__next__().read_bytes()
    edit(arch, PRIS, lambda d: [x for x in d if x["pris_id"] != 171000])
    build(conn, now=T0 + dt.timedelta(hours=1))
    build(conn, now=T0 + dt.timedelta(days=1, hours=2))
    put(arch, PRIS, keep)
    build(conn, now=T0 + dt.timedelta(days=2))
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE valid_to IS NULL AND origin = 'current'") == 35
    assert count(conn, "SELECT count(*) FROM silver.pris_act WHERE pris_id = 171000") == 2


def test_an_invalid_answer_writes_nothing_and_is_reported(conn, arch):
    build(conn)
    put(arch, CONS, b"<html><body>Service unavailable</body></html>")
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("невалиден отговор" in p for p in problems)
    assert count(conn, "SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL") == 30
    assert [r[0] for r in conn.execute("SELECT cause FROM ops.change_log")] == ["invalid"]
    # a truncated file as well
    full = (FX / f"{CONS}.json").read_bytes()
    put(arch, CONS, full[: len(full) // 2])
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=2))
    assert any("отрязан" in p for p in problems) and count(conn, "SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL") == 30


def test_a_resource_missing_from_the_archive_is_reported(conn, arch):
    build(conn)
    shutil.rmtree(arch / "egov" / SET / "403043ed-7b82-477d-bb0f-ec7e0db996e9")
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("липсва" in p for p in problems)
    assert conn.execute("SELECT status FROM ops.source_state WHERE ref = '403043ed-7b82-477d-bb0f-ec7e0db996e9'").fetchone()[0] == "липсва при източника"


def test_a_damaged_file_is_refused(conn, arch):
    build(conn)
    p = next((arch / "egov" / SET / CONS).glob("*.json"))
    p.write_bytes(p.read_bytes()[:-5] + b"xxxxx")      # same name, other content: the sha256 in the name no longer matches
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("повреден" in p for p in problems)
    assert count(conn, "SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL") == 30


def test_a_stale_archive_is_refused(conn, arch):
    from ingest import archive
    set_state(arch, T0 - dt.timedelta(hours=31))
    with pytest.raises(archive.ArchiveError, match="преди повече от 30 часа"):
        build(conn, fresh=False)
    assert count(conn, "SELECT count(*) FROM silver.pris_act") == 0


def test_gold_has_the_keys_the_checks_want_and_the_rest_is_unmatched(conn, arch):
    from ingest import gold

    def add(d):                                     # the consultation that act 170203 names (12326-K) is in the source
        r = json.loads(json.dumps(d[1]))
        r["reg_num"] = "12326-K"
        return d + [r]

    edit(arch, CONS, add)
    build(conn)
    st = {}
    gold.build(conn, st, 1)
    assert st["gold"]["act"] == 53 and st["gold"]["consultation"] == 31
    a = conn.execute("SELECT consultation_reg_num, gazette_year, confidential FROM gold.act WHERE pris_id = 170203").fetchone()
    assert a == ("12326-K", 2026, False)
    # an act that names a consultation we do not have keeps the row, loses the key and is listed
    u = conn.execute("SELECT tbl, field, reason FROM gold.unmatched WHERE tbl = 'act' AND field = 'public_consultation_number'").fetchall()
    assert len(u) == 1 and u[0][2] == "консултацията я няма в справката"       # 12447-K of act 170627
    d = conn.execute("SELECT municipality_id FROM gold.consultation WHERE reg_num = '12691-K'").fetchone()[0]
    assert d == conn.execute("SELECT id FROM ref.municipality WHERE name_bg = 'Девин'").fetchone()[0]
    assert count(conn, "SELECT count(*) FROM gold.consultation WHERE municipality_id IS NOT NULL AND level <> 'Общинско'") == 0


def test_the_short_term_indicator_follows_the_rule_of_article_26(conn, arch):
    from ingest import gold

    def add(d):                                  # a non-normative act after the rule: 14 days, but no indicator
        r = json.loads(json.dumps(d[1]))
        r.update(reg_num="99999-K", act_type="Ненормативен акт на общински съвет", date_open="2026-03-01", date_close="2026-03-15", short_term_reason=None)
        return d + [r]

    edit(arch, CONS, add)
    build(conn)
    gold.build(conn, {}, 1)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT reg_num, days, short_term, short_term_applies, reason_given FROM gold.consultation")}
    assert rows["12692-K"][:3] == (30, False, False)
    assert rows["12676-K"][:3] == (14, True, True)            # 09-23 minus 09-09, a municipal act, after 04.11.2016
    assert rows["2296-K"][:3] == (14, True, False)            # opened 03.11.2016, the day before the rule came into force
    assert rows["2297-K"][2] is False                         # a non-normative act: days are shown, the indicator is not
    assert rows["99999-K"][:3] == (14, True, False)           # ... also after 04.11.2016
    assert all(not v[3] or v[2] for v in rows.values())


def test_a_check_that_finds_a_row_keeps_the_previous_gold(conn, arch):
    from ingest import gold
    build(conn)
    gold.build(conn, {}, 1)
    before = gold_hash(conn)
    conn.execute("UPDATE silver.consultation SET date_close = date_open - 3 WHERE reg_num = '12692-K'")
    with pytest.raises(gold.CheckFailed) as e:
        gold.build(conn, {}, 2)
    assert {v["check"] for v in e.value.found} == {"consultation-closes-before-opens", "consultation-days-not-positive"}
    assert gold_hash(conn) == before                         # rolled back
    assert conn.execute("SELECT ok FROM gold.build ORDER BY build_id DESC LIMIT 1").fetchone()[0] is False


def test_freshness_is_quiet_when_all_is_well_and_loud_when_not(conn, arch):
    from ingest import checks, gold
    assert any("никога" in p for p in checks.freshness(conn, now=T0))      # nothing built yet
    build(conn)
    conn.execute("UPDATE ops.job_run SET step = 'build', status = 'ok', finished_at = %s", (T0,))
    gold.build(conn, {}, 1)
    assert checks.freshness(conn, now=T0 + dt.timedelta(hours=2)) == []
    # the archive did not read the source for two days
    assert any("архивът е чел" in p for p in checks.freshness(conn, now=T0 + dt.timedelta(days=2)))
    # our build did not succeed for four days
    set_state(arch, T0 + dt.timedelta(days=4))
    assert any("последното успешно изграждане" in p for p in checks.freshness(conn, now=T0 + dt.timedelta(days=4)))
    # a hold older than two days, an invalid answer
    conn.execute("INSERT INTO ops.held (source, ref, sha256, rows, first_at) VALUES ('egov', %s, 'x', 1, %s)", (PRIS, T0))
    conn.execute("UPDATE ops.source_state SET status = 'невалиден отговор', error = 'x' WHERE ref = %s", (CONS,))
    ps = checks.freshness(conn, now=T0 + dt.timedelta(days=3))
    assert any("задържан от" in p for p in ps) and any("невалиден отговор" in p for p in ps)


def test_a_reconciliation_that_fails_stops_the_build_of_gold(conn, arch, monkeypatch):
    build(conn)
    # a source whose silver count disagrees with the file: the state says 35 rows, silver is missing one
    conn.execute("UPDATE silver.pris_act SET valid_to = now() WHERE pris_id = 171000")
    problems, _, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("неуспешно" in p for p in problems)


def test_a_write_that_loses_a_record_is_caught_by_the_count_after_it(conn, arch, monkeypatch):
    from ingest import load, store
    real = store.apply

    def lossy(conn_, ref, sha, parsed, tables, origin=None, now=None):
        out = real(conn_, ref, sha, parsed, tables, origin, now)
        if ref == CONS:
            conn_.execute("UPDATE silver.consultation SET valid_to = now() WHERE reg_num = '12692-K'")
        return out

    monkeypatch.setattr(store, "apply", lossy)
    problems, _, _ = build(conn)
    assert any("неуспешно" in p and "след записа са" in p for p in problems)

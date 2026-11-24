"""The build in the shared test database: the landing zone through the shared module, the hold rule, the change log, the
totals, the clean tables and their rules, the publication, freshness, and the move (replay and parity).

Runs only when ZAKONI_TEST_DSN points at a disposable database whose name contains 'test':
    ZAKONI_TEST_DSN=dbname=zakoni_test pytest tests/test_store.py
"""
import datetime as dt
import hashlib
import json
import shutil

import pytest

from ingest import pipeline
from tests.conftest import FX, SET, T0, build, count, edit, put, set_state

PRIS, ARCH, CONS = "057c5c06-a11e-4e2d-8862-189c4d06e7b7", "e01f99a7-e1e0-4b5e-be8d-d5141a41e0f2", "b600c109-9d30-4ba0-8463-9ed88a89f745"
CUR_ACTS = """SELECT count(*) FROM zakoni_src.pris_act WHERE origin = 'current'
              AND load_id IN (SELECT load_id FROM ops.load WHERE tool = 'zakoni' AND is_current)"""


def fields(conn):
    """The lines of the change log that say what changed in a record (not the lines of the shared module)."""
    return conn.execute("""SELECT ref, field, new, cause FROM ops.change_log WHERE tool = 'zakoni' AND ref LIKE '%%/%%'
                           AND ref NOT LIKE 'db/%%' ORDER BY change_id""").fetchall()


def state(conn, ref):
    return conn.execute("SELECT status FROM zakoni.source_state WHERE ref = %s", (ref,)).fetchone()[0]


def published(conn):
    return count(conn, "SELECT count(*) FROM pub.publication WHERE tool = 'zakoni'")


def test_the_first_build_reads_every_resource_the_counts_agree_and_it_is_published(conn, arch):
    problems, stats = build(conn)
    assert problems == []
    assert count(conn, CUR_ACTS) == 35
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act WHERE origin = 'archive'") == 18
    assert count(conn, "SELECT count(*) FROM zakoni_src.consultation") == 30       # 31 records, one number twice
    assert count(conn, "SELECT count(*) FROM zakoni_src.strategy_doc") == 21
    states = dict(conn.execute("SELECT ref, status FROM zakoni.source_state").fetchall())
    assert len(states) == 14 and set(states.values()) <= {"наред", "същият файл като Стратегически документи, стандартна справка"}
    recon = dict(conn.execute("SELECT name, ok FROM ops.check_latest WHERE tool = 'zakoni' AND name LIKE 'records%%'").fetchall())
    assert recon == {n: True for n in ("records_pris_current", "records_pris_archive", "records_consultation", "records_strategy", "records_impact")}
    # a first read is one line per resource, not one per record
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'zakoni'") == 6 and fields(conn) == []
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'zakoni' AND is_current") == 6
    assert count(conn, "SELECT count(*) FROM zakoni.act") == 53 and count(conn, "SELECT count(*) FROM pub_zakoni.act") == 53
    assert published(conn) == 1 and stats["publication"]
    # every loaded file is the file of the archive, by sha256, and the act says which
    sha = hashlib.sha256(next((arch / "egov" / SET / PRIS).glob("*.json")).read_bytes()).hexdigest()
    assert count(conn, "SELECT count(*) FROM pub_zakoni.act WHERE origin = 'current' AND raw_sha256 = %s", (sha,)) == 35


def test_the_legislative_initiatives_report_is_the_strategic_documents_report(conn, arch):
    build(conn)
    assert state(conn, "8b718708-ab20-4c25-8d6f-133f0d5a5ce3").startswith("същият файл като")
    # if the source ever gives it its own data, it is said so, not silently treated as the other report
    edit(arch, "8b718708-ab20-4c25-8d6f-133f0d5a5ce3", lambda d: d[:5])
    build(conn, now=T0 + dt.timedelta(hours=1))
    assert state(conn, "8b718708-ab20-4c25-8d6f-133f0d5a5ce3") == "собствени данни, не се зареждат"


def clean_hash(conn):
    h = hashlib.sha256()
    for t, order in (("act", "pris_id"), ("consultation", "reg_num"), ("strategy_doc", "doc_key"), ("impact_contract", "ic_key"),
                     ("act_relation", "pris_id, ord")):
        for r in conn.execute(f"SELECT x::text FROM zakoni.{t} x ORDER BY {order}"):
            h.update(r[0].encode())
    return h.hexdigest()


def test_the_same_answer_again_changes_nothing_and_logs_nothing(conn, arch):
    build(conn)
    h1, n_log = clean_hash(conn), count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'zakoni'")
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert problems == [] and count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'zakoni'") == n_log
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act") == 53                         # no second copy
    assert clean_hash(conn) == h1                                                               # same input, same tables
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'zakoni' AND status = 'unchanged'") == 6
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=2), force=True)                       # parsed again: still nothing
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'zakoni'") == n_log
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act") == 53 and clean_hash(conn) == h1
    assert published(conn) == 3      # the data did not move; the time of each read of the sources did (/sources shows it)


def test_a_changed_act_is_logged_field_by_field_and_keeps_the_day_it_was_first_seen(conn, arch):
    build(conn)

    def change(d):
        for r in d[1:]:
            if r["pris_id"] == 171000:
                r["doc_about"] = "&lt;p&gt;ПОПРАВЕНО ЗАГЛАВИЕ&lt;/p&gt;"
        return d

    edit(arch, PRIS, change)
    build(conn, now=T0 + dt.timedelta(days=1))
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act WHERE pris_id = 171000") == 1      # landing policy latest
    assert count(conn, CUR_ACTS) == 35
    assert ("pris_act/171000", "about", "ПОПРАВЕНО ЗАГЛАВИЕ", "rewritten") in fields(conn)
    assert [c for c in fields(conn) if c[3] == "new-record"] == []
    a = conn.execute("SELECT about, first_seen, last_seen FROM pub_zakoni.act WHERE pris_id = 171000").fetchone()
    assert a == ("ПОПРАВЕНО ЗАГЛАВИЕ", dt.date(2026, 10, 2), dt.date(2026, 10, 3))
    other = conn.execute("SELECT first_seen, last_seen FROM pub_zakoni.act WHERE pris_id = 170973").fetchone()
    assert other == (dt.date(2026, 10, 2), dt.date(2026, 10, 2))
    change_page = conn.execute("SELECT field, new, cause FROM pub_zakoni.act_change WHERE pris_id = 171000").fetchall()
    assert ("about", "ПОПРАВЕНО ЗАГЛАВИЕ", "rewritten") in change_page


def test_a_new_record_is_logged_and_its_children_are_not_logged_one_by_one(conn, arch):
    build(conn)

    def add(d):
        r = json.loads(json.dumps(d[1]))
        r.update(pris_id=999001, doc_num="1", tags=["един", "два"], related=None)
        return d + [r]

    edit(arch, PRIS, add)
    build(conn, now=T0 + dt.timedelta(days=1))
    assert [(c[0], c[3]) for c in fields(conn)] == [("pris_act/999001", "new-record")]
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_tag WHERE pris_id = 999001") == 2
    assert count(conn, "SELECT count(*) FROM pub_zakoni.act WHERE pris_id = 999001") == 1


def test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees(conn, arch):
    build(conn)
    edit(arch, PRIS, lambda d: [x for x in d if not (isinstance(x["pris_id"], int) and x["pris_id"] == 171000)])
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert count(conn, CUR_ACTS) == 35                                                   # nothing replaced
    assert count(conn, "SELECT count(*) FROM ops.hold WHERE tool = 'zakoni'") == 1
    assert state(conn, PRIS) == "задържан до второ четене"
    # the same answer again but only hours later: still held, not confirmed
    build(conn, now=T0 + dt.timedelta(hours=5))
    assert count(conn, CUR_ACTS) == 35
    # a day later the same file: confirmed; the record left and is logged, the act is not on the pages any more
    build(conn, now=T0 + dt.timedelta(days=1, hours=2))
    assert count(conn, CUR_ACTS) == 34
    causes = [r[0] for r in conn.execute("SELECT cause FROM ops.change_log WHERE tool = 'zakoni' ORDER BY change_id")]
    assert "held" in causes and "confirmed" in causes and ("pris_act/171000", None, None, "removed") in fields(conn)
    assert count(conn, "SELECT count(*) FROM ops.hold WHERE tool = 'zakoni'") == 0
    assert count(conn, "SELECT count(*) FROM pub_zakoni.act WHERE pris_id = 171000") == 0


def test_an_answer_that_swaps_a_record_for_another_is_held(conn, arch):
    """As many records as before, but one we hold is missing: it waits for a second read, by key, not by count."""
    build(conn)

    def swap(d):
        r = json.loads(json.dumps([x for x in d if x["pris_id"] == 171000][0]))
        r.update(pris_id=999001)
        return [x for x in d if x["pris_id"] != 171000] + [r]

    edit(arch, PRIS, swap)
    build(conn, now=T0 + dt.timedelta(hours=1))
    assert state(conn, PRIS) == "задържан до второ четене"
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act WHERE pris_id = 171000") == 1


def test_a_different_second_answer_does_not_confirm_the_hold(conn, arch):
    build(conn)
    edit(arch, PRIS, lambda d: [x for x in d if x["pris_id"] != 171000])
    build(conn, now=T0 + dt.timedelta(hours=1))
    edit(arch, PRIS, lambda d: [x for x in d if x["pris_id"] not in (171000, 170993)])
    build(conn, now=T0 + dt.timedelta(days=2))
    assert count(conn, CUR_ACTS) == 35
    assert count(conn, "SELECT count(*) FROM ops.change_log WHERE tool = 'zakoni' AND cause = 'confirmed'") == 0


def test_an_invalid_answer_writes_nothing_and_is_reported(conn, arch):
    build(conn)
    put(arch, CONS, b"<html><body>Service unavailable</body></html>")
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("невалиден отговор" in p for p in problems)
    assert count(conn, "SELECT count(*) FROM zakoni_src.consultation") == 30
    assert count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'zakoni' AND status = 'rejected'") == 1
    # a truncated file as well
    full = (FX / f"{CONS}.json").read_bytes()
    put(arch, CONS, full[: len(full) // 2])
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=2))
    assert any("отрязан" in p for p in problems) and count(conn, "SELECT count(*) FROM zakoni_src.consultation") == 30


def test_a_resource_missing_from_the_archive_is_reported(conn, arch):
    build(conn)
    shutil.rmtree(arch / "egov" / SET / "403043ed-7b82-477d-bb0f-ec7e0db996e9")
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("липсва" in p for p in problems)
    assert state(conn, "403043ed-7b82-477d-bb0f-ec7e0db996e9") == "липсва при източника"


def test_a_damaged_file_is_refused(conn, arch):
    build(conn)
    p = next((arch / "egov" / SET / CONS).glob("*.json"))
    p.write_bytes(p.read_bytes()[:-5] + b"xxxxx")      # same name, other content: the sha256 in the name no longer matches
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("повреден" in p for p in problems)
    assert count(conn, "SELECT count(*) FROM zakoni_src.consultation") == 30


def test_a_stale_archive_is_refused(conn, arch):
    from ingest import archive
    set_state(arch, T0 - dt.timedelta(hours=31))
    with pytest.raises(archive.ArchiveError, match="преди повече от 30 часа"):
        build(conn, fresh=False)
    assert count(conn, "SELECT count(*) FROM zakoni_src.pris_act") == 0


def test_the_clean_tables_have_the_keys_the_rules_want_and_the_rest_is_unmatched(conn, arch):
    def add(d):                                     # the consultation that act 170203 names (12326-K) is in the source
        r = json.loads(json.dumps(d[1]))
        r["reg_num"] = "12326-K"
        return d + [r]

    edit(arch, CONS, add)
    build(conn)
    assert count(conn, "SELECT count(*) FROM zakoni.act") == 53 and count(conn, "SELECT count(*) FROM zakoni.consultation") == 31
    a = conn.execute("SELECT consultation_reg_num, gazette_year, confidential FROM zakoni.act WHERE pris_id = 170203").fetchone()
    assert a == ("12326-K", 2026, False)
    # an act that names a consultation we do not have keeps the row, loses the key and is listed
    u = conn.execute("SELECT reason FROM zakoni.unmatched WHERE tbl = 'act' AND field = 'public_consultation_number'").fetchall()
    assert u == [("консултацията я няма в справката",)]       # 12447-K of act 170627
    d = conn.execute("SELECT municipality_id FROM pub_zakoni.consultation WHERE reg_num = '12691-K'").fetchone()[0]
    assert d == conn.execute("SELECT eik FROM pub.municipality WHERE name_bg = 'Девин'").fetchone()[0]
    assert count(conn, "SELECT count(*) FROM zakoni.consultation WHERE municipality_code IS NOT NULL AND level <> 'Общинско'") == 0


def test_the_short_term_indicator_follows_the_rule_of_article_26(conn, arch):
    def add(d):                                  # a non-normative act after the rule: 14 days, but no indicator
        r = json.loads(json.dumps(d[1]))
        r.update(reg_num="99999-K", act_type="Ненормативен акт на общински съвет", date_open="2026-03-01", date_close="2026-03-15", short_term_reason=None)
        return d + [r]

    edit(arch, CONS, add)
    build(conn)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT reg_num, days, short_term, short_term_applies, reason_given FROM zakoni.consultation")}
    assert rows["12692-K"][:3] == (30, False, False)
    assert rows["12676-K"][:3] == (14, True, True)            # 09-23 minus 09-09, a municipal act, after 04.11.2016
    assert rows["2296-K"][:3] == (14, True, False)            # opened 03.11.2016, the day before the rule came into force
    assert rows["2297-K"][2] is False                         # a non-normative act: days are shown, the indicator is not
    assert rows["99999-K"][:3] == (14, True, False)           # ... also after 04.11.2016
    assert all(not v[3] or v[2] for v in rows.values())


def test_a_rule_that_fails_keeps_the_published_version(conn, arch):
    from ingest import run
    build(conn)
    before = conn.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'zakoni'").fetchone()[0]
    conn.execute("UPDATE zakoni_src.consultation SET date_close = date_open - 3 WHERE reg_num = '12692-K'")
    with pipeline.run(conn, "zakoni", "check") as r:
        problems = run.finish(conn, r, [])
    assert any("приключват преди да са открити" in p for p in problems)
    assert conn.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = 'zakoni'").fetchone()[0] == before
    assert conn.execute("SELECT closes - opened FROM pub_zakoni.consultation WHERE reg_num = '12692-K'").fetchone()[0] == 30


def test_freshness_is_quiet_when_all_is_well_and_loud_when_not(conn, arch):
    from ingest import checks
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    assert any("няма успешно четене" in p for p in checks.freshness(conn, now=now))     # nothing read yet
    build(conn, now=now)
    assert checks.freshness(conn, now=now) == []
    # the archive did not read the source for two days
    assert any("архивът е чел" in p for p in checks.freshness(conn, now=now + dt.timedelta(days=2)))
    # a hold older than two days, an invalid answer
    conn.execute("""INSERT INTO ops.hold (tool, source, ref, content_sha256, rows, reason, first_at)
                    VALUES ('zakoni', 'egov', %s, 'x', 1, 'липсват 1 от 35 реда', %s)""", (PRIS, now - dt.timedelta(days=3)))
    conn.execute("UPDATE zakoni.source_state SET status = 'невалиден отговор', error = 'x' WHERE ref = %s", (CONS,))
    ps = checks.freshness(conn, now=now)
    assert any("задържан от" in p for p in ps) and any("невалиден отговор" in p for p in ps)


def test_a_count_that_does_not_agree_stops_the_build_and_the_publication(conn, arch, monkeypatch):
    from ingest import store
    build(conn)
    before = clean_hash(conn)
    real = store.held_count
    monkeypatch.setattr(store, "held_count", lambda c, ref, parent: real(c, ref, parent) - (ref == PRIS))
    problems, _ = build(conn, now=T0 + dt.timedelta(hours=1))
    assert any("неуспешно" in p for p in problems) and any("нищо не е изградено" in p for p in problems)
    assert clean_hash(conn) == before and published(conn) == 1


def test_a_write_that_loses_a_record_is_caught_by_the_count_after_it(conn, arch, monkeypatch):
    from ingest import store
    real = store.writer

    def lossy(r, parsed, tables, ref, now):
        write = real(r, parsed, tables, ref, now)

        def w(c, load_id, rows):
            write(c, load_id, rows)
            if ref == CONS:
                c.execute("DELETE FROM zakoni_src.consultation WHERE load_id = %s AND reg_num = '12692-K'", (load_id,))
        return w

    monkeypatch.setattr(store, "writer", lossy)
    problems, _ = build(conn)
    assert any("неуспешно" in p and "след записа са" in p for p in problems)
    assert published(conn) == 0


def test_a_gazette_year_that_cannot_be_right_and_a_relation_to_itself_stay_off_the_pages(conn, arch):
    """Both were in the real register on 02.10.2026 (act 68303: year "1019", act 19160: 2003 for 2005, act 76245 related to itself)."""
    def bad(d):
        for r in d[1:]:
            if r["pris_id"] == 170203:
                r["state_gazette_year"] = "1019"
            if r["pris_id"] == 171000:
                r["related"] = [{"relation_type": "Изменен от", "pris_id": 171000, "act_type": "Решение", "act_name": "самият акт"}]
        return d

    edit(arch, PRIS, bad)
    problems, _ = build(conn)
    assert problems == []
    assert conn.execute("SELECT gazette_number, gazette_year FROM pub_zakoni.act WHERE pris_id = 170203").fetchone() == (None, None)
    assert conn.execute("SELECT gazette_year_raw, gazette_year FROM zakoni_src.pris_act WHERE pris_id = 170203").fetchone() == ("1019", 1019)
    assert count(conn, "SELECT count(*) FROM pub_zakoni.act_relation WHERE pris_id = related_pris_id") == 0
    u = conn.execute("SELECT tbl, field FROM zakoni.unmatched WHERE split_part(key, '/', 1) IN ('170203', '171000') AND field IN ('gazette', 'related_pris_id') ORDER BY tbl").fetchall()
    assert u == [("act", "gazette"), ("act_relation", "related_pris_id")]
    assert conn.execute("SELECT gazette_number, gazette_year FROM pub_zakoni.act WHERE pris_id = 170973").fetchone() == (84, 2026)


def test_a_new_version_of_the_contracts_with_the_price_in_euro_is_loaded_and_the_source_price_is_kept(conn, arch):
    """Version 5 of the report (12.11.2026) gives the price in euro too; the build takes it, and an older read had none."""
    build(conn)
    assert count(conn, "SELECT count(*) FROM zakoni_src.impact_contract WHERE price_eur IS NOT NULL") == 0
    before = conn.execute("SELECT price_eur FROM zakoni.impact_contract WHERE eik = '121026679' AND price_bgn = 31680").fetchone()[0]
    put(arch, "043ba0e4-18e1-4880-8a1f-ea739103dca4", (FX / "v5" / "043ba0e4-18e1-4880-8a1f-ea739103dca4.json").read_bytes(), version="5")
    problems, _ = build(conn, now=T0 + dt.timedelta(days=1, hours=1))
    assert not any("невалиден" in p for p in problems)
    assert state(conn, "043ba0e4-18e1-4880-8a1f-ea739103dca4") == "задържан до второ четене"   # the fixture has fewer records
    build(conn, now=T0 + dt.timedelta(days=2, hours=2))
    assert count(conn, "SELECT count(*) FROM zakoni_src.impact_contract WHERE price_eur IS NOT NULL") == 6
    rows = conn.execute("SELECT price_bgn, price_eur FROM zakoni.impact_contract WHERE eik = '121026679' ORDER BY price_bgn").fetchall()
    assert [(str(a), str(b)) for a, b in rows] == [("20340.00", "10399.68"), ("31680.00", "16197.73")]
    assert str(before) == "16197.73"                       # the old conversion by the fixed rate gave the same number
    assert count(conn, "SELECT actual FROM ops.check_latest WHERE tool = 'zakoni' AND name = 'impact_eur_is_the_fixed_rate'") == 0


def test_the_reference_list_is_a_source_of_its_own(conn, arch):
    build(conn)
    from ingest.config import ROOT
    lines = len((ROOT / "db" / "ref" / "institution_municipality.csv").read_text(encoding="utf-8").strip().splitlines()) - 1
    n = count(conn, "SELECT count(*) FROM zakoni_src.institution_municipality")
    assert n == lines > 50 and count(conn, "SELECT count(*) FROM ops.load WHERE tool = 'zakoni' AND source = 'ref' AND is_current") == 1
    assert count(conn, "SELECT actual FROM ops.check_latest WHERE tool = 'zakoni' AND name = 'institution_municipality_known'") == 0


# ---------------------------------------------------------------- the move

def old_database(conn, arch):
    """The old database of Закони in schemas silver, gold, ref and old_ops of the test database, as its code left it: one
    read, every row current. It is made from a build of the new code and checked to be its mirror, so the replay must
    give it back exactly."""
    from ingest.config import ROOT
    for f in ("0001_init.sql", "0002_gold.sql"):
        sql = (ROOT / "db" / "legacy" / f).read_text(encoding="utf-8")
        conn.execute(sql.replace("ops.", "old_ops.").replace("SCHEMA IF NOT EXISTS ops", "SCHEMA IF NOT EXISTS old_ops"))
    from ingest.schema import TABLES
    for t, (key, data) in TABLES.items():
        cols = ", ".join([*key, *(c for c in data if c != "price_eur")])     # the old silver predates the price in euro
        conn.execute(f"""INSERT INTO silver.{t} ({cols}, valid_from, row_sha, raw_sha256)
            SELECT {cols}, %s, 'x', 'x' FROM zakoni_src.{t}""", (T0,))
    for t in ("act", "act_institution", "act_relation", "consultation", "strategy_doc"):
        conn.execute(f"INSERT INTO gold.{t} SELECT * FROM pub_zakoni.{t}")
    conn.execute("INSERT INTO gold.impact_contract SELECT * FROM zakoni.impact_contract")
    conn.execute("INSERT INTO gold.build (ok) VALUES (true)")
    conn.execute("INSERT INTO gold.unmatched SELECT 1, tbl, key, field, value, reason FROM zakoni.unmatched")
    for p in sorted((arch / "egov" / SET).glob("*/*.json")):
        if p.parent.name != "_list":
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            conn.execute("INSERT INTO old_ops.raw_file (source, ref, path, sha256, bytes, fetched_at) VALUES ('egov', %s, %s, %s, %s, %s)",
                         (p.parent.name, "/opt/tender/arhiv/egov/" + SET + "/" + p.parent.name + "/" + p.name, sha, p.stat().st_size, T0))


def test_replay_and_parity_against_the_old_database(conn, dsn, arch, monkeypatch):
    from ingest import move, run
    build(conn)
    old_database(conn, arch)
    # the shared tables of Закони again empty: everything comes back from the files
    from tests.conftest import TABLES
    conn.execute(f"TRUNCATE {', '.join(TABLES)}")
    conn.execute("DELETE FROM pub.publication WHERE tool = 'zakoni'")
    for table in ("check_result", "change_log", "hold", "fetch", "load"):
        conn.execute(f"DELETE FROM ops.{table} WHERE tool = 'zakoni'")
    monkeypatch.setattr(move, "_old", lambda d: _schemas(dsn))
    with pipeline.run(conn, "zakoni", "replay") as r:
        assert run.finish(conn, r, move.replay(conn, r, dsn)) == []
    assert conn.execute("SELECT min(loaded_at) FROM ops.load WHERE tool = 'zakoni' AND source = 'egov'").fetchone()[0] == T0
    with pipeline.run(conn, "zakoni", "parity") as r:
        move.parity(conn, r, dsn)
    got = {n: (e, a) for n, e, a in conn.execute("SELECT name, expected, actual FROM ops.check_latest WHERE tool = 'zakoni' AND kind = 'parity'")}
    assert got["parity_act"] == (53, 53) and got["parity_src_pris_tag"][0] == got["parity_src_pris_tag"][1] > 0
    assert all(a == 0 for n, (e, a) in got.items() if n.endswith("_differ")), {n: v for n, v in got.items() if v[1] and n.endswith("_differ")}
    # one changed field of the old gold is found
    conn.execute("UPDATE gold.consultation SET days = days + 1 WHERE reg_num = '12692-K'")
    with pipeline.run(conn, "zakoni", "parity") as r:
        move.parity(conn, r, dsn)
    assert count(conn, "SELECT actual FROM ops.check_latest WHERE tool = 'zakoni' AND name = 'parity_consultation_differ'") == 1
    assert count(conn, "SELECT actual FROM ops.check_latest WHERE tool = 'zakoni' AND name = 'parity_periods_differ'") == 1


def test_replay_refuses_an_old_database_with_history(conn, dsn, arch, monkeypatch):
    from ingest import move
    build(conn)
    old_database(conn, arch)
    conn.execute("UPDATE silver.pris_act SET valid_to = now() WHERE pris_id = 171000")
    monkeypatch.setattr(move, "_old", lambda d: _schemas(dsn))
    with pytest.raises(RuntimeError, match="затворени версии"):
        with pipeline.run(conn, "zakoni", "replay") as r:
            move.replay(conn, r, dsn)


def _schemas(dsn):
    """The old database inside the test database: its ops is old_ops here."""
    import psycopg
    return _Renamed(psycopg.connect(dsn, autocommit=True))


class _Renamed:
    """A connection that reads ops.raw_file of the old database from old_ops.raw_file."""

    def __init__(self, c):
        self.c = c

    def execute(self, sql, *a):
        return self.c.execute(sql.replace("FROM ops.raw_file", "FROM old_ops.raw_file"), *a)

    def __enter__(self):
        return self

    def __exit__(self, *e):
        self.c.close()

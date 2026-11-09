"""The landing zone and the clean tables against the test database, through the shared module: the hold rule, a broken
answer, an election that leaves the register, the check of what is written, and that no person's name is stored.
Runs on the server and in CI."""
import copy
import datetime as dt
import re
from decimal import Decimal

import pytest

from ingest import archive, parse, pipeline, run, store
from tests import helpers
from tests.helpers import DONORS, FIX, add, donors_without, dump, js, raw, read_again, utc

D = Decimal


def q(c, sql, *args):
    return c.execute(sql, args).fetchall()


def one(c, sql, *args):
    return c.execute(sql, args).fetchone()


def start(c, tmp_path, erik_id=96):
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == erik_id)
    assert helpers.build(c, root)["problems"] == []
    return root


def shown(c, erik_id=96):
    return one(c, "SELECT records_total, total_money FROM partii.as_of('infinity') WHERE tbl = 'donors' AND erik_id = %s", erik_id)


def statuses(c, source="donors", ref="96"):
    return [r[0] for r in q(c, "SELECT status FROM partii.source_state WHERE source = %s AND ref = %s ORDER BY load_id", source, ref)]


def test_a_donor_with_a_legal_form_is_named_and_a_person_never_is(c, tmp_path):
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or (e["erik_id"] == 96 and e["table"] != "donors"))
    d = js("donors", 96)
    d["data"][0]["fullName"] = "АЛФА КОНСУЛТ ЕООД"
    add(root, DONORS.format(96), dump(d), utc(2026, 11, 8))
    helpers.build(c, root)
    assert q(c, "SELECT donor_kind, donor_name FROM partii.as_of('infinity') s JOIN partii.donation USING (load_id) WHERE s.tbl = 'donors' ORDER BY row_no") == \
        [("legal", "АЛФА КОНСУЛТ ЕООД"), ("person", None), ("person", None)]
    with pytest.raises(Exception, match="donation_no_person_ck"):      # the table itself refuses a named person
        c.execute("UPDATE partii_src.donation SET donor_name = 'Иван Иванов' WHERE donor_kind = 'person'")


def test_a_second_run_changes_nothing(c):
    helpers.build(c, FIX)
    before, log = helpers.public_hash(c), one(c, "SELECT count(*), max(change_id) FROM ops.change_log WHERE tool = 'partii'")
    rep = helpers.build(c, FIX)
    assert rep["read"] == 0 and rep["loaded"] == 0 and rep["problems"] == []
    assert helpers.public_hash(c) == before and one(c, "SELECT count(*), max(change_id) FROM ops.change_log WHERE tool = 'partii'") == log


def test_two_builds_from_the_same_archive_give_the_same_tables(c):
    from tests.conftest import reset
    helpers.build(c, FIX)
    first = helpers.public_hash(c)
    reset(c)
    helpers.build(c, FIX)
    assert helpers.public_hash(c) == first


def test_a_report_in_leva_that_matches_the_register_only_in_euro_leaves_the_currency_unknown(c, tmp_path, monkeypatch):
    import ingest.config as config
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == 79)
    monkeypatch.setattr(config, "EURO_FROM", dt.date(2024, 1, 1))      # as if the rule said "euro" for an election whose reports are in leva
    rep = helpers.build(c, root)
    assert any("валута не е установена" in p for p in rep["problems"])
    assert one(c, "SELECT report_currency FROM partii.election WHERE erik_id = 79")[0] is None
    assert one(c, """SELECT count(*) FROM partii.participant_money m JOIN partii.participant p USING (participant_id)
                     WHERE p.erik_id = 79 AND m.report_income IS NOT NULL AND m.report_income_eur IS NULL""")[0] > 0      # the original stays, no euro is made up
    assert any("валута не е установена" in p for p in pipeline.freshness(c, "partii"))


# ---------- a smaller, a broken, a vanished answer ----------

def test_a_smaller_answer_is_held_until_a_later_read_gives_the_same(c, tmp_path):
    root = start(c, tmp_path)
    t = utc(2026, 11, 10)
    add(root, DONORS.format(96), dump(donors_without(js("donors", 96), 3)), t)
    rep = helpers.build(c, root)
    assert rep["held"] == 1 and rep["confirmed"] == 0 and rep["problems"] == []
    assert shown(c) == (3, D("1500.00"))                                                  # what was shown stays
    assert statuses(c)[-1] == "held" and one(c, "SELECT reason FROM ops.hold WHERE tool = 'partii' AND source = 'donors'")[0].startswith("липсват 1 от")
    read_again(root, t + dt.timedelta(hours=10))                                          # a read too soon does not confirm
    assert helpers.build(c, root)["confirmed"] == 0 and shown(c)[0] == 3
    read_again(root, t + dt.timedelta(hours=21))                                          # a read a day later with the same answer does
    assert helpers.build(c, root)["confirmed"] == 1
    assert shown(c) == (2, D("1000.00")) and statuses(c)[-1] == "confirmed"
    assert one(c, "SELECT count(*) FROM ops.change_log WHERE tool = 'partii' AND source = 'donors' AND cause = 'confirmed'")[0] == 1
    assert one(c, "SELECT count(*) FROM ops.change_log WHERE tool = 'partii' AND source = 'donors' AND cause = 'removed'")[0] == 1      # the row that left, logged by its identity
    assert one(c, "SELECT records_total FROM partii.as_of(%s) WHERE tbl = 'donors' AND erik_id = 96", t + dt.timedelta(hours=12))[0] == 3      # history: it was not shown then
    assert q(c, "SELECT name FROM ops.check_latest WHERE tool = 'partii' AND ok IS NOT TRUE AND gating") == []


def test_a_held_answer_that_the_source_takes_back_is_never_shown(c, tmp_path):
    root = start(c, tmp_path)
    t = utc(2026, 11, 10)
    add(root, DONORS.format(96), dump(donors_without(js("donors", 96), 3)), t)
    helpers.build(c, root)
    add(root, DONORS.format(96), raw("donors", 96).replace(b'"draw":1', b'"draw": 1'), t + dt.timedelta(hours=24))      # the three rows are back
    rep = helpers.build(c, root)
    assert rep["unchanged"] >= 1 and rep["confirmed"] == 0 and shown(c) == (3, D("1500.00"))
    assert one(c, "SELECT count(*) FROM ops.hold WHERE tool = 'partii'")[0] == 0 and "confirmed" not in statuses(c)


def test_a_corrected_row_in_a_register_that_did_not_shrink_is_accepted_and_logged(c, tmp_path):
    root = start(c, tmp_path)
    d = js("donors", 96)
    d["data"][0].update(donationPriceTxt="700.00", donationPrice=700.0)                    # the source has no row number: a corrected sum is another key
    d["additionalParameters"]["TotalDonationPrice"] = 1700.0
    add(root, DONORS.format(96), dump(d), utc(2026, 11, 10))
    rep = helpers.build(c, root)
    assert rep["held"] == 0 and rep["loaded"] == 1 and shown(c) == (3, D("1700.00"))      # so the days of a campaign are not lost
    assert q(c, """SELECT cause, count(*) FROM ops.change_log WHERE tool = 'partii' AND source = 'donors' AND field ~ '^[0-9a-f]{20}/[0-9]+$'
                   AND detected_at = %s GROUP BY 1 ORDER BY 1""", utc(2026, 11, 10)) == [("new-record", 1), ("removed", 1)]
    assert one(c, "SELECT count(*) FROM partii.reading WHERE tbl = 'donors' AND erik_id = 96")[0] == 3


def test_a_lower_total_with_the_same_number_of_rows_is_held(c, tmp_path):
    root = start(c, tmp_path)
    d = js("donors", 96)
    d["data"][0].update(donationPriceTxt="100.00", donationPrice=100.0)
    d["additionalParameters"]["TotalDonationPrice"] = 1100.0
    add(root, DONORS.format(96), dump(d), utc(2026, 11, 10))
    assert helpers.build(c, root)["held"] == 1 and shown(c) == (3, D("1500.00"))


def test_an_election_read_only_on_sunday_waits_for_the_next_sunday(c, tmp_path):
    root = start(c, tmp_path, 79)                                                          # 79 is the 17th of the list: read weekly
    t = utc(2026, 11, 10)
    add(root, DONORS.format(79), dump(donors_without(js("donors", 79), 1)), t)
    helpers.build(c, root)
    read_again(root, t + dt.timedelta(hours=30))                                          # a daily run of the archive does not read this election
    assert helpers.build(c, root)["confirmed"] == 0 and shown(c, 79)[0] == 39
    read_again(root, t + dt.timedelta(days=5), full=True)                                 # the Sunday run does
    assert helpers.build(c, root)["confirmed"] == 1 and shown(c, 79)[0] == 38


def test_a_broken_answer_is_recorded_and_nothing_of_it_is_written(c, tmp_path):
    root = start(c, tmp_path)
    n = one(c, "SELECT count(*) FROM partii_src.donation")[0]
    t = utc(2026, 11, 10)
    add(root, DONORS.format(96), b"<!DOCTYPE html><html><body>Service Unavailable</body></html>", t)
    rep = helpers.build(c, root)
    assert rep["rejected"] == 1 and "HTML страница вместо таблица" in rep["problems"][0]
    assert any("последен отговор е отхвърлен" in p for p in rep["problems"])              # the rule says the shown data is older than the source
    assert shown(c) == (3, D("1500.00")) and one(c, "SELECT count(*) FROM partii_src.donation")[0] == n and statuses(c)[-1] == "rejected"
    assert one(c, "SELECT ok FROM ops.check_latest WHERE tool = 'partii' AND name = 'last_answer_valid'")[0] is False
    again = helpers.build(c, root)
    assert again["read"] == 0 and again["rejected"] == 0                                   # it is not read again and again
    add(root, DONORS.format(96), raw("donors", 96).replace(b'"draw":1', b'"draw": 1'), t + dt.timedelta(days=1))      # the source answers well again
    d = js("donors", 96)
    d["data"].append(copy.deepcopy(d["data"][0]))
    d["recordsTotal"] = d["recordsFiltered"] = 4
    d["additionalParameters"]["TotalDonationPrice"] += d["data"][0]["donationPrice"]
    add(root, DONORS.format(96), dump(d), t + dt.timedelta(days=2))                        # and then with a new row
    assert helpers.build(c, root)["problems"] == [] and shown(c)[0] == 4
    assert one(c, "SELECT ok FROM ops.check_latest WHERE tool = 'partii' AND name = 'last_answer_valid'")[0] is True


def test_nothing_is_published_while_a_rule_fails(c, tmp_path):
    root = start(c, tmp_path)
    state = lambda: one(c, "SELECT (SELECT max(publication_id) FROM pub.publication WHERE tool = 'partii'), (SELECT count(*) FROM pub_partii.source_state), (SELECT count(*) FROM pub_partii.donation)")
    before = state()
    assert before[0] is not None and before[2] == 3
    t = utc(2026, 11, 10)
    add(root, DONORS.format(96), b"<!DOCTYPE html><html><body>Service Unavailable</body></html>", t)
    helpers.build(c, root)                                                                 # the rule about the last answer fails
    assert state() == before                                                               # the pages keep the version they had
    d = js("donors", 96)
    d["data"].append(copy.deepcopy(d["data"][0]))
    d["recordsTotal"] = d["recordsFiltered"] = 4
    d["additionalParameters"]["TotalDonationPrice"] += d["data"][0]["donationPrice"]
    add(root, DONORS.format(96), dump(d), t + dt.timedelta(days=1))
    assert helpers.build(c, root)["problems"] == []
    after = state()
    assert after[0] > before[0] and after[2] == 7                                          # both readings, 3 and 4 rows, are published
    assert one(c, "SELECT count(*) FROM pub_partii.donation WHERE donor_name IS NOT NULL")[0] == 0


def test_an_answer_whose_rows_do_not_add_up_to_its_total_is_not_published(c, tmp_path):
    root = start(c, tmp_path, 81)
    d = js("donors", 81)
    d["data"].append(copy.deepcopy(d["data"][0]))                                          # a sixth row, the counter and the total say five
    add(root, DONORS.format(81), dump(d), utc(2026, 11, 10))
    rep = helpers.build(c, root)
    assert rep["rejected"] == 1 and "източникът брои 5" in rep["problems"][0] and shown(c, 81)[0] == 5


def test_what_is_written_is_checked_against_the_source_inside_the_same_transaction(c, tmp_path, monkeypatch):
    real = store.writer

    def lossy(e):      # a writer that loses the non-monetary value on the way to the table
        write = real(e)
        if e["table"] != "donors":
            return write
        return lambda conn, load_id, rows: write(conn, load_id, {k: r if k == "_answer" else dict(r, in_kind="0", kind="money") for k, r in rows.items()})
    monkeypatch.setattr(store, "writer", lossy)
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == 81)
    rep = helpers.build(c, root)
    assert rep["rejected"] == 1 and any("непарични 0" in p and "източникът дава" in p for p in rep["problems"])
    assert one(c, "SELECT count(*) FROM partii_src.donation")[0] == 0 and shown(c, 81) is None
    assert one(c, "SELECT count(*) FROM partii_src.answer WHERE source = 'donors'")[0] == 0      # the whole load went back, not only the rows


def test_an_election_that_leaves_the_register_stays_with_its_rows(c, tmp_path):
    root = start(c, tmp_path)
    page = raw("home").decode()
    gone = re.sub(r'<a[^>]*href="/Reports\?electionId=96"[^>]*>.*?</a>', "", page, flags=re.S)
    assert gone != page
    t = utc(2026, 11, 10)
    add(root, "home", gone.encode(), t, ext="html")
    rep = helpers.build(c, root)                                                           # a shorter list is held like any smaller answer
    assert rep["held"] == 1 and rep["problems"] == [] and one(c, "SELECT gone_at FROM partii.election WHERE erik_id = 96")[0] is None
    read_again(root, t + dt.timedelta(hours=21))
    rep = helpers.build(c, root)
    notices = [p for p in rep["problems"] if "вече не е в регистъра" in p]
    assert len(notices) == 1 and "(96)" in notices[0]
    assert one(c, "SELECT gone_at FROM partii.election WHERE erik_id = 96")[0] == t + dt.timedelta(hours=21)
    assert shown(c) == (3, D("1500.00")) and one(c, "SELECT count(*) FROM partii.participant WHERE erik_id = 96")[0] > 0
    assert helpers.build(c, root)["problems"] == []                                        # told once, by the run that saw it
    add(root, "home", raw("home") + b"<!-- back -->", t + dt.timedelta(days=2), ext="html")
    helpers.build(c, root)
    assert one(c, "SELECT gone_at FROM partii.election WHERE erik_id = 96")[0] is None


def test_the_same_report_in_a_new_file_is_not_a_new_report(c, tmp_path):
    root = start(c, tmp_path, 93)
    assert one(c, "SELECT count(*) FROM partii.reading WHERE tbl = 'report'")[0] == 3
    key = "GET https://erik.bulnao.government.bg/Reports/AfterElectionExport/?electionId=93&participantTypeId=0"
    add(root, key, raw("report", 93, "0") + b"\0", utc(2026, 11, 10), ext="zip")           # another file, the same cells
    rep = helpers.build(c, root)
    assert rep["read"] == 1 and rep["unchanged"] == 1 and one(c, "SELECT count(*) FROM partii.reading WHERE tbl = 'report'")[0] == 3
    assert statuses(c, "report", "93/0") == ["loaded", "unchanged"]


def test_a_text_that_two_participants_share_links_to_neither(c, tmp_path):
    start(c, tmp_path, 81)
    label, pid = one(c, """SELECT u.participant_label, u.participant_id FROM partii.as_of('infinity') s JOIN partii.agency_use u USING (load_id)
                           WHERE u.participant_id IS NOT NULL ORDER BY u.row_no LIMIT 1""")
    linked = lambda: one(c, "SELECT count(*), count(participant_id), min(participant_id) FROM partii.agency_use WHERE participant_label = %s", label)
    n = linked()[0]
    assert linked() == (n, n, pid)                                                         # one participant has this text: the rows are its
    c.execute("""INSERT INTO partii.participant (erik_id, reg_in, source_id, kind, name, name_norm, label, first_seen_at, last_seen_at)
                 SELECT erik_id, reg_in, source_id + 900000, kind, name, name_norm, label, first_seen_at, last_seen_at FROM partii.participant WHERE participant_id = %s""", (pid,))
    store.build(c)
    assert linked() == (n, 0, None)                                                        # two have it: nothing is guessed
    assert one(c, "SELECT row_count FROM partii.unmatched WHERE kind = 'participant:agencies' AND key = %s", f"81 | {label}")[0] == n


def test_changes_between_two_reads_are_logged_by_the_identity_of_the_row(c, tmp_path):
    start(c, tmp_path, 95)
    log = q(c, "SELECT cause, count(*) FROM ops.change_log WHERE tool = 'partii' AND source = 'donors' AND ref = '95' AND field ~ '^[0-9a-f]{20}/[0-9]+$' GROUP BY 1")
    assert log == [("new-record", 45)]                                                     # 2 rows in the second read, 43 more in the third; none left
    assert "Физическо лице" not in helpers.all_text(c)


def test_a_clean_table_that_does_not_add_up_fails_its_rule(c, tmp_path, monkeypatch):
    root = start(c, tmp_path, 81)
    broken = tmp_path / "build"
    broken.mkdir()
    for f in store.BUILD.glob("*.sql"):
        (broken / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    sql = (broken / "20_money.sql").read_text(encoding="utf-8")
    (broken / "20_money.sql").write_text(sql.replace("sum(d.in_kind) AS v,", "sum(d.in_kind) * 0 AS v,", 1), encoding="utf-8")
    monkeypatch.setattr(store, "BUILD", broken)
    with pipeline.run(c, "partii", "build") as r:
        store.build(c)
        problems = pipeline.run_checks(c, r)
    assert any("не дават брояча и двете суми" in p for p in problems)
    assert any("не дават брояча и двете суми" in p for p in pipeline.freshness(c, "partii"))


# ---------- the step and its freshness ----------

def test_the_step_refuses_an_archive_that_was_not_read_lately(c, tmp_path):
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == 96)
    read_again(root, dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40))
    rep = helpers.build(c, root, any_age=False)
    assert "Архивът е остарял" in rep["problems"][0] and one(c, "SELECT count(*) FROM partii.election")[0] == 0
    read_again(root, dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))
    rep = helpers.build(c, root, any_age=False)
    assert rep["problems"] == [] and rep["clean"]["election"] == 21 and shown(c) == (3, D("1500.00"))


def test_freshness_is_quiet_when_all_is_well_and_tells_when_the_archive_stops(c, tmp_path):
    root = helpers.copy(tmp_path, keep=lambda e: e["table"] == "home" or e["erik_id"] == 96)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    read_again(root, now - dt.timedelta(hours=2))
    assert helpers.build(c, root)["problems"] == []
    assert pipeline.freshness(c, "partii") == []                                           # the list of elections was looked at two hours ago
    assert statuses(c, "home", "list") == ["loaded", "unchanged"]
    c.execute("UPDATE ops.load SET loaded_at = loaded_at - interval '40 hours' WHERE tool = 'partii' AND source = 'home'")      # as if the archive stopped reading
    late = pipeline.freshness(c, "partii")
    assert len(late) == 1 and "списъкът на изборите" in late[0]
    c.execute("UPDATE partii_src.donation SET money = money + 1 WHERE row_no = 1")         # a number that no longer agrees with the source
    with pipeline.run(c, "partii", "check") as r:
        assert any("дарителите" in p for p in pipeline.run_checks(c, r))


def test_an_empty_database_says_so_and_still_answers(c):
    assert pipeline.freshness(c, "partii") != []
    from fastapi.testclient import TestClient
    from app import main
    cl = TestClient(main.app)
    for url in ("/", "/izbori", "/uchastnici", "/agencii", "/sources", "/how", "/healthz", "/sources.json", "/export-izbori.csv"):
        assert cl.get(url).status_code == 200, url
    assert "няма прочетени данни" in cl.get("/").text

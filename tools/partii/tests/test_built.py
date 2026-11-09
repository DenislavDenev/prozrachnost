"""What the whole fixture archive gives once it is built: the totals of the source, history, identities, links, the clean
tables. Read-only tests on one build (the `built` fixture); the tests that change the archive are in test_store.py."""
import datetime as dt
from decimal import Decimal

from ingest import pipeline
from tests import helpers
from tests.helpers import utc

D = Decimal


def q(c, sql, *args):
    return c.execute(sql, args).fetchall()


def one(c, sql, *args):
    return c.execute(sql, args).fetchone()


def test_the_fixture_archive_builds_and_every_total_is_the_source_s(built):
    c, rep = built
    assert rep["problems"] == [] and rep["rejected"] == 0 and rep["read"] == 54
    assert one(c, "SELECT count(*) FROM partii.election")[0] == 21
    got = {r[0]: r[1:] for r in q(c, "SELECT erik_id, records_total, total_money, total_in_kind FROM partii.as_of('infinity') WHERE tbl = 'donors'")}
    assert got == {79: (39, D("35369.1271736296099356"), D(0)), 81: (5, D("3067.7512871773109115"), D("1227.1005148709243646")),
                   92: (1, D("243.60"), D(0)), 95: (45, D("190000.00"), D(0)), 96: (3, D("1500.00"), D(0))}
    for eid, (n, money, in_kind) in got.items():      # what is written is what the source counts and adds, money and non-monetary apart
        assert one(c, """SELECT count(*), coalesce(sum(money), 0), coalesce(sum(in_kind), 0) FROM partii.as_of('infinity') s JOIN partii.donation d USING (load_id)
                         WHERE s.tbl = 'donors' AND s.erik_id = %s""", eid) == (n, money, in_kind)
    assert q(c, "SELECT name FROM ops.check_latest WHERE tool = 'partii' AND ok IS NOT TRUE AND gating") == []
    assert one(c, "SELECT count(*) FROM ops.check_latest WHERE tool = 'partii' AND ok")[0] >= 11


def test_the_ledger_is_the_shared_one_and_the_tool_has_no_tables_of_its_own_for_it(built):
    c, _ = built
    assert one(c, "SELECT count(*) FROM ops.load WHERE tool = 'partii' AND status IN ('loaded', 'confirmed')")[0] == one(c, """
        SELECT (SELECT count(*) FROM partii_src.answer) + (SELECT count(*) FROM partii_src.page_fact) + (SELECT count(DISTINCT load_id) FROM partii_src.election_row)""")[0]
    assert one(c, "SELECT count(*) FROM ops.raw_file f JOIN ops.fetch x USING (raw_file_id) WHERE x.tool = 'partii' AND f.path LIKE '%%fixtures%%'")[0] == 54      # the archive files, in place
    tables = {r[0] for r in q(c, "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('partii', 'partii_src')")}
    assert not tables & {"raw_file", "change_log", "job_run", "snapshot", "held", "schema_migrations", "dataset", "indicator"}
    assert one(c, "SELECT count(*) FROM core.dataset WHERE tool = 'partii' AND NOT is_distributable")[0] == 4
    assert one(c, "SELECT count(*) FROM information_schema.columns k WHERE k.table_schema = 'partii' AND k.table_name IN (SELECT table_name FROM information_schema.tables "
                  "WHERE table_schema = 'partii' AND table_type = 'BASE TABLE') AND col_description((k.table_schema || '.' || k.table_name)::regclass, k.ordinal_position) IS NULL")[0] == 0


def test_the_election_has_its_day_its_threshold_and_the_counts_of_the_page(built):
    c, _ = built
    assert one(c, "SELECT kind, election_day, day_source, list_position, threshold_shown, listed_cik, listed_oik FROM partii.election WHERE erik_id = 96") == \
        ("local_partial", dt.date(2026, 10, 18), "title", 2, D("620.20"), 15, 16)
    assert one(c, "SELECT election_day, day_source, threshold_shown FROM partii.election WHERE erik_id = 79") == (dt.date(2024, 6, 9), "ref", D("477.04"))
    assert one(c, "SELECT threshold_eur, listed_cik FROM partii.election WHERE erik_id = 93") == (None, None)      # its pages are not in the fixtures: unknown, not 0


def test_every_read_of_the_register_is_kept_and_any_day_can_be_asked_for(built):
    c, _ = built
    reads = q(c, "SELECT read_at::date, records_total, total_money, status FROM partii.reading WHERE erik_id = 95 AND tbl = 'donors' ORDER BY read_at")
    assert reads == [(dt.date(2026, 9, 28), 0, D(0), "loaded"), (dt.date(2026, 9, 29), 2, D("10000.00"), "loaded"), (dt.date(2026, 9, 30), 45, D("190000.00"), "loaded")]
    as_of = lambda t: one(c, "SELECT records_total FROM partii.as_of(%s) WHERE erik_id = 95 AND tbl = 'donors'", t)
    assert as_of(utc(2026, 9, 28, 23))[0] == 0 and as_of(utc(2026, 9, 29, 23))[0] == 2 and as_of(utc(2026, 11, 1))[0] == 45
    assert as_of(utc(2026, 9, 1)) is None                                               # before the first read there is nothing, not zero
    assert one(c, "SELECT count(*) FROM partii_src.donation WHERE erik_id = 95")[0] == 47    # the rows of every read stay


def test_an_empty_table_that_agrees_with_its_counter_is_an_answer(built):
    c, _ = built
    assert one(c, "SELECT records_total, total_money FROM partii.as_of('infinity') WHERE erik_id = 96 AND tbl = 'candidates'") == (0, D(0))
    assert one(c, "SELECT status FROM partii.source_state WHERE source = 'candidates' AND ref = '96'")[0] == "loaded"


def test_the_same_row_has_the_same_identity_in_every_read(built):
    c, _ = built
    early = {r for r in q(c, "SELECT d.row_key, d.ordinal FROM partii.donation d JOIN partii.reading s USING (load_id) WHERE s.erik_id = 95 AND s.records_total = 2")}
    late = {r for r in q(c, "SELECT d.row_key, d.ordinal FROM partii.donation d JOIN partii.reading s USING (load_id) WHERE s.erik_id = 95 AND s.records_total = 45")}
    assert len(early) == 2 and early <= late


def test_no_name_of_a_person_is_stored_anywhere(built):
    c, _ = built
    text = helpers.all_text(c)
    for placeholder in ("Физическо лице ", "Кандидат 1", "Лице 1"):      # the fixtures name every person like this
        assert placeholder not in text
    assert one(c, "SELECT count(*) FROM partii_src.donation WHERE donor_name IS NOT NULL")[0] == 0
    cols = {r[0] for r in q(c, "SELECT column_name FROM information_schema.columns WHERE table_schema IN ('partii', 'partii_src')")}
    assert not cols & {"full_name", "candidate_name", "person", "egn", "iban", "phone", "email", "address"}


def test_a_participant_that_left_the_list_is_held_then_accepted_and_stays(built):
    c, _ = built      # the list of ОИК of election 96 had 31 rows on 28.09 and 30 on 24.10; the archive read it again later
    assert [r[0] for r in q(c, "SELECT status FROM partii.source_state WHERE source = 'participants' AND ref = '96/3' ORDER BY load_id")] == ["loaded", "held", "confirmed"]
    assert one(c, "SELECT count(*) FROM partii.participant WHERE erik_id = 96 AND reg_in = 3")[0] == 31      # nothing is deleted


def test_rows_that_name_a_participant_by_text_get_it_when_one_has_that_text(built):
    c, _ = built
    assert one(c, """SELECT count(*), count(participant_id) FROM partii.as_of('infinity') s JOIN partii.candidate_fund f USING (load_id)
                     WHERE s.tbl = 'candidates' AND s.erik_id = 79""") == (51, 51)
    linked = one(c, "SELECT count(*), count(participant_id) FROM partii.as_of('infinity') s JOIN partii.report_row r USING (load_id) WHERE s.tbl = 'report' AND s.erik_id = 93 AND s.variant = '0'")
    assert linked == (14, 0)                                                 # the list of participants of 93 is not in the fixtures: nothing is guessed
    assert one(c, "SELECT count(*), count(participant_id) FROM partii.as_of('infinity') s JOIN partii.report_row r USING (load_id) WHERE s.tbl = 'report' AND s.erik_id = 79 AND s.variant = '0'") == (20, 20)
    assert one(c, "SELECT count(*) FROM partii.unmatched WHERE kind = 'participant:report'")[0] > 0


def test_the_currency_of_the_reports_is_a_rule_checked_on_the_data(built):
    c, _ = built
    cur, why = one(c, "SELECT report_currency, report_currency_evidence FROM partii.election WHERE erik_id = 79")
    assert cur == "BGN" and "от 4 участници" in why and "3 съвпадат при лева" in why and "0 при евро" in why
    assert one(c, "SELECT report_currency FROM partii.election WHERE erik_id = 93")[0] == "EUR"
    row = one(c, """SELECT m.report_income, m.report_currency, m.report_income_eur FROM partii.participant_money m JOIN partii.participant p USING (participant_id)
                    WHERE p.erik_id = 79 AND p.name = 'ИМА ТАКЪВ НАРОД'""")
    assert row[1] == "BGN" and row[0] > 0 and row[2] == round(row[0] / D("1.95583"), 2)


def test_every_participant_has_its_money_and_the_election_adds_up_to_the_source(built):
    c, _ = built
    top = one(c, """SELECT p.name, m.donation_count, m.donated_money, m.donated_in_kind FROM partii.participant_money m JOIN partii.participant p USING (participant_id)
                    WHERE p.erik_id = 79 ORDER BY m.donated_money DESC LIMIT 1""")
    assert top[:2] == ("ИМА ТАКЪВ НАРОД", 28) and top[2] == D("28632.35")
    assert one(c, """SELECT sum(m.donated_money), sum(m.donated_in_kind), sum(m.donation_count) FROM partii.participant_money m JOIN partii.participant p USING (participant_id)
                     WHERE p.erik_id = 81""") == (D("3067.75"), D("1227.10"), 5)
    assert one(c, "SELECT kind, is_listed FROM partii.participant WHERE erik_id = 81 AND reg_in = 3 AND source_id = 117586") == ("party", True)      # the key is the source's


def test_the_threshold_counts_use_the_threshold_of_the_election(built):
    c, _ = built
    row = one(c, """SELECT sum(m.over_threshold), sum(m.over_threshold_no_decl) FROM partii.participant_money m JOIN partii.participant p USING (participant_id) WHERE p.erik_id = 79""")
    by_hand = one(c, """SELECT count(*) FILTER (WHERE d.money + d.in_kind >= 933 / 1.95583), count(*) FILTER (WHERE d.money + d.in_kind >= 933 / 1.95583 AND d.decl = 'none')
                        FROM partii.as_of('infinity') s JOIN partii.donation d USING (load_id) WHERE s.tbl = 'donors' AND s.erik_id = 79""")
    assert row == by_hand and row[0] > row[1] >= 0 and row[0] > 0


def test_observations_carry_the_original_the_unit_the_period_and_the_reading(built):
    c, _ = built
    o = one(c, """SELECT amount, unit_code, amount_source, unit_source, period_kind, period_start, period_end, status, source_url
                  FROM partii.observation WHERE indicator_code = 'partii.donations.money' AND erik_id = 95 AND participant_id IS NULL""")
    assert o == (D("190000.00"), "EUR", D("190000.00"), "EUR", "election", dt.date(2026, 10, 25), dt.date(2026, 10, 25), "provisional",
                 "https://erik.bulnao.government.bg/Reports/Donation?electionId=95")
    assert one(c, "SELECT status FROM partii.observation WHERE indicator_code = 'partii.donations.money' AND erik_id = 79 AND participant_id IS NULL")[0] == "final"
    r = one(c, """SELECT o.amount, o.unit_code, o.amount_source, o.unit_source FROM partii.observation o JOIN partii.participant p USING (participant_id)
                  WHERE o.indicator_code = 'partii.reports.income' AND p.erik_id = 79 AND p.name = 'ИМА ТАКЪВ НАРОД'""")
    assert r[1:] == ("EUR", r[2], "BGN") and r[0] == round(r[2] / D("1.95583"), 2)
    assert one(c, "SELECT count(*) FROM partii.observation o JOIN partii.reading s USING (load_id)")[0] == one(c, "SELECT count(*) FROM partii.observation")[0]


def test_an_agency_is_a_company_only_by_a_valid_eik(built):
    c, _ = built
    assert one(c, "SELECT count(*) FROM partii.agency WHERE eik = 'BG1080620844'")[0] == 0
    assert one(c, "SELECT row_count, reason FROM partii.unmatched WHERE kind = 'agency' AND key = 'BG1080620844 | ГАРС ООД'") == (1, "ЕИК с грешна контролна цифра")
    a = one(c, "SELECT name, participant_count, org_eik FROM partii.agency WHERE eik = '116050625'")
    assert a[0] == "ЕТ Пирин Василев" and a[1] >= 1 and a[2] is None      # not in the shared register of organisations of the test database: no link is made up


def test_freshness_of_the_shared_module_knows_the_tool(built):
    c, _ = built
    assert isinstance(pipeline.freshness(c, "partii"), list)
    assert one(c, "SELECT count(*) FROM ops.source WHERE tool = 'partii'")[0] == 9

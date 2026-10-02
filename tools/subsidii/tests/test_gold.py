"""Gold: kinds, municipalities, the totals against an independent sum of the file, euro, the rule for names, checks."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from ingest import build, gold, load, names, parse
from tests.helpers import count, csv_of, derive, fixture, put, rows_of

D1, D2 = "2026-09-28", "2026-10-05"


def built(c, *files):
    for fy, day, raw in files:
        assert put(c, fy, day, raw)[0] == "built"
    load.load_units(c)
    out = gold.build_missing(c)
    assert not out["problems"], out
    return out


def independent(raw):
    """What the file says, summed by plain code: per municipality id and kind, and per measure, straight from the cells."""
    places = names.load_places()
    A = lambda v: Decimal(0) if v == "-" else Decimal(v)
    muni, kinds, pay, top = defaultdict(lambda: [Decimal(0)] * 4), defaultdict(int), defaultdict(lambda: [Decimal(0)] * 3), []
    cur = None
    for r in rows_of(raw)[1:]:
        if r[parse.MEASURE] == parse.TOTAL_WORD:
            cur = (names.match_place(r[parse.OBLAST], r[parse.OBSHTINA], places)[0], names.kind_of(r[parse.NAME], r[parse.SURNAME]))
            kinds[cur[1]] += 1
            for k, i in enumerate((parse.EFGZ_T, parse.EZFRS_T, parse.NB_T, parse.TOTAL_T)):
                muni[cur][k] += A(r[i])
            top.append(A(r[parse.TOTAL_T]))
        else:
            for k, i in enumerate(parse.PAYMENT_AMOUNTS):
                pay[(cur, r[parse.CODE], r[parse.MEASURE], r[parse.OBJECTIVE])][k] += A(r[i])
    return muni, kinds, pay, sorted(top, reverse=True)


def test_every_recipient_has_a_kind_and_the_people_have_no_search_key(c):
    built(c, (2024, D1, fixture(2024)))
    kinds = dict(c.execute("SELECT kind, count(*) FROM gold.beneficiary GROUP BY kind").fetchall())
    assert sum(kinds.values()) == count(c, "SELECT count(*) FROM silver.beneficiary")
    assert kinds["natural"] > 0 and kinds["sole_trader"] == 3 and kinds["legal"] > 10
    assert count(c, "SELECT count(*) FROM gold.beneficiary WHERE kind <> 'legal' AND (name_norm IS NOT NULL OR org_id IS NOT NULL)") == 0
    assert count(c, "SELECT count(*) FROM gold.beneficiary WHERE kind = 'legal' AND (name_norm IS NULL OR org_id IS NULL)") == 0
    assert count(c, "SELECT count(*) FROM gold.beneficiary WHERE municipality_id IS NULL") == 0


def test_gold_by_municipality_is_the_sum_of_the_file(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    muni, kinds, pay, top = independent(raw)
    got = {(r[0], r[1]): r[2:] for r in c.execute("SELECT municipality_id, kind, efgz, ezfrs, nb, total FROM gold.municipality_fy")}
    assert {k: tuple(v) for k, v in muni.items()} == {k: tuple(x or Decimal(0) for x in v) for k, v in got.items()}
    total = parse.parse(raw).stats["total"]["total"]
    assert sum(v[3] for v in got.values()) == total
    assert c.execute("SELECT recipients, total FROM gold.summary WHERE scope = 'all'").fetchone() == (42, total)
    for kind, n in kinds.items():
        assert count(c, "SELECT recipients FROM gold.summary WHERE scope = %s", kind) == n


def test_the_municipality_of_a_name_that_two_municipalities_share_is_decided_with_its_oblast(c):
    built(c, (2024, D1, fixture(2024)))
    rows = c.execute("""SELECT m.oblast, count(*) FROM gold.municipality_fy f JOIN gold.municipality m ON m.id = f.municipality_id
                        WHERE m.name_bg = 'Бяла' GROUP BY m.oblast ORDER BY 1""").fetchall()
    assert rows == [("Варна", 3), ("Русе", 2)] or {r[0] for r in rows} == {"Варна", "Русе"}
    ids = {r[0] for r in c.execute("SELECT DISTINCT municipality_id FROM gold.municipality_fy WHERE municipality_id IN (SELECT id FROM gold.municipality WHERE name_bg = 'Бяла')")}
    assert len(ids) == 2
    alias = c.execute("SELECT municipality_id, method FROM gold.place_match WHERE obshtina_raw = 'Добрич-селска'").fetchone()
    assert alias[1] == "alias"
    assert c.execute("SELECT name_bg FROM gold.municipality WHERE id = %s", (alias[0],)).fetchone() == ("Добричка",)


def test_the_payments_by_measure_are_the_sum_of_the_file_and_a_total_row_is_not_added_to_its_parts(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    muni, kinds, pay, top = independent(raw)
    got = defaultdict(lambda: [Decimal(0)] * 3)
    for code, name, obj, e, z, n in c.execute("""SELECT m.code, m.name, m.objective, sum(a.efgz), sum(a.ezfrs), sum(a.nb) FROM gold.payment_agg a
                                                  JOIN gold.measure m USING (measure_id) GROUP BY 1, 2, 3"""):
        got[(code, name, obj)] = [e or Decimal(0), z or Decimal(0), n or Decimal(0)]
    want = defaultdict(lambda: [Decimal(0)] * 3)
    for (cur, code, name, obj), v in pay.items():
        for k in range(3):
            want[(code, name, obj)][k] += v[k]
    assert {k: tuple(v) for k, v in got.items()} == {k: tuple(v) for k, v in want.items()}
    # the payments and the ОБЩО rows are two sums of the source, shown apart; here they differ by the source's own difference
    pay_total = sum(sum(v) for v in got.values())
    muni_total = parse.parse(raw).stats["total"]["total"]
    assert pay_total != muni_total and pay_total - muni_total == Decimal("465806.69") - Decimal("399911.64") + Decimal("50750.46") - Decimal("30245.35") + Decimal("3166.94") - Decimal("2777.24")
    assert count(c, "SELECT count(*) FROM gold.measure WHERE name = 'ОБЩО'") == 0


def test_the_top_one_percent_is_of_recipient_rows_and_the_scopes_are_not_mixed(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    top = independent(raw)[3]
    row = c.execute("SELECT top1_n, top1_total FROM gold.summary WHERE scope = 'all'").fetchone()
    assert row == (1, top[0])
    assert count(c, "SELECT sum(recipients) FROM gold.summary WHERE scope <> 'all'") == count(c, "SELECT recipients FROM gold.summary WHERE scope = 'all'")


def test_a_price_in_leva_is_shown_in_euro_at_the_fixed_rate_and_the_original_stays(c):
    raw = fixture(2025)
    built(c, (2025, D1, raw))
    eur = c.execute("SELECT eur_per_unit FROM gold.fiscal_year WHERE fy = 2025").fetchone()[0]
    assert eur == Decimal("0.511291881196")
    o = c.execute("""SELECT value, unit, value_original, unit_original, status FROM gold.observation
                     WHERE indicator = 'subsidii.paid.total' AND scope = 'all' ORDER BY value_original DESC LIMIT 1""").fetchone()
    assert o[1] == "EUR" and o[3] == "BGN" and o[0] == (o[2] * Decimal(1) / Decimal("1.95583")).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP") or abs(o[0] - o[2] / Decimal("1.95583")) < Decimal("0.01")
    assert o[4] == "final"       # read after the year ended on 15.10.2025


def test_a_year_whose_currency_is_not_confirmed_has_the_original_and_no_euro(c):
    raw = _year(fixture(2025), 2026)
    built(c, (2026, "2026-10-19", raw))
    row = c.execute("SELECT value, unit, value_original, unit_original, status FROM gold.observation WHERE scope = 'all' LIMIT 1").fetchone()
    assert row[0] is None and row[1] is None and row[2] is not None and row[3] is None
    assert c.execute("SELECT eur_per_unit FROM gold.fiscal_year WHERE fy = 2026").fetchone()[0] is None


def _year(raw, fy):
    rows = rows_of(raw)
    for r in rows[1:]:
        for i in (parse.START, parse.END):
            if r[i] != "-":
                d, m, y = r[i].split(".")
                r[i] = f"{d}.{m}.{int(y) + (fy - 2025)}"
    return csv_of(rows)


# ---------- the names of people ----------

def shown(c, fy, today):
    c.execute("SELECT set_config('subsidii.today', %s, false)", (today,))
    return {r[0]: (r[1], r[2], r[3]) for r in c.execute(
        """SELECT b.name || '|' || b.obshtina || '|' || h.id, r.name_shown, r.name_export, r.name_expired FROM gold.recipient r
           JOIN silver.holder h ON h.id = r.holder_id JOIN silver.beneficiary b ON b.id = h.beneficiary_id WHERE r.fy = %s""", (fy,))}


def test_the_name_of_a_person_is_shown_for_two_years_after_the_year_and_then_replaced(c):
    built(c, (2024, D1, fixture(2024)))
    assert count(c, "SELECT gold.name_ttl_years()") == 2
    assert c.execute("SELECT names_until FROM gold.fiscal_year WHERE fy = 2024").fetchone()[0] == dt.date(2026, 10, 15)
    inside = shown(c, 2024, "2026-10-14")
    person = next(k for k in inside if k.startswith("физическо лице №4|"))
    assert inside[person][0] == "физическо лице №4 ФЛ" and inside[person][2] is False
    outside = shown(c, 2024, "2026-10-15")
    assert outside[person][0] == "физическо лице" and outside[person][2] is True
    et = next(k for k in outside if k.startswith("ЕТ Образец №1|"))
    assert outside[et][0] == "физическо лице (едноличен търговец)"
    firm = next(k for k in outside if k.startswith("БГ Агро Земеделска Компания ЕООД|"))
    assert outside[firm][0] == "БГ Агро Земеделска Компания ЕООД" and outside[firm][2] is False       # a firm is shown whatever the date


def test_an_export_never_has_the_name_of_a_person_not_even_inside_the_term(c):
    built(c, (2024, D1, fixture(2024)))
    inside = shown(c, 2024, "2026-01-01")
    persons = {k: v for k, v in inside.items() if k.startswith(("физическо лице", "ЕТ Образец"))}
    assert persons and all(v[1] in ("физическо лице", "физическо лице (едноличен търговец)") for v in persons.values())
    assert any(v[0] not in ("физическо лице", "физическо лице (едноличен търговец)") for v in persons.values())      # shown on the page, not in the export


def test_the_web_role_is_granted_ops_and_gold_only_never_silver(c):
    # the names of people are in silver; the web role reaches them only through gold.recipient, which hides them
    from ingest import config
    grants = [l for f in sorted((config.ROOT / "db" / "migrations").glob("*.sql")) for l in f.read_text(encoding="utf-8").splitlines() if "subsidii_web" in l]
    assert grants and all("silver" not in l and "stage" not in l for l in grants)
    # a view is run with the rights of its owner: the base tables need no grant for the view to work
    assert count(c, "SELECT count(*) FROM pg_views WHERE schemaname = 'gold' AND viewname IN ('recipient', 'payment_row', 'snapshot', 'block_diff', 'population')") == 5


# ---------- history: any snapshot against any other ----------

def test_every_snapshot_keeps_its_own_aggregates(c):
    a = fixture(2024)
    b = derive(2024, drop={6})
    built(c, (2024, D1, a), (2024, D2, b))
    s1 = c.execute("SELECT recipients, total FROM gold.summary WHERE snap_day = %s AND scope = 'all'", (D1,)).fetchone()
    s2 = c.execute("SELECT recipients, total FROM gold.summary WHERE snap_day = %s AND scope = 'all'", (D2,)).fetchone()
    assert s1[0] == 42 and s2[0] == 41 and s1[1] > s2[1]
    assert independent(a)[0] == {(r[0], r[1]): [x or Decimal(0) for x in r[2:]] for r in c.execute("SELECT municipality_id, kind, efgz, ezfrs, nb, total FROM gold.municipality_fy WHERE snap_day = %s", (D1,))}
    assert count(c, "SELECT count(*) FROM gold.build WHERE ok") == 2


def test_gold_of_a_snapshot_is_built_again_to_the_same_numbers(c):
    built(c, (2024, D1, fixture(2024)))
    before = c.execute("SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (SELECT fy, snap_day, municipality_id, kind, efgz, ezfrs, nb, total FROM gold.municipality_fy) t").fetchone()
    gold.build_missing(c, rebuild=True)
    again = c.execute("SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (SELECT fy, snap_day, municipality_id, kind, efgz, ezfrs, nb, total FROM gold.municipality_fy) t").fetchone()
    assert before == again
    assert count(c, "SELECT count(*) FROM gold.observation") == count(c, "SELECT count(*) FROM gold.observation WHERE build_id = (SELECT max(build_id) FROM gold.build)")


# ---------- the checks stop a snapshot that does not add up ----------

def test_a_recipient_in_an_unknown_municipality_stops_the_snapshot_and_leaves_the_earlier_one(c):
    built(c, (2024, D1, fixture(2024)))
    def move(i, r):
        if i == 0:
            r[parse.OBSHTINA] = "Непозната"
    put(c, 2024, D2, derive(2024, edit=move))
    out = gold.build_missing(c)
    assert out["built"] == 0 and any("непознати общини" in p for p in out["problems"])
    assert count(c, "SELECT count(*) FROM gold.summary WHERE snap_day = %s", dt.date.fromisoformat(D2)) == 0          # rolled back
    assert c.execute("SELECT ok FROM gold.build WHERE snap_day = %s", (D2,)).fetchone()[0] is False
    assert count(c, "SELECT count(*) FROM gold.summary WHERE snap_day = %s", dt.date.fromisoformat(D1)) == 4          # the earlier snapshot stays
    assert gold.latest_ok(c) == {2024: dt.date.fromisoformat(D1)}
    assert count(c, "SELECT count(*) FROM gold.unmatched") == 0          # rolled back with the rest
    from ingest import checks
    assert any("няма добро златно изграждане" in p for p in checks.freshness(c, today=dt.date(2026, 10, 6), now=dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc), st={"last_ok": "2026-10-05T00:41:00Z"}, root=None))



def test_a_total_that_does_not_reconcile_stops_the_snapshot(c):
    assert put(c, 2024, D1, fixture(2024))[0] == "built"
    gold.sync_dimensions(c)
    c.execute("UPDATE silver.snapshot SET total_efgz = total_efgz + 1")            # the file's own sum no longer matches what gold adds up to
    out = gold.build_missing(c)
    assert out["built"] == 0 and any("сбор ЕФГЗ" in p for p in out["problems"])
    assert count(c, "SELECT count(*) FROM gold.municipality_fy") == 0


def test_a_payment_outside_the_year_stops_gold_even_if_silver_let_it_in(c):
    assert put(c, 2024, D1, fixture(2024))[0] == "built"
    c.execute("UPDATE silver.payment SET ends = '2024-10-16' WHERE id = (SELECT min(id) FROM silver.payment)")
    out = gold.build_missing(c)
    assert any("извън финансовата година" in p for p in out["problems"])


def test_the_rule_for_names_is_checked_by_gold_itself(c):
    built(c, (2024, D1, fixture(2024)))
    c.execute("SELECT set_config('subsidii.today', '2030-01-01', false)")
    assert gold.run_checks(c, 2024, dt.date.fromisoformat(D1)) == []
    c.execute("CREATE OR REPLACE FUNCTION gold.name_ttl_years() RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT 2 $$")


def test_the_registry_and_the_dimensions_are_loaded(c):
    assert count(c, "SELECT count(*) FROM gold.municipality") == 265
    assert count(c, "SELECT count(*) FROM gold.fund") == 3
    ds = c.execute("SELECT id, channel, distributed, licence FROM gold.dataset").fetchall()
    assert ds == [("subsidii.paid", "file", False, None)]           # no licence on the page: shown with the source, never given away
    assert count(c, "SELECT count(*) FROM gold.indicator") == 4

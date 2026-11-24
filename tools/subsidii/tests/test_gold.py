"""The clean tables and the serving layer: kinds, municipalities, the totals against an independent sum of the file, euro,
the rule for names, the checks, the publication."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from ingest import config, gold, names, parse, pipeline
from tests import helpers
from tests.helpers import built, count, csv_of, derive, fixture, publish, put, rows_of

D1, D2 = "2026-09-28", "2026-10-05"


def independent(c, raw):
    """What the file says, summed by plain code: per municipality id and kind, and per measure, straight from the cells."""
    places = names.load_places(c)
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
    kinds = dict(c.execute("SELECT kind, count(*) FROM subsidii.beneficiary GROUP BY kind").fetchall())
    assert sum(kinds.values()) == count(c, "SELECT count(*) FROM subsidii_src.beneficiary")
    assert kinds["natural"] > 0 and kinds["sole_trader"] == 3 and kinds["legal"] > 10
    assert count(c, "SELECT count(*) FROM subsidii.beneficiary WHERE kind <> 'legal' AND (name_norm IS NOT NULL OR org_id IS NOT NULL)") == 0
    assert count(c, "SELECT count(*) FROM subsidii.beneficiary WHERE kind = 'legal' AND (name_norm IS NULL OR org_id IS NULL)") == 0
    assert count(c, "SELECT count(*) FROM subsidii.beneficiary WHERE municipality_code IS NULL") == 0


def test_gold_by_municipality_is_the_sum_of_the_file(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    muni, kinds, pay, top = independent(c, raw)
    got = {(r[0], r[1]): r[2:] for r in c.execute("SELECT municipality_code, kind, efgz, ezfrs, nb, total FROM subsidii.municipality_fy")}
    assert {k: tuple(v) for k, v in muni.items()} == {k: tuple(x or Decimal(0) for x in v) for k, v in got.items()}
    total = parse.parse(raw).stats["total"]["total"]
    assert sum(v[3] for v in got.values()) == total
    assert c.execute("SELECT recipients, total FROM subsidii.summary WHERE scope = 'all'").fetchone() == (42, total)
    for kind, n in kinds.items():
        assert count(c, "SELECT recipients FROM subsidii.summary WHERE scope = %s", kind) == n


def test_the_municipality_of_a_name_that_two_municipalities_share_is_decided_with_its_oblast(c):
    built(c, (2024, D1, fixture(2024)))
    rows = c.execute("""SELECT o.label_bg, count(*) FROM subsidii.municipality_fy f JOIN core.municipality m USING (municipality_code)
                        JOIN core.oblast o USING (oblast_code) WHERE m.name_bg = 'Бяла' GROUP BY o.label_bg ORDER BY 1""").fetchall()
    assert rows == [("Варна", 3), ("Русе", 2)] or {r[0] for r in rows} == {"Варна", "Русе"}
    ids = {r[0] for r in c.execute("SELECT DISTINCT municipality_code FROM subsidii.municipality_fy WHERE municipality_code IN (SELECT municipality_code FROM core.municipality WHERE name_bg = 'Бяла')")}
    assert len(ids) == 2
    alias = c.execute("SELECT municipality_code, method FROM subsidii.place_match WHERE obshtina_raw = 'Добрич-селска'").fetchone()
    assert alias[1] == "alias"
    assert c.execute("SELECT name_bg FROM core.municipality WHERE municipality_code = %s", (alias[0],)).fetchone() == ("Добричка",)


def test_the_payments_by_measure_are_the_sum_of_the_file_and_a_total_row_is_not_added_to_its_parts(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    muni, kinds, pay, top = independent(c, raw)
    got = defaultdict(lambda: [Decimal(0)] * 3)
    for code, name, obj, e, z, n in c.execute("""SELECT m.code, m.name, m.objective, sum(a.efgz), sum(a.ezfrs), sum(a.nb) FROM subsidii.payment_agg a
                                                  JOIN subsidii.measure m USING (measure_id) GROUP BY 1, 2, 3"""):
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
    assert count(c, "SELECT count(*) FROM subsidii.measure WHERE name = 'ОБЩО'") == 0


def test_the_top_one_percent_is_of_recipient_rows_and_the_scopes_are_not_mixed(c):
    raw = fixture(2024)
    built(c, (2024, D1, raw))
    top = independent(c, raw)[3]
    row = c.execute("SELECT top1_n, top1_total FROM subsidii.summary WHERE scope = 'all'").fetchone()
    assert row == (1, top[0])
    assert count(c, "SELECT sum(recipients) FROM subsidii.summary WHERE scope <> 'all'") == count(c, "SELECT recipients FROM subsidii.summary WHERE scope = 'all'")


def test_a_price_in_leva_is_shown_in_euro_at_the_fixed_rate_and_the_original_stays(c):
    raw = fixture(2025)
    built(c, (2025, D1, raw))
    eur = c.execute("SELECT eur_per_unit FROM subsidii.fiscal_year WHERE fy = 2025").fetchone()[0]
    assert eur == Decimal("0.511291881196")
    o = c.execute("""SELECT value_eur, value_original, currency, status FROM subsidii.observation
                     WHERE indicator_code = 'subsidii.paid.total' AND scope = 'all' ORDER BY value_original DESC LIMIT 1""").fetchone()
    assert o[2] == "BGN" and abs(o[0] - o[1] / Decimal("1.95583")) < Decimal("0.01")
    assert o[3] == "final"       # read after the year ended on 15.10.2025


def test_a_year_whose_currency_is_not_confirmed_has_the_original_and_no_euro(c):
    raw = _year(fixture(2025), 2026)
    built(c, (2026, "2026-10-19", raw))
    row = c.execute("SELECT value_eur, value_original, currency FROM subsidii.observation WHERE scope = 'all' LIMIT 1").fetchone()
    assert row[0] is None and row[1] is not None and row[2] is None
    assert c.execute("SELECT eur_per_unit FROM subsidii.fiscal_year WHERE fy = 2026").fetchone()[0] is None


def _year(raw, fy):
    rows = rows_of(raw)
    for r in rows[1:]:
        for i in (parse.START, parse.END):
            if r[i] != "-":
                d, m, y = r[i].split(".")
                r[i] = f"{d}.{m}.{int(y) + (fy - 2025)}"
    return csv_of(rows)


# ---------- the names of people: only masked, only above the threshold, only in the term ----------

def published(c, fy, today=None):
    """{name|municipality|holder: (person_masked, org_name)} of the serving layer after a publication on `today`."""
    c.execute("SELECT set_config('subsidii.today', %s, false)", (today or "",))
    publish(c)
    return {r[0]: (r[1], r[2]) for r in c.execute(
        """SELECT b.name || '|' || b.obshtina || '|' || h.holder_id, r.person_masked, r.org_name FROM pub_subsidii.recipient r
           JOIN subsidii_src.holder h USING (holder_id) JOIN subsidii_src.beneficiary b USING (beneficiary_id) WHERE r.fy = %s""", (fy,))}


def test_a_person_above_the_threshold_is_masked_until_two_years_after_the_year_and_then_has_no_name(c):
    built(c, (2024, D1, fixture(2024)))
    assert count(c, "SELECT subsidii.name_ttl_years()") == 2 and count(c, "SELECT subsidii.name_threshold_eur()") == 1250
    assert c.execute("SELECT names_until FROM subsidii.fiscal_year WHERE fy = 2024").fetchone()[0] == dt.date(2026, 10, 15)
    inside = published(c, 2024, "2026-10-14")
    person = next(k for k in inside if k.startswith("физическо лице №4|"))
    assert inside[person] == ("физическо ***", None)
    et = next(k for k in inside if k.startswith("ЕТ Образец №1|"))
    assert inside[et] == ("ЕТ ***", None)
    firm = next(k for k in inside if k.startswith("БГ Агро Земеделска Компания ЕООД|"))
    assert inside[firm] == (None, "БГ Агро Земеделска Компания ЕООД")
    outside = published(c, 2024, "2026-10-15")
    assert outside[person] == (None, None) and outside[firm] == (None, "БГ Агро Земеделска Компания ЕООД")     # a firm is shown whatever the date


def test_a_person_at_or_below_the_threshold_is_only_in_the_sums(c):
    built(c, (2024, D1, fixture(2024)))
    publish(c)
    eur = c.execute("SELECT eur_per_unit FROM subsidii.fiscal_year WHERE fy = 2024").fetchone()[0]
    small = c.execute("""SELECT h.holder_id, h.total FROM subsidii_src.holder h JOIN subsidii.beneficiary g USING (beneficiary_id)
                         WHERE g.kind <> 'legal' AND h.total * %s <= 1250""", (eur,)).fetchall()
    assert small, "the FY2024 fixture has people below the threshold"
    assert count(c, "SELECT count(*) FROM pub_subsidii.recipient WHERE holder_id = ANY(%s)", [h for h, _ in small]) == 0
    assert count(c, "SELECT sum(recipients) FROM pub_subsidii.below_threshold") == len(small)
    assert c.execute("SELECT sum(total) FROM pub_subsidii.below_threshold").fetchone()[0] == sum(t for _, t in small)
    # every ОБЩО row is in exactly one of the two: a row, or the sums below the threshold
    assert count(c, "SELECT count(*) FROM pub_subsidii.recipient") + len(small) == count(c, "SELECT count(*) FROM subsidii_src.holder")
    # the sums of the municipalities still have them all
    assert c.execute("SELECT sum(total) FROM pub_subsidii.municipality_fy").fetchone()[0] == parse.parse(fixture(2024)).stats["total"]["total"]


def test_a_year_without_a_confirmed_currency_has_no_row_of_a_person(c):
    built(c, (2026, "2026-10-19", _year(fixture(2025), 2026)))
    publish(c)
    assert count(c, "SELECT count(*) FROM pub_subsidii.recipient WHERE kind <> 'legal'") == 0                # the threshold cannot be judged
    assert count(c, "SELECT count(*) FROM pub_subsidii.recipient WHERE kind = 'legal'") > 0
    assert count(c, "SELECT sum(recipients) FROM pub_subsidii.below_threshold") == count(c, "SELECT count(*) FROM subsidii_src.holder JOIN subsidii.beneficiary USING (beneficiary_id) WHERE kind <> 'legal'")


def test_the_serving_layer_has_no_full_name_and_no_key_of_a_person(c):
    built(c, (2024, D1, fixture(2024)), (2025, D1, fixture(2025)))
    publish(c)
    full = [r[0] for r in c.execute("SELECT btrim(name || ' ' || surname) FROM subsidii_src.beneficiary WHERE surname <> '-'")]
    text = " ".join(str(v) for t in ("recipient", "block_diff", "org", "concentration", "below_threshold")
                    for row in c.execute(f"SELECT * FROM pub_subsidii.{t}") for v in row)
    assert full and not [n for n in full if n in text]
    cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'pub_subsidii'")}
    assert "beneficiary_id" not in cols
    assert count(c, "SELECT count(*) FROM core.serving_violation WHERE relation LIKE 'pub_subsidii.%%'") == 0
    assert count(c, "SELECT count(*) FROM core.person_column_undecided WHERE schema_name LIKE '%%subsidii%%'") == 0


def test_a_publication_that_would_show_a_small_person_is_refused_and_changes_nothing(c):
    built(c, (2024, D1, fixture(2024)))
    first = publish(c).publication_id
    before = count(c, "SELECT count(*) FROM pub_subsidii.recipient")
    c.execute("CREATE OR REPLACE FUNCTION subsidii.name_threshold_eur() RETURNS numeric LANGUAGE sql IMMUTABLE AS $$ SELECT 0::numeric $$")
    try:
        c.execute("UPDATE ops.check_rule SET sql = replace(sql, 'y.name_threshold_eur', '1250') WHERE tool = 'subsidii' AND name = 'pub_no_person_below_threshold'")
        done = pipeline.publish(c, helpers.RUN, config.ROOT)
        assert done.publication_id is None and any("прага" in p for p in done.problems)
        assert count(c, "SELECT count(*) FROM pub_subsidii.recipient") == before
        assert count(c, "SELECT max(publication_id) FROM pub.publication WHERE tool = 'subsidii'") == first
    finally:
        c.execute("CREATE OR REPLACE FUNCTION subsidii.name_threshold_eur() RETURNS numeric LANGUAGE sql IMMUTABLE AS $$ SELECT 1250::numeric $$")


def test_the_pages_read_only_the_serving_layer(c):
    src = (config.ROOT / "app" / "queries.py").read_text(encoding="utf-8") + (config.ROOT / "app" / "main.py").read_text(encoding="utf-8")
    import re
    schemas = set(re.findall(r"\b(subsidii_src|subsidii|pub_subsidii|pub_naselenie|pub|core|ops|naselenie)\.[a-z_]+\b(?=[\s,)]|$)", src))
    assert schemas <= {"pub_subsidii", "pub", "pub_naselenie"}, schemas


# ---------- history: any snapshot against any other ----------

def test_every_snapshot_keeps_its_own_aggregates(c):
    a = fixture(2024)
    b = derive(2024, drop={6})
    built(c, (2024, D1, a), (2024, D2, b))
    s1 = c.execute("SELECT recipients, total FROM subsidii.summary WHERE snap_day = %s AND scope = 'all'", (D1,)).fetchone()
    s2 = c.execute("SELECT recipients, total FROM subsidii.summary WHERE snap_day = %s AND scope = 'all'", (D2,)).fetchone()
    assert s1[0] == 42 and s2[0] == 41 and s1[1] > s2[1]
    assert independent(c, a)[0] == {(r[0], r[1]): [x or Decimal(0) for x in r[2:]] for r in c.execute("SELECT municipality_code, kind, efgz, ezfrs, nb, total FROM subsidii.municipality_fy WHERE snap_day = %s", (D1,))}
    assert count(c, "SELECT count(*) FROM subsidii.build WHERE ok") == 2


def test_gold_of_a_snapshot_is_built_again_to_the_same_numbers(c):
    built(c, (2024, D1, fixture(2024)))
    before = c.execute("SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (SELECT fy, snap_day, municipality_code, kind, efgz, ezfrs, nb, total FROM subsidii.municipality_fy) t").fetchone()
    gold.build_missing(c, rebuild=True)
    again = c.execute("SELECT md5(string_agg(t::text, '|' ORDER BY t::text)) FROM (SELECT fy, snap_day, municipality_code, kind, efgz, ezfrs, nb, total FROM subsidii.municipality_fy) t").fetchone()
    assert before == again
    assert count(c, "SELECT count(*) FROM subsidii.observation") == count(c, "SELECT count(*) FROM subsidii.observation WHERE build_id = (SELECT max(build_id) FROM subsidii.build)")


# ---------- the checks stop a snapshot that does not add up ----------

def test_a_recipient_in_an_unknown_municipality_stops_the_snapshot_and_leaves_the_earlier_one(c):
    built(c, (2024, D1, fixture(2024)))
    def move(i, r):
        if i == 0:
            r[parse.OBSHTINA] = "Непозната"
    put(c, 2024, D2, derive(2024, edit=move))
    out = gold.build_missing(c)
    assert out["built"] == 0 and any("непознати общини" in p for p in out["problems"])
    assert count(c, "SELECT count(*) FROM subsidii.summary WHERE snap_day = %s", dt.date.fromisoformat(D2)) == 0          # rolled back
    assert c.execute("SELECT ok FROM subsidii.build WHERE snap_day = %s", (D2,)).fetchone()[0] is False
    assert count(c, "SELECT count(*) FROM subsidii.summary WHERE snap_day = %s", dt.date.fromisoformat(D1)) == 4          # the earlier snapshot stays
    assert gold.latest_ok(c) == {2024: dt.date.fromisoformat(D1)}
    assert count(c, "SELECT count(*) FROM subsidii.unmatched") == 0          # rolled back with the rest
    from ingest import checks
    assert any("няма добро изграждане" in p for p in checks.freshness(c, today=dt.date(2026, 10, 6), now=dt.datetime(2026, 10, 6, 8, tzinfo=dt.timezone.utc), st={"last_ok": "2026-10-05T00:41:00Z"}, root=None))



def test_a_total_that_does_not_reconcile_stops_the_snapshot(c):
    assert put(c, 2024, D1, fixture(2024))[0] == "built"
    gold.sync_dimensions(c)
    c.execute("UPDATE subsidii.snapshot SET total_efgz = total_efgz + 1")            # the file's own sum no longer matches what gold adds up to
    out = gold.build_missing(c)
    assert out["built"] == 0 and any("сбор ЕФГЗ" in p for p in out["problems"])
    assert count(c, "SELECT count(*) FROM subsidii.municipality_fy") == 0


def test_a_payment_outside_the_year_stops_the_build_even_if_the_landing_let_it_in(c):
    assert put(c, 2024, D1, fixture(2024))[0] == "built"
    c.execute("UPDATE subsidii_src.payment SET ends = '2024-10-16' WHERE payment_id = (SELECT min(payment_id) FROM subsidii_src.payment)")
    out = gold.build_missing(c)
    assert any("извън финансовата година" in p for p in out["problems"])


def test_the_registry_and_the_dimensions_are_loaded(c):
    ds = dict(c.execute("SELECT dataset, personal_data FROM core.dataset WHERE tool = 'subsidii'").fetchall())
    assert ds == {"subsidii.dfz": "names", "subsidii.paid": "public-role"}
    assert count(c, "SELECT count(*) FROM core.indicator WHERE dataset = 'subsidii.paid'") == 4
    assert count(c, "SELECT count(*) FROM ops.source WHERE tool = 'subsidii'") == 1


def test_the_municipality_is_found_by_name_together_with_the_oblast(c):
    places = names.load_places(c)
    ruse, varna = names.match_place("Русе", "Бяла", places), names.match_place("Варна", "Бяла", places)
    assert ruse[0] != varna[0] and ruse[1] == varna[1] == "name"
    assert names.match_place("София (област)", "Пирдоп", places)[0] == names.match_place("София област", "Пирдоп", places)[0]
    assert names.match_place("Варна", "Вълчи дол", places)[0] == names.match_place("Варна", "Вълчи Дол", places)[0]
    alias = names.match_place("Добрич", "Добрич-селска", places)
    assert alias[1] == "alias" and alias[0] == names.match_place("Добрич", "Добричка", places)[0] and "Добричка" in alias[2]
    assert names.match_place("Русе", "Варна", places) is None             # the name of a municipality of another oblast
    assert names.match_place("Видин", "Няма такава", places) is None


def test_every_municipality_of_the_fixtures_is_found(c):
    places = names.load_places(c)
    for fy in (2024, 2025):
        for l in parse.read_lines(fixture(fy)):
            if l.total:
                assert names.match_place(l.f[parse.OBLAST], l.f[parse.OBSHTINA], places), (l.f[parse.OBLAST], l.f[parse.OBSHTINA])

"""Each rule that guards the data is broken on purpose, in place, and the test that stands for it must fail.

    PARTII_TEST_DSN=dbname=partii_test python tests/mutations.py      (tools/danni must be beside this folder)

A mutation that no test notices is a rule nobody guards. The file is put back after each one.
"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent

# (file, text, replacement, the test that must fail)
CASES = [
    # the parsers: shape, counters, totals, the two kinds of donation, names
    ("ingest/parse.py", 'if len(d["data"]) != d["recordsTotal"] or d["recordsFiltered"] != d["recordsTotal"]:', "if False:",
     "tests/test_parse.py::test_fewer_rows_than_the_counter_or_a_sum_that_differs_is_a_mismatch"),
    ("ingest/parse.py", "if abs(Decimal(repr(float(v))) - rows_sum) > config.SUM_TOLERANCE:", "if False:",
     "tests/test_parse.py::test_fewer_rows_than_the_counter_or_a_sum_that_differs_is_a_mismatch"),
    ("ingest/parse.py", 'total(extra, "TotalDonationPrice", sum((r["money"] for r in rows), Decimal(0))',
     'total(extra, "TotalDonationPrice", sum((r["money"] + r["in_kind"] for r in rows), Decimal(0))',
     "tests/test_parse.py::test_money_and_non_monetary_value_are_separate_columns"),
    ("ingest/parse.py", 'donor_name=clean(r["fullName"]) if who == "legal" else None)', 'donor_name=clean(r["fullName"]))',
     "tests/test_parse.py::test_no_name_of_a_person_leaves_the_parser"),
    ("ingest/parse.py", 'if SOLE.search(name):\n        return "sole_trader"', 'if False:\n        return "sole_trader"',
     "tests/test_parse.py::test_a_legal_entity_is_named_and_a_sole_trader_is_not"),
    ("ingest/parse.py", "if value[:10] in NO_DATE:", "if False:", "tests/test_parse.py::test_the_empty_dates_of_the_source_are_no_date"),
    ("ingest/parse.py", "if not isinstance(r, dict) or not any(set(r) == k for k in row_keys):", "if False:",
     "tests/test_parse.py::test_a_changed_shape_stops_the_answer"),
    ("ingest/parse.py", "cells.append((c[\"col\"], x, None if v == -1 else v))", "cells.append((c[\"col\"], x, v))",
     "tests/test_parse.py::test_minus_one_is_nothing_filed_and_never_zero"),
    ("ingest/parse.py", 'if m.group(2) != "€":', "if False:", "tests/test_parse.py::test_a_threshold_in_another_currency_or_two_different_thresholds_are_refused"),
    ("ingest/parse.py", "if s != d[8]:\n        return False", "if False:\n        return False", "tests/test_parse.py::test_agencies_and_the_check_digit_of_their_eik"),
    ("ingest/parse.py", "if layouts is not None and headers not in layouts.values():", "if False:",
     "tests/test_parse.py::test_an_empty_sheet_is_no_reports_and_a_changed_sheet_stops"),
    ("ingest/archive.py", 'if sha != entry["sha256"] or len(raw) != entry["bytes"]:', "if False:", "tests/test_parse.py::test_a_file_that_differs_from_the_index_is_refused"),
    # the landing zone: what is given to the shared module, the check inside the load, nothing is deleted, text links, currency.
    # The shared rules themselves (hold, idempotency, change log, migrations) are mutated in tools/danni.
    ("ingest/store.py", "writer(e), raw_file_id=raw_file_id, current=current(e), now=now", "writer(e), raw_file_id=raw_file_id, current=None, now=now",
     "tests/test_store.py::test_a_lower_total_with_the_same_number_of_rows_is_held"),
    ("ingest/store.py", "            return 1\n    return 0", "            return 0\n    return 0", "tests/test_store.py::test_a_lower_total_with_the_same_number_of_rows_is_held"),
    ("ingest/store.py", 'hold_when=smaller if e["table"] in GROWING else None', "hold_when=None",
     "tests/test_store.py::test_a_corrected_row_in_a_register_that_did_not_shrink_is_accepted_and_logged"),
    ("ingest/store.py", "return (st.get(\"last\") or {}).get(\"elections\", 0) >= len(pos) > 0", "return True",
     "tests/test_store.py::test_an_election_read_only_on_sunday_waits_for_the_next_sunday"),
    ("ingest/store.py", "            if bad:\n                raise parse.Mismatch(bad[0])", "            if False:\n                raise parse.Mismatch(bad[0])",
     "tests/test_store.py::test_what_is_written_is_checked_against_the_source_inside_the_same_transaction"),
    ("ingest/store.py", "abs(x.m - a.total_money) > 0.000001 OR abs(x.v - a.total_in_kind) > 0.000001)", "abs(x.m - a.total_money) > 0.000001)",
     "tests/test_store.py::test_what_is_written_is_checked_against_the_source_inside_the_same_transaction"),
    ("ingest/store.py", 'conn.execute("UPDATE partii.election SET gone_at = %s WHERE erik_id = %s", (at, eid))',
     'conn.execute("DELETE FROM partii.observation WHERE %s IS NOT NULL AND erik_id = %s", (at, eid))',
     "tests/test_store.py::test_an_election_that_leaves_the_register_stays_with_its_rows"),
    ("ingest/store.py", 'again = {("home", "list")} | {(s, f)', "again = {(s, f)",
     "tests/test_store.py::test_freshness_is_quiet_when_all_is_well_and_tells_when_the_archive_stops"),
    ("ingest/store.py", 'if (source, ref, e["at"]) in done:', "if False:", "tests/test_store.py::test_a_second_run_changes_nothing"),
    ("ingest/store.py", 'rule = None if d is None else "BGN" if d < config.EURO_FROM else "EUR"', 'rule = "EUR"',
     "tests/test_built.py::test_the_currency_of_the_reports_is_a_rule_checked_on_the_data"),
    ("ingest/store.py", 'if rule and ((rule == "BGN" and eur > bgn) or (rule == "EUR" and bgn > eur)):', "if False:",
     "tests/test_store.py::test_a_report_in_leva_that_matches_the_register_only_in_euro_leaves_the_currency_unknown"),
    ("ingest/store.py", 'out = {"_answer": dict(records_total=t["records_total"], layout=t["kind"], columns=',
     'out = {"_answer": dict(records_total=t["records_total"], layout=t["kind"], file=e["sha256"], columns=',
     "tests/test_store.py::test_the_same_report_in_a_new_file_is_not_a_new_report"),
    ("ingest/run.py", "if age > config.MAX_ARCHIVE_AGE_H:", "if False:", "tests/test_store.py::test_the_step_refuses_an_archive_that_was_not_read_lately"),
    # the clean tables and the rules
    ("db/build/10_objects.sql", "WHERE label IS NOT NULL GROUP BY erik_id, label HAVING count(*) = 1;", "WHERE label IS NOT NULL GROUP BY erik_id, label;",
     "tests/test_store.py::test_a_text_that_two_participants_share_links_to_neither"),
    ("db/build/10_objects.sql", "WHERE s.tbl = 'agencies' AND u.is_eik_valid GROUP BY u.agency_eik;", "WHERE s.tbl = 'agencies' AND u.agency_eik IS NOT NULL GROUP BY u.agency_eik;",
     "tests/test_built.py::test_an_agency_is_a_company_only_by_a_valid_eik"),
    ("db/build/20_money.sql", "AND d.decl = 'none') AS over_none", ") AS over_none", "tests/test_built.py::test_the_threshold_counts_use_the_threshold_of_the_election"),
    ("db/build/20_money.sql", "core.to_eur(r.income, e.report_currency, e.election_day)", "round(r.income, 2)",
     "tests/test_built.py::test_the_currency_of_the_reports_is_a_rule_checked_on_the_data"),
    ("db/migrations/0001_init.sql", "FROM partii.reading WHERE read_at <= t", "FROM partii.reading WHERE true",
     "tests/test_built.py::test_every_read_of_the_register_is_kept_and_any_day_can_be_asked_for"),
    ("db/migrations/0001_init.sql", "CONSTRAINT donation_no_person_ck CHECK (donor_name IS NULL OR donor_kind = 'legal')", "CONSTRAINT donation_no_person_ck CHECK (true)",
     "tests/test_store.py::test_a_donor_with_a_legal_form_is_named_and_a_person_never_is"),
    ("db/checks.toml", "\n                            OR abs(coalesce(g.v, 0) - s.total_in_kind) > 0.01 * greatest(s.records_total, 1))", ")",
     "tests/test_store.py::test_a_clean_table_that_does_not_add_up_fails_its_rule"),
    ("db/checks.toml", "ORDER BY source, ref, load_id DESC) x WHERE status = 'rejected'", "ORDER BY source, ref, load_id DESC) x WHERE status = 'never'",
     "tests/test_store.py::test_a_broken_answer_is_recorded_and_nothing_of_it_is_written"),
    # the serving layer: nothing is published while a rule fails; what is published is the accepted readings; the pages read only it
    ("ingest/run.py", "    if failed:\n        return failed", "    if False:\n        return failed", "tests/test_store.py::test_nothing_is_published_while_a_rule_fails"),
    ("db/publish/08_pub_partii.donation.sql", "FROM partii.donation d WHERE EXISTS", "FROM partii.donation d WHERE d.row_no > 1 AND EXISTS",
     "tests/test_built.py::test_the_fixture_archive_builds_and_every_total_is_the_source_s"),
    ("app/queries.py", "FROM pub_partii.as_of(%(t)s) s JOIN pub_partii.donation d USING (load_id) JOIN pub_partii.participant p USING (participant_id)",
     "FROM partii.as_of(%(t)s) s JOIN partii.donation d USING (load_id) JOIN partii.participant p USING (participant_id)",
     "tests/test_pages.py::test_the_pages_read_only_the_serving_layer_and_say_which_publication"),
    # the pages: the same scope everywhere, no zero for what was not read
    ("app/main.py", "Q.donations(eid, d, parse_key(p)[1:] if p else None)]", "Q.donations(eid, None, parse_key(p)[1:] if p else None)]",
     "tests/test_pages.py::test_the_chosen_day_changes_the_number_the_table_the_chart_and_the_exports_alike"),
    ("app/main.py", 'return r["donor_name"] if r["donor_kind"] == "legal" else "физическо лице (едноличен търговец)" if r["donor_kind"] == "sole_trader" else "физическо лице"',
     'return r["row_key"]', "tests/test_pages.py::test_no_page_and_no_export_names_a_person"),
    ("app/main.py", 'w.writerow(["" if r[h] is None else r[h] for h in headers])', "w.writerow([0 if r[h] is None else r[h] for h in headers])",
     "tests/test_pages.py::test_a_table_that_was_not_read_is_no_data_not_zero"),
    ("app/templates/election.html", "{% if el.donations is none %}Регистърът на дарителите още не е прочетен.{% else %}", "{% if false %}{% else %}",
     "tests/test_pages.py::test_a_table_that_was_not_read_is_no_data_not_zero"),
]


def main():
    caught = 0
    for file, old, new, test in CASES:
        p = root / file
        original = p.read_bytes()
        text = original.decode("utf-8")
        if old not in text:
            raise SystemExit("mutation target missing: " + old)
        try:
            p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test], cwd=root,
                               capture_output=True, text=True, encoding="utf-8")
            if r.returncode == 0:
                raise SystemExit(f"SURVIVED {file}: {old}\n  no test failed: {test}")
            if "SKIPPED" in r.stdout or "skipped" in r.stdout.split("\n")[-2]:
                raise SystemExit("the database tests did not run: set PARTII_TEST_DSN")
            caught += 1
            print("caught", file, "|", old[:70].replace("\n", " "))
        finally:
            p.write_bytes(original)
    print(f"{caught} of {len(CASES)} mutations caught")


if __name__ == "__main__":
    main()

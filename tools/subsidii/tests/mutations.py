"""Each rule that guards the data is broken on purpose, in place, and the test that stands for it must fail.

    SUBSIDII_TEST_DSN=dbname=subsidii_test python tests/mutations.py

A mutation that no test notices is a rule nobody guards. The file is put back after each one.
"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent

# (file, text, replacement, the test that must fail)
CASES = [
    # the reader
    ("ingest/parse.py", "if not raw.endswith(b\"\\n\"):", "if False:", "tests/test_parse.py::test_a_truncated_file_is_not_a_file"),
    ("ingest/parse.py", "AMOUNT = re.compile(r\"^-?(\\d+(\\.\\d{1,2})?|\\.\\d{1,2})$\")", "AMOUNT = re.compile(r\"^-?[\\d.,]+$\")",
     "tests/test_parse.py::test_an_amount_with_a_decimal_comma_or_a_space_is_a_shape_error_not_a_number"),
    ("ingest/parse.py", "if len(f) != len(HEADER):", "if False:", "tests/test_parse.py::test_a_truncated_file_is_not_a_file"),
    ("ingest/parse.py", "for enc in (\"utf-8\", \"cp1251\"):", "for enc in (\"utf-8\",):", "tests/test_parse.py::test_the_report_says_utf8_but_is_cp1251_and_a_real_utf8_file_is_read_too"),
    ("ingest/parse.py", "if head == HEADER:", "if True:", "tests/test_parse.py::test_a_changed_header_or_another_page_stops_the_import"),
    ("ingest/parse.py", "if (f[OBLAST], f[OBSHTINA]) != owner[2:]:", "if False:", "tests/test_parse.py::test_the_structure_of_the_blocks_is_checked"),
    ("ingest/parse.py", "if ez + nb != en or e + en != tot:", "if False:", "tests/test_parse.py::test_a_recipient_whose_funds_do_not_add_up_is_counted"),
    ("ingest/parse.py", "if want != acc[fund]:", "if False:", "tests/test_parse.py::test_the_sums_of_a_file_agree_with_themselves_except_where_the_source_does_not"),
    ("ingest/parse.py", "if owner is None:", "if False:", "tests/test_parse.py::test_the_structure_of_the_blocks_is_checked"),
    # names
    ("ingest/names.py", "if _SOLE.match(n) and not _COMPANY.search(n):", "if _SOLE.match(n):", "tests/test_parse.py::test_the_kind_of_a_recipient"),
    ("ingest/names.py", "if surname.strip() not in (\"-\", \"\"):", "if False:", "tests/test_parse.py::test_the_kind_of_a_recipient"),
    ("ingest/names.py", ".replace(\"(област)\", \"област\")", "", "tests/test_parse.py::test_the_municipality_is_found_by_name_together_with_the_oblast"),
    ("ingest/names.py", "_LOOKALIKE = str.maketrans(\"ABCEHKMOPTXYabcehkmoptxy\", \"АВСЕНКМОРТХУАВСЕНКМОРТХУ\")", "_LOOKALIKE = str.maketrans(\"\", \"\")",
     "tests/test_parse.py::test_the_search_key_ignores_case_quotes_spaces_and_latin_look_alikes"),
    # the archive
    ("ingest/archive.py", "if not sha.startswith(snap.sha12):", "if False:", "tests/test_archive.py::test_the_file_is_checked_against_its_name"),
    ("ingest/archive.py", "if idx is not None and snap.rel in idx and (sha, len(raw)) != tuple(idx[snap.rel][:2]):", "if False:",
     "tests/test_archive.py::test_the_file_is_checked_against_the_record_of_the_archive"),
    ("ingest/population.py", "raise PopulationError(\"непознато име на серия: \" + repr(s[\"name\"]))", "continue", "tests/test_population.py::test_an_answer_of_another_shape_is_an_error"),
    # silver
    ("ingest/load.py", "if archive_rows is not None and stats[\"rows\"] != archive_rows:", "if False:", "tests/test_store.py::test_the_count_of_the_archive_must_agree_with_the_rows_read"),
    ("ingest/load.py", "stats[\"last_end\"] > e:", "stats[\"last_end\"] > e.replace(year=e.year + 5):", "tests/test_store.py::test_a_payment_date_outside_the_financial_year_is_not_loaded"),
    ("ingest/load.py", "if stats[\"identity_fail\"]:", "if False:", "tests/test_store.py::test_a_failed_check_writes_nothing"),
    ("ingest/load.py", "if len(_diff_blocks(stats)) > limit:", "if False:", "tests/test_store.py::test_too_many_recipients_that_do_not_add_up_are_not_loaded"),
    ("ingest/load.py", "row_number() OVER (PARTITION BY total, key, beneficiary_id ORDER BY n) AS rn", "1 AS rn",
     "tests/test_store.py::test_a_payment_row_listed_twice_is_two_rows_with_two_numbers"),
    ("ingest/load.py", "CASE WHEN s.name <> s.o_name THEN s.name END", "NULL",
     "tests/test_store.py::test_the_name_on_a_payment_row_that_differs_from_its_block_is_kept_and_the_row_stays_in_its_block"),
    ("ingest/load.py", "AND NOT EXISTS (SELECT 1 FROM stage.line s WHERE s.total AND s.key = h.key AND s.occ = h.occ)", "AND FALSE",
     "tests/test_store.py::test_a_recipient_that_leaves_and_comes_back_has_two_spans"),
    ("ingest/load.py", "AND s.beneficiary_id = p.beneficiary_id AND s.occ = p.occ)\"\"\", (prev, fy)).rowcount", "AND s.beneficiary_id = p.beneficiary_id AND s.occ = p.occ AND FALSE)\"\"\", (prev, fy)).rowcount",
     "tests/test_store.py::test_a_changed_amount_closes_one_span_and_opens_another_and_the_old_day_stays_readable"),
    # the policy
    ("ingest/build.py", "    if not prev:\n        return None\n    holders, payments, total = prev", "    return None\n    holders, payments, total = prev",
     "tests/test_store.py::test_a_much_smaller_file_is_held_until_the_second_read_a_day_later"),
    ("ingest/build.py", "if total and stats[\"total\"][\"total\"] < config.HOLD_SUM * total:", "if False:", "tests/test_store.py::test_a_smaller_total_is_held_too"),
    ("ingest/build.py", "last - first_seen >= dt.timedelta(hours=20)", "last - first_seen >= dt.timedelta(0)", "tests/test_store.py::test_a_much_smaller_file_is_held_until_the_second_read_a_day_later"),
    ("ingest/build.py", "return seen.get(\"sha\") == sha and", "return True and", "tests/test_store.py::test_a_much_smaller_file_is_held_until_the_second_read_a_day_later"),
    ("ingest/build.py", "if newest and (day < newest or", "if False and (day < newest or", "tests/test_store.py::test_an_older_file_arriving_late_rebuilds_the_year_in_order"),
    ("ingest/build.py", "if in_form and fy not in form:", "if False:", "tests/test_store.py::test_a_year_that_is_no_longer_in_the_form_stays_with_its_data"),
    ("ingest/checks.py", "if age > config.MAX_ARCHIVE_AGE_H:", "if False:", "tests/test_store.py::test_an_archive_older_than_eight_days_is_a_problem"),
    ("ingest/checks.py", "if today > dt.date(want, 12, 31) and want not in snaps:", "if False:", "tests/test_store.py::test_the_new_financial_year_is_expected_by_the_end_of_december"),
    ("ingest/checks.py", "if ccy is None:", "if False:", "tests/test_store.py::test_a_year_without_a_confirmed_currency_is_a_problem_not_a_guess"),
    # gold
    ("db/gold/10_snapshot.sql", "WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND (h.to_day IS NULL OR h.to_day >= %(snap)s);\n\nCREATE TEMP TABLE p",
     "WHERE h.fy = %(fy)s AND h.from_day <= %(snap)s AND h.to_day IS NULL;\n\nCREATE TEMP TABLE p", "tests/test_gold.py::test_every_snapshot_keeps_its_own_aggregates"),
    ("db/gold/10_snapshot.sql", "CASE WHEN y.eur_per_unit IS NULL THEN NULL ELSE round(i.amount * y.eur_per_unit, 2) END,", "round(i.amount, 2),",
     "tests/test_gold.py::test_a_price_in_leva_is_shown_in_euro_at_the_fixed_rate_and_the_original_stays"),
    ("db/migrations/0002_gold.sql", "WHEN gold.today() < y.names_until THEN btrim(", "WHEN true THEN btrim(",
     "tests/test_gold.py::test_the_name_of_a_person_is_shown_for_two_years_after_the_year_and_then_replaced"),
    ("db/migrations/0002_gold.sql", "ELSE 'физическо лице' END AS name_export,", "ELSE btrim(b.name || ' ' || b.surname) END AS name_export,",
     "tests/test_gold.py::test_an_export_never_has_the_name_of_a_person_not_even_inside_the_term"),
    ("db/migrations/0002_gold.sql", "SELECT 2 $$;\n\n-- the recipients", "SELECT 20 $$;\n\n-- the recipients",
     "tests/test_gold.py::test_the_name_of_a_person_is_shown_for_two_years_after_the_year_and_then_replaced"),
    ("db/checks/02_totals_reconcile.sql", "WHERE f.stated IS DISTINCT FROM f.summary OR", "WHERE false AND f.stated IS DISTINCT FROM f.summary OR false AND",
     "tests/test_gold.py::test_a_total_that_does_not_reconcile_stops_the_snapshot"),
    ("db/checks/05_unmatched_share.sql", "HAVING coalesce(sum(n), 0) * 500 >", "HAVING coalesce(sum(n), 0) * 500 > 1e9 + ",
     "tests/test_gold.py::test_a_recipient_in_an_unknown_municipality_stops_the_snapshot_and_leaves_the_earlier_one"),
    ("db/checks/04_periods_inside_year.sql", "AND (p.starts < y.starts OR p.ends > y.ends)", "AND false", "tests/test_gold.py::test_a_payment_outside_the_year_stops_gold_even_if_silver_let_it_in"),
    ("ingest/gold.py", "if problems:\n                raise ChecksFailed(problems)", "if False:\n                raise ChecksFailed(problems)",
     "tests/test_gold.py::test_a_recipient_in_an_unknown_municipality_stops_the_snapshot_and_leaves_the_earlier_one"),
]


def main():
    caught = 0
    for file, old, new, test in CASES:
        p = root / file
        original = p.read_bytes()
        text = original.decode("utf-8").replace("
", "
")
        if old not in text:
            raise SystemExit("mutation target missing: " + old)
        try:
            p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test], cwd=root,
                               capture_output=True, text=True, encoding="utf-8")
            if r.returncode == 0:
                raise SystemExit(f"SURVIVED {file}: {old}\n  no test failed: {test}")
            if "skipped" in r.stdout.split("\n")[-2]:
                raise SystemExit("the database tests did not run: set SUBSIDII_TEST_DSN")
            caught += 1
            print("caught", file, "|", old[:70].replace("\n", " "))
        finally:
            p.write_bytes(original)
    print(f"{caught} of {len(CASES)} mutations caught")


if __name__ == "__main__":
    main()

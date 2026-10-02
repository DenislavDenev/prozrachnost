"""Each rule that guards the data is broken on purpose, in place, and the test that stands for it must fail.

    SIGURNOST_TEST_DSN=dbname=sigurnost_test python tests/mutations.py

A mutation that no test notices is a rule nobody guards. The file is put back after each one.
"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent

STORE = "tests/test_store.py::"
SHEET = "tests/test_sheet.py::"
CHECKS = "tests/test_checks.py::"
HOLD = STORE + "test_a_smaller_answer_is_held_until_a_second_read_a_day_later_says_the_same"
FAILED = STORE + "test_a_failed_check_publishes_nothing_and_leaves_the_previous_gold"
FRESH = STORE + "test_freshness_is_quiet_when_all_is_well_and_speaks_for_each_problem"

# (file, text, replacement, the test that must fail)
CASES = [
    # the reader
    ("ingest/sheet.py", 'if t in ("", "-", "–"):', 'if t in ("",):', SHEET + "test_a_dash_is_no_value_and_no_issue_zero_is_a_value"),
    ("ingest/sheet.py", "    return None, True\n", "    return Decimal(0), False\n",
     SHEET + "test_structure_after_structure_gives_one_block_each_and_the_stray_letter_is_an_issue_not_a_zero"),
    ("ingest/sheet.py", 'elif out.endswith("-") and not out.endswith(" -"):', "elif False:",
     SHEET + "test_merged_header_rows_are_joined_and_words_cut_by_a_hyphen_are_rejoined"),
    ("ingest/sheet.py", "if grid and len({len(r) for r in grid}) != 1:", "if False:",
     SHEET + "test_ragged_rows_and_a_table_without_a_header_are_shape_errors"),
    ("ingest/sheet.py", "if bad * 50 > cells:", "if False:", SHEET + "test_texts_in_many_cells_are_not_a_table_of_numbers"),
    ("ingest/sheet.py", 'if d == {"success": True}:\n        return []', 'if False:\n        return []',
     SHEET + "test_a_resource_without_a_table_is_empty_not_an_error"),
    ("ingest/sheet.py", "if m:\n        marker = m.group(0).strip()", "if False:\n        marker = m.group(0).strip()",
     SHEET + "test_numbering_gives_the_levels_and_the_markers_the_parts"),
    # which table is it
    ("ingest/templates.py", 'if first.startswith("престъпления против личността"):', "if True:",
     SHEET + "test_the_kind_is_found_from_the_header_not_from_the_name"),
    ("ingest/templates.py", "    if got != labels:", "    if False:",
     SHEET + "test_a_table_of_a_known_title_with_other_columns_is_a_template_error"),
    ("ingest/templates.py", 'fam = "types" if dim.startswith("видове престъпления") else "structures" if dim.startswith("области") else None',
     'fam = "types"', SHEET + "test_the_kind_is_found_from_the_header_not_from_the_name"),
    # reconciliations
    ("ingest/checks.py", "for r in block.rows if not r.total), Decimal(0))\n        out.append(_res(\"structures_sum\"",
     "for r in block.rows), Decimal(0))\n        out.append(_res(\"structures_sum\"", CHECKS + "test_the_structures_add_up_to_the_total_row_exactly"),
    ("ingest/checks.py", "return Result(check_id, scope, expected, got, OK if expected == got else (DIFFERS if gating else SOURCE), gating, detail)",
     "return Result(check_id, scope, expected, got, OK, gating, detail)", CHECKS + "test_a_structure_changed_by_one_makes_the_sum_differ"),
    ("ingest/checks.py", "elif r.level == 2 and cur is not None:", "elif r.level >= 2 and cur is not None:",
     CHECKS + "test_the_points_add_up_to_their_subpoints_where_the_source_does_and_the_gap_is_named_where_it_does_not"),
    ("ingest/checks.py", "if reg > 0 and cl is not None and abs(sol * 100 / reg - cl) > TOL:", "if False:",
     CHECKS + "test_a_wrong_printed_share_is_counted"),
    ("ingest/checks.py", "            oblast_sum += value(t, c) or 0\n", "            oblast_sum += 0\n",
     CHECKS + "test_each_block_total_is_its_row_by_structures_and_the_blocks_add_up_to_the_country"),
    # silver
    ("ingest/load.py", 'if rows < config.HOLD_ROWS * prev["n_rows"]:', "if False:", HOLD),
    ("ingest/load.py", "HOLD_AFTER = dt.timedelta(hours=20)", "HOLD_AFTER = dt.timedelta(0)", HOLD),
    ("ingest/load.py", "if held and held[0] == sha and now - held[1] >= HOLD_AFTER:", "if held and now - held[1] >= HOLD_AFTER:",
     STORE + "test_a_different_second_answer_does_not_confirm_the_held_one"),
    ("ingest/load.py", 'if sh.kind != "table":\n        return "таблицата стана празна"', 'if False:\n        return "таблицата стана празна"',
     STORE + "test_a_table_that_turns_empty_is_held_not_taken"),
    ("ingest/load.py", "AND status IN ('built', 'invalid')", "AND status = 'xxx'",
     STORE + "test_a_second_run_of_the_same_answers_changes_nothing_and_logs_nothing"),
    ("ingest/load.py", 'c.execute("UPDATE silver.resource SET is_current = false WHERE resource_uri = %s AND is_current", (uri,))', "pass",
     STORE + "test_a_changed_answer_that_is_not_smaller_replaces_the_old_and_is_logged_field_by_field"),
    ("ingest/archive.py", 'if hashlib.sha256(raw).hexdigest() != ent["sha"]:', "if False:",
     "tests/test_archive.py::test_a_file_that_does_not_match_its_hash_is_an_error"),
    # gold
    ("ingest/gold.py", "ORDER BY r.n_rows DESC,", "ORDER BY r.n_rows ASC,",
     STORE + "test_two_versions_of_a_year_use_the_fuller_one_and_the_difference_is_logged"),
    ("ingest/gold.py", 'if "structures" in stop:\n            stop |=', 'if False:\n            stop |=', FAILED),
    ("ingest/gold.py", "bad = {fam: [r for r in rs if r.gating and r.status == checks.DIFFERS] for fam, rs in res.items()}",
     "bad = {fam: [] for fam, rs in res.items()}", FAILED),
    ("ingest/gold.py", "if code is None:\n                    c.execute(", "if False:\n                    c.execute(",
     STORE + "test_an_unknown_structure_is_unmatched_and_stops_the_tables_read_against_it"),
    # freshness
    ("ingest/checks.py", "if age > config.MAX_ARCHIVE_AGE_H:", "if False:", FRESH),
    ("ingest/checks.py", "if now - first > dt.timedelta(days=1):", "if False:", FRESH),
    ("ingest/run.py", "if age > config.MAX_ARCHIVE_AGE_H and not a.force:", "if False:", STORE + "test_the_step_refuses_an_old_archive"),
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
            if "skipped" in r.stdout.split("\n")[-2]:
                raise SystemExit("the database tests did not run: set SIGURNOST_TEST_DSN")
            caught += 1
            print("caught", file, "|", old[:70].replace("\n", " "))
        finally:
            p.write_bytes(original)
    print(f"{caught} of {len(CASES)} mutations caught")


if __name__ == "__main__":
    main()

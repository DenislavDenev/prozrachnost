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
MOVE = "tests/test_move.py::"
HOLD = STORE + "test_a_smaller_answer_is_held_until_a_second_read_a_day_later_says_the_same"
FAILED = STORE + "test_a_failed_check_publishes_nothing_and_leaves_the_previous_tables"
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
    ("ingest/sheet.py", "if bad * 20 > cells:", "if False:", SHEET + "test_texts_in_many_cells_are_not_a_table_of_numbers"),
    ("ingest/sheet.py", 'if d == {"success": True}:\n        return []', 'if False:\n        return []',
     SHEET + "test_a_resource_without_a_table_is_empty_not_an_error"),
    ("ingest/sheet.py", "if m:\n        marker = m.group(0).strip()", "if False:\n        marker = m.group(0).strip()",
     SHEET + "test_numbering_gives_the_levels_and_the_markers_the_parts"),
    # which table is it
    ("ingest/templates.py", 'if first.startswith("престъпления против личността"):', "if True:",
     SHEET + "test_the_kind_is_found_from_the_header_not_from_the_name"),
    ("ingest/templates.py", "    if unknown:", "    if False:",
     SHEET + "test_a_column_of_a_family_whose_meaning_is_unknown_is_a_template_error"),
    ("ingest/templates.py", 'fam = "types" if dim.startswith("видове престъпления") else "structures"',
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
    # the landing zone (through the shared module)
    ("ingest/load.py", "if n_rows(rows) < config.HOLD_ROWS * have:", "if False:", HOLD),
    ("ingest/load.py", "hold=dt.timedelta(hours=config.HOLD_AFTER_H), hold_when=smaller)", "hold=dt.timedelta(0), hold_when=smaller)", HOLD),
    ("ingest/load.py", "hold_when=smaller)", "hold_when=lambda old, rows: 0)", STORE + "test_a_table_that_turns_empty_is_held_not_taken"),
    ("ingest/load.py", 'if last and last[0] == sha and last[1] != "held" and not reread:', "if False:",
     STORE + "test_a_second_run_of_the_same_answers_changes_nothing_and_logs_nothing"),
    ("ingest/load.py", 'pipeline.reject(conn, r, SOURCE, uri, str(e)[:500], file.raw_file_id, now=now)\n        return "invalid"', "raise",
     STORE + "test_an_html_answer_is_rejected_and_the_good_version_stays"),
    ("ingest/load.py", "current=read_table, now=now,", "current=None, now=now,",
     STORE + "test_a_changed_answer_that_is_not_smaller_replaces_the_old_and_is_logged_row_by_row"),
    ("ingest/archive.py", 'if hashlib.sha256(raw).hexdigest() != ent["sha"]:', "if False:",
     "tests/test_archive.py::test_a_file_that_does_not_match_its_hash_is_an_error"),
    # the clean tables
    ("ingest/gold.py", "ORDER BY r.n_rows DESC,", "ORDER BY r.n_rows ASC,",
     STORE + "test_two_versions_of_a_year_use_the_fuller_one_and_the_note_says_so"),
    ("ingest/gold.py", 'if "structures" in stop:\n            stop |=', 'if False:\n            stop |=', FAILED),
    ("ingest/gold.py", "bad = {fam: [r for r in rs if r.gating and r.status == checks.DIFFERS] for fam, rs in res.items()}",
     "bad = {fam: [] for fam, rs in res.items()}", FAILED),
    ("ingest/gold.py", "if code is None:\n                    c.execute(", "if False:\n                    c.execute(",
     STORE + "test_an_unknown_structure_is_unmatched_and_stops_the_tables_read_against_it"),
    ("ingest/gold.py", 'c.execute("DELETE FROM sigurnost.reconciliation WHERE year = %s AND family = %s", (year, fam))', "pass", FAILED),
    # the original's own differences, duplicates, isolated tables
    ("ingest/checks.py", "note = known.get((resource_uri, r.check_id, r.scope, r.expected, r.got))", "note = known.get((r.check_id, r.scope, r.expected, r.got))",
     CHECKS + "test_a_known_difference_of_the_original_is_marked_source_and_any_other_number_still_differs"),
    ("ingest/sheet.py", "            if key in seen:", "            if False:", SHEET + "test_a_numbered_row_printed_twice_is_flagged_and_the_count_of_rows_keeps_it"),
    ("ingest/sheet.py", "    if not flat:\n        for k in range(1, len(live) - 1):", "    if False:\n        for k in range(1, len(live) - 1):",
     SHEET + "test_a_label_with_no_numbers_between_rows_of_numbers_is_a_row_without_values_not_a_new_block"),
    ("ingest/gold.py", "return [r for r in _candidates(c, year, fam) if r[0] not in skip]", "return list(_candidates(c, year, fam))",
     STORE + "test_an_isolated_table_is_kept_in_the_landing_zone_and_out_of_the_clean_tables_without_an_alarm"),
    ("ingest/gold.py", "AND (r.title_year IS NULL OR r.title_year = d.year)", "",
     STORE + "test_a_table_whose_banner_names_another_year_is_kept_in_the_landing_zone_and_left_out_of_the_clean_tables"),
    ("ingest/templates.py", 'return "persons_prev" if s.startswith("установени извършители") else "solved_prev"', 'return "solved_prev"',
     SHEET + "test_the_old_structures_table_and_the_old_main_table_are_found"),
    ("ingest/templates.py", "    if set(inds) not in ALLOWED[fam]:", "    if False:", SHEET + "test_two_columns_with_one_meaning_or_a_missing_indicator_are_template_errors"),
    ("ingest/checks.py", 'if code.startswith("GD-") and code not in by), Decimal(0))', 'if code.startswith("GD-")), Decimal(0))',
     CHECKS + "test_a_general_directorate_with_a_block_of_its_own_is_counted_once"),
    # links
    ("ingest/load.py", 'if e["resource_type"] != "Хиперлинк" or not e["resource_url"]:', "if False:",
     STORE + "test_the_monthly_bulletin_is_five_links_with_their_months_never_opened"),
    ("ingest/load.py", 'if res["resource_type"] == "Хиперлинк":', "if False:", STORE + "test_the_monthly_bulletin_is_five_links_with_their_months_never_opened"),
    ("ingest/load.py", "if not m or m.group(1).lower() not in MONTHS:", "if not m:", STORE + "test_the_period_of_a_bulletin_comes_from_its_name"),
    ("ingest/checks.py", "if last_bulletin and (now.date() - last_bulletin).days > 75:", "if False:", STORE + "test_the_monthly_bulletin_is_five_links_with_their_months_never_opened"),
    # freshness
    ("ingest/checks.py", "if age > config.MAX_ARCHIVE_AGE_H:", "if False:", FRESH),
    ("ingest/checks.py", "               WHERE x.status = 'rejected'", "               WHERE false", STORE + "test_an_html_answer_is_rejected_and_the_good_version_stays"),
    ("ingest/run.py", "if age > config.MAX_ARCHIVE_AGE_H and not a.force:", "if False:", STORE + "test_the_step_refuses_an_old_archive"),
    # the rules, the publication and the page of the sources
    ("ingest/run.py", "return problems + (failed or pipeline.publish(conn, r, ROOT).problems)",
     "return problems + failed + pipeline.publish(conn, r, ROOT).problems", STORE + "test_a_rule_that_fails_keeps_the_serving_layer_where_it_was"),
    ("ingest/run.py", 'for kind, lines in (("problem", problems), ("info", info)):', 'for kind, lines in (("info", info),):',
     STORE + "test_the_problems_and_notes_of_freshness_are_published_for_the_page"),
    ("db/checks.toml", "AND c.text = o.value_text AND c.value IS NOT DISTINCT FROM o.value", "AND c.text = o.value_text",
     STORE + "test_a_rule_that_fails_keeps_the_serving_layer_where_it_was"),
    ("db/checks.toml", "WHERE NOT r.dup),", "),", STORE + "test_a_known_difference_and_a_duplicate_row_publish_the_table_with_the_difference_named"),
    ("app/queries.py", "FROM pub_sigurnost.observation o\n                  JOIN pub_sigurnost.structure s", "FROM sigurnost.observation o\n                  JOIN pub_sigurnost.structure s",
     "tests/test_pages.py::test_every_answer_says_its_publication_and_the_web_reads_only_the_serving_layer"),
    ("ingest/load.py", "return t if t.tzinfo else t.replace(tzinfo=PORTAL_ZONE)", "return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)",
     STORE + "test_the_times_of_the_portal_are_local_time_of_sofia"),
    ("app/queries.py", "    if not rows:\n        return\n    same = bool(other) and", "    if not rows or not other:\n        return\n    same = bool(other) and",
     "tests/test_pages.py::test_types_compared_with_a_year_of_another_template_render_with_no_data_not_a_server_error"),
    # the move to the shared database
    ("ingest/parity.py", "groups += _diff(o, n, detail, \"стойности\")", "groups += 0", MOVE + "test_parity_names_a_value_a_table_and_a_reconciliation_that_differ"),
    ("ingest/parity.py", "o[uri] = (*row, dig[0][0], dig[1][0], dig[2][0])", "o[uri] = (*row, dig[0][0], dig[1][0], dig[2][0])[:-1] + (None,)",
     MOVE + "test_replay_from_the_files_the_old_database_names_gives_tables_equal_to_it"),
    ("ingest/run.py", "        out = load.store(conn, r, sets[set_uri], res, (raw, sha, path, read_at), now=read_at)\n", "        out = \"skipped\"\n",
     MOVE + "test_replay_from_the_files_the_old_database_names_gives_tables_equal_to_it"),
]


def main():
    caught = 0
    for file, old, new, test in CASES:
        p = root / file
        original = p.read_bytes()
        text = original.decode("utf-8")
        if "\r\n" in text:        # a Windows checkout (autocrlf): the targets are written with LF
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
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

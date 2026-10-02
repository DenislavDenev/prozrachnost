"""Mutation check of the tests (STANDARD 1А.7): each rule that guards the data is broken on purpose in a copy of
the tool and the named tests must fail. Run from the tool folder, with the test database:

    PARLAMENT_TEST_DSN=dbname=parlament_test python tests/mutations.py

Prints one line per mutation and exits 1 when a mutation survives (the tests did not notice it).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S, T = "tests/test_sources.py", "tests/test_store.py"

MUTATIONS = [
    ("a vote is counted by group", "ingest/parse.py",
     "        if lone or size > SMALL:", "        if False:",
     f"{S}::test_the_two_files_agree_and_prove_the_meaning_of_the_codes {T}::test_files_that_do_not_add_up_are_not_written_and_are_reported"),
    ("for is +, against is -", "ingest/parse.py",
     'got = {g: (c.count("+"), c.count("-"),', 'got = {g: (c.count("-"), c.count("+"),',
     f"{S}::test_the_two_files_agree_and_prove_the_meaning_of_the_codes"),
    ("only П is present at a registration", "ingest/parse.py",
     'got = {g: (c.count("П") + (c.count("Д") if remote else 0), len(c))', 'got = {g: (len(c) - c.count("О"), len(c))',
     f"{S}::test_the_two_files_agree_and_prove_the_meaning_of_the_codes"),
    ("a large difference stops the sitting", "ingest/parse.py",
     "or size > SMALL:", "or size > 99:",
     f"{S}::test_a_difference_of_a_vote_is_a_note_a_larger_one_stops_the_sitting"),
    ("the roll call and the file by group have the same items", "ingest/parse.py",
     "    if set(counted) != set(items):", "    if False:",
     f"{S}::test_the_roll_call_and_the_file_by_group_must_have_the_same_items"),
    ("an unknown code stops the roll call", "ingest/parse.py",
     "    if code not in VOTES and code not in MARKS:", "    if False:",
     f"{S}::test_the_roll_call_refuses_what_it_does_not_know"),
    ("when the groups differ, the whole assembly must add up", "ingest/parse.py",
     '        adds_up = off(roll, it["total"], allow_all) == 0', "        adds_up = True",
     f"{S}::test_the_two_files_agree_and_prove_the_meaning_of_the_codes"),
    ("an MP with codes out of place is set aside", "ingest/parse.py",
     "    return [v for v in votes if v[0] not in bad], bad", "    return votes, {}",
     f"{S}::test_an_mp_with_codes_out_of_place_is_set_aside_and_named {T}::test_an_mp_set_aside_is_said_on_the_sitting"),
    ("the file by group must be of the sitting's day", "ingest/load.py",
     '            if days != {s["date"]}:', "            if False:",
     f"{T}::test_the_day_is_the_contents_not_the_names"),
    ("a renamed group stays one group", "ingest/stats.py",
     "        for asm, code, group in aliases():", "        for asm, code, group in []:",
     f"{T}::test_the_groups_get_one_code_and_a_renamed_group_stays_one"),
    ("fewer votes wait for a second read", "ingest/load.py",
     "        if (len(items) < n_items or len(votes) < n_votes) and hold(", "        if False and hold(",
     f"{T}::test_fewer_votes_wait_for_a_second_read_a_day_later"),
    ("the second read must be a day later", "ingest/load.py",
     "    if not held[1]:", "    if False:",
     f"{T}::test_fewer_votes_wait_for_a_second_read_a_day_later"),
    ("the same files change nothing", "ingest/load.py",
     '        return "unchanged", s["assembly"]', "        pass",
     f"{T}::test_the_same_files_again_change_nothing"),
    ("a changed vote is logged", "ingest/load.py",
     "    if logged:", "    if False:",
     f"{T}::test_a_vote_changed_by_the_source_is_logged_and_the_small_difference_is_said"),
    ("a group's line needs a majority", "ingest/stats.py",
     "CASE WHEN y > n + a THEN '+' WHEN", "CASE WHEN y >= n + a THEN '+' WHEN",
     f"{T}::test_two_sittings_are_written_checked_and_counted"),
    ("against and abstained are one side", "ingest/stats.py",
     "AND (v.code = '+') <> (l.line = '+')),", "AND v.code <> l.line),",
     f"{T}::test_two_sittings_are_written_checked_and_counted"),
    ("the constituency only by a unique name", "ingest/stats.py",
     "              AND (SELECT count(*) FROM live.roster x WHERE x.assembly = r.assembly AND x.name = r.name) = 1\n", "",
     f"{T}::test_the_roster_gives_the_constituency_only_by_a_unique_full_name"),
    ("a sheet is read as the CSV it was exported from", "ingest/parse.py",
     "str(int(v)) if isinstance(v, float) and v.is_integer() else", "",
     f"{S}::test_a_sheet_of_2009_reads_as_the_csv_and_adds_up"),
    ("a stenogram with fewer speeches waits", "ingest/load.py",
     "    if old_sha and len(sp) < old_n and hold(", "    if False and hold(",
     f"{T}::test_a_sitting_keeps_its_stenogram_video_and_a_shorter_one_waits"),
    ("a person across assemblies only by a name that is there once", "ingest/people.py",
     "GROUP BY pp.profile, pp.assembly HAVING count(*) = 1", "GROUP BY pp.profile, pp.assembly",
     f"{T}::test_the_assemblies_their_people_and_the_same_person_across_them"),
    ("the absences are kept when the Assembly drops them", "ingest/people.py",
     "            n = 0\n", "            conn.execute(f\"DELETE FROM {table}\")\n            n = 0\n",
     f"{T}::test_the_assemblies_their_people_and_the_same_person_across_them"),
    ("an old sitting's assembly is the last one begun by its day", "ingest/load.py",
     "ORDER BY a.start DESC LIMIT 1", "ORDER BY a.start LIMIT 1",
     f"{T}::test_an_old_sitting_is_kept_with_its_scan_and_its_assembly_by_date {T}::test_a_sitting_of_2009_is_read_from_its_sheets"),
    ("online registrations are counted the way the file by group does", "ingest/parse.py",
     "(c.count(\"Д\") if remote else 0)", "c.count(\"Д\")",
     f"{S}::test_online_registration_is_read_and_counted_the_way_the_file_by_group_does"),
    ("a count written as hall + online is their sum", "ingest/parse.py",
     'return sum(_int(x, what) for x in (s or "").split("+"))', 'return _int((s or "").split("+")[0], what)',
     f"{S}::test_the_online_sittings_of_2021_count_the_hall_plus_online"),
    ("a sheet without the empty cell after the name is read from its own columns", "ingest/parse.py",
     "at = 1 if len(r) > 1 and r[1].strip().isdigit() else 2", "at = 2",
     f"{S}::test_two_more_sheets_of_the_wide_roll_call"),
    ("a header repeated on a printed page is not an MP", "ingest/parse.py",
     "if not any(x.strip() for x in r) or r == head:", "if not any(x.strip() for x in r):",
     f"{S}::test_two_more_sheets_of_the_wide_roll_call"),
    ("a failing sitting does not stop the run", "ingest/load.py",
     "                got, assembly = \"no-answer\", None\n                state(conn, f\"sten/{sid}\", status=\"no-answer\", error=str(e)[:2000])\n            except (http.Gone",
     "                raise\n            except (http.Gone",
     f"{T}::test_a_sitting_the_source_cannot_answer_is_reported_and_the_rest_go_on"),
    ("recheck does not try again a sitting the source cannot answer", "ingest/load.py",
     "AND status IN ('invalid', 'no-files') ORDER BY 1", "AND status IN ('invalid', 'no-files', 'no-answer') ORDER BY 1",
     f"{T}::test_a_sitting_the_source_cannot_answer_is_reported_and_the_rest_go_on"),
    ("a vote is a bill's only by the same short title", "ingest/bills.py",
     "                if key(topic) == k:", "                if True:",
     f"{T}::test_a_bill_its_steps_and_its_votes"),
    ("a bill with fewer steps waits", "ingest/bills.py",
     "        if old and len(b[\"steps\"]) < old[1] and hold(", "        if False and hold(",
     f"{T}::test_a_bill_its_steps_and_its_votes"),
    ("on the first days of a month last month's list is fresh", "ingest/checks.py",
     'months = [f"{today.year}-{today.month:02d}", f"{last.year}-{last.month:02d}"]', 'months = [f"{today.year}-{today.month:02d}"]',
     f"{T}::test_freshness_knows_a_stale_list"),
    ("a late sitting is reported", "ingest/checks.py",
     '        if status == "held":\n            continue', "        continue",
     f"{T}::test_an_empty_file_is_no_roll_call_and_a_late_sitting_is_reported_for_a_week"),
]


def pytest(copy, tests):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-rs", *tests],
                          cwd=copy, capture_output=True, text=True)


def main():
    # the same tests on the unbroken code must pass and none be skipped, else a broken environment (no database,
    # a missing package) would count every mutation as caught
    tests = sorted({t for *_, ts in MUTATIONS for t in ts.split()})
    r = pytest(ROOT, tests)
    if r.returncode != 0 or "SKIPPED" in r.stdout:
        print("the tests do not pass on the unbroken code, no mutation is checked:\n" + r.stdout[-2000:] + r.stderr[-1000:])
        sys.exit(1)
    print(f"baseline {r.stdout.strip().splitlines()[-1]}")
    survived = 0
    for name, path, old, new, tests in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "t"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".data"))
            f = copy / path
            src = f.read_text(encoding="utf-8")
            assert src.count(old) == 1, f"{name}: the code to break is not found once in {path}"
            f.write_text(src.replace(old, new), encoding="utf-8")
            r = pytest(copy, tests.split())
            caught = r.returncode != 0
            survived += not caught
            print(f"{'caught  ' if caught else 'SURVIVED'} {name}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()

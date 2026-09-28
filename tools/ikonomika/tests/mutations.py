"""Mutation check of the tests (STANDARD 1А.7): each rule that guards the data is broken on purpose in a copy of
the tool and the named tests must fail. Run from the tool folder, with the test database:

    IKONOMIKA_TEST_DSN=dbname=ikonomika_test python tests/mutations.py

Prints one line per mutation and exits 1 when a mutation survives (the tests did not notice it).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MUTATIONS = [
    ("the unit must be pinned", "ingest/jsonstat.py",
     'if "unit" in ids and "unit" not in pinned:', 'if False:', "tests/test_sources.py::test_an_unpinned_unit_is_refused"),
    ("the position comes from size (row-major)", "ingest/jsonstat.py",
     "        at.reverse()\n", "", "tests/test_sources.py"),
    ("the flag of a value is kept", "ingest/jsonstat.py",
     "rows.append((key, geo, time, v, status.get(pos) or None))", "rows.append((key, geo, time, v, None))",
     "tests/test_sources.py::test_gdp_values_and_provisional_flag"),
    ("the regions add up to the country", "ingest/checks.py",
     "            if abs(s - g[\"BG\"]) > NUTS_TOLERANCE:", "            if False:",
     "tests/test_sources.py::test_regions_add_up_to_the_country tests/test_store.py::test_eurostat_step_writes_checks_and_keeps_what_fails"),
    ("the parts add up to the whole", "ingest/checks.py",
     "            if abs(s - g[whole]) > SUM_TOLERANCE:", "            if False:",
     "tests/test_sources.py::test_the_parts_add_up_to_the_whole_and_one_moved_part_is_caught"),
    ("the rates follow from the index", "ingest/checks.py",
     "            if not lo <= v <= hi:", "            if False:",
     "tests/test_sources.py::test_monthly_and_annual_rates_follow_from_the_index"),
    ("a smaller answer is held", "ingest/store.py",
     "        if removed:\n            if not held", "        if False:\n            if not held",
     "tests/test_store.py::test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees"),
    ("a hold is confirmed only a day later", "ingest/store.py",
     "            if not held[1]:\n                return \"held\"\n", "",
     "tests/test_store.py::test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees"),
    ("a change is logged", "ingest/store.py",
     "                            db.log_change(conn, source, name, field, cur[k][i], new[k][i], \"rewritten\")",
     "                            pass", "tests/test_store.py::test_a_revision_and_a_new_period_are_logged_field_by_field"),
    ("the BNB rate and its inverse agree", "ingest/bnb.py",
     "                if inv is not None and not agree(rate / units, inv, g[3]):", "                if False:",
     "tests/test_sources.py::test_bnb_rate_and_inverse_must_agree"),
    ("the day's rates are the archive's", "ingest/bnb.py",
     "        if dd >= today - dt.timedelta(days=31) and arch.get((d[\"code\"], dd)) != v:", "        if False:",
     "tests/test_store.py::test_bnb_step_reads_windows_holds_and_reconciles"),
    ("no data is not 0", "app/main.py",
     '    if v is None:\n        return "няма данни"\n', "    if v is None:\n        v = 0\n",
     "tests/test_pages.py::test_pages_on_real_answers"),
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

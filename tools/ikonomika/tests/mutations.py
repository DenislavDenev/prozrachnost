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
    ("an ECB series has no gap", "ingest/ecb.py",
     "        if months_between(ts[0], ts[-1]) + 1 != len(ts):", "        if False:",
     "tests/test_sources.py::test_ecb_series_have_no_gaps_and_overnight_is_part_of_all_deposits"),
    ("a failed ECB series leaves its indicator as it was", "ingest/ecb.py",
     "        if ind in failed or bad:", "        if bad:",
     "tests/test_store.py::test_ecb_step_writes_both_places_checks_and_keeps_what_fails"),
    ("Bulgaria's bank rates start with the euro", "ingest/ecb.py",
     "parts = [(parts[0][0], lambda t: t >= BG_RATES_FROM)]", "parts = [(parts[0][0], lambda t: True)]",
     "tests/test_store.py::test_ecb_step_writes_both_places_checks_and_keeps_what_fails"),
    ("the average of the parts is weighted by the amounts", "ingest/ecb.py",
     "            sums[t] = (n + rate[part, t] * b, d + b)", "            sums[t] = (n + rate[part, t], d + 1)",
     "tests/test_sources.py::test_ecb_weighted_average_of_the_parts_is_the_published_total"),
    ("the average must agree with the published total", "ingest/ecb.py",
     "                    if off:", "                    if False:",
     "tests/test_store.py::test_ecb_step_writes_both_places_checks_and_keeps_what_fails"),
    ("the forecast's GDP adds up", "ingest/mf.py",
     "        if abs(nominal - implied) > GROWTH_TOLERANCE:", "        if False:",
     "tests/test_sources.py::test_nominal_gdp_must_follow_real_growth_and_the_deflator"),
    ("the forecast years are read from the table", "ingest/mf.py",
     '    starred = [y for y, c in zip(years, cells) if c.strip().endswith("**")]', "    starred = []",
     "tests/test_sources.py::test_every_forecast_since_2020_is_read_with_its_forecast_years"),
    ("the rates follow from the index", "ingest/checks.py",
     "            if not lo <= v <= hi:", "            if False:",
     "tests/test_sources.py::test_monthly_and_annual_rates_follow_from_the_index"),
    ("a smaller answer is held", "ingest/store.py",
     "hold_when=accept_all if replay else None", "hold_when=accept_all",
     "tests/test_store.py::test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees"),
    ("the replaced version leaves the landing zone", "ingest/store.py",
     "    if done.previous:   # landing policy `latest`", "    if False:",
     "tests/test_store.py::test_a_smaller_answer_is_held_until_a_second_read_a_day_later_agrees"),
    ("a corrected number supersedes the old one, nothing is overwritten", "db/build/observation.sql",
     "UPDATE ikonomika.observation c SET superseded_at = now()", "UPDATE ikonomika.observation c SET value = c.value",
     "tests/test_store.py::test_a_revision_and_a_new_period_are_logged_and_the_old_fact_is_superseded"),
    ("a place of Bulgaria points to core.nuts", "db/build/observation.sql",
     "FROM core.nuts n WHERE n.nuts_code = s.geo AND s.nuts_code IS NULL", "FROM core.nuts n WHERE false",
     "tests/test_store.py::test_eurostat_step_writes_checks_and_keeps_what_fails"),
    ("a rule that fails stops the publication", "ingest/run.py",
     "    return problems + (failed or pipeline.publish(conn, r, ROOT).problems)",
     "    return problems + failed + pipeline.publish(conn, r, ROOT).problems",
     "tests/test_store.py::test_a_step_publishes_only_when_its_rules_hold"),
    ("parity names a cell that differs", "ingest/move.py",
     "if old_cells.get(k) != new_cells.get(k))", "if False)",
     "tests/test_store.py::test_replay_builds_from_the_raw_files_and_parity_finds_no_difference"),
    ("the rates in leva reach core in euro by the fixed rate", "db/migrations/0001_shared.sql",
     "round(c.fixed_per_eur * x.units / x.value, 10)", "round(x.units / x.value, 10)",
     "tests/test_store.py::test_the_bnb_rates_reach_core_in_euro"),
    ("the pages read only the serving layer", "app/main.py",
     "FROM pub_ikonomika.hold ORDER BY first_at", "FROM ops.hold ORDER BY first_at",
     "tests/test_pages.py::test_every_answer_says_its_publication_and_the_pages_read_only_the_serving_layer"),
    ("every answer says its publication", "app/main.py",
     'response.headers["X-Publication"] = str(snap.publication[0])', "pass",
     "tests/test_pages.py::test_every_answer_says_its_publication_and_the_pages_read_only_the_serving_layer"),
    ("the BNB rate and its inverse agree", "ingest/bnb.py",
     "                if inv is not None and not agree(rate / units, inv, g[3]):", "                if False:",
     "tests/test_sources.py::test_bnb_rate_and_inverse_must_agree"),
    ("the day's rates are the archive's", "ingest/bnb.py",
     "        if dd >= today - dt.timedelta(days=31) and arch.get((d[\"code\"], dd)) != v:", "        if False:",
     "tests/test_store.py::test_bnb_step_reads_whole_quarters_and_reconciles"),
    ("no data is not 0", "app/main.py",
     '    if v is None:\n        return "няма данни"\n', "    if v is None:\n        v = 0\n",
     "tests/test_pages.py::test_pages_on_real_answers"),
]


def pytest(copy, tests):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-rs", *tests],
                          cwd=copy, capture_output=True, text=True)


def copy_of(tmp):
    """The tool and, beside it, the foundation its tests build the database from (tools/danni)."""
    copy = Path(tmp) / "ikonomika"
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".data")
    shutil.copytree(ROOT, copy, ignore=ignore)
    shutil.copytree(ROOT.parent / "danni", Path(tmp) / "danni", ignore=ignore)
    return copy


def main():
    # the same tests on an unbroken copy must pass and none be skipped, else a broken environment (no database, a
    # missing package, a missing folder) would count every mutation as caught
    tests = sorted({t for *_, ts in MUTATIONS for t in ts.split()})
    with tempfile.TemporaryDirectory() as tmp:
        r = pytest(copy_of(tmp), tests)
    if r.returncode != 0 or "SKIPPED" in r.stdout:
        print("the tests do not pass on the unbroken code, no mutation is checked:\n" + r.stdout[-2000:] + r.stderr[-1000:])
        sys.exit(1)
    print(f"baseline {r.stdout.strip().splitlines()[-1]}")
    survived = 0
    only = sys.argv[1:]   # words of the names to run, e.g. `python tests/mutations.py nuts publication`
    for name, path, old, new, tests in MUTATIONS:
        if only and not any(w in name for w in only):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            copy = copy_of(tmp)
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

"""Break one data guard at a time and see that a test fails. Run against a test database only.
The guards of the shared module (hold, idempotent load, corrupt file) are broken in tools/danni/tests/mutations.py."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("header", "ingest/parse.py", "table[0] != HEADER", "False", "tests/test_sources.py::test_changed_shape_is_rejected"),
    ("duplicate", "ingest/parse.py", "if neispuo in seen:", "if False:", "tests/test_sources.py::test_changed_shape_is_rejected"),
    ("missing-is-zero", "ingest/parse.py", 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return None', 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return 0', "tests/test_sources.py::test_missing_is_distinct_from_zero"),
    ("leading-zero", "ingest/parse.py", "neispuo, subject, takers, score))", "str(int(neispuo)), subject, takers, score))", "tests/test_sources.py::test_leading_zero_code_remains_text"),
    ("match-threshold", "ingest/sources.py", 'len(unmatched) / len(codes) > 0.02', 'len(unmatched) / len(codes) > 1', "tests/test_registry.py::test_no_name_based_matching_and_threshold"),
    ("store-match-threshold", "ingest/store.py", "if missing / total > 0.02:", "if missing / total > 1:", "tests/test_store.py::test_too_many_unknown_codes_stop_the_year_before_anything_is_written"),
    ("nvo-header", "ingest/nvo.py", 'if digest != layout["header_sha"]:', 'if False:', "tests/test_nvo.py::test_changed_scale_header_and_zero_vs_missing"),
    ("nvo-duplicate", "ingest/nvo.py", 'or code in seen:', 'or False:', "tests/test_nvo.py::test_duplicate_code_and_mismatch_threshold_hold"),
    ("nvo-scale", "ingest/nvo.py", 'if score is not None and score > 100:', 'if False:', "tests/test_nvo.py::test_invalid_code_and_score_hold"),
    ("context-fall-not-held", "ingest/store.py", "fell = (new_pupils < old_pupils) + (new_groups < old_groups)", "fell = 0",
     "tests/test_store.py::test_national_context_holds_a_falling_count"),
    ("ekatte-guessed", "db/migrations/0001_shared.sql", "  HAVING count(*) = 1\n", "\n",
     "tests/test_store.py::test_a_name_shared_by_two_settlements_is_not_guessed"),
    ("ekatte-anywhere", "db/migrations/0001_shared.sql", "THEN s.municipality_code = in_municipality",
     "THEN s.municipality_code = s.municipality_code", "tests/test_store.py::test_a_name_shared_by_two_settlements_is_not_guessed"),
    ("municipality-guessed", "db/migrations/0001_shared.sql", "    AND lower(m.name_bg) = lower(btrim(source_municipality)) AND m.gone_at IS NULL",
     "    AND m.gone_at IS NULL ORDER BY 1 LIMIT 1",
     "tests/test_store.py::test_schools_point_to_the_shared_places_and_unknown_names_get_no_key"),
    ("register-place-ignored", "ingest/store.py", "COALESCE(g.town, r.town), g.neispuo IS NOT NULL",
     "r.town, g.neispuo IS NOT NULL", "tests/test_store.py::test_nvo7_year_lands_builds_and_publishes_once"),
    ("dzi-only-codes-with-results", "ingest/store.py", "FROM unnest(d.school_codes) AS x (neispuo)",
     "FROM (SELECT DISTINCT neispuo FROM obrazovanie_src.dzi_row WHERE load_id = d.load_id AND neispuo IS NOT NULL) x",
     "tests/test_store.py::test_a_school_of_the_file_without_any_result_still_counts_as_unmatched"),
    ("parity-blind", "ingest/run.py", "        if missing or extra or len(set(a)) != len(a):", "        if False:",
     "tests/test_store.py::test_replay_from_raw_equals_the_old_database_and_parity_sees_a_difference"),
    ("pages-read-the-clean-tables", "app/main.py", "FROM pub_obrazovanie.nvo7_publication ORDER BY school_year DESC LIMIT 1",
     "FROM obrazovanie.nvo7_publication ORDER BY school_year DESC LIMIT 1",
     "tests/test_store.py::test_pages_read_only_the_serving_layer"),
    ("published-without-rule", "db/publish/03_pub_obrazovanie.exam_result.sql", "FROM obrazovanie.exam_result",
     "FROM obrazovanie.exam_result WHERE neispuo <> '105201'",
     "tests/test_store.py::test_nvo7_year_lands_builds_and_publishes_once"),
    ("rejected-forgotten", "db/checks.toml", "WHERE status = 'rejected'\"", "WHERE false\"",
     "tests/test_store.py::test_a_rejected_last_read_is_a_problem_until_a_good_one"),
]


def main():
    dsn = os.environ.get("OBRAZOVANIE_TEST_DSN", "")
    if "test" not in dsn:
        raise SystemExit("OBRAZOVANIE_TEST_DSN with test database required")
    for name, file, before, after, test in MUTATIONS:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "obrazovanie"
            shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            # the tests need the foundation beside the tool
            shutil.copytree(ROOT.parent / "danni", Path(temporary) / "danni",
                            ignore=shutil.ignore_patterns("__pycache__", "tests"))
            source = (target / file).read_text(encoding="utf-8")
            if source.count(before) != 1:
                raise SystemExit(f"Mutation {name} no longer matches exactly once")
            (target / file).write_text(source.replace(before, after), encoding="utf-8", newline="\n")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", test],
                cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if result.returncode == 0 or "failed" not in result.stdout:
                raise SystemExit(f"Mutation {name} survived or tests did not run:\n{result.stdout[-1500:]}\n{result.stderr[-500:]}")
            print(f"{name}: caught")


if __name__ == "__main__":
    main()

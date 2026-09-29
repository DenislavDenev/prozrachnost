"""Prove that the tests notice broken data guards."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("header", "ingest/parse.py", "table[0] != HEADER", "False", "test_sources.py::test_changed_shape_is_rejected"),
    ("duplicate", "ingest/parse.py", "if neispuo in seen:", "if False:", "test_sources.py::test_changed_shape_is_rejected"),
    ("missing-is-zero", "ingest/parse.py", 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return None', 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return 0', "test_sources.py::test_missing_is_distinct_from_zero"),
    ("leading-zero", "ingest/parse.py", "neispuo, subject, takers, score))", "str(int(neispuo)), subject, takers, score))", "test_sources.py::test_leading_zero_code_remains_text"),
    ("match-threshold", "ingest/sources.py", 'len(unmatched) / len(codes) > 0.02', 'len(unmatched) / len(codes) > 1', "test_registry.py::test_no_name_based_matching_and_threshold"),
    ("hold-removed-code", "ingest/db.py", 'old_codes - new_codes or new_count < old_count', 'new_count < old_count', "test_store.py::test_replaced_code_is_held_even_when_count_is_unchanged"),
]


def main():
    with tempfile.TemporaryDirectory() as temporary:
        for name, file, before, after, test in MUTATIONS:
            source = (TOOL / file).read_text(encoding="utf-8")
            target = Path(temporary) / name
            shutil.copytree(TOOL, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            if source.count(before) != 1:
                raise SystemExit(f"Mutation {name} no longer matches exactly once")
            (target / file).write_text(source.replace(before, after), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", f"tests/{test}"],
                cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 or "failed" not in result.stdout:
                raise SystemExit(f"Mutation {name} survived or tests did not run:\n{result.stdout}\n{result.stderr}")
            print(f"{name}: caught")


if __name__ == "__main__":
    main()

"""Prove that the tests notice broken data guards."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


TOOL = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("header", "table[0] != HEADER", "False", "test_changed_shape_is_rejected"),
    ("duplicate", "if neispuo in seen:", "if False:", "test_changed_shape_is_rejected"),
    ("missing-is-zero", 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return None', 'def _count(value: str, row_number: int) -> int | None:\n    if value == "":\n        return 0', "test_missing_is_distinct_from_zero"),
    ("leading-zero", "neispuo, subject, takers, score))", "str(int(neispuo)), subject, takers, score))", "test_leading_zero_code_remains_text"),
]


def main():
    source = (TOOL / "ingest/parse.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as temporary:
        for name, before, after, test in MUTATIONS:
            target = Path(temporary) / name
            shutil.copytree(TOOL, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            if source.count(before) != 1:
                raise SystemExit(f"Mutation {name} no longer matches exactly once")
            (target / "ingest/parse.py").write_text(source.replace(before, after), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", f"tests/test_sources.py::{test}"],
                cwd=target, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 or "failed" not in result.stdout:
                raise SystemExit(f"Mutation {name} survived or tests did not run:\n{result.stdout}\n{result.stderr}")
            print(f"{name}: caught")


if __name__ == "__main__":
    main()

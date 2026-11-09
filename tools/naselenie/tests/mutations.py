"""Break one data guard at a time and see that a test fails. Run against a test database only.
The guards of the shared module (hold, idempotent load, corrupt file) are broken in tools/danni/tests/mutations.py."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ('municipality-total', 'ingest/parse.py', "!= v:\n                    raise ShapeError('Общинският сбор",
     "== v:\n                    raise ShapeError('Общинският сбор", 'tests/test_sources.py::test_actual_source_totals'),
    ('ambiguous-name', 'ingest/reference.py', "len(candidates) == 1 and counts[(*key, r['name'])] == 1", "len(candidates) >= 1",
     'tests/test_store.py::test_reference_never_guesses_ambiguous_names'),
    ('unknown-municipality', 'ingest/reference.py', "        if not code:\n            raise ShapeError('Несъпоставена община: ' + repr(key))",
     "        if not code:\n            code = 'BLG01'", 'tests/test_store.py::test_unknown_municipality_stops_before_anything_is_written'),
    ('unmatched-threshold', 'ingest/run.py', "data['unmatched'] / len(data['places']) > 0.005", "data['unmatched'] / len(data['places']) > 1",
     'tests/test_store.py::test_too_many_unmatched_rows_refuse_a_recent_table'),
    ('correction-overwrites', 'ingest/store.py', "UPDATE naselenie.population p SET superseded_at = COALESCE(%(at)s, now())",
     "DELETE FROM naselenie.population p", 'tests/test_store.py::test_a_corrected_number_keeps_the_earlier_one'),
    ('parity-blind', 'ingest/run.py', "        if want != got:\n            row_diff += len(set(want) ^ set(got)) or 1",
     "        if False:\n            row_diff += 1",
     'tests/test_store.py::test_replay_from_raw_equals_the_old_database_and_parity_sees_a_difference'),
    ('no-data-as-zero', 'app/main.py', "bg=sum(row[column] for row in rows) if rows else None", "bg=sum(row[column] for row in rows)",
     'tests/test_pages.py::test_csv_sources_and_no_data'),
]


def main():
    dsn = os.environ.get('NASELENIE_TEST_DSN', '')
    if 'test' not in dsn:
        raise SystemExit('NASELENIE_TEST_DSN with test database required')
    for label, file, old, new, test in CASES:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'naselenie'
            for name in ('ingest', 'app', 'db', 'tests'):
                shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns('__pycache__'))
            # the tests need the foundation beside the tool
            shutil.copytree(ROOT.parent / 'danni', Path(td) / 'danni', ignore=shutil.ignore_patterns('__pycache__', 'tests'))
            src = dest / file
            text = src.read_text(encoding='utf-8')
            if text.count(old) != 1:
                raise SystemExit(f'{label}: mutation anchor changed')
            src.write_text(text.replace(old, new), encoding='utf-8', newline='\n')
            p = subprocess.run([sys.executable, '-m', 'pytest', '-q', test], cwd=dest,
                               env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
            if p.returncode == 0 or 'failed' not in p.stdout:
                print(p.stdout[-1500:], p.stderr[-500:])
                raise SystemExit(f'{label}: test did not catch the mutation')
            print(f'{label}: caught')


if __name__ == '__main__':
    main()

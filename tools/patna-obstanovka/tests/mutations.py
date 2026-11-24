"""Break one data guard at a time and see that a test fails. Run against a test database only.
The guards of the shared module (hold, idempotent load, corrupt file, publication) are broken in tools/danni/tests/mutations.py."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ('short-answer-replaces', 'ingest/run.py', "current=store.toll_current(kind), hold_when=pipeline.missing_over(0.2))",
     "current=store.toll_current(kind), hold_when=store.accept_all)",
     'tests/test_store.py::test_a_short_answer_is_held_and_the_map_keeps_the_full_one'),
    ('garbled-is-fatal', 'ingest/run.py', "if isinstance(e, ShapeError) and not isinstance(e, sources.Garbled):",
     "if isinstance(e, ShapeError):",
     'tests/test_store.py::test_a_failed_read_keeps_the_last_answer_until_the_source_is_stale'),
    ('new-shape-is-quiet', 'ingest/run.py', "            problems.append(line)\n", "            rejected.append(line)\n",
     'tests/test_store.py::test_an_answer_in_a_new_shape_is_a_problem_at_once'),
    ('stale-is-quiet', 'ingest/run.py', "problems = [p for p in problems if not p.startswith(prefixes)]",
     "problems = [p for p in problems if not p.startswith(prefixes) and 'последно успешно' not in p]",
     'tests/test_store.py::test_a_failed_read_keeps_the_last_answer_until_the_source_is_stale'),
    ('old-measurements', 'ingest/run.py', "if now - last > OBSERVATION_MAX_AGE or", "if False and now - last > OBSERVATION_MAX_AGE or",
     'tests/test_store.py::test_old_measurements_are_a_problem'),
    ('risk-not-reconciled', 'ingest/run.py', "        if features != counted:\n", "        if False:\n",
     'tests/test_store.py::test_the_sections_are_reconciled_with_the_count_of_the_source'),
    ('crash-overwritten', 'ingest/store.py',
     "\n          AND (n.crash_at, n.lat, n.lon, n.crash_type, n.killed, n.injured) = (c.crash_at, c.lat, c.lon, c.crash_type, c.killed, c.injured))'''",
     ")'''", 'tests/test_store.py::test_crashes_keep_their_history_and_link_to_the_municipality'),
    ('crash-archive-shrinks', 'ingest/run.py', "hold_when=pipeline.missing_over(0.01))", "hold_when=store.accept_all)",
     'tests/test_store.py::test_crashes_keep_their_history_and_link_to_the_municipality'),
    ('capital-as-oblast', 'ingest/places.py', "code = fixed.get(region) or oblast.get(region)", "code = oblast.get(region)",
     'tests/test_store.py::test_crashes_keep_their_history_and_link_to_the_municipality'),
    ('crossing-guessed', 'ingest/derive.py', "if len(nearest) > 1 and nearest[1][0] - distance < AMBIGUITY_M", "if False and len(nearest) > 1",
     'tests/test_store.py::test_arrows_and_crashes_linked_to_sections'),
    ('parity-blind', 'ingest/run.py', "n = sum(a != b for a, b in zip(old, new)) + abs(len(old) - len(new))",
     "n = abs(len(old) - len(new))", 'tests/test_store.py::test_copy_from_road_db_and_parity'),
    ('pages-read-the-clean-tables', 'app/queries.py', "FROM {P}.traffic ORDER BY row_no", "FROM patna_obstanovka.traffic ORDER BY row_no",
     'tests/test_pages.py::test_pages_read_only_the_serving_layer'),
    ('no-pause-for-api', 'ingest/config.py', "os.environ.get('PATNA_OBSTANOVKA_LIMA_PAUSE', '8')", "os.environ.get('PATNA_OBSTANOVKA_LIMA_PAUSE', '0')",
     'tests/test_sources.py::test_the_server_of_api_gets_a_pause_and_a_refused_connection_is_asked_again'),
    ('refusal-not-retried', 'ingest/http.py', "            last_error = str(getattr(e, 'reason', e))\n", "            raise\n",
     'tests/test_sources.py::test_the_server_of_api_gets_a_pause_and_a_refused_connection_is_asked_again'),
    ('landing-keeps-every-answer', 'ingest/store.py', "    for table in TABLES['lima' if source.startswith('lima_') else source]:",
     "    for table in ():", 'tests/test_store.py::test_a_short_answer_is_held_and_the_map_keeps_the_full_one'),
    ('plain-sum', 'app/main.py', "    return total + error\n", "    return total\n",
     'tests/test_pages.py::test_road_conditions_sum_as_sqlite_does'),
    ('crash-row-lost', 'ingest/sources.py', "        except (TypeError, ValueError, KeyError, AttributeError):
            skipped += 1",
     "        except (TypeError, ValueError, KeyError, AttributeError):
            pass",
     'tests/test_sources.py::test_every_row_of_the_crash_file_is_a_crash_or_left_out'),
]


def main():
    dsn = os.environ.get('PATNA_OBSTANOVKA_TEST_DSN', '')
    if 'test' not in dsn:
        raise SystemExit('PATNA_OBSTANOVKA_TEST_DSN with test database required')
    for label, file, old, new, test in CASES:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'patna-obstanovka'
            for name in ('ingest', 'app', 'db', 'tests'):
                shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns('__pycache__'))
            # the tests need the foundation beside the tool
            shutil.copytree(ROOT.parent / 'danni', Path(td) / 'danni', ignore=shutil.ignore_patterns('__pycache__'))
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

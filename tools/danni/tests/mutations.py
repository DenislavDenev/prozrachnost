"""Break one guard at a time and see that a test fails. Run against a test database only:

    DANNI_TEST_DSN=dbname=danni_test python tests/mutations.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ('hold-delay', 'pipeline.py', 'now - held[1] < hold:', 'now - held[1] < hold * 0:',
     'tests/test_pipeline.py::test_smaller_answer_is_held_until_the_same_comes_a_day_later'),
    ('hold-missing-keys', 'pipeline.py', 'if missing:', 'if False:',
     'tests/test_pipeline.py::test_smaller_answer_is_held_until_the_same_comes_a_day_later'),
    ('hold-rule-ignored', 'pipeline.py', '            elif hold_when:', '            elif False:',
     'tests/test_pipeline.py::test_a_register_that_grows_and_corrects_rows_keeps_every_day'),
    ('hold-alert-fixed-at-a-day', 'pipeline.py', 'h.first_at < now() - s.hold_alert_after', "h.first_at < now() - interval '1 day'",
     'tests/test_pipeline.py::test_a_hold_is_a_problem_only_after_the_time_of_its_source'),
    ('unchanged-writes-nothing', 'pipeline.py', 'if cur and cur[1] == sha:', 'if False:',
     'tests/test_pipeline.py::test_second_load_of_the_same_content_changes_nothing'),
    ('corrupt-raw', 'pipeline.py', "raise CorruptRaw(f'{path} does not match its name')", 'pass',
     'tests/test_pipeline.py::test_raw_is_stored_once_and_a_changed_file_is_refused'),
    ('check-tolerance', 'pipeline.py', "        ok = expected is not None and actual is not None and abs(actual - expected) <= tolerance\n        conn.execute",
     "        ok = True\n        conn.execute", 'tests/test_pipeline.py::test_checks_are_rows_with_a_tolerance'),
    ('edited-migration', 'pipeline.py', 'if done[f.name] != sha:', 'if False:',
     'tests/test_pipeline.py::test_migrate_runs_once_and_refuses_an_edited_file'),
    ('licence-guard', 'db/migrations/0003_api.sql', 'WHERE t.dataset IS NULL OR NOT ds.is_distributable OR', 'WHERE t.dataset IS NULL OR',
     'tests/test_pipeline.py::test_registry_sync_and_only_the_licence_list_opens_a_dataset'),
    ('publish-without-rules', 'pipeline.py', '            if problems:\n                raise _Refused()', '            if False:\n                raise _Refused()',
     'tests/test_dimensions.py::test_places_are_published_whole_with_a_version_or_not_at_all'),
    ('version-without-change', 'pipeline.py', '            if changes:\n                publication_id', '            if True:\n                publication_id',
     'tests/test_dimensions.py::test_places_are_published_whole_with_a_version_or_not_at_all'),
    ('serving-guard-foreign-key', 'db/migrations/0009_persons.sql', 'WHERE k.confrelid NOT IN (SELECT oid FROM serving)', 'WHERE false',
     'tests/test_dimensions.py::test_serving_layer_cannot_reach_outside_or_hold_what_may_not_be_shown'),
    ('serving-guard-display-right', 'db/migrations/0009_persons.sql', "  AND (t.dataset IS NULL OR NOT d.is_displayable OR d.personal_data = 'names')", '  AND (t.dataset IS NULL)',
     'tests/test_dimensions.py::test_serving_layer_cannot_reach_outside_or_hold_what_may_not_be_shown'),
    ('full-name-in-serving', 'db/migrations/0009_persons.sql', "       CASE WHEN a.atttypid = 'core.person_name'::regtype THEN 'пълно име на човек в слоя за показване: ' || a.attname", "       CASE WHEN false THEN 'пълно име на човек в слоя за показване: ' || a.attname",
     'tests/test_dimensions.py::test_no_name_of_a_person_reaches_the_serving_layer_without_a_decision'),
    ('undecided-name-passes', 'db/migrations/0009_persons.sql', "               AND p.kind = 'not-a-person'), false);", "               AND p.kind = 'not-a-person'), true);",
     'tests/test_dimensions.py::test_no_name_of_a_person_reaches_the_serving_layer_without_a_decision'),
    ('mask-keeps-the-surname', 'db/migrations/0009_persons.sql', "split_part(regexp_replace(btrim(name), '\\s+', ' ', 'g'), ' ', 1) || ' ***'", "btrim(name) || ' ***'",
     'tests/test_dimensions.py::test_names_are_masked_to_the_first_name_and_the_type_refuses_anything_else'),
    ('eik-control-digit', 'ingest/run.py', '    if s != d[8]:\n        return False', '    if s != d[8]:\n        return True',
     'tests/test_dimensions.py::test_eik_rule_is_the_same_in_python_and_sql'),
    ('ekatte-unknown-field', 'ingest/ekatte.py', 'or row.keys() - required - optional', '',
     'tests/test_dimensions.py::test_invalid_archive_is_refused'),
    ('gone-not-deleted', 'db/build/place.sql', "UPDATE core.settlement c SET gone_at = now()", "DELETE FROM core.settlement c",
     'tests/test_dimensions.py::test_a_closed_settlement_is_held_then_kept_with_gone_at'),
]


def main():
    if 'test' not in os.environ.get('DANNI_TEST_DSN', ''):
        raise SystemExit('DANNI_TEST_DSN with a test database required')
    for label, file, old, new, test in CASES:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            for name in ('ingest', 'db', 'tests'):
                shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(ROOT / 'pipeline.py', dest / 'pipeline.py')
            src = dest / file
            text = src.read_text(encoding='utf-8')
            if text.count(old) != 1:
                raise SystemExit(f'{label}: mutation anchor changed')
            src.write_text(text.replace(old, new), encoding='utf-8', newline='\n')
            p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-x', test], cwd=dest,
                               env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
            if p.returncode == 0 or 'failed' not in p.stdout:
                print(p.stdout[-1500:], p.stderr[-500:])
                raise SystemExit(f'{label}: no test caught the mutation')
            print(f'{label}: caught')


if __name__ == '__main__':
    main()

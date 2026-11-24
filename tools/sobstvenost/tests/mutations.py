"""Each mutation breaks one rule that guards the data and must make its test fail, in an isolated copied tree."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('ingest/parse.py', "share_pct=None,owner=None,share_text=original,participation='unknown'",
     "share_pct='0',owner=None,share_text=original,participation='unknown'", 'tests/test_sources.py::test_missing_percentage_and_eik',
     'the sign for no data taken as 0%'),
    ('ingest/parse.py', 'return check==digits[8]', 'return True', 'tests/test_sources.py::test_missing_percentage_and_eik',
     'an ЕИК with a wrong check digit accepted'),
    ('ingest/parse.py', "result['board']=[dict(original=item,verbatim=True) for item in board_items]", "result['board']=[]",
     'tests/test_sources.py::test_actual_freeform_board_preserved_without_person_guess', 'a free-form board dropped'),
    ('ingest/parse.py', "value.startswith(('АКТУАЛНА ИНФОРМАЦИЯ ЗА КОНЦЕСИЯТА','ОБЯВЛЕНИЕ ЗА ВЪЗЛОЖЕНА КОНЦЕСИЯ по чл. 8р'))",
     "value.startswith('АКТУАЛНА ИНФОРМАЦИЯ ЗА КОНЦЕСИЯТА')", 'tests/test_sources.py::test_beach_notice_under_black_sea_coast_act',
     'the beach notice of 02.10.2026 unknown again'),
    ('ingest/privacy.py', "PERSON_FORM = re.compile(r'^\\W*(ЕТ|ЗП)(?![А-Яа-яA-Za-z])')", "PERSON_FORM = re.compile(r'^$')",
     'tests/test_sources.py::test_a_concessionaire_who_is_not_a_legal_entity', 'a sole trader taken for a company'),
    ('ingest/progress.py', 'if self.end is not None and time.monotonic()+seconds>=self.end:', 'if False:',
     'tests/test_progress.py::test_deadline_does_not_start_request_that_crosses_budget', 'a request that crosses the budget'),
    ('ingest/progress.py', "if self.data['sha256']!=digest(rows) or not 0<=self.data['done']<=len(rows):", 'if False:',
     'tests/test_progress.py::test_corrupt_checkpoint_never_skips_source', 'a corrupt checkpoint skips records'),
    ('ingest/run.py', 'if 345 <= minute < 615 or 1065 <= minute < 1335:', 'if False:',
     'tests/test_progress.py::test_safe_budget_never_runs_into_the_day_windows', 'a heavy read starts in the day window'),
]
if os.environ.get('SOBSTVENOST_TEST_DSN'):
    MUTATIONS += [
        ('ingest/store.py', "    if len(rows) != expected:\n", "    if False:\n",
         'tests/test_store.py::test_an_incomplete_or_unknown_read_writes_nothing', 'a count that does not reconcile'),
        ('ingest/store.py', "    if set(row) != want:\n", "    if False:\n",
         'tests/test_store.py::test_an_incomplete_or_unknown_read_writes_nothing', 'an unknown field accepted'),
        ('ingest/store.py', "    n = len(old.keys() - new.keys())\n", "    n = 0\n",
         'tests/test_store.py::test_a_shorter_read_waits_a_day', 'a missing record replaces the published ones at once'),
        ('ingest/store.py', 'if any(items(a, f) - items(b, f) for f in NESTED) or ', 'if ',
         'tests/test_store.py::test_board_loss_is_held_and_people_are_not_merged', 'a board that empties is not held'),
        ('ingest/store.py', ' or any(a.get(f) is not None and b.get(f) is None for f in SCALAR):', ':',
         'tests/test_store.py::test_an_identity_that_becomes_empty_is_held', 'an ЕИК that becomes empty is not held'),
        ('ingest/store.py', "p['eik'] if p['eik'] in known else None,", "p['eik'],",
         'tests/test_store.py::test_an_eik_not_in_core_stays_unlinked_and_is_counted', 'an invented link to core.org'),
        ('ingest/store.py', "is_person(n['concessionaire_name'])", 'False',
         'tests/test_store.py::test_build_publishes_and_hides_a_person_concessionaire', 'a person concessionaire published'),
        ('db/publish/12_pub_sobstvenost.notice.sql', 'WHEN concessionaire_is_person', 'WHEN false',
         'tests/test_store.py::test_build_publishes_and_hides_a_person_concessionaire', 'the serving layer without the replacement'),
        ('ingest/run.py', "        if refused:\n            result['refused'] = refused", "        if False:\n            result['refused'] = refused",
         'tests/test_store.py::test_a_pass_with_a_refused_enterprise_is_not_complete', 'a pass with a refused enterprise counted complete'),
        ('ingest/documents.py', "WHERE r.kind = 'annual'", 'WHERE true',
         'tests/test_documents.py::test_selects_only_actual_latest_annual_year', 'quarterly reports archived as the annual one'),
        ('ingest/documents.py', "        if refused:\n            problems.append", "        if False:\n            problems.append",
         'tests/test_documents.py::test_attachment_versions_and_unknown_formats', 'a file of an unknown format counted archived'),
        ('ingest/move.py', 'if f[2] <= t]', ']', 'tests/test_move.py::test_replay_and_parity_against_the_old_database',
         'a later answer replayed as part of an older read'),
        ('ingest/move.py', 'differ = sorted(k for k, sha in old.items() if new.get(k) != sha)', 'differ = []',
         'tests/test_move.py::test_replay_and_parity_against_the_old_database', 'parity blind to a changed record'),
        ('app/queries.py', "if len(candidates)!=1:unmatched.add((row['id'],name));continue",
         "if not candidates:unmatched.add((row['id'],name));continue",
         'tests/test_pages.py::test_map_verified_dedup_ambiguous_and_filter', 'an ambiguous municipality placed on the map'),
        ('app/queries.py', "if participation and row.get('participation')!=participation:continue", 'if False:continue',
         'tests/test_pages.py::test_filters_summary_csv_match', 'the filter does not reach the CSV'),
        ('app/queries.py', "members[code].add(row['id'])", "members[code].add(row['id']+':'+name)",
         'tests/test_pages.py::test_multi_municipality_same_national_concession', 'one concession counted twice in a region'),
    ]


def main():
    baseline = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    if baseline.returncode:
        print('Baseline must pass before mutations')
        print(baseline.stdout)
        return 1
    caught = 0
    for file, before, after, test, label in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='sobstvenost-mutation-') as td:
            p = Path(td) / 'sobstvenost'
            shutil.copytree(ROOT, p, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            if (ROOT.parent / 'danni').exists():   # the foundation the database tests build from
                shutil.copytree(ROOT.parent / 'danni', Path(td) / 'danni', ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
            target = p / file
            text = target.read_text(encoding='utf-8')
            if before not in text:
                raise RuntimeError('Mutation target changed: ' + label)
            target.write_text(text.replace(before, after, 1), encoding='utf-8')
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', test], cwd=p, capture_output=True, text=True, encoding='utf-8')
            if result.returncode == 0:
                print('NOT CAUGHT: ' + label)
                print(result.stdout)
                return 1
            if result.returncode != 1:
                print('INFRASTRUCTURE ERROR: ' + label)
                print(result.stdout)
                print(result.stderr)
                return 1
            caught += 1
            print('caught: ' + label, flush=True)
    print(f'{caught}/{len(MUTATIONS)} mutations caught')
    return 0


if __name__ == '__main__':
    sys.exit(main())

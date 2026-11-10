"""Break one data guard at a time and see that a test fails. Run against a test database only.
The guards of the shared module (hold, idempotent load, the serving guard) are broken in tools/danni/tests/mutations.py."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PEOPLE = 'tests/test_serving.py::test_people_are_shown_only_in_entities_with_public_money_and_under_a_token_of_the_build'
MOVE = 'tests/test_serving.py::test_replay_copies_the_old_database_and_parity_sees_a_difference'
CASES = [
    ('everyone-in-the-register-is-shown', 'db/derive/85_shown.sql',
     "FROM (SELECT DISTINCT e.holder FROM edge e JOIN public_money m ON m.node = e.company\n      WHERE e.holder_kind = 'person' AND e.holder IS NOT NULL) h;",
     "FROM (SELECT DISTINCT e.holder FROM edge e\n      WHERE e.holder_kind = 'person' AND e.holder IS NOT NULL) h;", PEOPLE),
    ('every-role-of-a-shown-person', 'db/derive/85_shown.sql',
     "FROM edge e JOIN person_token t ON t.node = e.holder JOIN public_money m ON m.node = e.company\nWHERE e.holder_kind = 'person';",
     "FROM edge e JOIN person_token t ON t.node = e.holder\nWHERE e.holder_kind = 'person';", PEOPLE),
    ('our-number-as-the-key-of-a-person', 'db/derive/85_shown.sql',
     "SELECT h.holder AS node, left(h.holder, 2) || replace(gen_random_uuid()::text, '-', '') AS shown",
     "SELECT h.holder AS node, h.holder AS shown", PEOPLE),
    ('a-token-that-outlives-the-build', 'db/derive/85_shown.sql',
     "SELECT h.holder AS node, left(h.holder, 2) || replace(gen_random_uuid()::text, '-', '') AS shown",
     "SELECT h.holder AS node, left(h.holder, 2) || md5(h.holder) AS shown", PEOPLE),
    ('an-offer-counts-as-public-money', 'db/derive/85_shown.sql',
     "FROM (SELECT node, true AS by_contract FROM node_contract",
     "FROM (SELECT 'c:' || eik AS node, true AS by_contract FROM tender_src.tr_deed UNION ALL SELECT node, true AS by_contract FROM node_contract", PEOPLE),
    ('pages-read-the-clean-tables', 'app/queries.py', 'p = one("SELECT * FROM pub_tender.person WHERE id = %s", pid)',
     'p = one("SELECT * FROM tender.person WHERE id = %s", pid)', 'tests/test_serving.py::test_pages_read_only_the_serving_layer'),
    ('published-past-a-failed-rule', 'ingest/build.py', "    problems = pipeline.run_checks(conn, r, kinds=RULE_KINDS)\n    if not problems:",
     "    problems = pipeline.run_checks(conn, r, kinds=RULE_KINDS)\n    if True:",
     'tests/test_serving.py::test_a_failed_rule_keeps_the_pages_on_the_version_they_had'),
    ('published-tables-left-without-statistics', 'ingest/build.py', '            conn.execute(f"ANALYZE {table}")', '            pass',
     'tests/test_serving.py::test_every_answer_says_its_publication_and_the_number_moves_only_with_the_content'),
    ('a-row-number-shown-as-data', 'app/queries.py', "SELECT contract_id, published_at, source_day, last_value, current_value, difference, currency, reason, description\n",
     "SELECT *\n",
     'tests/test_serving.py::test_every_answer_says_its_publication_and_the_number_moves_only_with_the_content'),
    ('served-without-a-publication', 'app/main.py', '        response.headers["X-Publication"] = str(snap.publication[0])', '        pass',
     'tests/test_serving.py::test_every_answer_says_its_publication_and_the_number_moves_only_with_the_content'),
    ('parity-blind-to-the-content', 'ingest/move.py', 'pipeline.record_check(conn, r, f"parity_{table}_content", was[1], now[1])',
     'pipeline.record_check(conn, r, f"parity_{table}_content", was[1], was[1])', MOVE),
    ('parity-blind-to-the-money', 'ingest/move.py', 'pipeline.record_check(conn, r, f"parity_{table}_{column}_sum", was, now)',
     'pipeline.record_check(conn, r, f"parity_{table}_{column}_sum", was, was)', MOVE),
    ('parity-blind-to-the-names-of-nodes', 'ingest/move.py', "CASE WHEN a.several THEN NULL ELSE n.label END AS label", "NULL AS label", MOVE),
    ('a-unit-names-the-company', 'db/derive/20_network.sql', "(ref IS DISTINCT FROM 'eik:' || substr(id, 3)), label, ref;", "label, ref;", PEOPLE),
    ('replay-over-what-was-read-since', 'ingest/move.py', "    if filled and not again:", "    if False:", MOVE),
    ('an-invented-key-to-core', 'db/derive/90_core.sql', "UPDATE company c SET org_eik = o.eik FROM core.org o WHERE o.eik = c.eik;",
     "UPDATE company c SET org_eik = c.eik WHERE c.eik ~ '^[0-9]{9}$';",
     'tests/test_serving.py::test_the_build_links_to_the_shared_dimensions'),
    ('former-owner-keeps-the-contracts', 'app/queries.py',
     '"at_contract_date": q("AND c.effective_date >= e.valid_from AND (e.valid_to IS NULL OR c.effective_date < e.valid_to)"),',
     '"at_contract_date": q(""),', 'tests/test_integration.py::test_former_owner_gets_no_contracts_after_leaving'),
]


def main():
    dsn = os.environ.get('TENDER_TEST_DSN', '')
    if 'test' not in dsn:
        raise SystemExit('TENDER_TEST_DSN with a test database required')
    for label, file, old, new, test in CASES:
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'tender'
            for name in ('ingest', 'app', 'db', 'tests', 'docs'):
                shutil.copytree(ROOT / name, dest / name, ignore=shutil.ignore_patterns('__pycache__'))
            # the tests need the foundation beside the tool
            shutil.copytree(ROOT.parent / 'danni', Path(td) / 'danni', ignore=shutil.ignore_patterns('__pycache__'))
            src = dest / file
            text = src.read_text(encoding='utf-8')
            if text.count(old) != 1:
                raise SystemExit(f'{label}: mutation anchor changed')
            src.write_text(text.replace(old, new), encoding='utf-8', newline='\n')
            p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-x', test], cwd=dest,
                               env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
            if p.returncode == 0 or not ('failed' in p.stdout or 'error' in p.stdout):
                print(p.stdout[-1500:], p.stderr[-500:])
                raise SystemExit(f'{label}: test did not catch the mutation')
            print(f'{label}: caught')


if __name__ == '__main__':
    main()

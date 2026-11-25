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
    ('a-smaller-file-replaces-the-loaded-one', 'ingest/build.py', "eop.rows_of(pipeline.read_raw(conn, rid), kind) if rid else []",
     "eop.read_rows(day, kind) if rid else []", 'tests/test_changes.py::test_the_build_reads_the_loaded_file_not_the_newest_answer'),
    ('a-build-without-the-files-that-have-no-load', 'ingest/build.py', "        if raw_file_id is None:   # a file from before the loads that the step `adopt` has not taken yet",
     "        if False:", 'tests/test_changes.py::test_the_build_reads_the_loaded_file_not_the_newest_answer'),
    ('fewer-offers-taken-on-one-answer', 'ingest/eop_offers.py', '    if done.status == "held":\n        conn.execute("""UPDATE tender_src.eopsvc_queue SET status = \'pending\', reason = \'confirm\'',
     '    if False:\n        conn.execute("""UPDATE tender_src.eopsvc_queue SET status = \'pending\', reason = \'confirm\'',
     'tests/test_changes.py::test_offers_are_kept_until_a_second_read_agrees'),
    ('a-held-answer-confirmed-at-once', 'ingest/db.py', '    kw.setdefault("hold", HOLD)', '    kw.setdefault("hold", dt.timedelta(0))',
     'tests/test_changes.py::test_offers_are_kept_until_a_second_read_agrees'),
    ('a-partida-read-again-does-not-move-the-rotation', 'ingest/tr_worker.py',
     '        conn.execute("UPDATE tender_src.tr_deed SET fetched_at = %s, sha256 = %s, error = NULL WHERE eik = %s", (now, sha, eik))', '        pass',
     'tests/test_changes.py::test_a_partida_with_fewer_rows_is_held_until_a_second_read_agrees'),
    ('a-payment-without-its-load', 'ingest/sebra.py', 'cp.write_row([res["uri"], i, load_id] + [r[c] for c in COLUMNS])',
     'cp.write_row([res["uri"], i, None] + [r[c] for c in COLUMNS])', 'tests/test_changes.py::test_sebra_verify_hold_and_history'),
    ('adopt-takes-an-answer-the-table-was-not-written-from', 'ingest/adopt.py',
     '        if xml is None or hashlib.sha256(xml).hexdigest() != sha:', '        if xml is None:',
     'tests/test_changes.py::test_adopt_gives_a_load_to_what_was_read_before'),
    ('a-stale-source-is-not-named', 'ingest/daily.py', '    return bad + [p for p in pipeline.freshness(conn, db.TOOL) if p not in bad]', '    return bad',
     'tests/test_changes.py::test_freshness_names_each_late_source'),
    ('a-clean-column-without-a-description', 'db/derive/95_comments.sql',
     "('*', 'unp', 'Уникален номер на поръчката (УНП) в ЦАИС ЕОП.'),\n", '', PEOPLE),
    ('money-without-two-decimals', 'db/derive/70_payments.sql', "p.amount::numeric(20,2) AS amount", "p.amount", PEOPLE),
    ('the-change-list-waits-for-the-hourly-read', 'ingest/db.py', '"tr-changes": 7_070_710}', '"tr-changes": 7_070_702}',
     'tests/test_changes.py::test_the_change_list_is_read_beside_the_hourly_read_and_catches_up'),
    ('a-day-of-the-change-list-left-unfinished', 'ingest/tr_worker.py',
     "    todo += [(today - dt.timedelta(days=lag), 1) for lag in range(2, CHANGES_WINDOW)]\n", "",
     'tests/test_changes.py::test_the_change_list_is_read_beside_the_hourly_read_and_catches_up'),
    ('a-killed-chain-rebuilds-every-quarter-hour', 'ingest/daily.py', "status IN ('failed', 'running')", "status = 'failed'",
     'tests/test_changes.py::test_daily_waits_runs_once_and_does_not_loop_on_failure'),
    ('adopt-over-a-newer-read', 'ingest/adopt.py', "        if not now or now[0] != sha:\n            return False\n        path = RAW_TR",
     "        path = RAW_TR", 'tests/test_changes.py::test_adopt_leaves_a_partida_a_reader_took_meanwhile'),
    ('the-build-without-a-memory-limit', 'ingest/build.py', "    resource.setrlimit(resource.RLIMIT_AS, (mb * 1024 * 1024, hard))\n", "",
     'tests/test_changes.py::test_the_build_stops_at_its_memory_limit_and_gives_it_back'),
    ('a-possible-person-keeps-a-profile', 'db/publish/12_pub_tender.company.sql', "c WHERE NOT EXISTS (SELECT FROM tender.unnamed_party u WHERE u.party_key = c.key)", "c",
     'tests/test_serving.py::test_a_supplier_by_name_only_stays_on_the_contract_and_has_no_profile'),
    ('a-possible-person-in-a-ranking', 'app/queries.py', "THEN 'Без ЕИК и правна форма' ELSE max(s.name) END", "THEN max(s.name) ELSE max(s.name) END",
     'tests/test_serving.py::test_a_supplier_by_name_only_stays_on_the_contract_and_has_no_profile'),
    ('a-possible-person-in-the-contract-lists', 'db/publish/04_pub_tender.contract.sql', "coalesce(m.display, t.supplier_display)", "t.supplier_display",
     'tests/test_serving.py::test_a_name_without_an_eik_is_on_its_contract_and_in_no_list'),
    ('a-possible-person-on-the-next-contract', 'app/queries.py', 'name if name.upper() in named else "Без ЕИК и правна форма"', 'name',
     'tests/test_serving.py::test_the_release_of_a_procedure_names_a_person_only_on_the_contract_of_that_person'),
    ('a-possible-person-named-in-an-offer', 'db/publish/21_pub_tender.offer.sql', "CASE WHEN n.upper_name IS NULL THEN o.bidder_name", "CASE WHEN true THEN o.bidder_name",
     'tests/test_changes.py::test_a_bidder_by_name_only_is_not_named_and_the_bidders_stay_distinct'),
    ('bidders-without-a-name-become-one', 'db/publish/21_pub_tender.offer.sql', "ORDER BY n.upper_name) END", "ORDER BY n.upper_name IS NULL) END",
     'tests/test_changes.py::test_a_bidder_by_name_only_is_not_named_and_the_bidders_stay_distinct'),
    ('one-bad-day-stops-the-rest', 'ingest/tr_worker.py', "            failed.append(str(day))\n", "            raise\n",
     'tests/test_changes.py::test_a_day_the_register_does_not_answer_for_does_not_stop_the_days_after_it'),
    ('adopt-gives-up-on-a-deadlock', 'ingest/adopt.py', "                if attempt == TRIES - 1:\n", "                if True:\n",
     'tests/test_changes.py::test_adopt_attaches_the_rows_again_after_a_deadlock'),
    ('a-sunday-stops-the-changes', 'ingest/registry.py', "                if empty == 3:", "                if False:",
     'tests/test_changes.py::test_a_day_without_entries_is_an_empty_list_but_one_empty_answer_is_not'),
    ('public-figures-from-one-query', 'ingest/enrich.py', 'WHO = ("?p wdt:P39 ?pos.", "?p wdt:P106 wd:Q82955.")', 'WHO = ("?p wdt:P39 ?pos.",)',
     'tests/test_changes.py::test_the_public_figures_come_from_two_light_queries'),
    ('payments-sorted-in-full', 'db/migrations/0004_payment_list.sql', "(settlement_date DESC NULLS LAST, resource_uri, row_no)", "(settlement_date)",
     'tests/test_serving.py::test_the_list_of_payments_reads_an_index_in_its_order'),
    ('components-ignore-the-end-of-a-role', 'ingest/networks.py', "            current.append(to is None or to > today)\n", "            current.append(True)\n",
     'tests/test_changes.py::test_the_components_are_read_as_a_stream_and_match_the_plain_count'),
    ('a-unit-names-the-company', 'db/derive/20_network.sql', "(ref IS DISTINCT FROM 'eik:' || substr(id, 3)), label, ref;", "label, ref;", PEOPLE),
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

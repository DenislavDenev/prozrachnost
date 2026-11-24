"""Mutation check of the tests (STANDARD 1А.7): each rule that guards the data is broken on purpose in a copy of the tool and
the named tests must fail. Run from the tool folder, with the test database:

    ZAKONI_TEST_DSN=dbname=zakoni_test python tests/mutations.py

The database tests need the foundation beside the tool (../danni), as the tests themselves do.

Prints one line per mutation and exits 1 when a mutation survives (the tests did not notice it).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = "tests/test_sources.py"
STO = "tests/test_store.py"
PAG = "tests/test_pages.py"

MUTATIONS = [
    ("an answer with success:false is refused", "ingest/stream.py", 'if m.group(1) != "true":', "if False:", f"{SRC}::test_bad_answers_are_refused"),
    ("a file cut inside the data is refused", "ingest/stream.py", 'raise ShapeError("отрязан файл: последният запис не е цял") from None',
     "return", f"{SRC}::test_bad_answers_are_refused"),
    ("something after the data is refused", "ingest/stream.py", 'if rest != "}":', "if False:", f"{SRC}::test_bad_answers_are_refused"),
    ("the first record must be the legend", "ingest/parse.py", "    if head.get(key) != label or not all(", "    if False and (",
     f"{SRC}::test_the_legend_is_not_data"),
    ("an unknown or missing field stops the import", "ingest/parse.py", "    if extra or missing:", "    if False:",
     f"{SRC}::test_an_unknown_missing_or_mistyped_field_stops_the_import"),
    ("a wrong type stops the import", "ingest/parse.py", "        if type(rec[k]) not in types:", "        if False:",
     f"{SRC}::test_an_unknown_missing_or_mistyped_field_stops_the_import"),
    ("a repeated pris_id stops the import", "ingest/parse.py", "        if pid in seen:", "        if False:", f"{SRC}::test_a_repeated_key_stops_the_import"),
    ("one number with different data stops the import", "ingest/parse.py", "            if first != row:", "            if False:",
     f"{SRC}::test_a_repeated_key_stops_the_import"),
    ("a consultation closing before it opens is refused", "ingest/parse.py", "    if closed < opened:", "    if False:",
     f"{SRC}::test_a_consultation_that_closes_before_it_opens_stops_the_import"),
    ("a two-digit gazette year is the one near the act", "ingest/parse.py", "            if abs(c + int(s) - accepted.year) <= 1:", "            if True:",
     f"{SRC}::test_gazette_year_is_the_one_near_the_date_of_the_act"),
    ("9999-01-01 is no date", "ingest/text.py", "    return None if d is not None and d.year >= 9000 else d", "    return d",
     f"{SRC}::test_strategic_documents_and_the_sentinel_date"),
    ("e-mail addresses are not kept", "ingest/text.py", '    return None if s is None else _MAIL.sub("[имейл]", s)', "    return s",
     f"{SRC}::test_personal_data_never_reaches_the_rows"),
    ("escaped HTML is unescaped before the tags go", "ingest/text.py", "    t = html.unescape(scrub(s))", "    t = scrub(s)",
     f"{SRC}::test_text_is_unescaped_once_and_twice"),
    ("a 10-digit ЕИК (can be an ЕГН) is dropped", "ingest/parse.py", '        if eik and not re.fullmatch(r"\\d{9}|\\d{13}", eik):', "        if False:",
     f"{SRC}::test_impact_contracts_keep_no_natural_person"),
    ("no name of a natural person is kept", "ingest/parse.py", 'executor=name if kind == "юридическо лице" else None', "executor=name",
     f"{SRC}::test_impact_contracts_keep_no_natural_person"),
    ("the ЕИК check digit", "ingest/parse.py", "    if not re.fullmatch(r\"\\d{9}|\\d{13}\", e or \"\"):\n        return False", "    return True",
     f"{SRC}::test_eik_check_digit"),
    ("the archive must be fresh", "ingest/archive.py", "    if now - t > dt.timedelta(hours=STALE_HOURS):", "    if False:",
     f"{STO}::test_a_stale_archive_is_refused"),
    ("the file must match its sha256", "ingest/archive.py", "    if not sha.startswith(sha12):", "    if False:",
     f"{STO}::test_a_damaged_file_is_refused"),
    ("a read is held by the keys it lacks, not by its count", "ingest/store.py", "current=current(tables[0]), now=now, hold_when=hold_when)",
     "current=None, now=now, hold_when=hold_when)", f"{STO}::test_an_answer_that_swaps_a_record_for_another_is_held"),
    ("a changed record is logged field by field", "ingest/store.py", "              AND x.value IS DISTINCT FROM y.value", "              AND FALSE",
     f"{STO}::test_a_changed_act_is_logged_field_by_field_and_keeps_the_day_it_was_first_seen"),
    ("the children of a new record are not logged one by one", "ingest/store.py",
     'child = "" if t == parent else f" AND EXISTS', 'child = "" if True else f" AND EXISTS',
     f"{STO}::test_a_new_record_is_logged_and_its_children_are_not_logged_one_by_one"),
    ("a record keeps the day it was first seen", "ingest/store.py", "vals += [sha, old[1] if old else now,", "vals += [sha, now,",
     f"{STO}::test_a_changed_act_is_logged_field_by_field_and_keeps_the_day_it_was_first_seen"),
    ("the landing zone keeps only the current load", "ingest/store.py",
     '                conn.execute(f"DELETE FROM zakoni_src.{t} WHERE load_id = %s", (prev,))', "                pass",
     f"{STO}::test_a_changed_act_is_logged_field_by_field_and_keeps_the_day_it_was_first_seen"),
    ("the count after the write is the count in the file", "ingest/load.py",
     "    if not pipeline.record_check(conn, r, res.check, parsed.count, held + folded, res.name):", "    if False:",
     f"{STO}::test_a_write_that_loses_a_record_is_caught_by_the_count_after_it"),
    ("the count of an unchanged file is checked against the landing zone", "ingest/load.py",
     "        if not pipeline.record_check(conn, r, res.check, want, held, res.name):", "        if False:",
     f"{STO}::test_a_count_that_does_not_agree_stops_the_build_and_the_publication"),
    ("a count that does not agree stops the build", "ingest/run.py", '    if any("неуспешно" in p for p in problems):', "    if False:",
     f"{STO}::test_a_count_that_does_not_agree_stops_the_build_and_the_publication"),
    ("a rule that fails stops the publication", "ingest/run.py", "    if failed:\n        return problems", "    if False:\n        return problems",
     f"{STO}::test_a_rule_that_fails_keeps_the_published_version"),
    ("the legislative initiatives report is compared with the other", "ingest/load.py",
     'status = "същият файл като " + BY_ID[res.same_as].name if other == sha else',
     'status = "същият файл като " + BY_ID[res.same_as].name if True else',
     f"{STO}::test_the_legislative_initiatives_report_is_the_strategic_documents_report"),
    ("an invalid answer is reported", "ingest/load.py", "    except (ShapeError, pipeline.ShapeError) as e:", "    except ZeroDivisionError as e:",
     f"{STO}::test_an_invalid_answer_writes_nothing_and_is_reported"),
    ("a consultation closing before it opens is a failed rule", "db/checks.toml", "WHERE closes < opened OR days < 1", "WHERE FALSE",
     f"{STO}::test_a_rule_that_fails_keeps_the_published_version"),
    ("the 30-day rule starts on 04.11.2016", "db/build/10_clean.sql", "c.date_open >= DATE '2016-11-04' AND ", "",
     f"{STO}::test_the_short_term_indicator_follows_the_rule_of_article_26"),
    ("a non-normative act has no short-term indicator", "db/build/10_clean.sql", "coalesce(c.act_type, '') NOT LIKE 'Ненормативен%' AND ", "",
     f"{STO}::test_the_short_term_indicator_follows_the_rule_of_article_26"),
    ("a key that points nowhere is listed", "db/build/10_clean.sql",
     "WHERE s.public_consultation_number IS NOT NULL AND a.consultation_reg_num IS NULL", "WHERE FALSE",
     f"{STO}::test_the_clean_tables_have_the_keys_the_rules_want_and_the_rest_is_unmatched"),
    ("a municipal consultation gets its municipality", "db/build/10_clean.sql", "c.institution_id, c.institution_name, m.municipality_code,",
     "c.institution_id, c.institution_name, NULL,", f"{STO}::test_the_clean_tables_have_the_keys_the_rules_want_and_the_rest_is_unmatched"),
    ("a gazette year that cannot be right is not shown", "db/build/10_clean.sql",
     "       CASE WHEN abs(a.gazette_year - extract(year FROM a.accepted)) <= 1 THEN a.gazette_year END, a.active,",
     "       a.gazette_year, a.active,", f"{STO}::test_a_gazette_year_that_cannot_be_right_and_a_relation_to_itself_stay_off_the_pages"),
    ("a relation to itself is not shown", "db/build/10_clean.sql", "FROM zk_related r WHERE r.related_pris_id <> r.pris_id;", "FROM zk_related r;",
     f"{STO}::test_a_gazette_year_that_cannot_be_right_and_a_relation_to_itself_stay_off_the_pages"),
    ("the archive is part of freshness", "ingest/checks.py", "        archive.check_fresh(now)", "        pass",
     f"{STO}::test_freshness_is_quiet_when_all_is_well_and_loud_when_not"),
    ("a resource whose last read failed is reported", "ingest/checks.py", "(list(BAD),)):", "([],)):",
     f"{STO}::test_freshness_is_quiet_when_all_is_well_and_loud_when_not"),
    ("replay refuses an old database with history", "ingest/move.py", "        if history:", "        if False:",
     f"{STO}::test_replay_refuses_an_old_database_with_history"),
    ("parity counts the rows that differ", "ingest/move.py",
     "    differ = len(old.keys() ^ new.keys()) + sum(1 for k in old.keys() & new.keys() if old[k] != new[k])", "    differ = 0",
     f"{STO}::test_replay_and_parity_against_the_old_database"),
    ("the pages read only the serving layer", "app/queries.py", '    j = f"FROM pub_zakoni.consultation c LEFT JOIN {MUNI} m ON m.id = c.municipality_id"',
     '    j = f"FROM zakoni.consultation c LEFT JOIN {MUNI} m ON m.id = c.municipality_code"', f"{PAG}::test_the_pages_read_nothing_but_the_serving_layer"),
]


def pytest(copy, tests):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-rs", *tests],
                          cwd=copy, capture_output=True, text=True)


def main():
    # the same tests on the unbroken code must pass and none be skipped, else a broken environment (no database,
    # a missing package) would count every mutation as caught
    tests = sorted({t.split("::")[0] for *_, ts in MUTATIONS for t in ts.split()})
    r = pytest(ROOT, tests)
    if r.returncode != 0 or "SKIPPED" in r.stdout:
        print("the tests do not pass on the unbroken code, no mutation is checked:\n" + r.stdout[-2000:] + r.stderr[-1000:])
        sys.exit(1)
    print(f"baseline {r.stdout.strip().splitlines()[-1]}")
    survived = 0
    for name, path, old, new, tests in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "t"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".data"))
            f = copy / path
            src = f.read_text(encoding="utf-8")
            assert src.count(old) == 1, f"{name}: the code to break is not found once in {path}"
            f.write_text(src.replace(old, new), encoding="utf-8")
            r = pytest(copy, tests.split())
            caught = r.returncode != 0
            survived += not caught
            print(f"{'caught  ' if caught else 'SURVIVED'} {name}: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]}", flush=True)
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()

"""Each rule that guards the data is broken on purpose, in place, and the test that stands for it must fail.

    PAZAR_TEST_DSN=dbname=pazar_test python tests/mutations.py

A mutation that no test notices is a rule nobody guards. The file is put back after each one.
"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent

# (file, text, replacement, the test that must fail)
CASES = [
    # the reader
    ("ingest/parse.py", 'if tuple(_norm(h) for h in header) != HEADER:', "if False:",
     "tests/test_parse.py::test_header_change_makes_the_file_bad_and_the_rows_are_kept"),
    ("ingest/parse.py", "elif pv >= rv:", "elif pv > rv:", "tests/test_parse.py::test_promotion_flags"),
    ("ingest/parse.py", 't = (text or "").strip().strip(\'"\').strip()', 't = (text or "").strip()',
     "tests/test_parse.py::test_place_and_category"),
    ("ingest/parse.py", 'if r.truncated and i == last:', "if False:",
     "tests/test_parse.py::test_a_file_of_exactly_the_cut_size_is_truncated_and_its_last_record_is_not_trusted"),
    ("ingest/parse.py", "        if len(g) < 2:\n            continue", "        if True:\n            continue",
     "tests/test_parse.py::test_a_copy_of_another_chain_is_found"),
    ("ingest/parse.py", "if rv <= 0:", "if rv < 0:", "tests/test_parse.py::test_bad_price_texts_are_bad_rows_not_zeros"),
    ("ingest/parse.py", "MAX_RETAIL_E4 = 5000 * 10_000", "MAX_RETAIL_E4 = 500000 * 10_000",
     "tests/test_parse.py::test_high_price_is_an_anomaly_not_a_bad_row"),
    ("ingest/parse.py", "if any(h.category == c and h.retail == rv and h.promo == pv for h in have):", "if False:",
     "tests/test_parse.py::test_exact_duplicate_rows_count_once"),
    ("ingest/parse.py", "if have:\n            for h in have + [row_]:", "if False:\n            for h in have + [row_]:",
     "tests/test_parse.py::test_conflicting_prices_are_both_kept_and_flagged"),
    # the clean tables
    ("ingest/load.py", "if got.get(cid) != want:", "if False:", "tests/test_store.py::test_a_failed_check_writes_nothing"),
    ("ingest/load.py", "filing = {f.eik: f for f in parsed.files if f.eik and f.rows and not f.error}",
     "filing = {f.eik: f for f in parsed.files if f.eik}", "tests/test_store.py::test_a_file_without_prices_does_not_close_the_chains_spans"),
    ("ingest/load.py", "WHERE st.store_id = s.store_id AND st.chain_id = ANY(%(chains)s) AND s.to_day IS NULL",
     "WHERE st.store_id = s.store_id AND s.to_day IS NULL",
     "tests/test_store.py::test_a_chain_that_did_not_file_is_a_break_not_zero_prices"),
    ("ingest/load.py", "AND (n.retail >= 5 * k.retail OR 5 * n.retail <= k.retail)", "AND (n.retail >= 50 * k.retail OR 50 * n.retail <= k.retail)",
     "tests/test_store.py::test_a_price_five_times_higher_or_lower_is_marked_but_kept"),
    ("ingest/load.py", 'if before == "BGN" and dn / matched >= 0.6:', "if False:",
     "tests/test_store.py::test_currency_switch_is_found_from_the_prices"),
    ("ingest/load.py", "WHERE day < %s AND day >= %s ORDER BY chain_id, day DESC", "WHERE day < %s AND day >= %s - 100000 ORDER BY chain_id, day DESC",
     "tests/test_gold.py::test_a_price_in_leva_is_shown_in_euro"),
    ("ingest/build.py", "if chains < config.HOLD_CHAINS * prev[0]:", "if False:", "tests/test_store.py::test_a_big_drop_is_held_until_the_second_read_a_day_later"),
    ("ingest/build.py", "now - held[1] >= dt.timedelta(hours=config.HOLD_AGAIN_H)", "now - held[1] >= dt.timedelta(0)",
     "tests/test_store.py::test_a_big_drop_is_held_until_the_second_read_a_day_later"),
    ("ingest/build.py", "l.is_current AND l.ref >= %s AND l.ref < %s ORDER BY l.ref", "l.is_current AND l.ref = %s AND l.ref < %s ORDER BY l.ref",
     "tests/test_store.py::test_a_rewritten_day_rebuilds_the_whole_month_from_the_archive"),
    ("ingest/build.py", "    if not replay:\n        why = drop_is_big(c, day, parsed)", "    if True:\n        why = drop_is_big(c, day, parsed)",
     "tests/test_move.py::test_a_replay_builds_a_small_day_that_a_nightly_build_would_hold"),

    ("ingest/build.py", "if rewritten or later:", "if False:", "tests/test_store.py::test_an_older_day_arriving_late_rebuilds_the_month_in_order"),
    ("ingest/build.py", "            log(c, r, day, None, None, sha, \"confirmed\", now)", "            pass",
     "tests/test_store.py::test_a_held_day_confirmed_after_a_later_day_is_built_into_the_month"),
    ("ingest/memory.py", "if have is None or have >= need_mb:", "if True:",
     "tests/test_store.py::test_without_free_memory_the_step_waits_and_stops_before_a_day"),
    # the daily observations
    ("db/build/20_category_day.sql", "AND cd.day = %(d)s AND cd.filed AND cd.copy_of IS NULL", "AND cd.day = %(d)s AND cd.filed",
     "tests/test_gold.py::test_a_chain_has_its_own_figures_and_a_copy_of_another_chain_has_none"),
    ("db/build/20_category_day.sql", "CASE WHEN cd.currency = 'BGN' THEN %(rate)s ELSE 1 END AS retail", "CASE WHEN cd.currency = 'XXX' THEN %(rate)s ELSE 1 END AS retail",
     "tests/test_gold.py::test_a_price_in_leva_is_shown_in_euro"),
    ("db/build/20_category_day.sql", "FROM cur WHERE municipality_id IS NOT NULL AND NOT national_price GROUP BY municipality_id, category",
     "FROM cur WHERE municipality_id IS NOT NULL GROUP BY municipality_id, category",
     "tests/test_gold.py::test_the_online_shop_is_in_the_country_and_the_chain_but_not_on_a_municipality"),
    ("db/build/20_category_day.sql", "AND (s.flags & 29) = 0 AND s.category BETWEEN 1 AND 101;", "AND s.category BETWEEN 1 AND 101;",
     "tests/test_gold.py::test_anomalies_are_out_of_the_figures_but_stay_in_silver"),
    ("db/build/20_category_day.sql", "s.promo > 0 AND s.promo < s.retail AND (s.flags & 31) = 0", "s.promo > 0 AND s.promo <= s.retail AND (s.flags & 29) = 0",
     "tests/test_gold.py::test_promotion_equal_to_the_price_is_not_a_promotion"),
    ("db/build/20_category_day.sql", "n.promo <= p.prior * 0.99", "n.promo <= p.prior * 1.5", "tests/test_gold.py::test_a_promotion_is_real_only_below_the_shops_own_earlier_price"),
    ("db/build/20_category_day.sql", "ln(n.retail / (o.retail::numeric / 10000 / n.prev_rate))", "ln(n.retail / (o.retail::numeric / 10000))",
     "tests/test_gold.py::test_the_index_across_the_month_boundary_and_across_the_euro"),
    # fuel
    ("ingest/fuel.py", "raw_file_id=f.raw_file_id, current=current, now=now)", "raw_file_id=f.raw_file_id, current=None, now=now)",
     "tests/test_fuel.py::test_a_changed_value_is_logged_and_a_smaller_answer_is_held"),
    # the serving layer and the move
    ("ingest/run.py", "return min(days) - dt.timedelta(days=1) if days else FAR", "return min(days) if days else FAR",
     "tests/test_move.py::test_each_day_is_published_in_its_window_and_the_layer_equals_the_clean_tables"),
    ("db/publish/07_pub_pazar.price_span.sql", "-- window: from_day >= date_trunc('month', %(since)s::date)::date AND coalesce(to_day, 'infinity') >= %(since)s::date",
     "-- window: from_day >= %(since)s::date", "tests/test_move.py::test_each_day_is_published_in_its_window_and_the_layer_equals_the_clean_tables"),
    ("db/publish/07_pub_pazar.price_span.sql", "PARTITION BY store_id, product_id, from_day ORDER BY", "PARTITION BY store_id, product_id ORDER BY",
     "tests/test_move.py::test_two_prices_of_one_shop_and_code_on_one_day_are_both_served"),
    ("ingest/run.py", "    if blocking:\n        return problems", "    if False:\n        return problems",
     "tests/test_move.py::test_a_failed_data_rule_stops_the_publication"),
    ("ingest/parity.py", "sum(hashtextextended({SPAN_ROW}, 0))", "0",
     "tests/test_move.py::test_parity_is_clean_for_the_same_data_and_finds_a_changed_price"),
    ("ingest/parity.py", "SELECT day, status, zip_sha256, files", "SELECT day, 'built', zip_sha256, files",
     "tests/test_move.py::test_parity_is_clean_for_the_same_data_and_finds_a_changed_price"),
    ("app/queries.py", 'q("SELECT max(day) FROM pub_pazar.aggregate_day", one=True)', 'q("SELECT max(day) FROM pazar.aggregate_day", one=True)',
     "tests/test_pages.py::test_the_pages_read_the_serving_layer_and_say_its_version"),
    ("ingest/load.py", "first_day = least(pazar.chain.first_day, EXCLUDED.first_day)", "first_day = EXCLUDED.first_day",
     "tests/test_store.py::test_a_month_built_later_keeps_the_first_day_and_the_newest_name"),
    ("ingest/fuel.py", "if not isinstance(v, (int, float)) or v <= 0:", "if False:", "tests/test_fuel.py::test_a_missing_column_or_sheet_or_a_bad_price_stops_the_import"),
]


def main():
    caught = 0
    for file, old, new, test in CASES:
        p = root / file
        original = p.read_bytes()
        text = original.decode("utf-8")
        if old not in text:
            raise SystemExit("mutation target missing: " + old)
        try:
            p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test], cwd=root,
                               capture_output=True, text=True, encoding="utf-8")
            if r.returncode == 0:
                raise SystemExit(f"SURVIVED {file}: {old}\n  no test failed: {test}")
            if "SKIPPED" in r.stdout or "skipped" in r.stdout.split("\n")[-2]:
                raise SystemExit("the database tests did not run: set PAZAR_TEST_DSN")
            caught += 1
            print("caught", file, "|", old[:70].replace("\n", " "))
        finally:
            p.write_bytes(original)
    print(f"{caught} of {len(CASES)} mutations caught")


if __name__ == "__main__":
    main()

"""The old database of its own (schemas silver and gold) against the shared one (sigurnost_src and sigurnost), number by
number. Each comparison is a result of a rule of kind `parity` in ops.check_result; the step returns the ones that differ.

Compared: the sets (licence, year, listed resources), the links of the bulletin, every current table (file, kind, family,
template, rows, columns, issues, note, and a digest of its columns, rows and cells), the refused answers, the cells and
their sum, the tables that stand for each (year, family), their rows and values (count and sum per year, family and
indicator, and a digest of every row and value), the reconciliations and the unmatched structures.
"""
import psycopg

from . import config, pipeline

# the same text on both sides: one line per row, sorted, then md5
DIGEST_OLD = {
    "cols": "SELECT md5(coalesce(string_agg(concat_ws('|', col_no, label), ',' ORDER BY col_no), '')) FROM silver.col WHERE resource_uri = %s AND sha256 = %s",
    "rows": """SELECT md5(coalesce(string_agg(concat_ws('|', row_no, block_no, structure, code, label, text, marker, level, is_total, dup), ',' ORDER BY row_no), ''))
               FROM silver.row WHERE resource_uri = %s AND sha256 = %s""",
    "cells": """SELECT md5(coalesce(string_agg(concat_ws('|', row_no, col_no, text, value, issue), ',' ORDER BY row_no, col_no), '')), count(*), coalesce(sum(value), 0)
                FROM silver.cell WHERE resource_uri = %s AND sha256 = %s""",
}
DIGEST_NEW = {
    "cols": "SELECT md5(coalesce(string_agg(concat_ws('|', col_no, label), ',' ORDER BY col_no), '')) FROM sigurnost_src.col WHERE load_id = %s",
    "rows": """SELECT md5(coalesce(string_agg(concat_ws('|', row_no, block_no, structure, code, label, text, marker, level, is_total, dup), ',' ORDER BY row_no), ''))
               FROM sigurnost_src.row WHERE load_id = %s""",
    "cells": """SELECT md5(coalesce(string_agg(concat_ws('|', row_no, col_no, text, value, issue), ',' ORDER BY row_no, col_no), '')), count(*), coalesce(sum(value), 0)
                FROM sigurnost_src.cell WHERE load_id = %s""",
}
GOLD_ROWS = """SELECT year, family, md5(string_agg(concat_ws('|', structure_code, row_no, code, text, level, parent_row_no, is_total), ','
                                              ORDER BY structure_code, row_no)), count(*)
               FROM {schema}.crime_row GROUP BY 1, 2"""
GOLD_VALUES = """SELECT year, family, indicator, count(*), coalesce(sum(value), 0),
                        md5(string_agg(concat_ws('|', structure_code, row_no, value, value_text, template, resource_uri, {sha}, period_start, period_end), ','
                                       ORDER BY structure_code, row_no))
                 FROM {schema}.observation GROUP BY 1, 2, 3"""
RECON = """SELECT year, family, check_id, scope, expected, got, status, gating, coalesce(detail, ''), resource_uri FROM {schema}"""


def _diff(a, b, detail, what, limit=20):
    """The keys whose values differ between two {key: value} dicts; each one is named in `detail`."""
    bad = sorted((k for k in a.keys() | b.keys() if a.get(k) != b.get(k)), key=str)
    detail += [f"{what} {k}: старо {a.get(k)}, ново {b.get(k)}" for k in bad[:limit]]
    return len(bad)


def parity(conn, r, old_dsn=None):
    detail = []
    with psycopg.connect(old_dsn or config.OLD_DSN, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        old.execute("SET TIME ZONE 'UTC'")
        q_old = lambda sql, *a: old.execute(sql, a).fetchall()
        q_new = lambda sql, *a: conn.execute(sql, a).fetchall()

        # the sets
        o = {x[0]: x[1:] for x in q_old("SELECT set_uri, kind, year, terms_of_use_id, licence, shared, listed FROM silver.dataset")}
        n = {x[0]: x[1:] for x in q_new("SELECT set_uri, kind, year, terms_of_use_id, licence, shared, listed FROM sigurnost.dataset")}
        pipeline.record_check(conn, r, "parity_datasets", len(o), len(n))
        pipeline.record_check(conn, r, "parity_datasets_differ", 0, _diff(o, n, detail, "набор"), "; ".join(detail)[:2000] or None)

        # the links of the bulletin
        d0 = len(detail)
        o = {x[0]: x[1:] for x in q_old("SELECT resource_uri, set_uri, name, url, period, source_updated_at FROM silver.link")}
        n = {x[0]: x[1:] for x in q_new("SELECT resource_uri, set_uri, name, url, period, source_updated_at FROM sigurnost.link")}
        pipeline.record_check(conn, r, "parity_links", len(o), len(n))
        pipeline.record_check(conn, r, "parity_links_differ", 0, _diff(o, n, detail, "връзка"), "; ".join(detail[d0:])[:2000] or None)

        # every current table, field by field and digest by digest
        d0 = len(detail)
        fields = "kind, family, template, n_rows, n_cols, n_blocks, issues, title, note, title_year, set_uri"
        o_res = {x[0]: x[1:] for x in q_old(f"SELECT resource_uri, sha256, {fields} FROM silver.resource WHERE is_current")}
        n_res = {x[0]: x[1:] for x in q_new(f"SELECT resource_uri, raw_sha256, {fields}, load_id FROM sigurnost_src.current_resource")}
        o, n, cells_o, cells_n = {}, {}, [0, 0], [0, 0]
        for uri, row in o_res.items():
            dig = [q_old(DIGEST_OLD[k], uri, row[0])[0] for k in ("cols", "rows", "cells")]
            o[uri] = (*row, dig[0][0], dig[1][0], dig[2][0])
            cells_o[0] += dig[2][1]
            cells_o[1] += dig[2][2]
        for uri, row in n_res.items():
            dig = [q_new(DIGEST_NEW[k], row[-1])[0] for k in ("cols", "rows", "cells")]
            n[uri] = (*row[:-1], dig[0][0], dig[1][0], dig[2][0])
            cells_n[0] += dig[2][1]
            cells_n[1] += dig[2][2]
        pipeline.record_check(conn, r, "parity_resources", len(o), len(n))
        pipeline.record_check(conn, r, "parity_resources_differ", 0, _diff(o, n, detail, "таблица"), "; ".join(detail[d0:])[:2000] or None)
        pipeline.record_check(conn, r, "parity_cells", cells_o[0], cells_n[0])
        pipeline.record_check(conn, r, "parity_cell_sum", cells_o[1], cells_n[1])
        o_bad = q_old("SELECT count(*) FROM silver.resource WHERE status = 'invalid'")[0][0]
        n_bad = q_new("SELECT count(DISTINCT (ref, raw_file_id)) FROM ops.load WHERE tool = %s AND source = 'egov' AND status = 'rejected'", config.TOOL)[0][0]
        pipeline.record_check(conn, r, "parity_rejected", o_bad, n_bad)

        # the clean tables: which table stands for each (year, family), its rows and values
        d0 = len(detail)
        o = {x[:2]: x[2:] for x in q_old("SELECT year, family, resource_uri, sha256, set_uri, template, n_rows, issues, coalesce(note, '') FROM gold.source_table")}
        n = {x[:2]: x[2:] for x in q_new("SELECT year, family, resource_uri, raw_sha256, set_uri, template, n_rows, issues, coalesce(note, '') FROM sigurnost.source_table")}
        pipeline.record_check(conn, r, "parity_gold_tables", len(o), len(n))
        pipeline.record_check(conn, r, "parity_gold_tables_differ", 0, _diff(o, n, detail, "(година, вид)"), "; ".join(detail[d0:])[:2000] or None)
        d0 = len(detail)
        o = {x[:2]: x[2:] for x in q_old(GOLD_ROWS.format(schema="gold"))}
        n = {x[:2]: x[2:] for x in q_new(GOLD_ROWS.format(schema="sigurnost"))}
        pipeline.record_check(conn, r, "parity_rows", sum(x[1] for x in o.values()), sum(x[1] for x in n.values()))
        groups = _diff(o, n, detail, "редове")
        o = {x[:3]: x[3:] for x in q_old(GOLD_VALUES.format(schema="gold", sha="raw_sha256"))}
        n = {x[:3]: x[3:] for x in q_new(GOLD_VALUES.format(schema="sigurnost", sha="raw_sha256"))}
        pipeline.record_check(conn, r, "parity_observations", sum(x[0] for x in o.values()), sum(x[0] for x in n.values()))
        pipeline.record_check(conn, r, "parity_observation_sum", sum(x[1] for x in o.values()), sum(x[1] for x in n.values()))
        groups += _diff(o, n, detail, "стойности")
        pipeline.record_check(conn, r, "parity_groups_differ", 0, groups, "; ".join(detail[d0:])[:2000] or None)

        # the reconciliations (as a multiset: the old database keeps the results of every build, here of the last one)
        d0 = len(detail)
        o_rec = q_old(RECON.format(schema="gold.check_result"))
        n_rec = q_new(RECON.format(schema="sigurnost.reconciliation"))
        count = lambda rows: {k: rows.count(k) for k in set(rows)}
        pipeline.record_check(conn, r, "parity_reconciliations", len(o_rec), len(n_rec))
        pipeline.record_check(conn, r, "parity_reconciliations_differ", 0, _diff(count(o_rec), count(n_rec), detail, "сверка"),
                              "; ".join(detail[d0:])[:2000] or None)
        o_un = q_old("SELECT count(*) FROM gold.unmatched")[0][0]
        pipeline.record_check(conn, r, "parity_unmatched", o_un, q_new("SELECT count(*) FROM sigurnost.unmatched")[0][0])
    r.stats["parity_detail"] = detail[:200]
    bad = conn.execute("""SELECT title_bg, expected, actual FROM ops.check_latest
                          WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name""", (config.TOOL,)).fetchall()
    return [f"{title}: старо {e}, ново {a}" for title, e, a in bad]

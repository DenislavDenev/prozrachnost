"""The move from the old database of its own to the shared one: `replay` loads what the old database holds from the archive
files it names and through the same parser, and `parity` compares the two number by number.

The test database stands for both: the old schemas (db/legacy, with its own `ops` renamed `old_ops`) are made beside the
shared ones and filled from a build, as the old tool wrote them. The steps read them through the same DSN."""
import datetime as dt
from pathlib import Path

import pytest

from ingest import config, parity, pipeline, run as runmod
from tests import helpers as h

UTC = dt.timezone.utc


@pytest.fixture
def arch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ARCHIVE", tmp_path)
    return tmp_path


def legacy(c, root):
    """The schemas of the old database, filled from what the build wrote in the shared one."""
    c.execute("DROP SCHEMA IF EXISTS silver, gold, old_ops CASCADE")
    for f in sorted((config.ROOT / "db" / "legacy").glob("*.sql")):
        sql = f.read_text(encoding="utf-8").replace("ops.", "old_ops.").replace("SCHEMA IF NOT EXISTS ops", "SCHEMA IF NOT EXISTS old_ops")
        c.execute(sql)
    rel = lambda p: Path(p).relative_to(root).as_posix()
    for set_uri, lid, path, sha, at in c.execute(
            """SELECT d.set_uri, d.list_load_id, f.path, f.sha256, x.fetched_at FROM sigurnost.dataset d
               LEFT JOIN ops.load l ON l.load_id = d.list_load_id LEFT JOIN ops.raw_file f USING (raw_file_id)
               LEFT JOIN LATERAL (SELECT max(fetched_at) AS fetched_at FROM ops.fetch WHERE raw_file_id = f.raw_file_id) x ON true""").fetchall():
        c.execute("""INSERT INTO silver.dataset (set_uri, title, kind, year, terms_of_use_id, licence, shared, licence_checked, source_updated,
                       listed, list_sha256, list_path, list_read_at)
                     SELECT set_uri, title, kind, year, terms_of_use_id, licence, shared, licence_checked, source_updated, listed, %s, %s, %s
                     FROM sigurnost.dataset WHERE set_uri = %s""", (sha, path and rel(path), at, set_uri))
    for lid, path in c.execute("""SELECT r.load_id, f.path FROM sigurnost_src.current_resource r JOIN ops.load l USING (load_id)
                                  JOIN ops.raw_file f USING (raw_file_id)""").fetchall():
        c.execute("""INSERT INTO silver.resource (resource_uri, sha256, set_uri, name, version, source_updated_at, path, bytes, read_at, kind, title,
                       family, template, n_rows, n_cols, n_blocks, issues, note, status, is_current, title_year)
                     SELECT resource_uri, raw_sha256, set_uri, name, version, source_updated_at, %s, 0, read_at, kind, title, family, template,
                       n_rows, n_cols, n_blocks, issues, note, 'built', true, title_year FROM sigurnost_src.resource WHERE load_id = %s""",
                  (rel(path), lid))
        key = "(SELECT resource_uri FROM sigurnost_src.resource WHERE load_id = %s), (SELECT raw_sha256 FROM sigurnost_src.resource WHERE load_id = %s)"
        c.execute(f"INSERT INTO silver.col SELECT {key}, col_no, label FROM sigurnost_src.col WHERE load_id = %s", (lid, lid, lid))
        c.execute(f"""INSERT INTO silver.row (resource_uri, sha256, row_no, block_no, structure, code, label, text, marker, level, is_total, dup)
                      SELECT {key}, row_no, block_no, structure, code, label, text, marker, level, is_total, dup FROM sigurnost_src.row WHERE load_id = %s""",
                  (lid, lid, lid))
        c.execute(f"INSERT INTO silver.cell SELECT {key}, row_no, col_no, text, value, issue FROM sigurnost_src.cell WHERE load_id = %s", (lid, lid, lid))
    c.execute("""INSERT INTO silver.link (resource_uri, set_uri, name, url, period, source_updated_at, list_sha256)
                 SELECT resource_uri, set_uri, name, url, period, source_updated_at, 'x' FROM sigurnost.link""")
    c.execute("INSERT INTO gold.structure (code, name, kind, oblast, sort) SELECT code, name, kind, oblast, sort FROM sigurnost.structure")
    c.execute("INSERT INTO gold.family SELECT code, title, sort FROM sigurnost.family")
    c.execute("INSERT INTO gold.indicator SELECT code, title, unit, kind, definition, example, formula, sort FROM sigurnost.indicator")
    c.execute("""INSERT INTO gold.source_table (year, family, resource_uri, sha256, set_uri, template, n_rows, issues, note)
                 SELECT year, family, resource_uri, raw_sha256, set_uri, template, n_rows, issues, note FROM sigurnost.source_table""")
    c.execute("INSERT INTO gold.crime_row SELECT year, family, structure_code, row_no, code, text, level, parent_row_no, is_total FROM sigurnost.crime_row")
    c.execute("""INSERT INTO gold.observation SELECT year, family, structure_code, row_no, indicator, period_kind, period_start, period_end, value,
                 value_text, template, resource_uri, raw_sha256 FROM sigurnost.observation""")
    c.execute("""INSERT INTO gold.check_result (year, family, check_id, scope, expected, got, diff, status, gating, detail, resource_uri, sha256)
                 SELECT year, family, check_id, scope, expected, got, diff, status, gating, detail, resource_uri, raw_sha256 FROM sigurnost.reconciliation""")


def step(c, name, fn):
    with pipeline.run(c, "sigurnost", name) as r:
        return fn(r)


def test_replay_from_the_files_the_old_database_names_gives_tables_equal_to_it(c, arch, schema):
    from tests.conftest import reset
    st = h.build_archive(arch, {h.POLICE: h.police_2024(), h.BULLETIN: None})
    h.build(c, st)
    legacy(c, arch)
    assert step(c, "parity", lambda r: parity.parity(c, r, schema)) == []
    # the shared tables empty again, then loaded only from what the old database names
    reset(c)
    rep = step(c, "replay", lambda r: runmod.replay(c, r, schema))
    assert rep["lists"] == 2 and rep["outcomes"] == {"new": 6} and rep["problems"] == []
    assert step(c, "parity", lambda r: parity.parity(c, r, schema)) == []
    got = dict(c.execute("SELECT name, actual FROM ops.check_latest WHERE tool = 'sigurnost' AND kind = 'parity'").fetchall())
    assert got["parity_resources"] == 6 and got["parity_links"] == 5 and got["parity_gold_tables"] == 5 and got["parity_observations"] > 10000
    assert got["parity_resources_differ"] == 0 and got["parity_groups_differ"] == 0


def test_parity_names_a_value_a_table_and_a_reconciliation_that_differ(c, arch, schema):
    st = h.build_archive(arch, {h.POLICE: h.police_2024()})
    h.build(c, st)
    legacy(c, arch)
    c.execute("UPDATE gold.observation SET value = value + 1 WHERE family = 'structures' AND structure_code = 'BG331' AND indicator = 'reg'")
    c.execute("UPDATE silver.cell SET text = 'x' WHERE resource_uri = %s AND row_no = 3 AND col_no = 2", (h.A,))
    c.execute("UPDATE gold.check_result SET got = got + 1 WHERE id = (SELECT min(id) FROM gold.check_result)")
    problems = step(c, "parity", lambda r: parity.parity(c, r, schema))
    names = {t for t in problems}
    assert any(p.startswith("Сбор на стойностите на публикуваните таблици") for p in names)
    assert any(p.startswith("Групи (година, вид, показател)") for p in names)
    assert any(p.startswith("Действащи таблици с различен файл") for p in names)
    assert any(p.startswith("Сверки с различно") for p in names)
    detail = c.execute("SELECT detail FROM ops.check_latest WHERE tool = 'sigurnost' AND name = 'parity_resources_differ'").fetchone()[0]
    assert h.A in detail

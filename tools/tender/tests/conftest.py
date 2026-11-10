"""The test database: the foundation of tools/danni migrated and its places loaded from the real EKATTE archive by
danni's own step, then the tables of Тендер. The foundation is built once for the session; a test that asks for
`conn` starts with the tables of Тендер empty and its part of the ledger gone."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / "danni"   # the foundation is not copied into the tool: the tests need the sibling folder
OWN = ("pub_tender", "tender_stage", "tender", "tender_src")
OLD = ("live", "stage", "previous", "tr", "ed", "eopsvc", "sebra", "old_ops")   # what the old database of its own had
EVERYTHING = ("api_v1",) + OWN + ("pub", "danni_src", "core", "ops") + OLD


def danni(dsn, tmp, *steps, check=True):
    env = {**os.environ, "DANNI_DSN": dsn, "DANNI_TENDER_DSN": dsn, "DANNI_ARCHIVE": str(tmp / "archive"), "DANNI_DATA": str(tmp / "danni")}
    for step in steps:
        subprocess.run([sys.executable, "-m", "ingest.run", "--step", step], cwd=DANNI, env=env, check=check, capture_output=True)


def empty(c):
    """The tables of Тендер made again from its migrations, its ledger emptied. The datasets stay registered, with the
    right to be shown that the licence list of danni gave them."""
    from ingest import db, pipeline
    pipeline.require_test_db(c)
    for schema in OWN + OLD:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    for table in ("check_result", "change_log", "hold", "fetch", "load", "run", "schema_migration"):
        c.execute(f"DELETE FROM ops.{table} WHERE tool = 'tender'")
    c.execute("DELETE FROM pub.publication WHERE tool = 'tender'")
    db.migrate(c)


@pytest.fixture(scope="session", autouse=True)
def test_database(tmp_path_factory):
    """Every connection of the tests goes to TENDER_TEST_DSN. ingest.config reads TENDER_DSN once, when the first
    test file imports ingest, so setting the environment in a test file is too late when an earlier file imported
    it: the fixtures would then wipe the production database. The DSN is set on the two modules every connection
    goes through: ingest.db (the ingest) and app.queries (the pages)."""
    dsn = os.environ.get("TENDER_TEST_DSN")
    if not dsn:
        yield None
        return
    assert "test" in psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", ""), f"refusing to use {dsn!r}"
    from app import queries
    from ingest import db, pipeline
    db.DSN = queries.DSN = dsn
    tmp = tmp_path_factory.mktemp("foundation")
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in EVERYTHING:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.schema_migrations")
    (tmp / "archive" / "ekatte").mkdir(parents=True)
    shutil.copy(DANNI / "tests" / "fixtures" / "ekatte.zip", tmp / "archive" / "ekatte" / "2026-09-29.002862bf0655.zip")
    danni(dsn, tmp, "migrate", "place")
    with pipeline.connect(dsn) as c:
        db.migrate(c)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from its licence
    # list, the decisions for the columns with names from its list of persons, and the places are published
    danni(dsn, tmp, "check", check=False)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM core.dataset WHERE tool = 'tender' AND is_displayable").fetchone()[0] == 4
        assert c.execute("SELECT count(*) FROM pub.municipality").fetchone()[0] == 265
    yield dsn


@pytest.fixture
def conn(test_database):
    if not test_database:
        pytest.skip("TENDER_TEST_DSN required")
    from ingest import db
    with db.connect(autocommit=True) as c:
        empty(c)
        yield c


def landed(c):
    """Rows a test wrote into the landing zone by hand get what a reader would have given them: a load, and for a
    file of the open data its place in the register of raw files."""
    import tempfile
    from ingest import db
    tmp = Path(tempfile.mkdtemp())
    for day, kind in c.execute("SELECT day, kind FROM tender_src.eop_file WHERE load_id IS NULL").fetchall():
        file = db.keep_raw(c, "eop-file", f"{day}/{kind}", f"[] {day} {kind}".encode(), tmp, "file.json", compress=False)
        db.land(c, "eop-file", f"{day}/{kind}", {"empty": True},
                lambda conn, load_id, rows: conn.execute("UPDATE tender_src.eop_file SET load_id = %s, raw_file_id = %s WHERE day = %s AND kind = %s",
                                                         (load_id, file.raw_file_id, day, kind)), file)
    tables = [t for t, in c.execute("""SELECT table_name FROM information_schema.columns WHERE table_schema = 'tender_src'
        AND column_name = 'load_id' AND table_name <> 'eop_file' ORDER BY 1""")]
    if any(c.execute(f"SELECT EXISTS (SELECT FROM tender_src.{t} WHERE load_id IS NULL)").fetchone()[0] for t in tables):
        def write(conn, load_id, rows):
            for t in tables:
                conn.execute(f"UPDATE tender_src.{t} SET load_id = %s WHERE load_id IS NULL", (load_id,))
        db.land(c, "tr", f"test/{id(tmp)}", {"by hand": True}, write)

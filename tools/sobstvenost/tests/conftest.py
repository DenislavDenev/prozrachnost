"""The test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, then the tables of Държавна собственост. Built once for the session; every database test starts with
the tables of Собственост empty. Only a database whose name contains `test` is touched."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / 'danni'           # the foundation is not copied into the tool: the tests need the sibling folder
SCHEMAS = ('api_v1', 'pub_sobstvenost', 'pub', 'sobstvenost', 'sobstvenost_src', 'danni_src', 'core', 'ops', 'live', 'stage',
           'old_ops')
TABLES = ('sobstvenost.notice_municipality', 'sobstvenost.notice_place', 'sobstvenost.notice', 'sobstvenost.concession_document',
          'sobstvenost.concession_profile', 'sobstvenost.concession', 'sobstvenost.report_document', 'sobstvenost.report',
          'sobstvenost.profile', 'sobstvenost.enterprise', 'sobstvenost.ministry',
          'sobstvenost_src.read', 'sobstvenost_src.enterprise', 'sobstvenost_src.ministry', 'sobstvenost_src.concession',
          'sobstvenost_src.board_entry', 'sobstvenost_src.report_document', 'sobstvenost_src.report', 'sobstvenost_src.profile',
          'sobstvenost_src.notice_place', 'sobstvenost_src.notice_municipality', 'sobstvenost_src.notice',
          'sobstvenost_src.concession_document', 'sobstvenost_src.concession_profile', 'sobstvenost_src.full_read',
          'sobstvenost_src.attachment',
          'pub_sobstvenost.enterprise', 'pub_sobstvenost.profile', 'pub_sobstvenost.profile_version', 'pub_sobstvenost.board_entry',
          'pub_sobstvenost.report', 'pub_sobstvenost.report_document', 'pub_sobstvenost.report_version',
          'pub_sobstvenost.report_version_document', 'pub_sobstvenost.concession', 'pub_sobstvenost.concession_profile',
          'pub_sobstvenost.concession_document', 'pub_sobstvenost.notice', 'pub_sobstvenost.notice_place',
          'pub_sobstvenost.notice_municipality', 'pub_sobstvenost.attachment', 'pub_sobstvenost.source_state')


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
    (tmp / 'archive' / 'ekatte').mkdir(parents=True)
    shutil.copy(DANNI / 'tests' / 'fixtures' / 'ekatte.zip', tmp / 'archive' / 'ekatte' / '2026-09-29.002862bf0655.zip')
    env = {**os.environ, 'DANNI_DSN': dsn, 'DANNI_ARCHIVE': str(tmp / 'archive'), 'DANNI_DATA': str(tmp / 'danni')}
    for step in ('migrate', 'place'):
        subprocess.run([sys.executable, '-m', 'ingest.run', '--step', step], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, '0007')
        pipeline.migrate(c, 'sobstvenost', ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, the persons from its list of decisions, and the places of the map are published by it
    subprocess.run([sys.executable, '-m', 'ingest.run', '--step', 'check'], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'sobstvenost'").fetchone()[0]
        assert c.execute('SELECT count(*) FROM pub.municipality').fetchone()[0] == 265


@pytest.fixture(scope='session')
def dsn(tmp_path_factory):
    dsn = os.environ.get('SOBSTVENOST_TEST_DSN')
    if not dsn:
        pytest.skip('SOBSTVENOST_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    rebuild(dsn, tmp_path_factory.mktemp('foundation'))
    return dsn


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    from ingest import progress, store
    monkeypatch.setattr(store, 'RAW', tmp_path / 'raw')
    monkeypatch.setattr(progress, 'DATA', tmp_path)
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in ('live', 'stage', 'old_ops'):
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        c.execute(f'TRUNCATE {", ".join(TABLES)}')
        c.execute("DELETE FROM pub.publication WHERE tool = 'sobstvenost'")
        c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'sobstvenost'")
        for table in ('check_result', 'change_log', 'hold', 'fetch', 'load', 'run'):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'sobstvenost'")
        yield c


@pytest.fixture
def site(conn, dsn, monkeypatch):
    from fastapi.testclient import TestClient
    from app import queries
    from app.main import app
    monkeypatch.setattr(queries, 'DSN', dsn)
    queries._cache.clear()
    return TestClient(app)

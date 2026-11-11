"""The test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, then the tables of Одити и контрол. Built once for the session; every database test starts with the
tables of Контрол empty."""
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
SCHEMAS = ('api_v1', 'pub_kontrol', 'pub', 'kontrol', 'kontrol_src', 'danni_src', 'core', 'ops', 'live', 'src', 'stage', 'old_ops')
TABLES = ('kontrol.inspection', 'kontrol.adfi_list', 'kontrol.adfi_report', 'kontrol.audit_report', 'kontrol.decision',
          'kontrol.document', 'kontrol_src.read', 'kontrol_src.audit_report', 'kontrol_src.adfi_report', 'kontrol_src.adfi_list',
          'kontrol_src.inspection', 'kontrol_src.decision', 'kontrol_src.document', 'pub_kontrol.audit_report',
          'pub_kontrol.adfi_report', 'pub_kontrol.adfi_list', 'pub_kontrol.inspection', 'pub_kontrol.decision',
          'pub_kontrol.document', 'pub_kontrol.record_version', 'pub_kontrol.document_version', 'pub_kontrol.source_state',
          'pub_kontrol.inspection_gap')


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
        pipeline.migrate(c, 'kontrol', ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from the licence
    # list of danni, and the persons from its list of decisions
    subprocess.run([sys.executable, '-m', 'ingest.run', '--step', 'check'], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = 'kontrol'").fetchone()[0]


@pytest.fixture(scope='session')
def dsn(tmp_path_factory):
    dsn = os.environ.get('KONTROL_TEST_DSN')
    if not dsn:
        pytest.skip('KONTROL_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    rebuild(dsn, tmp_path_factory.mktemp('foundation'))
    return dsn


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    from ingest import store
    monkeypatch.setattr(store, 'RAW', tmp_path / 'raw')
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in ('live', 'src', 'stage', 'old_ops'):
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        c.execute(f'TRUNCATE {", ".join(TABLES)}')
        c.execute("DELETE FROM pub.publication WHERE tool = 'kontrol'")
        c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'kontrol'")
        for table in ('check_result', 'change_log', 'hold', 'fetch', 'load', 'run'):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'kontrol'")
        yield c

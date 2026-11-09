"""The test database: the foundation of tools/danni migrated and its places loaded from the real EKATTE archive
by danni's own step, then the tables of Население. Built once for the session; every test starts with them empty."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / 'danni'   # the foundation is not copied into the tool: the tests need the sibling folder
FIX = ROOT / 'tests' / 'fixtures'
SCHEMAS = ('api_v1', 'naselenie', 'naselenie_src', 'danni_src', 'core', 'ops', 'live', 'old_ops')


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        c.execute('DROP TABLE IF EXISTS public.schema_migrations')
    (tmp / 'archive' / 'ekatte').mkdir(parents=True)
    shutil.copy(FIX / 'ekatte.zip', tmp / 'archive' / 'ekatte' / '2026-09-29.002862bf0655.zip')
    env = {**os.environ, 'DANNI_DSN': dsn, 'DANNI_ARCHIVE': str(tmp / 'archive'), 'DANNI_DATA': str(tmp / 'danni')}
    for step in ('migrate', 'place'):
        subprocess.run([sys.executable, '-m', 'ingest.run', '--step', step], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, '0004')
        pipeline.migrate(c, 'naselenie', ROOT)


@pytest.fixture(scope='session')
def dsn(tmp_path_factory):
    dsn = os.environ.get('NASELENIE_TEST_DSN')
    if not dsn:
        pytest.skip('NASELENIE_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    rebuild(dsn, tmp_path_factory.mktemp('foundation'))
    return dsn


@pytest.fixture
def conn(dsn):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        c.execute('DROP SCHEMA IF EXISTS live CASCADE; DROP SCHEMA IF EXISTS old_ops CASCADE')
        c.execute('''TRUNCATE naselenie.population, naselenie.settlement_population, naselenie_src.grao_row,
            naselenie_src.grao_file, naselenie_src.grao_catalog RESTART IDENTITY''')
        for table in ('check_result', 'change_log', 'hold', 'fetch', 'load', 'run'):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'naselenie'")
        c.execute('UPDATE core.settlement SET gone_at = NULL WHERE gone_at IS NOT NULL')
        yield c

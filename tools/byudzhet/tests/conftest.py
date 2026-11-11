"""The municipalities for the parser (a copy of core.municipality, read from the shared database on 11.11.2026), and
the test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, the serving layer of Население (Бюджет reads the registrations there), then the tables of Бюджет.
Built once for the session; every database test starts with the tables of Бюджет empty."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import parse, pipeline

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / 'danni'           # the foundation is not copied into the tool: the tests need the sibling folder
NASELENIE = ROOT.parent / 'naselenie'   # the registrations Бюджет divides by are published by Население
FIX = ROOT / 'tests' / 'fixtures'
REF = json.loads((FIX / 'municipalities.json').read_text(encoding='utf-8'))
parse.use(REF)
SCHEMAS = ('api_v1', 'pub_byudzhet', 'pub_naselenie', 'pub', 'byudzhet', 'byudzhet_src', 'naselenie', 'naselenie_src',
           'danni_src', 'core', 'ops', 'old_src', 'old_ops', 'live')


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
        pipeline.migrate(c, 'naselenie', NASELENIE)
        pipeline.migrate(c, 'byudzhet', ROOT)
    # danni again, now that the dataset of this tool is registered: its right to be shown comes from the licence
    # list of danni, and the places are published into the serving layer
    subprocess.run([sys.executable, '-m', 'ingest.run', '--step', 'check'], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute("SELECT is_distributable FROM core.dataset WHERE dataset = 'byudzhet.mf'").fetchone()[0]
        assert c.execute('SELECT count(*) FROM pub.municipality').fetchone()[0] == 265


@pytest.fixture(scope='session')
def dsn(tmp_path_factory):
    dsn = os.environ.get('BYUDZHET_TEST_DSN')
    if not dsn:
        pytest.skip('BYUDZHET_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    rebuild(dsn, tmp_path_factory.mktemp('foundation'))
    return dsn


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    from ingest import run
    monkeypatch.setattr(run, 'RAW', tmp_path / 'raw')
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in ('old_src', 'old_ops', 'live'):
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        c.execute('''TRUNCATE byudzhet.state_line, byudzhet.kfp_value, byudzhet.reserve_line, byudzhet.municipal_value,
            byudzhet.report, byudzhet.known_gap, byudzhet_src.egov_row, byudzhet_src.egov_file, byudzhet_src.egov_catalog,
            pub_byudzhet.report, pub_byudzhet.state_line, pub_byudzhet.kfp_value, pub_byudzhet.reserve_line,
            pub_byudzhet.municipal_value, pub_byudzhet.source_state, pub_byudzhet.problem,
            pub_naselenie.population, pub_naselenie.snapshot RESTART IDENTITY''')
        c.execute("DELETE FROM pub.publication WHERE tool IN ('byudzhet', 'naselenie')")
        c.execute("UPDATE ops.check_rule SET is_active = true WHERE tool = 'byudzhet'")
        for table in ('check_result', 'change_log', 'hold', 'fetch', 'load', 'run'):
            c.execute(f"DELETE FROM ops.{table} WHERE tool = 'byudzhet'")
        yield c

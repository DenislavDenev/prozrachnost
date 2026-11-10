import os
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pipeline  # noqa: E402

SCHEMAS = ('api_v1', 'pub_demo', 'pub', 'danni_src', 'core', 'ops', 'live', 'demo', 'demo_src')
DATA = ('ops.check_result', 'ops.change_log', 'ops.hold', 'ops.load', 'ops.fetch', 'ops.raw_file', 'ops.run',
        'core.org', 'core.settlement', 'core.municipality', 'core.oblast', 'core.nuts', 'core.xwalk', 'core.fx_rate',
        'core.indicator', 'pub.publication', 'pub.indicator', 'pub.dataset', 'pub.settlement', 'pub.municipality',
        'pub.oblast', 'pub.nuts')


def rebuild(conn):
    """An empty test database with the foundation migrated from nothing. Refuses a database whose name lacks `test`."""
    pipeline.require_test_db(conn)
    for schema in SCHEMAS:
        conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
    pipeline.migrate(conn, 'danni', ROOT)


def reset(conn):
    """Back to the state right after the migrations, without running them again (they take seconds)."""
    pipeline.require_test_db(conn)
    for schema in ('pub_demo', 'live', 'demo', 'demo_src'):
        conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
    conn.execute('DROP TABLE IF EXISTS api_v1.leak')
    landing = [f'danni_src.{t}' for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'danni_src'")]
    conn.execute(f"TRUNCATE {', '.join(DATA + tuple(landing))} RESTART IDENTITY CASCADE")
    conn.execute("""DELETE FROM ops.source WHERE tool <> 'danni'; DELETE FROM ops.check_rule WHERE tool <> 'danni';
        DELETE FROM core.dataset_table WHERE dataset NOT LIKE 'danni.%'; DELETE FROM core.dataset WHERE dataset NOT LIKE 'danni.%';
        DELETE FROM core.currency WHERE currency_code NOT IN ('EUR', 'BGN'); DELETE FROM ops.schema_migration WHERE tool <> 'danni';
        UPDATE core.dataset SET is_distributable = false, is_displayable = false""")
    pipeline.sync_registry(conn, 'danni', ROOT)
    from ingest import run
    run.licences(conn)


@pytest.fixture(scope='session')
def dsn():
    dsn = os.environ.get('DANNI_TEST_DSN')
    if not dsn:
        pytest.skip('DANNI_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    with pipeline.connect(dsn) as c:
        rebuild(c)
    return dsn


@pytest.fixture
def conn(dsn):
    with pipeline.connect(dsn) as c:
        reset(c)
        yield c

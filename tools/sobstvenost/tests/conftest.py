import os
import pytest
import psycopg

@pytest.fixture
def conn():
    dsn=os.environ.get('SOBSTVENOST_TEST_DSN')
    if not dsn:pytest.skip('requires isolated SOBSTVENOST_TEST_DSN')
    from psycopg.conninfo import conninfo_to_dict
    name=conninfo_to_dict(dsn).get('dbname','')
    assert name=='sobstvenost_test','refusing any non-isolated database'
    from ingest import db
    with psycopg.connect(dsn,autocommit=True) as c:
        db.migrate(c)
        c.execute('TRUNCATE live.record,stage.record,ops.version,ops.change_log,ops.held,ops.source_state,ops.job_run,ops.raw_file,ops.document_file RESTART IDENTITY')
        yield c


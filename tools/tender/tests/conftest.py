import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Every connection of the tests goes to TENDER_TEST_DSN. ingest.config reads TENDER_DSN once, when
    the first test file imports ingest, so setting the environment in a test file is too late when an
    earlier file imported it: the fixtures would then wipe the production database. The DSN is set on
    ingest.db, which every connection goes through (the app's queries too)."""
    dsn = os.environ.get("TENDER_TEST_DSN")
    if dsn:
        assert "test" in dsn, f"refusing to use {dsn!r}: the test database name must contain 'test'"
        from ingest import db
        db.DSN = dsn
    yield

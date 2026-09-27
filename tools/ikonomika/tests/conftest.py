import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Every connection of the tests goes to IKONOMIKA_TEST_DSN. ingest.config reads IKONOMIKA_DSN once, when
    the first test file imports ingest, so setting the environment in a test file is too late: the fixtures
    would wipe the production database. The DSN is set on the modules that connect."""
    dsn = os.environ.get("IKONOMIKA_TEST_DSN")
    if dsn:
        assert "test" in dsn, f"refusing to use {dsn!r}: the test database name must contain 'test'"
        from ingest import db
        db.DSN = dsn
        from app import main
        main.DSN = dsn
    yield

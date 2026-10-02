import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Every connection of the tests goes to ZAKONI_TEST_DSN. ingest.config reads ZAKONI_DSN once, when the first test
    file imports ingest, so the DSN is set on the modules that connect, and a DSN without 'test' is refused."""
    dsn = os.environ.get("ZAKONI_TEST_DSN")
    if dsn:
        assert "test" in dsn, f"refusing to use {dsn!r}: the test database name must contain 'test'"
        from ingest import db
        db.DSN = dsn
        try:
            from app import main
            main.DSN = dsn
        except ImportError:
            pass
    yield

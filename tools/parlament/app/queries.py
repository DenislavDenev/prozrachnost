"""How the pages reach the data: the serving layer only (pub_parlament.* and pub.*). Not the clean tables, not the
landing zone, not the ledger: the web role has no right to them, and the serving layer can be hosted without them.

One request is one snapshot. The first read of a request opens a read-only connection in REPEATABLE READ and reads
the number of the publication in it; every later read of the same request goes through that connection, so the
answer is of one version, the old one or the new one, never a mix."""
import contextlib
import contextvars

import psycopg

from ingest.config import DSN


class Snapshot:
    """The connection of one request, opened on its first read."""

    def __init__(self):
        self.conn = None
        self.publication = None

    def open(self):
        if self.conn is None:
            self.conn = psycopg.connect(DSN)
            self.conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            self.conn.read_only = True
            self.publication = self.conn.execute(
                'SELECT publication_id, published_at FROM pub.publication ORDER BY publication_id DESC LIMIT 1').fetchone()
        return self.conn

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


current = contextvars.ContextVar('parlament_snapshot', default=None)


@contextlib.contextmanager
def connect():
    """The snapshot of the request when there is one; outside a request (a script, a test) a snapshot of its own."""
    snap = current.get()
    if snap is not None:
        yield snap.open()
        return
    own = Snapshot()
    try:
        yield own.open()
    finally:
        own.close()

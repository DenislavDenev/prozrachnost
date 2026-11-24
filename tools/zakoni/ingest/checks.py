"""Freshness: what the n8n card reports (quiet when all is well)."""
import datetime as dt

from . import archive, pipeline
from .config import TOOL

BAD = ("невалиден отговор", "липсва при източника", "грешка при четене", "неуспешно")


def freshness(conn, now=None):
    """The shared list (a source not read for its time, a hold past a day, a failed rule, a failed step), the archive
    that has not read data.egov.bg, and every resource whose last read failed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    problems = [f"Закони: {p}" for p in pipeline.freshness(conn, TOOL)]
    try:
        archive.check_fresh(now)
    except archive.ArchiveError as e:
        problems.append(f"Закони: {e}")
    for ref, status, error in conn.execute("SELECT ref, status, error FROM zakoni.source_state WHERE status = ANY(%s) ORDER BY ref",
                                           (list(BAD),)):
        problems.append(f"Закони: ресурс {ref[:8]}: {status}: {(error or '')[:200]}")
    return problems

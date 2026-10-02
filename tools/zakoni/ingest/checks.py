"""Freshness: what the n8n card reports (quiet when all is well)."""
import datetime as dt

from . import archive
from .store import SOURCE

BUILD_STALE_DAYS = 3        # our own build did not succeed for this long
HELD_TOO_LONG = 2
SOURCE_OLD_DAYS = 14        # the source did not change a resource for this long: information on /sources, not our fault
BAD = ("невалиден отговор", "липсва при източника", "грешка при четене", "неуспешно")


def freshness(conn, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    problems = []
    try:
        archive.check_fresh(now)
    except archive.ArchiveError as e:
        problems.append(f"Закони: {e}")
    last = conn.execute("SELECT max(finished_at) FROM ops.job_run WHERE step = 'build' AND status = 'ok'").fetchone()[0]
    if not last or last < now - dt.timedelta(days=BUILD_STALE_DAYS):
        problems.append(f"Закони: последното успешно изграждане е от {last:%d.%m.%Y}" if last else "Закони: изграждането не е минало никога")
    for ref, status, error in conn.execute("SELECT ref, status, error FROM ops.source_state WHERE source = %s AND status = ANY(%s)", (SOURCE, list(BAD))):
        problems.append(f"Закони: ресурс {ref[:8]}: {status}: {(error or '')[:200]}")
    for ref, first in conn.execute("SELECT ref, first_at FROM ops.held WHERE source = %s", (SOURCE,)):
        if first < now - dt.timedelta(days=HELD_TOO_LONG):
            problems.append(f"Закони: ресурс {ref[:8]} е задържан от {first:%d.%m.%Y}")
    for name, exp, act in conn.execute("""SELECT name, expected, actual FROM ops.reconciliation
                                          WHERE kind = 'block' AND NOT ok AND checked_at > %s""", (now - dt.timedelta(days=1),)):
        problems.append(f"Закони: сверка {name}: {exp} срещу {act}")
    bad = conn.execute("SELECT checks FROM gold.build ORDER BY build_id DESC LIMIT 1").fetchone()
    if bad and bad[0] and bad[0].get("violations"):
        problems.append(f"Закони: последното злато не мина проверките ({len(bad[0]['violations'])} нарушения)")
    return problems

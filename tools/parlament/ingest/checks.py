"""Freshness: what the n8n card reports when the import is late, held or broken (quiet when all is well)."""
import datetime as dt

from .load import SOURCE

# The two CSV files of a sitting come out with it or days later: of the sittings since 12.2021, most within a week,
# some after two to four weeks (the date in the file's name, checked 28.09.2026). A sitting without them after
# LATE_FILES days is reported for a week; after that it stays on /sources only (some sittings get XLSX and PDF only).
LATE_FILES = 14
REPORT_FOR = 7
LIST_STALE = 2          # days since the last good read of this month's list of sittings
ROSTER_STALE = 8
HELD_TOO_LONG = 2


def freshness(conn, today=None):
    today = today or dt.date.today()
    problems = []
    month = f"month/{today.year}-{today.month:02d}"
    for ref, days, what in ((month, LIST_STALE, "списъкът на заседанията за месеца"), ("roster", ROSTER_STALE, "списъкът на депутатите")):
        r = conn.execute("SELECT last_ok FROM ops.source_state WHERE source = %s AND ref = %s", (SOURCE, ref)).fetchone()
        if not r or not r[0] or r[0].date() < today - dt.timedelta(days=days):
            problems.append(f"Парламент: {what} не е четен успешно от {r[0].date() if r and r[0] else 'никога'}")
    for sid, date, status, error in conn.execute("""
            SELECT s.id, s.date, st.status, st.error FROM live.sitting s
            LEFT JOIN ops.source_state st ON st.source = %s AND st.ref = 'sten/' || s.id
            WHERE s.date BETWEEN %s AND %s AND (s.iv_sha IS NULL OR st.status NOT IN ('ok'))
            ORDER BY s.date""", (SOURCE, today - dt.timedelta(days=LATE_FILES + REPORT_FOR), today - dt.timedelta(days=LATE_FILES))):
        if status == "held":
            continue   # reported below when it waits too long
        problems.append(f"Парламент: заседание {date:%d.%m.%Y} ({sid}): " +
                        (f"{LATE_FILES} дни без поименно гласуване в CSV" if status in (None, "no-files") else f"{status}: {error}"))
    for ref, first in conn.execute("SELECT ref, first_at FROM ops.held WHERE source = %s", (SOURCE,)):
        if first.date() < today - dt.timedelta(days=HELD_TOO_LONG):
            problems.append(f"Парламент: {ref} задържан от {first.date():%d.%m.%Y}")
    return problems

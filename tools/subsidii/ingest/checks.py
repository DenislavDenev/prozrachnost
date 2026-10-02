"""Freshness and the weekly summary. `freshness` returns the list of problems; empty when all is well."""
import datetime as dt

from . import archive, config

TEXT = "Субсидии: ДФЗ {}"


def last_completed_year(today):
    """The newest financial year that has ended: the one named after the year, from 16.10, else the year before."""
    return today.year if (today.month, today.day) > (10, 15) else today.year - 1


def freshness(c, today=None, now=None, st=None, root=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []

    def add(msg):
        out.append(TEXT.format(msg))

    try:
        st = st or archive.state(root)
        age = archive.age_hours(st, now)
        if age > config.MAX_ARCHIVE_AGE_H:
            add(f"архивът не е четен успешно {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)")
    except archive.ArchiveError as e:
        add(str(e))
    snaps = {r[0]: r for r in c.execute("SELECT fy, max(day) FILTER (WHERE status = 'built'), max(day) FROM silver.snapshot GROUP BY fy").fetchall()}
    if not snaps:
        add("няма нито една заредена година")
    # the files the archive has that are not loaded: the newest file of a year must be built (or held for less than a day)
    try:
        newest = {fy: s[-1] for fy, s in archive.snapshots(root).items()}
    except archive.ArchiveError as e:
        newest = {}
        add(str(e))
    for fy, snap in newest.items():
        have = snaps.get(fy)
        if not have or have[1] is None or have[1] < snap.day:
            row = c.execute("SELECT status FROM silver.snapshot WHERE fy = %s AND day = %s", (fy, snap.day)).fetchone()
            if row and row[0] == "held":
                continue         # the held list below tells when it is over a day
            add(f"ФГ {fy}: файлът от {snap.day} не е зареден" + (f" ({row[0]})" if row else ""))
    for ref, first, reason in c.execute("SELECT ref, first_seen, reason FROM ops.held"):
        if now - first > dt.timedelta(days=1):
            add(f"{ref} е задържан над ден: {reason}")
    for fy, day, note in c.execute("SELECT fy, day, note FROM silver.snapshot WHERE status = 'invalid' AND day >= %s", (today - dt.timedelta(days=10),)):
        add(f"ФГ {fy}, файл от {day}: невалиден: {note}")
    for fy, ccy in c.execute("SELECT fy, currency FROM silver.fiscal_year y WHERE EXISTS (SELECT 1 FROM silver.snapshot s WHERE s.fy = y.fy AND s.status = 'built')"):
        if ccy is None:
            add(f"ФГ {fy}: валутата на справката не е потвърдена (db/ref/dfz_units.csv); сумите в евро не се показват")
    missing = c.execute("""SELECT s.fy, s.day FROM silver.snapshot s LEFT JOIN gold.build b ON b.fy = s.fy AND b.snap_day = s.day
                           WHERE s.status = 'built' AND b.ok IS NOT TRUE ORDER BY s.fy, s.day""").fetchall()
    for fy, day in missing:
        add(f"ФГ {fy}, файл от {day}: няма добро златно изграждане")
    # the new financial year: ended on 15.10, expected in the archive by 31.12
    want = last_completed_year(today)
    if today > dt.date(want, 12, 31) and want not in snaps:
        add(f"финансова година {want} още не се вижда в справката (очаквана до 31.12.{want})")
    pop = c.execute("SELECT max(read_at), count(DISTINCT municipality_id) FROM silver.population").fetchone()
    if pop[0] is None or now - pop[0] > dt.timedelta(days=45):
        out.append("Субсидии: жителите от Население не са прочетени над 45 дни; „на жител“ не се показва")
    return out


def weekly(c):
    rows = c.execute("""SELECT fy, max(day) FILTER (WHERE status = 'built'), max(holders), max(payments), max(total_all), max(block_diffs)
                        FROM silver.snapshot GROUP BY fy ORDER BY fy""").fetchall()
    return {"years": [dict(fy=r[0], last=str(r[1]), recipients=r[2], payments=r[3], total=str(r[4]), block_diffs=r[5]) for r in rows]}

"""Freshness and the weekly summary. `freshness` returns the list of problems of this source on top of the shared ones
(pipeline.freshness: a stale source, a hold past its time, a failed rule, a step whose last run failed); empty when all
is well."""
import datetime as dt

from . import archive, config, pipeline

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
    snaps = {r[0]: r for r in c.execute("SELECT fy, max(day) FILTER (WHERE status = 'built'), max(day) FROM subsidii.snapshot GROUP BY fy").fetchall()}
    if not snaps:
        add("няма нито една заредена година")
    # the files the archive has that are not loaded: the newest file of a year must be built (a held one is told by the
    # shared check once it is held longer than its source allows)
    try:
        newest = {fy: s[-1] for fy, s in archive.snapshots(root).items()}
    except archive.ArchiveError as e:
        newest = {}
        add(str(e))
    for fy, snap in newest.items():
        have = snaps.get(fy)
        if not have or have[1] is None or have[1] < snap.day:
            row = c.execute("SELECT status FROM subsidii.snapshot WHERE fy = %s AND day = %s", (fy, snap.day)).fetchone()
            if row and row[0] == "held":
                continue
            add(f"ФГ {fy}: файлът от {snap.day} не е зареден" + (f" ({row[0]})" if row else ""))
    for fy, day, note in c.execute("SELECT fy, day, note FROM subsidii.snapshot WHERE status = 'invalid' AND day >= %s", (today - dt.timedelta(days=10),)):
        add(f"ФГ {fy}, файл от {day}: невалиден: {note}")
    for fy, ccy in c.execute("SELECT fy, currency FROM subsidii.source_year y WHERE EXISTS (SELECT 1 FROM subsidii.snapshot s WHERE s.fy = y.fy AND s.status = 'built')"):
        if ccy is None:
            add(f"ФГ {fy}: валутата на справката не е потвърдена (db/ref/dfz_units.csv); сумите в евро не се показват")
    missing = c.execute("""SELECT s.fy, s.day FROM subsidii.snapshot s LEFT JOIN subsidii.build b ON b.fy = s.fy AND b.snap_day = s.day
                           WHERE s.status = 'built' AND b.ok IS NOT TRUE ORDER BY s.fy, s.day""").fetchall()
    for fy, day in missing:
        add(f"ФГ {fy}, файл от {day}: няма добро изграждане на сборовете")
    # the new financial year: ended on 15.10, expected in the archive by 31.12
    want = last_completed_year(today)
    if today > dt.date(want, 12, 31) and want not in snaps:
        add(f"финансова година {want} още не се вижда в справката (очаквана до 31.12.{want})")
    # "per inhabitant" divides by the registrations Население publishes (pub_naselenie): monthly, so two months is late
    pop = c.execute("""SELECT max(as_of) FROM (SELECT as_of FROM pub_naselenie.population WHERE table_kind = 'monthly'
                       GROUP BY as_of HAVING count(DISTINCT municipality_code) >= 250) x""").fetchone()[0]
    if pop is None or today - pop > dt.timedelta(days=75):
        out.append("Субсидии: жителите от Население (pub_naselenie) са по-стари от 75 дни; „на жител“ е към " + (f"{pop:%d.%m.%Y}" if pop else "няма данни"))
    return out


def all_problems(c, **kw):
    """The shared problems and those of this source, each once."""
    out = pipeline.freshness(c, config.TOOL)
    return out + [p for p in freshness(c, **kw) if p not in out]


def summary(c):
    """The weekly card (the same shape for every tool): counts, last good reads, changes and holds, problems."""
    years = c.execute("""SELECT s.fy, s.holders, s.payments FROM subsidii.snapshot s
                         WHERE s.status = 'built' AND s.day = (SELECT max(day) FROM subsidii.snapshot x WHERE x.fy = s.fy AND x.status = 'built')
                         ORDER BY s.fy""").fetchall()
    counts = {}
    for fy, holders, payments in years:
        counts[f"ФГ {fy}: получателски редове"] = holders
        counts[f"ФГ {fy}: плащания"] = payments
    pub = c.execute("SELECT max(publication_id) FROM pub.publication WHERE tool = %s", (config.TOOL,)).fetchone()[0]
    counts["публикация"] = pub
    row = c.execute("""SELECT (SELECT max(loaded_at) FROM ops.load WHERE tool = %s AND status IN ('loaded', 'confirmed', 'unchanged')),
                              (SELECT max(finished_at) FROM ops.run WHERE tool = %s AND step = 'dfz' AND status = 'ok'),
                              (SELECT count(*) FROM ops.change_log WHERE tool = %s AND detected_at > now() - interval '7 days'),
                              (SELECT count(*) FROM ops.hold WHERE tool = %s)""", (config.TOOL,) * 4).fetchone()
    stamp = lambda t: t and f"{t:%Y-%m-%d %H:%M}"
    return {"tool": "Субсидии и помощи", "counts": counts, "last_ok": {"ДФЗ (архив)": stamp(row[0]), "изграждане": stamp(row[1])},
            "changes": row[2], "held": row[3], "problems": all_problems(c)}

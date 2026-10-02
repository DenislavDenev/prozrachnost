"""Freshness and the weekly summary. `freshness` returns the list of problems; empty when all is well."""
import datetime as dt

from . import archive, config

TEXT = "Пазар: {}. Последен валиден ден: {}."


def last_built(c):
    return c.execute("SELECT max(day) FROM silver.day WHERE status = 'built'").fetchone()[0]


def freshness(c, today=None, now=None, st=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    now = now or dt.datetime.now(dt.timezone.utc)
    last = last_built(c)
    out = []

    def add(msg):
        out.append(TEXT.format(msg, last))

    if last is None:
        return ["Пазар: няма построен ден"]
    # the file of day D is on the server about 22:00 UTC and the archive reads it at 23:40; the build is at 05:55 next day,
    # so by 07:00 UTC the day before yesterday must be built, and yesterday normally is
    expected = today - dt.timedelta(days=2 if now.hour < 7 else 1)
    if last < expected:
        add(f"няма построен ден след {last}")
    try:
        st = st or archive.state()
        age = archive.age_hours(st, now)
        if age > config.MAX_ARCHIVE_AGE_H:
            add(f"архивът не е четен успешно {age:.0f} ч")
        newest = max(st["seen"])
        if dt.date.fromisoformat(newest) < today - dt.timedelta(days=3):
            add(f"в архива няма нов ден след {newest}")
    except archive.ArchiveError as e:
        add(str(e))
    for ref, first, reason in c.execute("SELECT ref, first_seen, reason FROM ops.held"):
        if now - first > dt.timedelta(days=1):
            add(f"{ref} е задържан над ден: {reason}")
    for day, note in c.execute("SELECT day, note FROM silver.day WHERE status = 'invalid' AND day >= %s", (today - dt.timedelta(days=10),)):
        add(f"{day} е невалиден: {note}")
    for day, records, bad in c.execute("SELECT day, records, bad FROM silver.day WHERE status = 'built' AND day >= %s AND records > 0 AND bad * 100 > records", (today - dt.timedelta(days=3),)):
        add(f"{day}: над 1% невалидни редове ({bad} от {records})")
    missing = c.execute("""SELECT d.day FROM silver.day d LEFT JOIN gold.day g USING (day)
                           WHERE d.status = 'built' AND g.day IS NULL AND d.day >= %s""", (today - dt.timedelta(days=5),)).fetchall()
    if missing:
        add(f"няма златни таблици за {len(missing)} дни")
    read = c.execute("SELECT max(read_at), max(newest) FROM silver.fuel_read").fetchone()
    if read[0] is None or now - read[0] > dt.timedelta(days=9):
        add("бюлетинът за горивата не е четен успешно над 9 дни")
    elif read[1] < today - dt.timedelta(days=21):
        add(f"в бюлетина за горивата няма нова седмица след {read[1]}")
    return out


def weekly(c):
    since = dt.date.today() - dt.timedelta(days=7)
    row = c.execute("""SELECT count(*), coalesce(sum(valid), 0), coalesce(sum(bad), 0), coalesce(max(chains), 0), coalesce(max(stores), 0)
                       FROM silver.day WHERE status = 'built' AND day >= %s""", (since,)).fetchone()
    return {"days": row[0], "valid": row[1], "bad": row[2], "chains": row[3], "stores": row[4], "last": str(last_built(c))}

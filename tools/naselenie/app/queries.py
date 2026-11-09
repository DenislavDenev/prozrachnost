"""What the pages read: the clean tables naselenie.* and the places of core.*, nothing else.
Every function takes the period it is asked about; "the latest" is just the newest period."""
import threading
import time

import psycopg

from ingest.config import DSN

OLD_KIND = {'monthly': 'municipalities', 'detailed': 'places'}   # the names the JSON of the pages has always used
_cache, _lock = {}, threading.Lock()


def connect():
    return psycopg.connect(DSN)


def reference():
    """{ЕИК of the municipality: its names and the codes of the oblast, region and macro-region}, and the names of
    the regions. Read from core and kept for five minutes: it changes a few times in a decade."""
    with _lock:
        if time.monotonic() - _cache.get('at', -1e9) < 300:
            return _cache['ref'], _cache['regions']
        with connect() as c:
            ref = {r[0]: dict(id=r[0], code=r[1], name_bg=r[2], oblast=r[3], nuts3=r[4], nuts2=r[5], nuts1=r[6])
                   for r in c.execute('''SELECT m.eik, m.municipality_code, m.name_bg, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
                       FROM core.municipality m JOIN core.oblast o USING (oblast_code)
                       JOIN core.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN core.nuts n2 ON n2.nuts_code = n3.parent_code
                       WHERE m.gone_at IS NULL ORDER BY m.municipality_code''')}
            regions = dict(c.execute('SELECT nuts_code, label_bg FROM core.nuts WHERE level IN (1, 2)'))
        _cache.update(at=time.monotonic(), ref=ref, regions=regions)
        return ref, regions


def snapshots(c):
    """{date: (kind, address)} of the published tables. The monthly table wins when both exist for a date."""
    return {str(d): (k, u) for d, k, u in c.execute(
        "SELECT DISTINCT ON (as_of) as_of, table_kind, url FROM naselenie.snapshot ORDER BY as_of, (table_kind = 'monthly') DESC")}


def latest(c, kind=None):
    """(date, address) of the newest published table, of one kind or of any."""
    row = c.execute('''SELECT as_of, url FROM naselenie.snapshot WHERE %s::text IS NULL OR table_kind = %s
        ORDER BY as_of DESC, (table_kind = 'monthly') LIMIT 1''', (kind, kind)).fetchone()
    return (str(row[0]), row[1]) if row else None


def municipal(c, day, kind):
    """(ЕИК, permanent, current) of every municipality in the table of that date and kind."""
    return c.execute('''SELECT m.eik, p.permanent, p.current FROM naselenie.population p
        JOIN core.municipality m USING (municipality_code)
        WHERE p.as_of = %s AND p.table_kind = %s AND p.superseded_at IS NULL''', (day, kind)).fetchall()


def series(c, eik=None):
    """One row for each published date: the totals of the country or of one municipality. None where the table has no row."""
    return c.execute('''WITH s AS (SELECT DISTINCT ON (as_of) as_of, table_kind, url FROM naselenie.snapshot
                                   ORDER BY as_of, (table_kind = 'monthly') DESC)
        SELECT s.as_of, s.table_kind, s.url, sum(p.permanent), sum(p.current)
        FROM s LEFT JOIN (naselenie.population p JOIN core.municipality m USING (municipality_code))
          ON p.as_of = s.as_of AND p.table_kind = s.table_kind AND p.superseded_at IS NULL AND (%s::text IS NULL OR m.eik = %s)
        GROUP BY 1, 2, 3 ORDER BY 1''', (eik, eik)).fetchall()


def places(c, day, eik=None, unmatched=False):
    """The rows of the detailed table of that date, in the order of the source: for one municipality, or only
    the rows without an unambiguous ЕКАТТЕ. `municipality` is the name as ГРАО writes it."""
    return [dict(name=n, ekatte=e, permanent=p, current=cu, municipality=mu) for n, e, p, cu, mu in c.execute(
        '''SELECT sp.source_name, sp.ekatte, sp.permanent, sp.current, g.municipality
           FROM naselenie.settlement_population sp
           JOIN core.municipality m USING (municipality_code)
           JOIN naselenie.snapshot s ON s.as_of = sp.as_of AND s.table_kind = 'detailed'
           JOIN naselenie_src.grao_row g ON g.load_id = s.load_id AND g.row_kind = 'settlement' AND g.row_no = sp.row_no
           WHERE sp.as_of = %s AND sp.superseded_at IS NULL AND (%s::text IS NULL OR m.eik = %s)
             AND (NOT %s OR sp.ekatte IS NULL)
           ORDER BY sp.row_no''', (day, eik, eik, unmatched))]


def detailed_current(c, day, eik):
    """The registrations by current address of one municipality in the detailed table of that date."""
    row = c.execute('''SELECT p.current FROM naselenie.population p JOIN core.municipality m USING (municipality_code)
        WHERE p.as_of = %s AND p.table_kind = 'detailed' AND p.superseded_at IS NULL AND m.eik = %s''', (day, eik)).fetchone()
    return row[0] if row else None


def source_state(c):
    """The last read of every address: the tables of ГРАО, and EKATTE as the shared dimensions read it."""
    return c.execute('''SELECT url, last_read, last_ok, status, error, rows FROM naselenie.source_state
        UNION ALL
        SELECT s.url, f.last_read_at, f.last_ok_at,
               CASE WHEN f.last_status IN ('loaded', 'confirmed', 'unchanged') THEN 'ok' WHEN f.last_status = 'held' THEN 'held'
                    WHEN f.last_status IS NULL THEN 'none' ELSE 'invalid' END,
               NULL, (SELECT count(*)::int FROM core.settlement WHERE gone_at IS NULL)
        FROM ops.freshness f JOIN ops.source s USING (tool, source) WHERE f.tool = 'danni' AND f.source = 'ekatte'
        ORDER BY 1''').fetchall()

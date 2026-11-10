"""What the pages read: the serving layer only (pub_naselenie.* and pub.*). Not the clean tables, not the landing
zone, not the ledger: the web role has no right to them, and the serving layer can be hosted without them.

One request is one snapshot: every query of a request sees the same publication, the old one or the new one,
never a mix. Every function takes the period it is asked about; "the latest" is just the newest period."""
import contextlib

import psycopg

from ingest.config import DSN

OLD_KIND = {'monthly': 'municipalities', 'detailed': 'places'}   # the names the JSON of the pages has always used


@contextlib.contextmanager
def snapshot():
    """A read-only connection whose every statement sees the database as of its first one."""
    with psycopg.connect(DSN) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.read_only = True
        yield c


def version(c):
    """(number, time) of the publication this snapshot shows; None before the first one. The number is what a
    cache in front of the site is keyed on: it moves only when the published content does."""
    return c.execute('SELECT publication_id, published_at FROM pub.publication ORDER BY publication_id DESC LIMIT 1').fetchone()


def reference(c):
    """{ЕИК of the municipality: its names and the codes of the oblast, region and macro-region}, and the names of
    the regions."""
    ref = {r[0]: dict(id=r[0], code=r[1], name_bg=r[2], oblast=r[3], nuts3=r[4], nuts2=r[5], nuts1=r[6])
           for r in c.execute('''SELECT m.eik, m.municipality_code, m.name_bg, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
               FROM pub.municipality m JOIN pub.oblast o USING (oblast_code)
               JOIN pub.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN pub.nuts n2 ON n2.nuts_code = n3.parent_code
               WHERE NOT m.is_gone ORDER BY m.municipality_code''')}
    regions = dict(c.execute('SELECT nuts_code, label_bg FROM pub.nuts WHERE level IN (1, 2)'))
    return ref, regions


def snapshots(c):
    """{date: (kind, address)} of the published tables. The monthly table wins when both exist for a date."""
    return {str(d): (k, u) for d, k, u in c.execute(
        "SELECT DISTINCT ON (as_of) as_of, table_kind, url FROM pub_naselenie.snapshot ORDER BY as_of, (table_kind = 'monthly') DESC")}


def latest(c, kind=None):
    """(date, address) of the newest published table, of one kind or of any."""
    row = c.execute('''SELECT as_of, url FROM pub_naselenie.snapshot WHERE %s::text IS NULL OR table_kind = %s
        ORDER BY as_of DESC, (table_kind = 'monthly') LIMIT 1''', (kind, kind)).fetchone()
    return (str(row[0]), row[1]) if row else None


def municipal(c, day, kind):
    """(ЕИК, permanent, current) of every municipality in the table of that date and kind."""
    return c.execute('''SELECT m.eik, p.permanent, p.current FROM pub_naselenie.population p
        JOIN pub.municipality m USING (municipality_code)
        WHERE p.as_of = %s AND p.table_kind = %s''', (day, kind)).fetchall()


def series(c, eik=None):
    """One row for each published date: the totals of the country or of one municipality. None where the table has no row."""
    return c.execute('''WITH s AS (SELECT DISTINCT ON (as_of) as_of, table_kind, url FROM pub_naselenie.snapshot
                                   ORDER BY as_of, (table_kind = 'monthly') DESC)
        SELECT s.as_of, s.table_kind, s.url, sum(p.permanent), sum(p.current)
        FROM s LEFT JOIN (pub_naselenie.population p JOIN pub.municipality m USING (municipality_code))
          ON p.as_of = s.as_of AND p.table_kind = s.table_kind AND (%s::text IS NULL OR m.eik = %s)
        GROUP BY 1, 2, 3 ORDER BY 1''', (eik, eik)).fetchall()


def places(c, day, eik=None, unmatched=False):
    """The rows of the detailed table of that date, in the order of the source: for one municipality, or only
    the rows without an unambiguous ЕКАТТЕ. `municipality` is the name as ГРАО writes it."""
    return [dict(name=n, ekatte=e, permanent=p, current=cu, municipality=mu) for n, e, p, cu, mu in c.execute(
        '''SELECT sp.source_name, sp.ekatte, sp.permanent, sp.current, sp.source_municipality
           FROM pub_naselenie.settlement_population sp JOIN pub.municipality m USING (municipality_code)
           WHERE sp.as_of = %s AND (%s::text IS NULL OR m.eik = %s) AND (NOT %s OR sp.ekatte IS NULL)
           ORDER BY sp.row_no''', (day, eik, eik, unmatched))]


def detailed_current(c, day, eik):
    """The registrations by current address of one municipality in the detailed table of that date."""
    row = c.execute('''SELECT p.current FROM pub_naselenie.population p JOIN pub.municipality m USING (municipality_code)
        WHERE p.as_of = %s AND p.table_kind = 'detailed' AND m.eik = %s''', (day, eik)).fetchone()
    return row[0] if row else None


def source_state(c):
    """The last read of every address: the tables of ГРАО, and EKATTE as the shared dimensions read it."""
    return c.execute('SELECT url, last_read, last_ok, status, error, rows FROM pub_naselenie.source_state ORDER BY url').fetchall()

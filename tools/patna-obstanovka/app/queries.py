"""What the map reads: the serving layer only (pub_patna_obstanovka.* and pub.*). Not the clean tables, not the
landing zone, not the ledger: the web role has no right to them, and the serving layer can be hosted without them.

One request is one snapshot: every query of a request sees the same publication, the old one or the new one, never
a mix. The orders reproduce the answers of the SQLite version byte for byte (step `parity`)."""
import contextlib

import psycopg
from psycopg.rows import dict_row

from ingest.config import DSN

P = 'pub_patna_obstanovka'


@contextlib.contextmanager
def snapshot():
    """A read-only connection whose every statement sees the database as of its first one."""
    with psycopg.connect(DSN, row_factory=dict_row) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.read_only = True
        yield c


def version(c):
    """(number, time) of the publication this snapshot shows; None before the first one."""
    row = c.execute('SELECT publication_id, published_at FROM pub.publication ORDER BY publication_id DESC LIMIT 1').fetchone()
    return (row['publication_id'], row['published_at']) if row else None


def healthy_sources(c):
    return c.execute(f'SELECT count(*) AS n FROM {P}.source_state WHERE error IS NULL').fetchone()['n']


def status(c):
    return c.execute(f'SELECT key, label, url, updated, count, error FROM {P}.source_state ORDER BY key COLLATE "C"').fetchall()


def traffic(c):
    return c.execute(f'SELECT scp, name, lat, lon, count15, count60, speed15, observed, bearing FROM {P}.traffic ORDER BY row_no').fetchall()


def weather(c):
    return c.execute(f'SELECT scp, name, lat, lon, air, surface, humidity, wind, observed FROM {P}.weather ORDER BY row_no').fetchall()


def events(c):
    return c.execute(f'''SELECT id, kind, title, detail, lat, lon, valid_from, valid_until, source_url FROM {P}.event
        ORDER BY position''').fetchall()


def risk_version(c):
    row = c.execute(f"SELECT updated FROM {P}.source_state WHERE key = 'risk'").fetchone()
    return row['updated'] if row else None


def risk_sections(c):
    return c.execute(f'''SELECT id, layer, road, risk_level, condition, reference, district, length_km, geometry
        FROM {P}.risk_section ORDER BY position''')


def road_conditions(c):
    """The length of the national roads (layer 5) by the condition of the surface, the sums in the order of the
    sections, as SQLite summed them."""
    return c.execute(f'''SELECT condition, length_km FROM {P}.risk_section WHERE layer = 5 AND length_km > 0
        ORDER BY position''').fetchall()


def crashes_in(c, south, north, west, east, since):
    """The crashes of an area in the order of the index (lat, lon, date) the SQLite version scanned."""
    return c.execute(f'''SELECT lat, lon, killed, injured FROM {P}.crash
        WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s AND date >= %s
        ORDER BY lat, lon, date COLLATE "C", row_no''', (south, north, west, east, since))


def crash_points(c, south, north, west, east, since, limit=None):
    return c.execute(f'''SELECT id, date, lat, lon, description, killed, injured FROM {P}.crash
        WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s AND date >= %s
        ORDER BY date COLLATE "C" DESC, lat, lon, row_no''' + (' LIMIT %s' if limit else ''),
                     (south, north, west, east, since, *([limit] if limit else []))).fetchall()


def crash_totals(c, since):
    total = c.execute(f'SELECT count(*) AS n FROM {P}.crash WHERE date >= %s', (since,)).fetchone()['n']
    matched = c.execute(f'''SELECT count(*) AS n FROM {P}.crash_match m JOIN {P}.crash k ON k.id = m.crash_id
        WHERE k.date >= %s''', (since,)).fetchone()['n']
    return total, matched


def dangerous_sections(c, since, condition):
    return c.execute(f'''SELECT r.id, r.road, r.risk_level, r.condition, r.reference, r.length_km, r.geometry,
            count(*)::int AS crashes, sum(CASE WHEN k.killed > 0 THEN 1 ELSE 0 END)::int AS fatal,
            sum(CASE WHEN k.killed = 0 AND k.injured > 0 THEN 1 ELSE 0 END)::int AS injury,
            sum(k.killed)::int AS killed, sum(k.injured)::int AS injured
        FROM {P}.crash_match m JOIN {P}.crash k ON k.id = m.crash_id JOIN {P}.risk_section r ON r.id = m.risk_id
        WHERE k.date >= %s AND (%s = '' OR (r.layer = 5 AND r.condition = %s))
        GROUP BY r.id ORDER BY crashes DESC, fatal DESC, r.id COLLATE "C" LIMIT 25''', (since, condition, condition)).fetchall()

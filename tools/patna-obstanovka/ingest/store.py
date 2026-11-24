"""One answer of a source into the landing zone (patna_obstanovka_src) and, in the same transaction, into the clean
tables (patna_obstanovka). The ledger, the hold and the change log are pipeline.load().

What each source gives load() as its rows (the keys the hold and the change log compare):

    traffic, weather   {scp: name and place} and one row '_reading' with the sha256 of the measurements. The counts
                       change every 15 minutes: they are measurements, not corrections, so the log records a new
                       reading as one line, and a station that comes or goes as its own line.
    lima_*             {record id: the message} and one row '_file' with the address of the day's file, so a day
                       without messages is a valid answer.
    risk               {layer:FID: the section with its geometry}.
    crashes            {time|lat|lon|n: the crash}. The archive has no number of its own for a record.
    matches            {crash key: the section and the distance}: derived, read from our own tables.

Every source is read whole, so the landing zone keeps only the current answer (policy `latest`): each writer deletes
the earlier answers of its source in the transaction of the load; the clean tables keep the history."""
import hashlib
import json
from collections import Counter

LIMA_ORDER = ('lima_r01', 'lima_r02', 'lima_d01')


def _sha(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


TABLES = {'traffic': ('patna_obstanovka_src.traffic_reading', 'patna_obstanovka_src.toll_answer'),
          'weather': ('patna_obstanovka_src.weather_reading', 'patna_obstanovka_src.toll_answer'),
          'lima': ('patna_obstanovka_src.lima_event', 'patna_obstanovka_src.lima_file'),
          'risk': ('patna_obstanovka_src.risk_section', 'patna_obstanovka_src.risk_layer'),
          'crashes': ('patna_obstanovka_src.crash', 'patna_obstanovka_src.crash_file'),
          'matches': ('patna_obstanovka.crash_match',)}


def prune(conn, source, keep):
    """Policy `latest`, in the transaction of the load: every earlier answer of the source leaves the landing zone
    (its raw file stays). The clean tables keep the history."""
    for table in TABLES['lima' if source.startswith('lima_') else source]:
        conn.execute(f'''DELETE FROM {table} WHERE load_id IN (SELECT load_id FROM ops.load
            WHERE tool = 'patna-obstanovka' AND source = %s AND load_id <> %s)''', (source, keep))


# ---------------------------------------------------------------- БГТОЛ

def toll_rows(stations):
    rows = {s['scp']: {'name': s['name'], 'lat': s['lat'], 'lon': s['lon']} for s in stations}
    rows['_reading'] = {'sha256': _sha(stations)}
    return rows


def toll_current(kind):
    def current(conn, load_id):
        rows = {scp: {'name': n, 'lat': la, 'lon': lo} for scp, n, la, lo in conn.execute(
            f'SELECT scp, name, lat, lon FROM patna_obstanovka_src.{kind}_reading WHERE load_id = %s', (load_id,))}
        sha = conn.execute('SELECT sha256 FROM patna_obstanovka_src.toll_answer WHERE load_id = %s', (load_id,)).fetchone()
        rows['_reading'] = {'sha256': sha[0] if sha else None}
        return rows
    return current


def toll_writer(kind, stations, skipped):
    columns = ('count15', 'count60', 'speed15') if kind == 'traffic' else ('air', 'surface', 'humidity', 'wind')

    def write(conn, load_id, _rows):
        prune(conn, kind, load_id)
        conn.execute('INSERT INTO patna_obstanovka_src.toll_answer (load_id, kind, sha256, skipped) VALUES (%s, %s, %s, %s)',
                     (load_id, kind, _sha(stations), skipped))
        with conn.cursor().copy(f'''COPY patna_obstanovka_src.{kind}_reading (load_id, row_no, scp, name, lat, lon,
                {', '.join(columns)}, observed) FROM STDIN''') as copy:
            for i, s in enumerate(stations, 1):
                copy.write_row((load_id, i, s['scp'], s['name'], s['lat'], s['lon'], *(s[c] for c in columns), s['observed']))
    return write


# ---------------------------------------------------------------- ЛИМА

def _event_row(e):
    return {k: e[k] for k in ('title', 'detail', 'lat', 'lon', 'valid_from', 'valid_until')}


def lima_rows(events, xml_url):
    rows = {e['id']: _event_row(e) for e in events}
    rows['_file'] = {'url': xml_url}
    return rows


def lima_current(conn, load_id):
    rows = {r: dict(title=t, detail=d, lat=la, lon=lo, valid_from=vf, valid_until=vu)
            for r, t, d, la, lo, vf, vu in conn.execute('''SELECT record_id, title, detail, lat, lon, valid_from, valid_until
                FROM patna_obstanovka_src.lima_event WHERE load_id = %s''', (load_id,))}
    url = conn.execute('SELECT xml_url FROM patna_obstanovka_src.lima_file WHERE load_id = %s', (load_id,)).fetchone()
    rows['_file'] = {'url': url[0] if url else None}
    return rows


def lima_hold(old, rows):
    """A list of restrictions changes every day: a few may end between two reads. Held only when at least five and
    more than half of the current messages are missing at once."""
    missing = len(old.keys() - rows.keys())
    return missing if missing >= 5 and missing > len(old) / 2 else 0


def lima_writer(source, kind, events, xml_url):
    def write(conn, load_id, _rows):
        prune(conn, source, load_id)
        conn.execute('INSERT INTO patna_obstanovka_src.lima_file (load_id, kind, xml_url) VALUES (%s, %s, %s)',
                     (load_id, kind, xml_url))
        with conn.cursor().copy('''COPY patna_obstanovka_src.lima_event (load_id, row_no, record_id, title, detail, lat, lon,
                valid_from, valid_until) FROM STDIN''') as copy:
            for i, e in enumerate(events, 1):
                copy.write_row((load_id, i, e['id'], e['title'], e['detail'], e['lat'], e['lon'], e['valid_from'], e['valid_until']))
    return write


# ---------------------------------------------------------------- ДАБДП

RISK_FIELDS = ('road', 'risk_level', 'condition', 'reference', 'district', 'geometry', 'length_km')


def risk_rows(sections):
    return {s['id']: {k: s[k] for k in RISK_FIELDS} for s in sections}


def risk_current(conn, load_id):
    return {f'{layer}:{fid}': dict(zip(RISK_FIELDS, rest)) for layer, fid, *rest in conn.execute(
        f'''SELECT layer, fid, {', '.join(RISK_FIELDS)} FROM patna_obstanovka_src.risk_section WHERE load_id = %s''', (load_id,))}


def risk_writer(sections, layers, at=None):
    """layers: {layer: (features read, features the source counts)}."""
    def write(conn, load_id, _rows):
        prune(conn, 'risk', load_id)
        for layer, (features, counted) in sorted(layers.items()):
            conn.execute('INSERT INTO patna_obstanovka_src.risk_layer (load_id, layer, features, counted) VALUES (%s, %s, %s, %s)',
                         (load_id, layer, features, counted))
        with conn.cursor().copy('''COPY patna_obstanovka_src.risk_section (load_id, row_no, layer, fid, road, risk_level,
                condition, reference, district, minlat, maxlat, minlon, maxlon, geometry, length_km) FROM STDIN''') as copy:
            for i, s in enumerate(sections, 1):
                copy.write_row((load_id, i, s['layer'], s['fid'], s['road'], s['risk_level'], s['condition'], s['reference'],
                                s['district'], s['minlat'], s['maxlat'], s['minlon'], s['maxlon'], s['geometry'], s['length_km']))
        build_risk(conn, load_id, at)
    return write


def build_risk(conn, load_id, at=None):
    """The clean sections from the landing rows of one load. An unchanged section stays as it is; a changed or
    vanished one gets superseded_at and the new value is added. The order follows the newest read."""
    params = dict(at=at, load=load_id)
    conn.execute('''CREATE TEMP TABLE new_section ON COMMIT DROP AS
        SELECT s.layer || ':' || s.fid AS section_id, s.layer, s.fid, s.row_no AS position, s.road, s.risk_level, s.condition,
               s.reference, s.district, o.oblast_code, s.minlat, s.maxlat, s.minlon, s.maxlon, s.geometry, s.length_km
        FROM patna_obstanovka_src.risk_section s LEFT JOIN core.oblast o ON o.oblast_code = s.district
        WHERE s.load_id = %(load)s''', params)
    same = '''(n.road, n.risk_level, n.condition, n.reference, n.district, n.minlat, n.maxlat, n.minlon, n.maxlon, n.geometry)
              = (c.road, c.risk_level, c.condition, c.reference, c.district, c.minlat, c.maxlat, c.minlon, c.maxlon, c.geometry)
              AND n.length_km IS NOT DISTINCT FROM c.length_km'''
    conn.execute(f'''UPDATE patna_obstanovka.risk_section c SET superseded_at = COALESCE(%(at)s, now())
        WHERE c.superseded_at IS NULL AND NOT EXISTS (SELECT FROM new_section n WHERE n.section_id = c.section_id AND {same})''', params)
    conn.execute('''UPDATE patna_obstanovka.risk_section c SET position = n.position, oblast_code = n.oblast_code
        FROM new_section n WHERE c.superseded_at IS NULL AND n.section_id = c.section_id
          AND (c.position <> n.position OR c.oblast_code IS DISTINCT FROM n.oblast_code)''', params)
    conn.execute('''INSERT INTO patna_obstanovka.risk_section (section_id, layer, fid, position, road, risk_level, condition,
            reference, district, oblast_code, minlat, maxlat, minlon, maxlon, geometry, length_km, load_id, first_seen_at)
        SELECT n.section_id, n.layer, n.fid, n.position, n.road, n.risk_level, n.condition, n.reference, n.district, n.oblast_code,
               n.minlat, n.maxlat, n.minlon, n.maxlon, n.geometry, n.length_km, %(load)s, COALESCE(%(at)s, now())
        FROM new_section n WHERE NOT EXISTS (
          SELECT FROM patna_obstanovka.risk_section c WHERE c.superseded_at IS NULL AND c.section_id = n.section_id)
        ORDER BY n.position''', params)
    conn.execute('DROP TABLE new_section')


# ---------------------------------------------------------------- МВР

CRASH_FIELDS = ('type', 'killed', 'injured')


def crash_keys(crashes):
    """The key of every crash in file order: its time and place, and a number among the crashes with the same three."""
    seen, keys = Counter(), []
    for c in crashes:
        base = f"{c['at']}|{c['lat']!r}|{c['lon']!r}"
        seen[base] += 1
        keys.append(f'{base}|{seen[base]}')
    return keys


def crash_rows(crashes):
    """The facts the pages show. The names of the oblast and the municipality ride along in the landing zone; they
    only link the crash to core and are not a version of it."""
    return {k: {'at': c['at'], 'lat': c['lat'], 'lon': c['lon'], **{f: c[f] for f in CRASH_FIELDS}}
            for k, c in zip(crash_keys(crashes), crashes)}


def crash_current(conn, load_id):
    crashes = [dict(at=a, lat=la, lon=lo, type=t, killed=k, injured=i) for a, la, lo, t, k, i in conn.execute(
        '''SELECT crash_at, lat, lon, crash_type, killed, injured FROM patna_obstanovka_src.crash
           WHERE load_id = %s ORDER BY row_no''', (load_id,))]
    return crash_rows(crashes)


def crash_writer(crashes, skipped, csv_rows, places=None, at=None):
    """places: {(region, municipality): municipality_code}, only the unambiguous ones (ingest/places.py)."""
    places = places or {}

    def write(conn, load_id, _rows):
        prune(conn, 'crashes', load_id)
        conn.execute('INSERT INTO patna_obstanovka_src.crash_file (load_id, csv_rows, skipped) VALUES (%s, %s, %s)',
                     (load_id, csv_rows, skipped))
        with conn.cursor().copy('''COPY patna_obstanovka_src.crash (load_id, row_no, crash_at, lat, lon, crash_type, killed,
                injured, region, municipality) FROM STDIN''') as copy:
            for c in crashes:
                copy.write_row((load_id, c['row_no'], c['at'], c['lat'], c['lon'], c['type'], c['killed'], c['injured'],
                                c.get('region'), c.get('municipality')))
        build_crashes(conn, load_id, crashes, places, at)
    return write


def build_crashes(conn, load_id, crashes, places, at=None):
    """The clean crashes of one load. A crash whose facts changed, or that left the archive, gets superseded_at and
    the new one is added. The line number and the municipality follow the newest file without a new version: the
    first is only the order of the file, the second is our matching."""
    params = dict(at=at, load=load_id)
    conn.execute('''CREATE TEMP TABLE new_crash (crash_key text, row_no int, crash_at text, lat float8, lon float8, crash_type text,
        killed int, injured int, municipality_code text) ON COMMIT DROP''')
    with conn.cursor().copy('COPY new_crash FROM STDIN') as copy:
        for key, c in zip(crash_keys(crashes), crashes):
            copy.write_row((key, c['row_no'], c['at'], c['lat'], c['lon'], c['type'], c['killed'], c['injured'],
                            places.get((c.get('region'), c.get('municipality')))))
    conn.execute('''UPDATE patna_obstanovka.crash c SET superseded_at = COALESCE(%(at)s, now())
        WHERE c.superseded_at IS NULL AND NOT EXISTS (SELECT FROM new_crash n WHERE n.crash_key = c.crash_key
          AND (n.crash_at, n.lat, n.lon, n.crash_type, n.killed, n.injured) = (c.crash_at, c.lat, c.lon, c.crash_type, c.killed, c.injured))''',
                 params)
    conn.execute('''UPDATE patna_obstanovka.crash c SET row_no = n.row_no,
          municipality_code = COALESCE(n.municipality_code, c.municipality_code)
        FROM new_crash n WHERE c.superseded_at IS NULL AND n.crash_key = c.crash_key
          AND (c.row_no <> n.row_no OR (n.municipality_code IS NOT NULL AND c.municipality_code IS DISTINCT FROM n.municipality_code))''',
                 params)
    conn.execute('''INSERT INTO patna_obstanovka.crash (crash_key, row_no, crash_at, lat, lon, crash_type, killed, injured,
            municipality_code, load_id, first_seen_at)
        SELECT n.crash_key, n.row_no, n.crash_at, n.lat, n.lon, n.crash_type, n.killed, n.injured, n.municipality_code,
               %(load)s, COALESCE(%(at)s, now())
        FROM new_crash n WHERE NOT EXISTS (
          SELECT FROM patna_obstanovka.crash c WHERE c.superseded_at IS NULL AND c.crash_key = n.crash_key)
        ORDER BY n.row_no''', params)
    conn.execute('DROP TABLE new_crash')


# ---------------------------------------------------------------- the crashes linked to sections (derived)

def match_rows(matches):
    return {key: {'section': section, 'distance': distance} for key, section, distance in matches}


def match_current(conn, load_id):
    return {k: {'section': s, 'distance': d} for k, s, d in conn.execute(
        'SELECT crash_key, section_id, distance_m FROM patna_obstanovka.crash_match WHERE load_id = %s', (load_id,))}


def match_writer(matches):
    def write(conn, load_id, _rows):
        prune(conn, 'matches', load_id)
        with conn.cursor().copy('COPY patna_obstanovka.crash_match (load_id, crash_key, section_id, distance_m) FROM STDIN') as copy:
            for key, section, distance in matches:
                copy.write_row((load_id, key, section, distance))
    return write


def accept_all(_old, _rows):
    """hold_when for what we derive ourselves from data that is already accepted: nothing to hold."""
    return 0

"""The steps of Пътна обстановка: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    current    БГТОЛ traffic and weather and the three lists of ЛИМА (every 15 minutes)
    mvr        the crash archive of МВР, then the crashes linked to the sections
    risk       the assessed sections of ДАБДП, the arrows of БГТОЛ, the crashes linked to the sections
    copy       once: the last state of road.db, the SQLite file of the tool until 24.11.2026
    parity     road.db against the shared database, table by table and row by row
    check      every quality rule, then the serving layer
    freshness  the list of problems, empty when all is well; --group current|mvr|risk: only those sources

A read that fails (the source does not answer, refuses, or answers with something that is not its format) is
recorded as rejected and the last good answer stays shown; it becomes a problem when the source is stale
(ops.source.stale_after). An answer in a different shape (a field gone, an unknown root) is a problem at once:
the parser has to learn it first.

Ingest and serving are apart. The steps fill patna_obstanovka_src and patna_obstanovka; the map reads
pub_patna_obstanovka, which changes only by a publication at the end of a step whose rules all hold.
"""
import argparse
import datetime as dt
import sqlite3
from collections import Counter

from . import derive, http, pipeline, places, sources, store
from .config import DSN, OLD_DB, RAW, ROOT, TOOL
from .pipeline import ShapeError

GROUPS = {   # the sources whose freshness a scheduled step answers for
    'current': ('traffic', 'weather', 'lima_r01', 'lima_r02', 'lima_d01'),
    'mvr': ('crashes', 'matches'),
    'risk': ('risk', 'matches'),
}
TOLL = (('traffic', sources.TRAFFIC), ('weather', sources.WEATHER))
OBSERVATION_MAX_AGE = dt.timedelta(minutes=60)


def attempt(conn, r, source, ref, read, problems, rejected):
    """Run one read; a failure is recorded as rejected and the other sources go on."""
    try:
        return read()
    except Exception as e:   # noqa: BLE001 - every failure of a read is recorded, none stops the others
        raw_file_id = getattr(e, 'raw_file_id', None)
        pipeline.reject(conn, r, source, ref, e, raw_file_id)
        line = f'{source}: {e}'
        if isinstance(e, ShapeError) and not isinstance(e, sources.Garbled):
            problems.append(line)
        else:
            rejected.append(line)
        return None


def _keep(conn, r, source, ref, raw, name, media_type):
    return pipeline.save_raw(conn, r, source, ref, raw, RAW, name=name, media_type=media_type)


def _tag(e, file):
    e.raw_file_id = file.raw_file_id if file else None
    return e


# ---------------------------------------------------------------- current: БГТОЛ and ЛИМА

def read_toll(conn, r, kind, url, get=http.get):
    """The live measurements are not kept as raw files (1.6 MB every 15 minutes): the parsed rows are the record.
    An answer that is refused is kept, to show what came."""
    raw = get(url)
    try:
        stations, skipped = sources.parse_toll(raw, kind)
    except ShapeError as e:
        raise _tag(e, _keep(conn, r, kind, url, raw, f'{kind}.json', 'application/json'))
    done = pipeline.load(conn, r, kind, url, store.toll_rows(stations), store.toll_writer(kind, stations, skipped),
                         current=store.toll_current(kind), hold_when=pipeline.missing_over(0.2))
    r.stats[f'{kind}_skipped'] = skipped
    return done


def read_lima(conn, r, source, group, code, kind, get=http.get):
    ref = sources.CATALOG.format(group=group, code=code)
    xml_url = sources.catalog_xml(get(ref))
    raw = get(xml_url)
    file = _keep(conn, r, source, xml_url, raw, xml_url.rsplit('/', 1)[-1], 'application/xml')
    try:
        events = sources.parse_datex(raw, kind)
    except ShapeError as e:
        raise _tag(e, file)
    done = pipeline.load(conn, r, source, ref, store.lima_rows(events, xml_url), store.lima_writer(source, kind, events, xml_url),
                         raw_file_id=file.raw_file_id, current=store.lima_current, hold_when=store.lima_hold)
    return done


def current(conn, r, get=http.get):
    problems, rejected = [], []
    for kind, url in TOLL:
        attempt(conn, r, kind, url, lambda: read_toll(conn, r, kind, url, get), problems, rejected)
    # the arrows of points that are new or moved, from the assessed sections
    r.stats['bearings'] = derive.bearings_from_sections(conn)
    for source, group, code, kind in sources.LIMA:
        attempt(conn, r, source, sources.CATALOG.format(group=group, code=code),
                lambda: read_lima(conn, r, source, group, code, kind, get), problems, rejected)
    return problems, rejected


# ---------------------------------------------------------------- mvr, risk and what follows from them

def rebuild_matches(conn, r):
    matches = derive.crash_matches(conn)
    if not matches:
        return None
    done = pipeline.load(conn, r, 'matches', sources.MVR, store.match_rows(matches), store.match_writer(matches),
                         current=store.match_current, hold_when=store.accept_all)
    r.stats['matches'] = len(matches)
    return done


def read_mvr(conn, r, get=http.get, min_rows=10000):
    raw = get(sources.MVR, timeout=180)
    file = _keep(conn, r, 'crashes', sources.MVR, raw, 'ptp.zip', 'application/zip')
    try:
        crashes, skipped, total = sources.parse_mvr(raw, min_rows)
    except ShapeError as e:
        raise _tag(e, file)
    done = pipeline.load(conn, r, 'crashes', sources.MVR, store.crash_rows(crashes),
                         store.crash_writer(crashes, skipped, total, places.load(conn)), raw_file_id=file.raw_file_id,
                         current=store.crash_current, hold_when=pipeline.missing_over(0.01))
    r.stats.update(crashes=len(crashes), crashes_skipped=skipped)
    return done


def mvr(conn, r, get=http.get, min_rows=10000):
    problems, rejected = [], []
    done = attempt(conn, r, 'crashes', sources.MVR, lambda: read_mvr(conn, r, get, min_rows), problems, rejected)
    if done and done.status in ('loaded', 'confirmed'):
        attempt(conn, r, 'matches', sources.MVR, lambda: rebuild_matches(conn, r), problems, rejected)
    return problems, rejected


def read_risk(conn, r, get=http.get, min_rows=10000):
    """Every layer page by page, each layer reconciled with the count the source gives for it."""
    sections, layers = [], {}
    for layer in sources.RISK_LAYERS:
        counted = sources.parse_risk_count(get(sources.risk_count_query(layer), timeout=90))
        offset = features = 0
        while True:
            url = sources.risk_query(layer, offset)
            raw = get(url, timeout=90)
            file = _keep(conn, r, 'risk', url, raw, f'risk-{layer}-{offset}.json', 'application/json')
            try:
                rows, n = sources.parse_risk_page(raw, layer)
            except ShapeError as e:
                raise _tag(e, file)
            sections += rows
            features += n
            offset += n
            if n < 1000:
                break
        if features != counted:
            raise ShapeError(f'ArcGIS: слой {layer} има {counted} обекта, прочетени {features}')
        layers[layer] = (features, counted)
    if len(sections) < min_rows:
        raise ShapeError(f'ArcGIS: непълна мрежа, {len(sections)} участъка')
    done = pipeline.load(conn, r, 'risk', sources.ARCGIS, store.risk_rows(sections), store.risk_writer(sections, layers),
                         current=store.risk_current, hold_when=pipeline.missing_over(0.05))
    r.stats['sections'] = len(sections)
    return done


def risk(conn, r, get=http.get, min_rows=10000):
    problems, rejected = [], []
    done = attempt(conn, r, 'risk', sources.ARCGIS, lambda: read_risk(conn, r, get, min_rows), problems, rejected)
    if done and done.status in ('loaded', 'confirmed'):
        r.stats['bearings'] = derive.bearings_from_sections(conn, everything=True)
        attempt(conn, r, 'matches', sources.MVR, lambda: rebuild_matches(conn, r), problems, rejected)
    r.stats['bearings_osm'], r.stats['osm_failed'] = derive.bearings_from_osm(conn, get)
    return problems, rejected


# ---------------------------------------------------------------- the move from road.db

def _old(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')   # one snapshot for every table
    return db


def copy(conn, r, old_db=OLD_DB):
    """Once, at the move: the last state of road.db, as it is. The tool never kept the answers of its sources, so
    the SQLite file is the only place that holds what the map shows; all its tables are current answers, not a
    history, and the next scheduled reads replace them. Each load carries the time road.db records for it; an
    error road.db records for a source is a rejected read after it. The arrows of the points are copied too: some
    came from OpenStreetMap in reads that left no trace."""
    if conn.execute('SELECT 1 FROM ops.load WHERE tool = %s LIMIT 1', (TOOL,)).fetchone():
        raise RuntimeError('the shared database already has reads of this tool: copy runs once, before the first read')
    db = _old(old_db)
    state = {row['key']: row for row in db.execute('SELECT * FROM sources')}
    at = {k: dt.datetime.fromisoformat(v['updated']) for k, v in state.items() if v['updated']}
    for kind, url in TOLL:
        columns = ('count15', 'count60', 'speed15') if kind == 'traffic' else ('air', 'surface', 'humidity', 'wind')
        stations = [dict(scp=x['scp'], name=x['name'], lat=x['lat'], lon=x['lon'], **{c: x[c] for c in columns},
                         observed=dt.datetime.fromisoformat(x['observed']) if x['observed'] else None)
                    for x in db.execute(f'SELECT * FROM {kind} ORDER BY rowid')]
        pipeline.load(conn, r, kind, url, store.toll_rows(stations), store.toll_writer(kind, stations, 0),
                      current=store.toll_current(kind), now=at[kind])
        r.stats[kind] = len(stations)
    with conn.transaction():
        conn.cursor().executemany('''INSERT INTO patna_obstanovka.station_bearing (scp, lat, lon, bearing, method, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s)''', [(x['scp'], x['lat'], x['lon'], x['bearing'], 'road.db' if x['bearing'] is not None else 'none',
                                                  at['traffic']) for x in db.execute('SELECT * FROM traffic ORDER BY rowid')])
    for source, group, code, kind in sources.LIMA:
        events = [dict(id=x['id'], kind=kind, title=x['title'], detail=x['detail'], lat=x['lat'], lon=x['lon'],
                       valid_from=x['valid_from'], valid_until=x['valid_until'], url=x['source_url'])
                  for x in db.execute('SELECT * FROM events WHERE kind = ? ORDER BY rowid', (kind,))]
        if source not in at:
            continue
        xml_url = events[0]['url'] if events else state[source]['url']
        pipeline.load(conn, r, source, sources.CATALOG.format(group=group, code=code), store.lima_rows(events, xml_url),
                      store.lima_writer(source, kind, events, xml_url), current=store.lima_current, now=at[source])
        r.stats[source] = len(events)
    sections = [dict(x) | {'fid': int(x['id'].split(':')[1])} for x in db.execute('SELECT * FROM risk ORDER BY rowid')]
    # road.db kept no count of the source for a layer: what it holds is both numbers
    layers = {layer: (n, n) for layer, n in Counter(s['layer'] for s in sections).items()}
    pipeline.load(conn, r, 'risk', sources.ARCGIS, store.risk_rows(sections), store.risk_writer(sections, layers, at['risk']),
                  current=store.risk_current, now=at['risk'])
    crashes = [dict(row_no=int(x['id']), at=x['date'], lat=x['lat'], lon=x['lon'], type=x['description'], killed=x['killed'],
                    injured=x['injured']) for x in db.execute('SELECT * FROM crashes ORDER BY rowid')]
    # road.db does not keep the rows it left out; the file had as many rows as its last number (the journal of
    # 23.11.2026 says 4 089 without coordinates, as the difference does)
    total = max(c['row_no'] for c in crashes)
    pipeline.load(conn, r, 'crashes', sources.MVR, store.crash_rows(crashes),
                  store.crash_writer(crashes, total - len(crashes), total, at=at['crashes']), current=store.crash_current,
                  now=at['crashes'])
    key = dict(zip((c['row_no'] for c in crashes), store.crash_keys(crashes)))
    matches = [(key[int(x['crash_id'])], x['risk_id'], x['distance_m']) for x in db.execute('SELECT * FROM crash_matches ORDER BY rowid')]
    pipeline.load(conn, r, 'matches', sources.MVR, store.match_rows(matches), store.match_writer(matches),
                  current=store.match_current, now=at['matches'])
    for name, x in sorted(state.items()):
        if x['error']:
            pipeline.reject(conn, r, name, x['url'], x['error'])
    r.stats.update(risk=len(sections), crashes=len(crashes), matches=len(matches), errors=sum(1 for x in state.values() if x['error']))
    db.close()


PARITY = {   # table of road.db: (its query, the query of the serving layer)
    'traffic': ('SELECT scp, name, lat, lon, count15, count60, speed15, observed, bearing FROM traffic ORDER BY rowid',
                'SELECT scp, name, lat, lon, count15, count60, speed15, observed, bearing FROM pub_patna_obstanovka.traffic ORDER BY row_no'),
    'weather': ('SELECT scp, name, lat, lon, air, surface, humidity, wind, observed FROM weather ORDER BY rowid',
                'SELECT scp, name, lat, lon, air, surface, humidity, wind, observed FROM pub_patna_obstanovka.weather ORDER BY row_no'),
    'events': ('SELECT id, kind, title, detail, lat, lon, valid_from, valid_until, source_url FROM events ORDER BY rowid',
               'SELECT id, kind, title, detail, lat, lon, valid_from, valid_until, source_url FROM pub_patna_obstanovka.event ORDER BY position'),
    'risk': ('SELECT id, layer, road, risk_level, condition, reference, district, minlat, maxlat, minlon, maxlon, geometry, length_km FROM risk ORDER BY rowid',
             'SELECT id, layer, road, risk_level, condition, reference, district, minlat, maxlat, minlon, maxlon, geometry, length_km FROM pub_patna_obstanovka.risk_section ORDER BY position'),
    'crashes': ('SELECT id, date, lat, lon, description, killed, injured FROM crashes ORDER BY rowid',
                'SELECT id, date, lat, lon, description, killed, injured FROM pub_patna_obstanovka.crash ORDER BY row_no'),
    'crash_matches': ('SELECT crash_id, risk_id, distance_m FROM crash_matches ORDER BY CAST(crash_id AS INTEGER)',
                      'SELECT crash_id, risk_id, distance_m FROM pub_patna_obstanovka.crash_match ORDER BY crash_id::int'),
    'sources': ('SELECT key, label, url, updated, count, error FROM sources ORDER BY key',
                'SELECT key, label, url, updated, count, error FROM pub_patna_obstanovka.source_state ORDER BY key COLLATE "C"'),
}


def parity(conn, r, old_db=OLD_DB):
    """road.db against what the map reads now, every table in its order, every field. Each comparison is a result of
    a rule of kind `parity` in ops.check_result."""
    db = _old(old_db)
    differ, detail = 0, []
    for table, (old_sql, new_sql) in PARITY.items():
        old = [tuple(x) for x in db.execute(old_sql)]
        new = [tuple(x) for x in conn.execute(new_sql)]
        n = sum(a != b for a, b in zip(old, new)) + abs(len(old) - len(new))
        differ += n
        detail += [f'{table}: {a} -> {b}' for a, b in zip(old, new) if a != b][:5]
        pipeline.record_check(conn, r, f'parity_{table}_rows', len(old), len(new))
        r.stats[table] = {'old': len(old), 'new': len(new), 'differ': n}
    for name, column in (('killed', 'killed'), ('injured', 'injured')):
        pipeline.record_check(conn, r, f'parity_{name}', db.execute(f'SELECT sum({column}) FROM crashes').fetchone()[0],
                              conn.execute(f'SELECT sum({column}) FROM pub_patna_obstanovka.crash').fetchone()[0])
    pipeline.record_check(conn, r, 'parity_rows_differ', 0, differ, '; '.join(detail)[:2000] or None)
    db.close()


# ---------------------------------------------------------------- freshness

def observations(conn):
    """БГТОЛ answers, but with old measurements: the clock of the source stopped (the old rule: an hour)."""
    problems = []
    now = pipeline.utcnow()
    for kind, title in (('traffic', 'БГТОЛ · измерен трафик'), ('weather', 'БГТОЛ · метеостанции')):
        last = conn.execute(f'SELECT max(observed) FROM patna_obstanovka.{kind}').fetchone()[0]
        if last is None:
            continue
        if now - last > OBSERVATION_MAX_AGE or last - now > dt.timedelta(minutes=10):
            problems.append(f'{title}: последното измерване е от {last.astimezone(dt.timezone.utc):%d.%m.%Y %H:%M} UTC')
    return problems


def freshness(conn, group=None):
    """pipeline.freshness() of the tool; with a group only what that scheduled step answers for: its sources, its
    own step, and every rule (a failed rule keeps the whole serving layer back)."""
    problems = pipeline.freshness(conn, TOOL)
    if group:
        other = {s for g, names in GROUPS.items() for s in names} - set(GROUPS[group])
        titles = dict(conn.execute('SELECT source, title_bg FROM ops.source WHERE tool = %s', (TOOL,)))
        steps = {s for (s,) in conn.execute('SELECT DISTINCT step FROM ops.run WHERE tool = %s', (TOOL,))} - {group}
        prefixes = tuple(f'{titles[s]}:' for s in other if s in titles) + tuple(f'{s} ' for s in other) \
            + tuple(f'стъпка {s}:' for s in steps)
        problems = [p for p in problems if not p.startswith(prefixes)]
    if group in (None, 'current'):
        problems += observations(conn)
    return problems


# ---------------------------------------------------------------- the steps

READS = {'current': current, 'mvr': mvr, 'risk': risk}


def perform(conn, step, **kw):
    """One step as n8n runs it: the reads (or the copy, or the comparison), the rules, the publication. kw goes to
    the read (the tests give it their own get())."""
    try:
        with pipeline.run(conn, TOOL, step, sha=pipeline.code_sha(ROOT)) as r:
            problems, rejected = [], []
            if step in READS:
                problems, rejected = READS[step](conn, r, **kw)
            elif step == 'copy':
                copy(conn, r, **kw)
            elif step == 'parity':
                parity(conn, r, **kw)
            if step != 'parity':
                failed = pipeline.run_checks(conn, r)
                published = None if failed else pipeline.publish(conn, r, ROOT)
                problems += failed or published.problems
                # a table a publication has just filled has no statistics: the first page would scan it
                for table in (published.changes if published else {}):
                    conn.execute(f'ANALYZE {table}')
            r.stats['rejected'] = len(rejected)
            result = {'step': step, 'stats': r.stats, 'problems': problems, 'rejected': rejected}
    except pipeline.Busy as e:
        return {'step': step, 'status': 'skipped', 'note': str(e)}
    if step == 'parity':
        bad = conn.execute('''SELECT title_bg, expected, actual FROM ops.check_latest
            WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name''', (TOOL,)).fetchall()
        result['problems'] = [f'{title}: старо {e}, ново {n}' for title, e, n in bad]
    return result


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', choices=['migrate', *READS, 'copy', 'parity', 'check', 'freshness'], required=True)
    p.add_argument('--group', choices=sorted(GROUPS))
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == 'migrate':
            pipeline.require_core(conn, '0007')
            result = {'migrations': pipeline.migrate(conn, TOOL, ROOT)}
        elif a.step == 'freshness':
            result = {'problems': freshness(conn, a.group)}
        else:
            result = perform(conn, a.step)
    return pipeline.report(result)


if __name__ == '__main__':
    raise SystemExit(main())

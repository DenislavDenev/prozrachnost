"""The reads of Държавна собственост into the landing zone (sobstvenost_src), each as one batch of pipeline.load(), and
the clean tables (sobstvenost) built whole from the current reads. The ledger, the hold and the change log are
pipeline.load().

Five kinds of resource, each a load under its own source and ref:

    appk / catalogue        the enterprises of the APPK catalogue         {appk_id: record, '#read': manifest}
    appk / ministries       the ministries of the APPK list               {ministry_id: record, '#read': manifest}
    ncr / catalogue         the concessions of the NCR search             {concession_id: record, '#read': manifest}
    appk-details / <id>     one enterprise: its profile and its reports   {'#profile': profile, report_id: report}
    ncr-details / <id>      one concession: its documents and notices     {'#profile': profile}

A record is the dict the parser gives (ingest/parse.py). Its columns rebuild it exactly (record()), and the sha256 of
the record (version()) is what the old database stored as its version; the pages list versions by it."""
import datetime as dt
import hashlib
import json
from decimal import Decimal

from psycopg.types.json import Jsonb

from . import pipeline
from .config import RAW, SOURCES, TOOL
from .parse import ShapeError
from .privacy import is_person


def canonical(rows):
    return json.dumps(sorted(rows, key=lambda r: str(r['id'])), sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(rows):
    return hashlib.sha256(canonical(rows).encode()).hexdigest()


def version(row):
    """The sha256 of one record: the version the old database (ops.version) and the pages give it."""
    return digest([row])


def reconcile(rows, expected):
    if len({r['id'] for r in rows}) != len(rows):
        raise ShapeError('duplicate source identity')
    if len(rows) != expected:
        raise ShapeError(f'reconciliation: {len(rows)} != {expected}')


# ---------------------------------------------------------------- the fields of each record

ENTERPRISE = ('name', 'status', 'share_text', 'share_pct', 'owner', 'participation', 'source_warning', 'source_url')
MINISTRY = ('name', 'status')
CONCESSION = ('reg_number', 'status', 'procedure_status', 'name', 'kind', 'subject', 'area_extent', 'conceder', 'related_id',
              'related_reg', 'source_url')
CONCESSION_EMPTY = {'municipalities': [], 'concessionaire_eik': None}   # always empty in the search; filled nowhere
PROFILE = ('source_url', 'eik_original', 'eik', 'kind', 'legal_form', 'seat', 'principal', 'kid', 'category', 'subject',
           'standards', 'board_original', 'board_format')
REPORT = ('report_id', 'kind', 'year', 'quarter', 'filed_on', 'title', 'source_url', 'appk_id')
NOTICE = ('source_url', 'location_original', 'concessionaire_name', 'concessionaire_eik', 'term_months')


def check(row, fields, extra=()):
    """The record has exactly these fields; an unknown or missing one stops the read before anything is written."""
    want = {'id', *fields, *extra}
    if set(row) != want:
        raise ShapeError(f"Непознато или липсващо поле в {row.get('id')}: {sorted(set(row) ^ want)}")


def _check_enterprise(row):
    check(row, ENTERPRISE)


def _check_profile(row):
    check(row, PROFILE, ('board', 'reports'))
    for b in row['board']:
        if set(b) not in ({'name', 'country'}, {'original', 'verbatim'}) or b.get('verbatim') not in (None, True):
            raise ShapeError(f"Непознат запис в борда на {row['id']}: {sorted(b)}")


def _check_report(row):
    check(row, REPORT, ('documents', 'financials'))
    if row['financials'] is not None:
        raise ShapeError(f"Финансови числа в {row['id']}: парсерът не ги извлича")
    for d in row['documents']:
        if set(d) != {'id', 'title', 'url'}:
            raise ShapeError(f"Непознат документ в {row['id']}: {sorted(d)}")


def _check_concession(row):
    check(row, CONCESSION, CONCESSION_EMPTY)
    if any(row[k] != v for k, v in CONCESSION_EMPTY.items()):
        raise ShapeError(f"Поле, което търсенето не дава, има стойност в {row['id']}")


def _check_concession_profile(row):
    check(row, ('source_url',), ('documents', 'notices'))
    for d in row['documents']:
        if set(d) != {'url', 'title'}:
            raise ShapeError(f"Непознат документ в {row['id']}: {sorted(d)}")
    for n in row['notices']:
        if set(n) != {*NOTICE, 'municipality_names', 'location_places', 'financials'} or n['financials'] is not None:
            raise ShapeError(f"Непознато поле в обявление на {row['id']}: {sorted(n)}")
        if any(set(p) != {'oblast', 'municipality'} for p in n['location_places']):
            raise ShapeError(f"Непознато място в обявление на {row['id']}")


def _plain(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v.isoformat()
    return v


def _rows(conn, sql, params):
    cur = conn.execute(sql, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, v)) for v in cur.fetchall()]


# ---------------------------------------------------------------- holding a read

NESTED = ('board', 'documents', 'report_links', 'municipalities')
SCALAR = ('eik', 'share_pct', 'principal', 'filed_on', 'owner', 'share_text', 'participation', 'year', 'quarter', 'unit')


def lost(old, new):
    """How many records of the current read the new one loses: a record that is missing, an item gone from a list of
    the record (a board member, a document), or a value that became empty. The old database held on the same."""
    def items(row, field):
        return {json.dumps(x, sort_keys=True) for x in row.get(field) or []}
    n = len(old.keys() - new.keys())
    for key in old.keys() & new.keys():
        a, b = old[key], new[key]
        if any(items(a, f) - items(b, f) for f in NESTED) or any(a.get(f) is not None and b.get(f) is None for f in SCALAR):
            n += 1
    return n


def hold_when(old, new):
    """For pipeline.load(): the description of a read is not a record."""
    return lost({k: v for k, v in old.items() if k != '#read'}, {k: v for k, v in new.items() if k != '#read'})


# ---------------------------------------------------------------- the catalogues

def _write_read(conn, load_id, rows, n):
    m = rows['#read']
    conn.execute('INSERT INTO sobstvenost_src.read (load_id, pages, row_count, source_count, manifest) VALUES (%s,%s,%s,%s,%s)',
                 (load_id, m['pages'], n, m['source_count'], Jsonb(m)))


def write_catalogue(table, key, fields, convert=None):
    convert = convert or {}

    def write(conn, load_id, rows):
        records = [r for k, r in rows.items() if k != '#read']
        _write_read(conn, load_id, rows, len(records))
        with conn.cursor() as cur:
            cur.executemany(f'''INSERT INTO sobstvenost_src.{table} (load_id, {key}, {', '.join(fields)}, row_sha256)
                VALUES (%s, %s, {', '.join(['%s'] * len(fields))}, %s)''',
                            [(load_id, r['id'], *(convert.get(f, lambda v: v)(r[f]) for f in fields), version(r)) for r in records])
    return write


def read_catalogue(table, key, fields, empty=None):
    def current(conn, load_id):
        out = {}
        for db in _rows(conn, f'SELECT * FROM sobstvenost_src.{table} WHERE load_id = %s ORDER BY {key}', (load_id,)):
            row = {'id': db[key], **{f: _plain(db[f]) for f in fields}, **{k: (list(v) if isinstance(v, list) else v)
                                                                            for k, v in (empty or {}).items()}}
            out[row['id']] = row
        out['#read'] = conn.execute('SELECT manifest FROM sobstvenost_src.read WHERE load_id = %s', (load_id,)).fetchone()[0]
        return out
    return current


def _decimal(v):
    return None if v is None else Decimal(v)


CATALOGUES = {
    ('appk', 'catalogue'): ('enterprise', 'appk_id', ENTERPRISE, _check_enterprise, {'share_pct': _decimal}, None),
    ('appk', 'ministries'): ('ministry', 'ministry_id', MINISTRY, lambda r: check(r, MINISTRY), None, None),
    ('ncr', 'catalogue'): ('concession', 'concession_id', CONCESSION, _check_concession, None, CONCESSION_EMPTY),
}


def load_catalogue(conn, r, source, ref, rows, manifest, now=None):
    """One complete read of a catalogue. ShapeError before anything is written when the read is not sound."""
    table, key, fields, checker, convert, empty = CATALOGUES[(source, ref)]
    reconcile(rows, len(rows))
    for row in rows:
        checker(row)
    keyed = {row['id']: row for row in rows}
    keyed['#read'] = manifest
    return pipeline.load(conn, r, source, ref, keyed, write_catalogue(table, key, fields, convert),
                         current=read_catalogue(table, key, fields, empty), hold_when=hold_when, now=now)


def catalogue(conn, source):
    """The records of the current read of a catalogue, in the order of their id, as the parser gave them."""
    load = conn.execute("SELECT load_id FROM ops.load WHERE tool = %s AND source = %s AND ref = 'catalogue' AND is_current",
                        (TOOL, source)).fetchone()
    if not load:
        return []
    table, key, fields, *_rest, empty = CATALOGUES[(source, 'catalogue')]
    rows = read_catalogue(table, key, fields, empty)(conn, load[0])
    return [rows[k] for k in sorted(k for k in rows if k != '#read')]


# ---------------------------------------------------------------- one enterprise

def write_enterprise(conn, load_id, rows):
    p = rows['#profile']
    conn.execute(f'''INSERT INTO sobstvenost_src.profile (load_id, appk_id, {', '.join(PROFILE)}, report_count, row_sha256)
        VALUES (%s, %s, {', '.join(['%s'] * len(PROFILE))}, %s, %s)''', (load_id, p['id'], *(p[f] for f in PROFILE), p['reports'], version(p)))
    with conn.cursor() as cur:
        cur.executemany('''INSERT INTO sobstvenost_src.board_entry (load_id, position, member_name, country, entry)
            VALUES (%s,%s,%s,%s,%s)''', [(load_id, i, b.get('name'), b.get('country'), b.get('original'))
                                         for i, b in enumerate(p['board'], 1)])
        reports = [x for k, x in rows.items() if k != '#profile']
        cur.executemany(f'''INSERT INTO sobstvenost_src.report (load_id, record_id, {', '.join(REPORT)}, row_sha256)
            VALUES (%s, %s, {', '.join(['%s'] * len(REPORT))}, %s)''',
                        [(load_id, x['id'], *(x[f] for f in REPORT), version(x)) for x in reports])
        cur.executemany('''INSERT INTO sobstvenost_src.report_document (load_id, record_id, position, document_id, title, url)
            VALUES (%s,%s,%s,%s,%s,%s)''', [(load_id, x['id'], i, d['id'], d['title'], d['url'])
                                           for x in reports for i, d in enumerate(x['documents'], 1)])


def read_enterprise(conn, load_id):
    db = _rows(conn, 'SELECT * FROM sobstvenost_src.profile WHERE load_id = %s', (load_id,))[0]
    board = [({'name': b['member_name'], 'country': b['country']} if b['entry'] is None else {'original': b['entry'], 'verbatim': True})
             for b in _rows(conn, 'SELECT * FROM sobstvenost_src.board_entry WHERE load_id = %s ORDER BY position', (load_id,))]
    out = {'#profile': {'id': db['appk_id'], **{f: db[f] for f in PROFILE}, 'board': board, 'reports': db['report_count']}}
    docs = {}
    for d in _rows(conn, 'SELECT * FROM sobstvenost_src.report_document WHERE load_id = %s ORDER BY record_id, position', (load_id,)):
        docs.setdefault(d['record_id'], []).append({'id': d['document_id'], 'title': d['title'], 'url': d['url']})
    for x in _rows(conn, 'SELECT * FROM sobstvenost_src.report WHERE load_id = %s ORDER BY record_id', (load_id,)):
        out[x['record_id']] = {'id': x['record_id'], **{f: _plain(x[f]) for f in REPORT}, 'documents': docs.get(x['record_id'], []),
                               'financials': None}
    return out


def load_enterprise(conn, r, profile, reports, now=None):
    """The profile of one enterprise and the reports it links, read together."""
    _check_profile(profile)
    reconcile(reports, len(reports))
    if profile['reports'] != len(reports):
        raise ShapeError(f"Профилът на {profile['id']} брои {profile['reports']} отчета, прочетени са {len(reports)}")
    rows = {'#profile': profile}
    for x in reports:
        _check_report(x)
        if x['appk_id'] != profile['id']:
            raise ShapeError(f"Отчетът {x['id']} е на друго предприятие")
        rows[x['id']] = x
    return pipeline.load(conn, r, 'appk-details', profile['id'], rows, write_enterprise, current=read_enterprise,
                         hold_when=lost, now=now)


# ---------------------------------------------------------------- one concession

def write_concession(conn, load_id, rows):
    p = rows['#profile']
    conn.execute('INSERT INTO sobstvenost_src.concession_profile (load_id, concession_id, source_url, row_sha256) VALUES (%s,%s,%s,%s)',
                 (load_id, p['id'], p['source_url'], version(p)))
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO sobstvenost_src.concession_document (load_id, position, url, title) VALUES (%s,%s,%s,%s)',
                        [(load_id, i, d['url'], d['title']) for i, d in enumerate(p['documents'], 1)])
        cur.executemany(f'''INSERT INTO sobstvenost_src.notice (load_id, position, {', '.join(NOTICE)})
            VALUES (%s, %s, {', '.join(['%s'] * len(NOTICE))})''', [(load_id, i, *(n[f] for f in NOTICE)) for i, n in enumerate(p['notices'], 1)])
        cur.executemany('''INSERT INTO sobstvenost_src.notice_place (load_id, notice_position, position, oblast, municipality)
            VALUES (%s,%s,%s,%s,%s)''', [(load_id, i, j, x['oblast'], x['municipality'])
                                         for i, n in enumerate(p['notices'], 1) for j, x in enumerate(n['location_places'], 1)])
        cur.executemany('''INSERT INTO sobstvenost_src.notice_municipality (load_id, notice_position, position, name)
            VALUES (%s,%s,%s,%s)''', [(load_id, i, j, x) for i, n in enumerate(p['notices'], 1) for j, x in enumerate(n['municipality_names'], 1)])


def read_concession(conn, load_id):
    db = _rows(conn, 'SELECT * FROM sobstvenost_src.concession_profile WHERE load_id = %s', (load_id,))[0]
    places, names = {}, {}
    for x in _rows(conn, 'SELECT * FROM sobstvenost_src.notice_place WHERE load_id = %s ORDER BY notice_position, position', (load_id,)):
        places.setdefault(x['notice_position'], []).append({'oblast': x['oblast'], 'municipality': x['municipality']})
    for x in _rows(conn, 'SELECT * FROM sobstvenost_src.notice_municipality WHERE load_id = %s ORDER BY notice_position, position', (load_id,)):
        names.setdefault(x['notice_position'], []).append(x['name'])
    notices = [{**{f: n[f] for f in NOTICE}, 'municipality_names': names.get(n['position'], []),
                'location_places': places.get(n['position'], []), 'financials': None}
               for n in _rows(conn, 'SELECT * FROM sobstvenost_src.notice WHERE load_id = %s ORDER BY position', (load_id,))]
    documents = [{'url': d['url'], 'title': d['title']}
                 for d in _rows(conn, 'SELECT * FROM sobstvenost_src.concession_document WHERE load_id = %s ORDER BY position', (load_id,))]
    return {'#profile': {'id': db['concession_id'], 'source_url': db['source_url'], 'documents': documents, 'notices': notices}}


def load_concession(conn, r, profile, now=None):
    _check_concession_profile(profile)
    return pipeline.load(conn, r, 'ncr-details', profile['id'], {'#profile': profile}, write_concession, current=read_concession,
                         hold_when=lost, now=now)


# ---------------------------------------------------------------- complete passes and files

def write_full(conn, load_id, rows):
    f = rows['#full']
    conn.execute('INSERT INTO sobstvenost_src.full_read (load_id, started_at, entity_count) VALUES (%s,%s,%s)',
                 (load_id, f['started_at'], f['entities']))


def load_full(conn, r, source, ref, started_at, entities, now=None):
    """A complete pass over the profiles, the concessions or the files: its start and how many it read."""
    return pipeline.load(conn, r, source, ref, {'#full': {'started_at': str(started_at), 'entities': entities}}, write_full, now=now)


def rejected_since(conn, source, since):
    """The refs of a source whose last load since `since` was refused."""
    return [ref for (ref,) in conn.execute('''SELECT ref FROM (SELECT DISTINCT ON (ref) ref, status FROM ops.load
            WHERE tool = %s AND source = %s AND loaded_at >= %s ORDER BY ref, loaded_at DESC, load_id DESC) x
        WHERE status = 'rejected' ORDER BY ref''', (TOOL, source, since))]


ATTACHMENT = ('url', 'sha256', 'media_type', 'bytes', 'title', 'report_id', 'year', 'appk_id', 'concession_id')


def write_attachment(source):
    def write(conn, load_id, rows):
        a = rows['#file']
        conn.execute(f'''INSERT INTO sobstvenost_src.attachment (load_id, attachment_id, source, {', '.join(ATTACHMENT)})
            VALUES (%s, %s, %s, {', '.join(['%s'] * len(ATTACHMENT))})''', (load_id, rows['#ref'], source, *(a.get(f) for f in ATTACHMENT)))
    return write


def read_attachment(conn, load_id):
    db = _rows(conn, 'SELECT * FROM sobstvenost_src.attachment WHERE load_id = %s', (load_id,))[0]
    return {'#ref': db['attachment_id'], '#file': {f: db[f] for f in ATTACHMENT if db[f] is not None}}


def save_attachment(conn, r, source, row, raw, media_type, now=None, raw_file=None):
    """One archived original: the file under its sha256, its metadata as a load. A new file is a new version."""
    if raw_file is None:
        raw_file = pipeline.save_raw(conn, r, source + '-files', row['url'], raw, RAW, name=row['url'].rsplit('/', 1)[-1],
                                     media_type=media_type, fetched_at=now)
    meta = {'url': row['url'], 'sha256': raw_file.sha256, 'media_type': media_type, 'bytes': len(raw), 'title': row['title'],
            'report_id': row.get('report_id'), 'year': row.get('year'), 'appk_id': row.get('appk_id'), 'concession_id': row.get('ncr_id')}
    return pipeline.load(conn, r, source + '-files', row['id'], {'#ref': row['id'], '#file': {k: v for k, v in meta.items() if v is not None}},
                         write_attachment(source), raw_file_id=raw_file.raw_file_id, current=read_attachment,
                         hold_when=lambda old, new: 0, now=now)


# ---------------------------------------------------------------- the clean tables

def current_loads(conn, source):
    return conn.execute('SELECT ref, load_id, loaded_at FROM ops.load WHERE tool = %s AND source = %s AND is_current ORDER BY ref',
                        (TOOL, source)).fetchall()


def build(conn, r):
    """The clean tables again, whole, from the current reads, in one transaction. An ЕИК points to core.org only when
    core.org has it; the published ЕИК stays beside it. Returns how many records the landing rows rebuild exactly (by
    their sha256), and how many there are."""
    same = total = 0

    def keep(row, sha):
        nonlocal same, total
        total += 1
        same += version(row) == sha
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (pipeline._lock_key('sobstvenost-build'),))
        for t in ('notice_municipality', 'notice_place', 'notice', 'concession_document', 'concession_profile', 'concession',
                  'report_document', 'report', 'profile', 'enterprise', 'ministry'):
            conn.execute(f'DELETE FROM sobstvenost.{t}')
        out = {}
        for (source, ref), (table, key, fields, _c, _v, empty) in CATALOGUES.items():
            load = conn.execute('SELECT load_id FROM ops.load WHERE tool = %s AND source = %s AND ref = %s AND is_current',
                                (TOOL, source, ref)).fetchone()
            if not load:
                continue
            rows = read_catalogue(table, key, fields, empty)(conn, load[0])
            shas = dict(conn.execute(f'SELECT {key}, row_sha256 FROM sobstvenost_src.{table} WHERE load_id = %s', (load[0],)).fetchall())
            records = [v for k, v in rows.items() if k != '#read']
            for x in records:
                keep(x, shas[x['id']])
            out[table] = records
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO sobstvenost.ministry (ministry_id, name, status) VALUES (%s,%s,%s)',
                            [(x['id'], x['name'], x['status']) for x in out.get('ministry', [])])
            cur.executemany(f'''INSERT INTO sobstvenost.enterprise (appk_id, {', '.join(ENTERPRISE)})
                VALUES (%s, {', '.join(['%s'] * len(ENTERPRISE))})''',
                            [(x['id'], *(_decimal(x[f]) if f == 'share_pct' else x[f] for f in ENTERPRISE)) for x in out.get('enterprise', [])])
            cur.executemany(f'''INSERT INTO sobstvenost.concession (concession_id, {', '.join(CONCESSION)})
                VALUES (%s, {', '.join(['%s'] * len(CONCESSION))})''', [(x['id'], *(x[f] for f in CONCESSION)) for x in out.get('concession', [])])
        profiles, reports, documents, eiks = [], [], [], set()
        for ref, load_id, loaded_at in current_loads(conn, 'appk-details'):
            rows = read_enterprise(conn, load_id)
            shas = dict(conn.execute('SELECT record_id, row_sha256 FROM sobstvenost_src.report WHERE load_id = %s', (load_id,)).fetchall())
            p = rows.pop('#profile')
            keep(p, conn.execute('SELECT row_sha256 FROM sobstvenost_src.profile WHERE load_id = %s', (load_id,)).fetchone()[0])
            profiles.append((p, loaded_at))
            eiks.add(p['eik'])
            for x in rows.values():
                keep(x, shas[x['id']])
                reports.append(x)
                documents += [(x['appk_id'], x['id'], i, d['id'], d['title'], d['url']) for i, d in enumerate(x['documents'], 1)]
        concessions = []
        for ref, load_id, loaded_at in current_loads(conn, 'ncr-details'):
            p = read_concession(conn, load_id)['#profile']
            keep(p, conn.execute('SELECT row_sha256 FROM sobstvenost_src.concession_profile WHERE load_id = %s', (load_id,)).fetchone()[0])
            concessions.append((p, loaded_at))
            eiks.update(n['concessionaire_eik'] for n in p['notices'])
        eiks.discard(None)
        known = {e for (e,) in conn.execute('SELECT eik FROM core.org WHERE eik = ANY(%s)', (list(eiks),))} if eiks else set()
        with conn.cursor() as cur:
            cur.executemany(f'''INSERT INTO sobstvenost.profile (appk_id, {', '.join(f for f in PROFILE if f != 'board_original')},
                    org_eik, report_count, read_at) VALUES (%s, {', '.join(['%s'] * (len(PROFILE) - 1))}, %s, %s, %s)''',
                            [(p['id'], *(p[f] for f in PROFILE if f != 'board_original'), p['eik'] if p['eik'] in known else None,
                              p['reports'], at) for p, at in profiles])
            cur.executemany(f'''INSERT INTO sobstvenost.report (appk_id, record_id, {', '.join(f for f in REPORT if f != 'appk_id')})
                VALUES (%s, %s, {', '.join(['%s'] * (len(REPORT) - 1))})''',
                            [(x['appk_id'], x['id'], *(x[f] for f in REPORT if f != 'appk_id')) for x in reports])
            cur.executemany('''INSERT INTO sobstvenost.report_document (appk_id, record_id, position, document_id, title, url)
                VALUES (%s,%s,%s,%s,%s,%s)''', documents)
            cur.executemany('INSERT INTO sobstvenost.concession_profile (concession_id, source_url, read_at) VALUES (%s,%s,%s)',
                            [(p['id'], p['source_url'], at) for p, at in concessions])
            cur.executemany('INSERT INTO sobstvenost.concession_document (concession_id, position, url, title) VALUES (%s,%s,%s,%s)',
                            [(p['id'], i, d['url'], d['title']) for p, _ in concessions for i, d in enumerate(p['documents'], 1)])
            cur.executemany(f'''INSERT INTO sobstvenost.notice (concession_id, position, {', '.join(NOTICE)}, concessionaire_is_person, org_eik)
                VALUES (%s, %s, {', '.join(['%s'] * len(NOTICE))}, %s, %s)''',
                            [(p['id'], i, *(n[f] for f in NOTICE), is_person(n['concessionaire_name']),
                              n['concessionaire_eik'] if n['concessionaire_eik'] in known else None)
                             for p, _ in concessions for i, n in enumerate(p['notices'], 1)])
            cur.executemany('''INSERT INTO sobstvenost.notice_place (concession_id, notice_position, position, oblast, municipality)
                VALUES (%s,%s,%s,%s,%s)''', [(p['id'], i, j, x['oblast'], x['municipality']) for p, _ in concessions
                                             for i, n in enumerate(p['notices'], 1) for j, x in enumerate(n['location_places'], 1)])
            cur.executemany('''INSERT INTO sobstvenost.notice_municipality (concession_id, notice_position, position, name)
                VALUES (%s,%s,%s,%s)''', [(p['id'], i, j, x) for p, _ in concessions
                                          for i, n in enumerate(p['notices'], 1) for j, x in enumerate(n['municipality_names'], 1)])
    r.stats.update(enterprises=len(out.get('enterprise', [])), concessions=len(out.get('concession', [])), profiles=len(profiles),
                   reports=len(reports), concession_profiles=len(concessions))
    return same, total


def source_problems(conn):
    """The reads whose last answer was refused or is held, in the words of the sources page."""
    words = {'catalogue': 'каталог', 'ministries': 'министерства'}
    out = [f"{SOURCES[s][0]}, {words.get(scope, scope)}: {'задържан до второ четене' if status == 'held' else 'невалиден отговор'}"
           + (f': {error}' if error else '')
           for s, scope, status, error in conn.execute('''SELECT source, scope, status, error FROM sobstvenost.source_state
               WHERE scope IN ('catalogue', 'ministries') AND status <> 'ok' ORDER BY source, scope''')]
    for source, ref, reason in conn.execute('''SELECT source, ref, reason FROM (SELECT DISTINCT ON (source, ref) source, ref, status, reason
            FROM ops.load WHERE tool = %s AND source IN ('appk-details', 'ncr-details', 'appk-files', 'ncr-files')
            ORDER BY source, ref, loaded_at DESC, load_id DESC) x WHERE status = 'rejected' ORDER BY source, ref''', (TOOL,)):
        out.append(f'{source} {ref}: невалиден отговор: {reason}')
    return out

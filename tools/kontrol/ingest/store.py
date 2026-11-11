"""One complete read of a list into the landing zone (kontrol_src) as one batch of pipeline.load(), and the clean
tables (kontrol) built whole from the current reads. The ledger, the hold and the change log are pipeline.load().

A record is the dict the parser gives (ingest/parse.py, ingest/pdf.py). Each kind of record has its own landing
table whose columns are the fields of the record; record() rebuilds the dict from them exactly, and the sha256 of
the dict (row_sha256) is the version of the record that the pages list."""
import datetime as dt
import hashlib
import json

from psycopg.types.json import Jsonb

from . import pipeline
from .config import RAW, SOURCES, TOOL
from .parse import ShapeError, canonical
from .privacy import private_fields


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


# the fields every record has (parse.base), without its id
BASE = ('source', 'categories', 'title', 'url', 'published_on', 'publication_year', 'report_period', 'kind', 'sector',
        'auditee', 'eik', 'unp', 'recommendation_text')
# per kind: fields always there, fields there only with a value, fields the parser always leaves empty
KINDS = {
    'audit_report': dict(fields=(), optional=('source_discrepancy', 'source_kind_values', 'source_sector_values',
                                              'source_publication_year_values'), empty=('completed_on',)),
    'adfi_report': dict(fields=('basis', 'updated_label'), optional=(), empty=('completed_on',)),
    'adfi_list': dict(fields=('inspection_count', 'inspection_status'), optional=(), empty=('completed_on',)),
    'inspection': dict(fields=('source_row', 'document_id', 'document_page', 'document_sha256', 'excerpt', 'excerpt_page',
                               'result_text', 'violations_count'),
                       optional=('detail_status', 'report_no', 'result_page'), empty=('completed_on', 'damage_amount', 'currency')),
    'decision': dict(fields=('case_no', 'source_case_id', 'act_date', 'act_no', 'law', 'outcome_text', 'source_gap'),
                     optional=('source_pager_discrepancy',), empty=('completed_on', 'court_result', 'complaint_status')),
}
COLUMN = {'source_pager_discrepancy': 'pager_discrepancy'}   # the field under another column name
DATES = {'published_on', 'act_date'}
INSPECTION = 'Приключила финансова инспекция'


def kind_of(source, row):
    if source.startswith(('nao', 'recommendations')):
        return 'audit_report'
    if source == 'adfi':
        return 'adfi_report'
    if source == 'adfi-history':
        return 'inspection' if row.get('kind') == INSPECTION else 'adfi_list'
    if source == 'cpc':
        return 'decision'
    raise ShapeError('Непознат източник ' + source)


def _columns(kind):
    spec = KINDS[kind]
    return ['record_id', *BASE, *spec['fields'], *(COLUMN.get(f, f) for f in spec['optional'])]


def check(kind, row):
    """The record has exactly the fields of its kind; an unknown or missing field stops the read."""
    spec = KINDS[kind]
    want = {'id', *BASE, *spec['fields'], *spec['empty']} | {f for f in spec['optional'] if row.get(f) is not None}
    if set(row) != want:
        raise ShapeError(f"Непознато или липсващо поле в {row.get('id')}: {sorted(set(row) ^ want)}")
    if any(row[f] is not None for f in spec['empty']):
        raise ShapeError(f"Поле, което източникът не дава, има стойност в {row['id']}")


def record(kind, db):
    """The parser's dict back from a landing row (a dict of its columns)."""
    spec = KINDS[kind]
    out = {'id': db['record_id']}
    for f in (*BASE, *spec['fields']):
        v = db[f]
        out[f] = v.isoformat() if isinstance(v, dt.date) else v
    for f in spec['empty']:
        out[f] = None
    for f in spec['optional']:
        v = db[COLUMN.get(f, f)]
        if v is not None:
            out[f] = v
    return out


# ---------------------------------------------------------------- one read

def prepare(source, rows, manifest):
    """The checks of a complete read, then one record per id (the old store.publish did the same)."""
    if not manifest.get('complete') or manifest.get('occurrence_count') != len(rows):
        raise ShapeError('Непълен обхват или неуспешна сверка по брой')
    if manifest.get('source_count') is not None and manifest['source_count'] != len(rows):
        raise ShapeError('Броячът на източника не съвпада')
    rows = canonical([{k: v for k, v in r.items() if k not in ('pdf_event', 'document_event')} for r in rows])
    for r in rows:
        check(kind_of(source, r), r)
    return rows


def keyed(rows, manifest):
    """{key: row} for pipeline.load(): every record under its source id, and the description of the read."""
    out = {r['id']: r for r in rows}
    out['#read'] = {k: v for k, v in manifest.items() if k != 'answer_sha256'}
    return out


def should_hold(old, new):
    """A record of the current read is missing, or one of its values became empty (at any depth)."""
    return bool(lost(old, new))


def lost(old, new):
    a = {r['id']: r for r in old}
    b = {r['id']: r for r in new}

    def loss(before, after):
        if before not in (None, '', [], {}) and after in (None, '', [], {}):
            return True
        if isinstance(before, dict) and isinstance(after, dict):
            return any(k not in after or loss(v, after[k]) for k, v in before.items())
        if isinstance(before, list) and isinstance(after, list):
            return any(encode(v) not in {encode(x) for x in after} for v in before)
        return False
    return sum(1 for key, row in a.items() if key not in b or loss(row, b[key]))


def hold_when(old, new):
    """For pipeline.load(): how many records count as lost. The description of the read is not a record."""
    return lost([v for k, v in old.items() if k != '#read'], [v for k, v in new.items() if k != '#read'])


def writer(source, manifest):
    def write(conn, load_id, rows):
        records = [r for k, r in rows.items() if k != '#read']
        conn.execute('''INSERT INTO kontrol_src.read (load_id, source, pages, occurrence_count, unique_count, source_count,
            scope, manifest) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (load_id, source, manifest['pages'], manifest['occurrence_count'], len(records),
                      manifest.get('source_count'), manifest['scope'], Jsonb(rows['#read'])))
        by = {}
        for r in records:
            by.setdefault(kind_of(source, r), []).append(r)
        with conn.cursor() as cur:
            for kind, rs in by.items():
                cols = _columns(kind)
                fields = ['id' if c == 'record_id' else next((f for f, col in COLUMN.items() if col == c), c) for c in cols]
                cur.executemany(f'''INSERT INTO kontrol_src.{kind} (load_id, {', '.join(cols)}, row_sha256)
                    VALUES (%s, {', '.join(['%s'] * len(cols))}, %s)''',
                                [(load_id, *(Jsonb(r[f]) if isinstance(r.get(f), dict) else r.get(f) for f in fields),
                                  digest(encode(r))) for r in rs])
    return write


def records(conn, load_id):
    """The records of a load, rebuilt from the landing rows, each with the sha256 it was stored with."""
    out = []
    for kind in KINDS:
        cur = conn.execute(f'SELECT * FROM kontrol_src.{kind} WHERE load_id = %s ORDER BY record_id', (load_id,))
        names = [d.name for d in cur.description]
        for values in cur.fetchall():
            db = dict(zip(names, values))
            out.append((kind, record(kind, db), db['row_sha256']))
    return out


def current(conn, load_id):
    out = {r['id']: r for _, r, _ in records(conn, load_id)}
    out['#read'] = conn.execute('SELECT manifest FROM kontrol_src.read WHERE load_id = %s', (load_id,)).fetchone()[0]
    return out


def load(conn, r, source, rows, manifest, now=None):
    """One complete read of a list. ShapeError before anything is written when the read is not sound."""
    rows = prepare(source, rows, manifest)
    return pipeline.load(conn, r, source, SOURCES[source][1], keyed(rows, manifest), writer(source, manifest),
                         current=current, hold_when=hold_when, now=now)


# ---------------------------------------------------------------- documents

DOCUMENT = ('document_sha256', 'text_available', 'excerpt', 'excerpt_page', 'text_status', 'unp', 'fetch_url', 'fetch_method')


def write_document(conn, load_id, rows):
    d = rows['document']
    unknown = set(d) - {'url', *DOCUMENT}
    if unknown:
        raise ShapeError('Непознато поле в документ: ' + ', '.join(sorted(unknown)))
    conn.execute(f'''INSERT INTO kontrol_src.document (load_id, record_id, url, {', '.join(DOCUMENT)})
        VALUES (%s, %s, %s, {', '.join(['%s'] * len(DOCUMENT))})''',
                 (load_id, rows['#ref'], d['url'], *(d.get(k) for k in DOCUMENT)))


def current_document(conn, load_id):
    cur = conn.execute('SELECT * FROM kontrol_src.document WHERE load_id = %s', (load_id,))
    db = dict(zip([d.name for d in cur.description], cur.fetchone()))
    return {'#ref': db['record_id'], 'document': {k: db[k] for k in ('url', *DOCUMENT) if db[k] is not None}}


def save_document(conn, r, row, raw, metadata, fetch_url=None, now=None, raw_file=None):
    """One read document of a record: the file under its sha256, the metadata as a load of the source `document`.
    A new file of the same record is a new version; the same file again is unchanged."""
    if raw_file is None:
        raw_file = pipeline.save_raw(conn, r, 'document', row['id'], raw, RAW, name=(fetch_url or row['url']).rsplit('/', 1)[-1],
                                     fetched_at=now)
    metadata = {k: v for k, v in dict(metadata, url=row['url']).items() if v is not None}
    return pipeline.load(conn, r, 'document', row['id'], {'#ref': row['id'], 'document': metadata}, write_document,
                         raw_file_id=raw_file.raw_file_id, current=current_document, hold_when=lambda old, new: 0, now=now)


def document_state(conn, ref):
    """(url, last read) of the current document of a record; None when it was never read."""
    return conn.execute('''SELECT d.url, (SELECT max(x.loaded_at) FROM ops.load x WHERE x.tool = l.tool AND x.source = l.source
            AND x.ref = l.ref AND x.status IN ('loaded', 'confirmed', 'unchanged'))
        FROM ops.load l JOIN kontrol_src.document d USING (load_id)
        WHERE l.tool = %s AND l.source = 'document' AND l.ref = %s AND l.is_current''', (TOOL, ref)).fetchone()


# ---------------------------------------------------------------- the clean tables

CLEAN = {
    'audit_report': ('record_id', 'source', 'categories', 'title', 'url', 'publication_year', 'report_period', 'kind',
                     'sector', 'recommendation_text', 'eik', 'unp', 'source_discrepancy', 'source_kind_values',
                     'source_sector_values', 'source_publication_year_values'),
    'adfi_report': ('record_id', 'source', 'categories', 'title', 'url', 'kind', 'auditee', 'eik', 'basis', 'published_on',
                    'publication_year', 'updated_label'),
    'adfi_list': ('record_id', 'source', 'categories', 'title', 'url', 'kind', 'report_period', 'inspection_count',
                  'inspection_status'),
    'inspection': ('record_id', 'source', 'categories', 'list_id', 'source_row', 'title', 'url', 'kind', 'auditee', 'eik',
                   'report_period', 'document_page', 'document_sha256', 'excerpt', 'excerpt_page', 'result_text',
                   'violations_count', 'detail_status', 'report_no', 'result_page'),
    'decision': ('record_id', 'source', 'categories', 'title', 'url', 'kind', 'act_no', 'act_date', 'case_no',
                 'source_case_id', 'published_on', 'publication_year', 'law', 'outcome_text', 'unp', 'source_gap'),
}
FIELD = {'record_id': 'id', 'list_id': 'document_id'}   # clean column -> field of the record


def current_records(conn):
    """Every record of the current reads, with its kind, as the landing zone keeps it; and how many kept it exactly."""
    out, same = [], 0
    for (load_id,) in conn.execute('''SELECT load_id FROM ops.load WHERE tool = %s AND is_current AND source <> 'document'
            ORDER BY source''', (TOOL,)).fetchall():
        for kind, row, sha in records(conn, load_id):
            same += digest(encode(row)) == sha
            out.append((kind, row))
    return out, same


def build(conn, r):
    """The clean tables again, whole, from the current reads, in one transaction. The five lists of the National Audit
    Office merge into one record per document (parse.canonical, as the pages always did). An ЕИК stays only when
    core.org has it. Returns the number of records whose landing row rebuilds the record exactly, and their count."""
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (pipeline._lock_key('kontrol-build'),))
        rows, same = current_records(conn)
        kinds = {row['id']: kind for kind, row in rows}
        merged = canonical([row for _, row in rows])
        eiks = {x['eik'] for x in merged if x.get('eik')}
        known = {e for (e,) in conn.execute('SELECT eik FROM core.org WHERE eik = ANY(%s)', (list(eiks),))} if eiks else set()
        for t in ('inspection', 'adfi_list', 'adfi_report', 'audit_report', 'decision', 'document'):
            conn.execute(f'DELETE FROM kontrol.{t}')
        by = {}
        for x in merged:
            x = dict(x, eik=x['eik'] if x.get('eik') in known else None)
            by.setdefault(kinds[x['id']], []).append(x)
        with conn.cursor() as cur:
            for kind in ('adfi_list', 'audit_report', 'adfi_report', 'inspection', 'decision'):
                cols = CLEAN[kind]
                cur.executemany(f'''INSERT INTO kontrol.{kind} ({', '.join(cols)}, private_fields)
                    VALUES ({', '.join(['%s'] * len(cols))}, %s)''',
                                [(*(x.get(FIELD.get(c, c)) for c in cols), private_fields(x)) for x in by.get(kind, [])])
            docs = conn.execute(f'''SELECT d.record_id, d.url, {', '.join('d.' + k for k in DOCUMENT)},
                    (SELECT max(x.loaded_at) FROM ops.load x WHERE x.tool = l.tool AND x.source = l.source AND x.ref = l.ref
                       AND x.status IN ('loaded', 'confirmed', 'unchanged'))
                FROM ops.load l JOIN kontrol_src.document d USING (load_id)
                WHERE l.tool = %s AND l.source = 'document' AND l.is_current''', (TOOL,)).fetchall()
            cur.executemany(f'''INSERT INTO kontrol.document (record_id, url, {', '.join(DOCUMENT)}, read_at, private_fields)
                VALUES ({', '.join(['%s'] * (len(DOCUMENT) + 3))}, %s)''',
                            [(*d, private_fields({'excerpt': d[4]})) for d in docs])
    r.stats.update(records=len(merged), documents=len(docs))
    return same, len(rows)

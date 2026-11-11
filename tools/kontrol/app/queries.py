"""What the pages read: the serving layer only (pub_kontrol.* and pub.publication). Not the clean tables, not the
landing zone, not the ledger: the web role has no right to them.

One request is one snapshot: every query of a request sees the same publication, never a mix. The tables of a
publication are turned into the rows the pages have always used (one dict per record, its document laid over it),
once per publication, and shared by the requests that show it."""
import contextlib
import contextvars
import csv
import datetime as dt
import io
import threading
from collections import Counter
from urllib.parse import urlencode

import psycopg

from ingest.config import DSN, SOURCES
from ingest.privacy import public_row

TABLES = {
    'audit_report': None, 'adfi_report': None, 'adfi_list': None, 'inspection': {'list_id': 'document_id'}, 'decision': None,
}
_lock = threading.Lock()
_cache = {}
_state = contextvars.ContextVar('state', default=None)


@contextlib.contextmanager
def snapshot():
    """A read-only connection whose every statement sees the database as of its first one."""
    with psycopg.connect(DSN) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.read_only = True
        yield c


def version(c):
    """(number, time) of the publication this snapshot shows; None before the first one."""
    return c.execute("SELECT publication_id, published_at FROM pub.publication WHERE tool = 'kontrol' "
                     'ORDER BY publication_id DESC LIMIT 1').fetchone()


def _dicts(c, sql, params=()):
    cur = c.execute(sql, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, v)) for v in cur.fetchall()]


def _plain(v):
    return v.isoformat() if isinstance(v, dt.date) and not isinstance(v, dt.datetime) else v


def build_rows(c):
    """Every record as the dict the pages read: its fields, the document of the record laid over it when it is the
    document of the same address, and private text replaced (it already is, in the serving layer)."""
    rows = []
    for table, rename in TABLES.items():
        for x in _dicts(c, f'SELECT * FROM pub_kontrol.{table}'):
            row = {(rename or {}).get(k, k): _plain(v) for k, v in x.items() if k != 'record_id'}
            row['id'] = x['record_id']
            rows.append(row)
    rows.sort(key=lambda r: r['id'])
    docs = {d.pop('record_id'): d for d in _dicts(c, 'SELECT * FROM pub_kontrol.document')}
    for row in rows:
        if row['source'] == 'cpc' and not row.get('act_no'):
            row['act_no'] = row['title']
        doc = docs.get(row['id'], {})
        if doc.get('url') == row['url']:
            row.update({k: v for k, v in doc.items() if k not in ('url', 'read_at')})
    return [public_row(row) for row in rows]


def load(c):
    """The rows and the state of the publication this snapshot shows, built once per publication."""
    v = version(c)
    key = v[0] if v else None
    with _lock:
        if _cache.get('key', -1) != key or 'rows' not in _cache:
            _cache.update(key=key, rows=build_rows(c), docs={r['record_id']: r for r in _dicts(c, 'SELECT * FROM pub_kontrol.document')},
                          resources=_resources(c), gaps=_dicts(c, 'SELECT record_id AS id, url, error, sha256 FROM pub_kontrol.inspection_gap ORDER BY record_id'))
        return v, dict(_cache)


def use(c, state):
    return _state.set(dict(state, conn=c))


def current():
    return _state.get() or {}


def allrows():
    return current().get('rows', [])


def _resources(c):
    saved = {x['source']: x for x in _dicts(c, 'SELECT * FROM pub_kontrol.source_state')}
    out = []
    for key, (label, url) in SOURCES.items():
        x = saved.get(key)
        if x is None or x['status'] == 'Няма доказан пълен прочит':
            out.append(dict(source=key, label=label, url=url, status='Няма доказан пълен прочит'))
        else:
            out.append(dict(source=key, label=label, url=url, **{k: v for k, v in x.items() if k not in ('source', 'url', 'label')}))
    return out


def resources():
    return current().get('resources', [])


def snapshots():
    """The descriptions of the reads the sources page shows: the lists of АДФИ that are only a document."""
    gaps = current().get('gaps', [])
    return [dict(manifest=dict(inspection_gaps=gaps))] if gaps else []


def document_coverage():
    eligible = {r['id'] for r in allrows() if r.get('kind') != 'Приключила финансова инспекция'}
    docs = current().get('docs', {})
    done = eligible & docs.keys()
    return dict(eligible=len(eligible), processed=len(done), pending=len(eligible - docs.keys()),
                text_available=sum(bool(docs[r].get('text_available')) for r in done),
                excerpts=sum(bool(docs[r].get('excerpt')) for r in done))


def versions(ref):
    c = current()['conn']
    return [dict(sha256=s, detected_at=t) for s, t in c.execute(
        'SELECT sha256, detected_at FROM pub_kontrol.record_version WHERE record_id = %s ORDER BY detected_at', (ref,))]


def document_versions(ref):
    c = current()['conn']
    return [dict(sha256=s, url=u, fetched_at=t) for s, u, t in c.execute(
        'SELECT sha256, url, fetched_at FROM pub_kontrol.document_version WHERE record_id = %s ORDER BY fetched_at', (ref,))]


def select(view, year='', kind='', sector='', q='', sort='published', source='', period=''):
    rows = allrows()
    if view == 'oditi':
        rows = [r for r in rows if any(s.startswith('nao') for s in r['categories']) and not r.get('recommendation_text')]
    elif view == 'preporaki':
        rows = [r for r in rows if any(s.startswith('recommendations') for s in r['categories'])]
    elif view == 'inspekcii':
        rows = [r for r in rows if r['source'].startswith('adfi')]
    elif view == 'kzk':
        rows = [r for r in rows if r['source'] == 'cpc']
    options = {k: sorted({r.get(k) for r in rows if r.get(k)}, reverse=k == 'publication_year') for k in ['publication_year', 'kind', 'sector', 'report_period']}
    rows = [r for r in rows if (not year or r.get('publication_year') == year)
            and (not kind or r.get('kind') == kind) and (not sector or r.get('sector') == sector)
            and (not source or source in r['categories']) and (not period or r.get('report_period') == period)
            and (not q or q.casefold() in ' '.join(str(r.get(k) or '') for k in ['title', 'auditee', 'case_no', 'report_period', 'recommendation_text', 'excerpt']).casefold())]
    if sort == 'title':
        rows.sort(key=lambda r: (r['title'].casefold(), r['id']))
    elif sort == 'year':
        rows.sort(key=lambda r: (r.get('publication_year') or '', r['id']), reverse=True)
    else:
        rows.sort(key=lambda r: (r.get('published_on') or '', r.get('publication_year') or '', r['id']), reverse=True)
    return rows, options


def summary(rows):
    audits = {r['id'] for r in rows if any(s.startswith('nao') for s in r['categories'])}
    decisions = {r['id'] for r in rows if r['source'] == 'cpc'}
    cases = {r.get('source_case_id') for r in rows if r['source'] == 'cpc' and r.get('source_case_id')}
    inspections = {r['id'] for r in rows if r.get('kind') == 'Приключила финансова инспекция'}
    adfi_docs = {r['id'] for r in rows if r['source'] == 'adfi'}
    recs = {r['id'] for r in rows if any(s.startswith('recommendations') for s in r['categories'])}
    return dict(total=len(rows), audits=len(audits), decisions=len(decisions), cases=len(cases), inspections=len(inspections),
                adfi_documents=len(adfi_docs), recommendations=len(recs))


def timeline(rows):
    return sorted(Counter(r.get('publication_year') or 'Не е посочена' for r in rows).items())


FIELDS = ['id', 'source', 'title', 'kind', 'sector', 'auditee', 'eik', 'case_no', 'act_no', 'act_date', 'published_on', 'publication_year',
          'completed_on', 'report_period', 'unp', 'recommendation_text', 'violations_count', 'document_page', 'document_sha256', 'url']


def csv_response(rows):
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(FIELDS)
    for r in rows:
        cells = []
        for k in FIELDS:
            v = '' if r.get(k) is None else str(r[k])
            if v.startswith(('=', '+', '-', '@')):
                v = "'" + v
            cells.append(v)
        writer.writerow(cells)
    return '﻿' + buf.getvalue()


def link(path, **kwargs):
    return path + '?' + urlencode({k: v for k, v in kwargs.items() if v is not None and v != ''})

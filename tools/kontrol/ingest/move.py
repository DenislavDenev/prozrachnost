"""The move from the database of its own (kontrol, JSON documents) into the shared one, 11.11.2026.

replay  every list the old database holds is read again from its archived raw files, through the same traversal and
        parser as a live read (a client that answers each request with the archived answer), at the time the old
        database stored it; then every archived document, through the same text extraction.
parity  the old database against the shared one: every record by its sha256, the records the pages show field by field,
        the documents, the versions and the state of each list. Each is a result of a rule of kind `parity`.
"""
import hashlib
import json
from pathlib import Path

import psycopg

from . import pipeline, store
from .config import DATA, OLD_DSN, SOURCES
from .parse import ShapeError, canonical
from .privacy import public_row


class Archive:
    """A client whose answers are the raw files the old database archived, in the order it archived them. The key of
    a request is the address under which the reader stores its answer (sources.py), so the traversal is the same."""

    def __init__(self, files, forms=None):
        self.files, self.posts = {}, 0
        for url, path, sha in files:
            self.files.setdefault(url, []).append((path, sha))
        # an answer the old archive did not keep and that only carries the search form (the first GET of КЗК): its
        # original from the evidence of the source, read on 01.10.2026
        for url, path in (forms or {}).items():
            if url not in self.files and Path(path).exists():
                self.files[url] = [(str(path), hashlib.sha256(Path(path).read_bytes()).hexdigest())]
        self.served = []

    def answer(self, key):
        if key not in self.files:
            raise ShapeError('Няма архивиран отговор за ' + key)
        queue = self.files[key]
        path, sha = queue.pop(0) if len(queue) > 1 else queue[0]
        self.served.append((key, path, sha))
        return Path(path).read_bytes()

    def get(self, url):
        return self.answer(url)

    def request(self, method, url, data=None, **kw):
        if method == 'GET':
            return self.answer(url)
        if data and 'page' in data and 'csrfmiddlewaretoken' in data:
            return self.answer(url + '&page=' + str(data['page']))
        self.posts += 1
        return self.answer(url + '?results_page=' + str(self.posts))

    def close(self):
        pass


def _old(old_dsn):
    o = psycopg.connect(old_dsn, autocommit=True)
    o.execute('SET default_transaction_read_only = on')
    return o


def replay(conn, r, old_dsn=OLD_DSN, ops='ops', only=None):
    from .run import collect
    problems = []
    with _old(old_dsn) as o:
        published = dict(o.execute('SELECT source, published_at FROM live.snapshot'))
        raw = o.execute(f'SELECT source, url, path, sha256, fetched_at FROM {ops}.raw_file ORDER BY fetched_at, url').fetchall()
        docs = o.execute(f'''SELECT v.ref, v.sha256, v.url, v.payload, v.fetched_at, f.path FROM {ops}.document_version v
            JOIN LATERAL (SELECT path FROM {ops}.raw_file f WHERE f.sha256 = v.sha256 LIMIT 1) f ON true
            ORDER BY v.fetched_at, v.ref''').fetchall()
    for source in SOURCES:
        if source not in published or only and source != only:
            continue
        at = published[source]
        # the files of the read: archived before it was stored; the documents read later are not part of it
        files = [(u, p, s) for src, u, p, s, t in raw if src == source and t <= at]
        client = Archive(files, {SOURCES['cpc'][1]: DATA / 'evidence' / 'cpc-search.html'} if source == 'cpc' else None)
        times = {(u, s): t for src, u, p, s, t in raw if src == source}

        def save(src, url, data, client=client, at=at):
            key, path, sha = client.served[-1]
            pipeline.register_raw(conn, r, src, url, path, fetched_at=times.get((key, sha), at), expect_sha=sha)
        try:
            rows, manifest = collect(source, client, save, lambda *a: None)
            store.load(conn, r, source, rows, manifest, now=at)
            r.count('reads')
        except ShapeError as e:
            pipeline.reject(conn, r, source, SOURCES[source][1], e, now=at)
            problems.append(f'{source}: {e}')
    store.build(conn, r)   # the records the documents belong to
    rows = {x['id']: x for x in _records(conn)}
    from .pdf import cpc_document, title_excerpt
    for ref, sha, url, old, fetched_at, path in docs:
        row = rows.get(ref)
        if row is None:
            problems.append(f'{ref}: документът е на запис, който го няма')
            continue
        file, data = pipeline.register_raw(conn, r, 'document', ref, path, fetched_at=fetched_at, expect_sha=sha)
        if ref.startswith('adfi-list:'):
            # the lists are read in full by the historical register; their document is what that read found
            metadata = dict(document_sha256=sha, text_available=row.get('inspection_count') is not None, excerpt=None,
                            excerpt_page=None, text_status=row['inspection_status'])
        elif ref.startswith('cpc:'):
            metadata = dict(cpc_document(data), fetch_url=old.get('fetch_url'), fetch_method=old.get('fetch_method'))
        else:
            metadata = title_excerpt(data)
        store.save_document(conn, r, dict(id=ref, url=row['url']), data, metadata, now=fetched_at, raw_file=file)
        r.count('documents')
    return problems


def _records(conn):
    """The records of the clean tables as the ingest side has them (before the serving layer replaces private text)."""
    out = []
    for kind, cols in store.CLEAN.items():
        cur = conn.execute(f'SELECT {", ".join(cols)} FROM kontrol.{kind}')
        for values in cur.fetchall():
            out.append({store.FIELD.get(c, c): v for c, v in zip(cols, values)})
    return out


def old_rows(o):
    """The rows the old pages showed: every list merged by id, the document of a record laid over it, private text
    replaced (the old app.queries._loadrows)."""
    rows = canonical([x for (p,) in o.execute('SELECT payload FROM live.snapshot ORDER BY source') for x in p['rows']])
    docs = dict(o.execute('SELECT ref, payload FROM live.document'))
    for row in rows:
        if row['source'] == 'cpc' and not row.get('act_no'):
            row['act_no'] = row['title']
        doc = docs.get(row['id'], {})
        if doc.get('url') == row['url']:
            row.update({k: v for k, v in doc.items() if k != 'url'})
    return [public_row(row) for row in rows]


def as_shown(source, row):
    """An old record as the old pages read it. The old archive of КЗК was made by an earlier parser that did not keep
    act_no; the old app filled it from the title (app/queries.py before 11.11.2026). The parser keeps it now."""
    if source == 'cpc' and 'act_no' not in row:
        return dict(row, act_no=row['title'])
    return row


def _same(a, b):
    """Every field of the record, compared as text. Not compared: the oddity of a page counter of КЗК, which the old
    database kept on the first record of the last page and no page showed (it stays in the landing zone)."""
    keys = (a.keys() | b.keys()) - {'read_at', 'source_pager_discrepancy'}
    return all(json.dumps(a.get(k), ensure_ascii=False, default=str, sort_keys=True)
               == json.dumps(b.get(k), ensure_ascii=False, default=str, sort_keys=True) for k in keys)


def parity(conn, r, old_dsn=OLD_DSN, ops='ops'):
    from app.queries import build_rows
    with _old(old_dsn) as o:
        snap = {s: p['rows'] for s, p in o.execute('SELECT source, payload FROM live.snapshot')}
        old = old_rows(o)
        old_docs = dict(o.execute('SELECT ref, payload FROM live.document'))
        old_versions = set(o.execute(f'SELECT ref, sha256, detected_at FROM {ops}.version'))
        old_doc_versions = set(o.execute(f'SELECT ref, sha256, url, fetched_at FROM {ops}.document_version'))
        old_res = {x[0]: x[1:] for x in o.execute('SELECT source, status, row_count, page_count, source_count, scope FROM src.resource')}
    new_by_source = {}
    for (load_id, source) in conn.execute("SELECT load_id, source FROM ops.load WHERE tool = 'kontrol' AND is_current AND source <> 'document'").fetchall():
        new_by_source[source] = {sha for _, _, sha in store.records(conn, load_id)}
    n_old = sum(len(v) for v in snap.values())
    n_new = sum(len(v) for v in new_by_source.values())
    filled = sum(1 for s, rows in snap.items() for x in rows if as_shown(s, x) is not x)
    differ = sum(1 for s, rows in snap.items() for x in rows
                 if store.digest(store.encode(as_shown(s, x))) not in new_by_source.get(s, set()))
    pipeline.record_check(conn, r, 'parity_rows', n_old, n_new)
    pipeline.record_check(conn, r, 'parity_rows_differ', 0, differ,
                          f'решения на КЗК без act_no в старата база, сравнени с act_no = заглавието: {filled}' if filled else None)
    with conn.transaction():
        new = build_rows(conn)
    by = {x['id']: x for x in new}
    bad = [x['id'] for x in old if x['id'] not in by or not _same(x, by[x['id']])]
    pipeline.record_check(conn, r, 'parity_records', len(old), len(new))
    pipeline.record_check(conn, r, 'parity_records_differ', 0, len(bad), ', '.join(bad[:40]) or None)
    cur = conn.execute('SELECT * FROM pub_kontrol.document')
    names = [d.name for d in cur.description]
    new_docs = {x['record_id']: {k: v for k, v in x.items() if k not in ('record_id', 'read_at') and v is not None}
                for x in (dict(zip(names, v)) for v in cur.fetchall())}
    same_docs = sum(1 for ref, d in old_docs.items() if new_docs.get(ref) == {k: v for k, v in d.items() if v is not None})
    pipeline.record_check(conn, r, 'parity_documents', len(old_docs), same_docs,
                          ', '.join(ref for ref in old_docs if new_docs.get(ref) != {k: v for k, v in old_docs[ref].items() if v is not None})[:2000] or None)
    new_versions = set(conn.execute('SELECT record_id, sha256, detected_at FROM pub_kontrol.record_version'))
    # a version is the record at a time; its sha256 is new where the old record lacked act_no (see as_shown)
    old_at, new_at = {(a, t) for a, _, t in old_versions}, {(a, t) for a, _, t in new_versions}
    pipeline.record_check(conn, r, 'parity_versions', len(old_versions), len(old_at & new_at),
                          f'с еднакъв sha256: {len(old_versions & new_versions)}; с нов sha256: {len(old_versions - new_versions)}; '
                          f'нови версии: {len(new_at - old_at)}')
    new_doc_versions = set(conn.execute('SELECT record_id, sha256, url, fetched_at FROM pub_kontrol.document_version'))
    pipeline.record_check(conn, r, 'parity_document_versions', len(old_doc_versions), len(old_doc_versions & new_doc_versions))
    new_res = {x[0]: x[1:] for x in conn.execute('SELECT source, status, row_count, page_count, source_count, scope FROM pub_kontrol.source_state')}
    pipeline.record_check(conn, r, 'parity_sources', len(old_res), sum(1 for s, v in old_res.items() if new_res.get(s) == v))
    r.stats.update(rows=dict(old=n_old, new=n_new, differ=differ, act_no_filled=filled), records=dict(old=len(old), new=len(new), differ=len(bad)),
                   documents=dict(old=len(old_docs), same=same_docs), versions=dict(old=len(old_versions), new=len(new_versions)),
                   document_versions=dict(old=len(old_doc_versions), new=len(new_doc_versions)))

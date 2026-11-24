"""The move from the database of its own (sobstvenost_verified, JSON records) into the shared one, 24.11.2026.

replay  every read the old database stored is read again from its archived raw files, through the same traversal and
        parser as a live read (fetch() answers each address with the last answer archived before that read), at the
        time the old database stored it: the catalogues, then each enterprise and each concession at the time of its
        version; then the complete passes the old database recorded and its archived attachments.
parity  the old database against the shared one: every current record by its sha256, every version with its time,
        the archived files and the state of each read. Each is a result of a rule of kind `parity`.

The old database is read only. Its schemas are live (records) and ops (versions, raw files, states); a test passes
another name for ops, because the shared database has its own.
"""
import json
from collections import defaultdict

import psycopg

from . import pipeline, store
from .config import OLD_DSN, TOOL
from .parse import ShapeError
from .run import read_appk_catalogue, read_concession, read_enterprise, read_ncr_catalogue


def _old(old_dsn):
    o = psycopg.connect(old_dsn, autocommit=True)
    o.execute('SET default_transaction_read_only = on')
    return o


class Archive:
    """fetch() for a read at time t: the answer to an address is the last raw file the old database archived for it
    at or before t (a POST is archived under its address, like a GET). The file is registered, not copied."""

    def __init__(self, conn, r, files):
        self.conn, self.r, self.files = conn, r, defaultdict(list)
        for source, url, path, sha, fetched_at in sorted(files, key=lambda f: f[4]):
            self.files[(source, url)].append((path, sha, fetched_at))

    def at(self, t):
        def fetch(source, url, data=None, keep=False):
            found = [f for f in self.files.get((source, url), []) if f[2] <= t]
            if not found:
                raise ShapeError(f'Няма архивиран отговор за {url} до {t}')
            path, sha, fetched_at = found[-1]
            file, raw = pipeline.register_raw(self.conn, self.r, source, url, path, fetched_at=fetched_at, expect_sha=sha)
            return (raw, file) if keep else raw
        return fetch


def replay(conn, r, old_dsn=OLD_DSN, ops='ops', live='live'):
    problems = []
    with _old(old_dsn) as o:
        versions = o.execute(f'SELECT DISTINCT source, scope, observed_at FROM {ops}.version').fetchall()
        files = o.execute(f'SELECT source, url, path, sha256, fetched_at FROM {ops}.raw_file').fetchall()
        records = {(s, scope, ref): p for s, scope, ref, p in o.execute(
            f'SELECT source, scope, ref, payload FROM {live}.record WHERE gone_at IS NULL')}
        states = {(s, scope): x for s, scope, *x in o.execute(
            f"SELECT source, scope, status, last_read, last_success, rows FROM {ops}.source_state WHERE scope IN ('details', 'pdfs')")}
        has_docs = o.execute(f"SELECT to_regclass('{ops}.document_file')").fetchone()[0]
        docs = o.execute(f'''SELECT d.source, d.ref, d.sha256, d.url, d.media_type, d.metadata, d.first_seen, f.path
            FROM {ops}.document_file d JOIN LATERAL (SELECT path FROM {ops}.raw_file f WHERE f.sha256 = d.sha256 LIMIT 1) f ON true
            ORDER BY d.first_seen, d.ref''').fetchall() if has_docs else []
    archive = Archive(conn, r, files)
    # the times the old database stored something: a catalogue, or an enterprise (its profile or its reports) or a
    # concession, each read again at each of its times
    times = defaultdict(set)
    for source, scope, at in versions:
        kind, _, key = scope.partition(':')
        times[(source, 'details', key) if key else (source, kind)].add(at)

    def one(source, ref, at, work):
        # one transaction per read: its files, fetches and load commit together (a commit is slow on a busy server)
        try:
            with conn.transaction():
                work(archive.at(at), at)
            r.count('reads')
        except (ShapeError, pipeline.CorruptRaw) as e:
            pipeline.reject(conn, r, source, ref, e, now=at)
            problems.append(f'{source} {ref}: {e}')

    def appk(fetch, at):
        rows, manifest, ministries, mmanifest = read_appk_catalogue(fetch)
        with conn.transaction():
            store.load_catalogue(conn, r, 'appk', 'ministries', ministries, mmanifest, now=at)
            store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest, now=at)

    def ncr(fetch, at):
        store.load_catalogue(conn, r, 'ncr', 'catalogue', *read_ncr_catalogue(fetch), now=at)
    for at in sorted(times[('appk', 'catalogue')]):
        one('appk', 'catalogue', at, appk)
    for at in sorted(times[('ncr', 'catalogue')]):
        one('ncr', 'catalogue', at, ncr)
    # each enterprise and each concession at the time of each of its versions
    for at, source, key in sorted((at, k[0], k[2]) for k, ats in times.items() if len(k) == 3 for at in ats):
        row = records.get((source, 'catalogue', key))
        if row is None:
            problems.append(f'{source} {key}: профилът е на запис, който го няма в каталога')
            continue
        if source == 'appk':
            one('appk-details', key, at, lambda fetch, at, row=row: store.load_enterprise(conn, r, *read_enterprise(fetch, row), now=at))
        else:
            one('ncr-details', key, at, lambda fetch, at, row=row: store.load_concession(conn, r, read_concession(fetch, row), now=at))
    # the complete passes the old database recorded: started when its state says, finished when it wrote the state
    for (source, scope), (status, last_read, last_success, rows) in sorted(states.items()):
        if status == 'ok' and last_success:
            target = (source + '-full', 'details') if scope == 'details' else (source + '-files', '#full')
            store.load_full(conn, r, *target, last_success, rows, now=last_read)
    for source, ref, sha, url, media_type, metadata, first_seen, path in docs:
        file, raw = pipeline.register_raw(conn, r, source + '-files', url, path, fetched_at=first_seen, expect_sha=sha)
        store.save_attachment(conn, r, source, dict(metadata, id=ref), raw, media_type, now=first_seen, raw_file=file)
        r.count('documents')
    return problems


# ---------------------------------------------------------------- parity

def _new_records(conn):
    """{(source, scope, ref): sha256} of the current reads, under the scopes of the old database."""
    out = {}
    for (source, ref), (table, key, *_rest) in store.CATALOGUES.items():
        for k, sha in conn.execute(f'''SELECT x.{key}, x.row_sha256 FROM sobstvenost_src.{table} x JOIN ops.load l USING (load_id)
                WHERE l.tool = %s AND l.source = %s AND l.ref = %s AND l.is_current''', (TOOL, source, ref)):
            out[(source, ref, k)] = sha
    for appk_id, sha in conn.execute('''SELECT p.appk_id, p.row_sha256 FROM sobstvenost_src.profile p JOIN ops.load l USING (load_id)
            WHERE l.tool = %s AND l.is_current''', (TOOL,)):
        out[('appk', 'profile:' + appk_id, appk_id)] = sha
    for appk_id, record_id, sha in conn.execute('''SELECT x.appk_id, x.record_id, x.row_sha256 FROM sobstvenost_src.report x
            JOIN ops.load l USING (load_id) WHERE l.tool = %s AND l.is_current''', (TOOL,)):
        out[('appk', 'reports:' + appk_id, record_id)] = sha
    for concession_id, sha in conn.execute('''SELECT p.concession_id, p.row_sha256 FROM sobstvenost_src.concession_profile p
            JOIN ops.load l USING (load_id) WHERE l.tool = %s AND l.is_current''', (TOOL,)):
        out[('ncr', 'profile:' + concession_id, concession_id)] = sha
    return out


def _new_versions(conn):
    """{(source, scope, ref, sha256, first seen)} of every version in the landing zone, under the old scopes."""
    ok = "l.tool = 'sobstvenost' AND l.status IN ('loaded', 'confirmed')"
    out = set()
    for (source, ref), (table, key, *_rest) in store.CATALOGUES.items():
        out |= {(source, ref, k, sha, at) for k, sha, at in conn.execute(f'''SELECT x.{key}, x.row_sha256, min(l.loaded_at)
            FROM sobstvenost_src.{table} x JOIN ops.load l USING (load_id) WHERE {ok} AND l.source = %s AND l.ref = %s
            GROUP BY 1, 2''', (source, ref))}
    out |= {('appk', 'profile:' + k, k, sha, at) for k, sha, at in conn.execute(f'''SELECT x.appk_id, x.row_sha256, min(l.loaded_at)
        FROM sobstvenost_src.profile x JOIN ops.load l USING (load_id) WHERE {ok} GROUP BY 1, 2''')}
    out |= {('appk', 'reports:' + k, rid, sha, at) for k, rid, sha, at in conn.execute(f'''SELECT x.appk_id, x.record_id, x.row_sha256,
        min(l.loaded_at) FROM sobstvenost_src.report x JOIN ops.load l USING (load_id) WHERE {ok} GROUP BY 1, 2, 3''')}
    out |= {('ncr', 'profile:' + k, k, sha, at) for k, sha, at in conn.execute(f'''SELECT x.concession_id, x.row_sha256,
        min(l.loaded_at) FROM sobstvenost_src.concession_profile x JOIN ops.load l USING (load_id) WHERE {ok} GROUP BY 1, 2''')}
    return out


def parity(conn, r, old_dsn=OLD_DSN, ops='ops', live='live'):
    with _old(old_dsn) as o:
        old = {(s, scope, ref): store.version(p) for s, scope, ref, p in o.execute(
            f'SELECT source, scope, ref, payload FROM {live}.record WHERE gone_at IS NULL')}
        old_versions = set(o.execute(f'SELECT source, scope, ref, sha256, observed_at FROM {ops}.version'))
        has_docs = o.execute(f"SELECT to_regclass('{ops}.document_file')").fetchone()[0]
        old_docs = set(o.execute(f'SELECT source, ref, sha256 FROM {ops}.document_file')) if has_docs else set()
        old_states = {(s, scope): x for s, scope, *x in o.execute(f'''SELECT source, scope, status, rows, last_success
            FROM {ops}.source_state WHERE scope NOT LIKE 'profile:%%' AND scope NOT LIKE 'reports:%%' ''')}
    # a record's sha256 in the old database is the version it stored: a payload that does not give it was changed there
    stored = {(s, scope, ref, sha) for s, scope, ref, sha, _ in old_versions}
    unversioned = [k for k, sha in old.items() if k + (sha,) not in stored]
    new = _new_records(conn)
    differ = sorted(k for k, sha in old.items() if new.get(k) != sha)
    pipeline.record_check(conn, r, 'parity_records', len(old), len(new))
    pipeline.record_check(conn, r, 'parity_records_differ', 0, len(differ),
                          '; '.join('/'.join(k) for k in differ[:40]) or (f'без версия в старата база: {len(unversioned)}' if unversioned else None))
    new_versions = _new_versions(conn)
    pipeline.record_check(conn, r, 'parity_versions', len(old_versions), len(old_versions & new_versions),
                          f'нови версии: {len(new_versions - old_versions)}' if new_versions - old_versions else None)
    new_docs = set(conn.execute('SELECT source, attachment_id, sha256 FROM sobstvenost.attachment'))
    pipeline.record_check(conn, r, 'parity_documents', len(old_docs), len(old_docs & new_docs))
    new_states = {(s, scope): x for s, scope, *x in conn.execute(
        'SELECT source, scope, status, rows, last_success FROM sobstvenost.source_state')}
    bad = sorted('/'.join(k) for k, v in old_states.items() if new_states.get(k) != v)
    pipeline.record_check(conn, r, 'parity_sources', len(old_states), len(old_states) - len(bad), ', '.join(bad) or None)
    r.stats.update(records=dict(old=len(old), new=len(new), differ=len(differ), unversioned=len(unversioned)),
                   versions=dict(old=len(old_versions), new=len(new_versions), same=len(old_versions & new_versions)),
                   documents=dict(old=len(old_docs), new=len(new_docs)), sources=dict(old=len(old_states), differ=bad))
    return json.loads(json.dumps(r.stats, default=str))

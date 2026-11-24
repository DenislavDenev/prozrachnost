"""The archive of original attachments: the documents of the latest annual report of each enterprise and the files of
each concession. No financial extraction and no inferred currency: the file is kept under its sha256, its format is
read from its first bytes, and every new file of the same attachment is a new version."""
import hashlib
import json

from . import pipeline, store
from .parse import ShapeError
from .progress import Queue, pending


def media_type(raw):
    if raw.startswith(b'%PDF-'):
        return 'application/pdf'
    if raw.startswith(b'PK\x03\x04'):
        return 'application/zip'
    if raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return 'application/x-ole-storage'
    raise ShapeError('unrecognized original attachment format; raw retained, not called PDF')


def selected(conn, source):
    """What to archive, from the clean tables: only after a complete read of the profiles or the concessions."""
    state = conn.execute("SELECT status FROM sobstvenost.source_state WHERE source = %s AND scope = 'details'", (source,)).fetchone()
    if not state or state[0] != 'ok':
        raise ShapeError(source + ' attachment archive requires complete verified metadata')
    result = []
    if source == 'appk':
        for appk_id, record_id, report_id, year, document_id, title, url in conn.execute('''
                WITH annual AS (SELECT r.*, max(r.year) OVER (PARTITION BY r.appk_id) AS latest
                                FROM sobstvenost.report r WHERE r.kind = 'annual')
                SELECT a.appk_id, a.record_id, a.report_id, a.year, d.document_id, d.title, d.url
                FROM annual a JOIN sobstvenost.report_document d USING (appk_id, record_id)
                WHERE a.year = a.latest'''):
            result.append(dict(id=f'{appk_id}:{record_id}:{document_id}', url=url, title=title, report_id=record_id, year=year,
                               appk_id=appk_id))
    else:
        for concession_id, url, title in conn.execute('SELECT concession_id, url, title FROM sobstvenost.concession_document'):
            result.append(dict(id=concession_id + ':' + hashlib.sha256(url.encode()).hexdigest(), url=url, title=title,
                               ncr_id=concession_id))
    # the same source attachment may be linked more than once inside a profile
    unique = {row['id']: row for row in result}
    return sorted(unique.values(), key=lambda row: row['id'])


def archive(conn, r, fetch, continue_pending=False):
    """Every selected attachment, one by one, with a checkpoint after each. A file of an unknown format is refused for
    its attachment (the raw file stays) and the others go on; the pass is complete only without a refusal."""
    results, problems = {}, []
    continuing = continue_pending and (pending('appk', 'pdfs') or pending('ncr', 'pdfs'))
    for source in ('appk', 'ncr'):
        if continuing and not pending(source, 'pdfs') and source == 'appk':
            continue
        queue = Queue(source, 'pdfs', selected(conn, source), continue_pending)
        for row in queue.rows[queue.done:]:
            raw, file = fetch(source + '-files', row['url'])
            try:
                store.save_attachment(conn, r, source, row, raw, media_type(raw), raw_file=file)
            except ShapeError as e:
                pipeline.reject(conn, r, source + '-files', row['id'], e, raw_file_id=file.raw_file_id)
            queue.advance()
            print(json.dumps(dict(progress=source + '-documents', completed=queue.done, total=len(queue.rows))), flush=True)
        refused = store.rejected_since(conn, source + '-files', queue.data['started_at'])
        if refused:
            problems.append(f'{source}: файлови оригинали с непознат формат: {len(refused)} ({", ".join(refused[:5])})')
        else:
            store.load_full(conn, r, source + '-files', '#full', queue.data['started_at'], queue.done)
        queue.complete()
        results[source] = dict(completed=queue.done, total=len(queue.rows))
    return results, problems


"""The documents of the records (PDF, DOC): read once, kept under their sha256, with what their text layer says.
Each read document is a load of the source `document` (store.save_document); the clean table kontrol.document has
the current one of every record. A read from the last 7 days is not repeated."""
import datetime as dt
import time

from . import store
from .config import TOOL
from .parse import ShapeError
from .pdf import title_excerpt

FRESH = dt.timedelta(days=7)


def _fresh(conn, ref, url=None):
    state = store.document_state(conn, ref)
    return bool(state and state[1] and dt.datetime.now(dt.timezone.utc) - state[1] < FRESH and (url is None or state[0] == url))


def pending(conn, source=None):
    """How many records that have a document have none read yet."""
    rows = eligible(conn, source)
    done = {x for (x,) in conn.execute('''SELECT DISTINCT ref FROM ops.load WHERE tool = %s AND source = 'document'
        AND is_current''', (TOOL,))}
    return len({r['id'] for r in rows} - done)


def eligible(conn, source=None):
    """The records whose document is read: every report, list and decision (an inspection is a row of its list)."""
    rows = [dict(id=i, source=s, url=u, categories=c) for i, s, u, c in conn.execute('''
        SELECT record_id, source, url, categories FROM kontrol.audit_report
        UNION ALL SELECT record_id, source, url, categories FROM kontrol.adfi_report
        UNION ALL SELECT record_id, source, url, categories FROM kontrol.adfi_list
        UNION ALL SELECT record_id, source, url, categories FROM kontrol.decision ORDER BY 1''')]
    if source == 'cpc':
        return [r for r in rows if r['source'] == 'cpc']
    return [r for r in rows if r['source'] != 'cpc' and (source is None or source in r['categories'])]


def cpc(conn, r, client, limit=None, max_seconds=None):
    from . import parse, sources
    from .pdf import cpc_document
    report = dict(stored=0, unchanged=0, pending=0, problems=[], checkpoint=False)
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None

    class Checkpoint(Exception):
        pass

    def save(source, url, raw):
        store.pipeline.save_raw(conn, r, source, url, raw, store.RAW, name='cpc.html')

    def callback(rows, raw, url):
        data = parse.postback_form(raw)
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                raise Checkpoint()
            if _fresh(conn, row['id']):
                report['unchanged'] += 1
                continue
            if not row.get('document_event'):
                report['pending'] += 1
                continue
            if limit is not None and report['stored'] >= limit:
                report['pending'] += 1
                continue
            try:
                form = dict(data, __EVENTTARGET=row['document_event'], __EVENTARGUMENT='')
                document = client.request('POST', url, data=form)
                if document.startswith(b'%PDF-'):
                    metadata = cpc_document(document)
                elif document.startswith((bytes.fromhex('d0cf11e0a1b11ae1'), b'PK')):
                    metadata = dict(document_sha256=store.digest(document), text_available=False, excerpt=None, excerpt_page=None,
                                    unp=None, text_status='само документ: стар формат, без надеждно извлечен текст')
                else:
                    raise ShapeError('КЗК: отговорът не е разпознат документ')
                metadata['fetch_url'] = url
                metadata['fetch_method'] = 'POST: източников бутон за конкретен акт'
                store.save_document(conn, r, row, document, metadata, fetch_url=url)
                report['stored'] += 1
            except Exception as e:
                report['problems'].append(row['id'] + ': ' + str(e))
    # Reuses the proven real stateful search/pagination contract, never fabricated URLs.
    try:
        _, manifest = sources.cpc(client, save, document_callback=callback)
        report['source_count'] = manifest['source_count']
        report['pages'] = manifest['pages']
    except Checkpoint:
        report['checkpoint'] = True
        report['pending'] = pending(conn, 'cpc')
        report['scope'] = 'Планирано прекъсване по време; каталогът и последният пълен прочит не се променят'
    if report['pending'] and not report['checkpoint']:
        report['problems'].append('КЗК: само документ/неприключено архивиране: ' + str(report['pending']))
    return report


def refresh(conn, r, client, source=None, limit=None, max_seconds=None):
    if source == 'cpc':
        return cpc(conn, r, client, limit, max_seconds)
    rows = eligible(conn, source)
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    report = dict(stored=0, unchanged=0, pending=0, problems=[])
    for row in rows:
        if deadline is not None and time.monotonic() >= deadline:
            report['checkpoint'] = True
            report['pending'] = pending(conn, source)
            break
        if _fresh(conn, row['id'], row['url']):
            report['unchanged'] += 1
            continue
        if limit is not None and report['stored'] >= limit:
            report['pending'] += 1
            continue
        try:
            raw = client.get(row['url'])
            if row['url'].lower().endswith('.pdf'):
                metadata = title_excerpt(raw)
            else:
                if not raw.startswith((bytes.fromhex('d0cf11e0a1b11ae1'), b'PK', b'{\\rtf')):
                    raise ShapeError('Връзката не върна разпознат документен формат')
                metadata = dict(text_available=False, excerpt=None, excerpt_page=None, document_sha256=store.digest(raw),
                                text_status='само документ: стар формат, текстът не е надеждно прочетен')
            store.save_document(conn, r, row, raw, metadata)
            report['stored'] += 1
        except Exception as e:
            report['problems'].append(row['id'] + ': ' + str(e))
    if report['pending'] and not report.get('checkpoint'):
        report['problems'].append('Недовършено наваксване на документи: ' + str(report['pending']))
    return report

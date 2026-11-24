"""The steps of Държавна собственост: python -m ingest.run --step <step>

    migrate       the tables and the registers (the foundation of danni must be there)
    appk          the catalogue of enterprises and the list of ministries of АППК, all pages, checked against its counters
    ncr           the search of the National Concession Register, all pages, checked against its total
    appk-details  the profile and the reports of every enterprise in the current catalogue (a checkpoint after each)
    ncr-details   the documents and the notices of every concession in the current catalogue
    refresh       all four, a complete new read; --continue-pending goes on with a pass the budget cut
    pdfs          the original attachments: the latest annual report of each enterprise, the files of each concession
    build, check  the clean tables again from the current reads, the rules, the publication
    replay        the reads of the old database, from its raw files and through the same parser (the move)
    parity        the old database against the shared one
    freshness     the list of problems, empty when all is well
    summary       the weekly report

Ingest and serving are apart. The steps fill sobstvenost_src and sobstvenost; the pages read pub_sobstvenost, which
changes only by a publication at the end of a step whose rules all hold. A read of a catalogue is complete or it is
not stored; an enterprise or a concession whose answer has an unknown shape is refused on its own and the pass goes on.
"""
import argparse
import datetime as dt
import json
import sys

from . import parse, pipeline, store
from .config import DSN, ROOT, TOOL
from .http import Client
from .parse import ShapeError
from .progress import BudgetReached, Deadline, Queue, pending

NCR_FIELDS = ['RegNumber', 'FromDate', 'ToDate', 'LotStatusId', 'ProcedureStatusId', 'ConcessionName', 'ConcessionTypeId',
              'ConcessionSubjectId', 'AreaExtentId', 'Conceder', 'ProcedureTypeId']


# ---------------------------------------------------------------- reading (the same for a live read and the replay)
# fetch(source, url, data=None) -> the bytes of the answer; it keeps the raw file.

def read_appk_catalogue(fetch):
    """The enterprises and the ministries, every page, each list checked against the counter on the home page."""
    s = parse.soup(fetch('appk', parse.APPK + '/'))
    counters = {}
    for slide in s.select('.info-blocks-swiper .swiper-slide'):
        label = parse.text(slide).casefold()
        number = slide.select_one('p')
        if number and parse.text(number).isdigit():
            counters['enterprises' if label.startswith('предприятия') else 'ministries' if label.startswith('министерства')
                     else 'unknown'] = int(parse.text(number))
    if set(counters) != {'enterprises', 'ministries'}:
        raise ShapeError('homepage counters schema changed')
    url = parse.APPK + '/Public/Public/Companies'
    rows, last = parse.companies(fetch('appk', url))
    for page in range(2, last + 1):
        more, page_count = parse.companies(fetch('appk', url + f'?searchType=company&page={page}'))
        if page_count != last:
            raise ShapeError('enterprise pagination changed during crawl')
        rows.extend(more)
    store.reconcile(rows, len(rows))
    if sum(r['status'] == 'Активно' for r in rows) != counters['enterprises']:
        raise ShapeError('active enterprise counter mismatch')
    url = parse.APPK + '/Public/Public/Ministries'
    ministries, mlast = parse.ministries(fetch('appk', url))
    for page in range(2, mlast + 1):
        more, page_count = parse.ministries(fetch('appk', url + f'?page={page}'))
        if page_count != mlast:
            raise ShapeError('ministry pagination changed during crawl')
        ministries.extend(more)
    store.reconcile(ministries, len(ministries))
    if sum(r['status'] == 'Активно' for r in ministries) != counters['ministries']:
        raise ShapeError('active ministry counter mismatch')
    return (rows, dict(pages=last, source_count=counters['enterprises']),
            ministries, dict(pages=mlast, source_count=counters['ministries']))


def read_ncr_catalogue(fetch):
    """The search with empty fields (a POST), then the session's pages; every page must carry the same total."""
    fetch('ncr', parse.NCR + '/Concessions')
    url = parse.NCR + '/Concessions/Search'
    rows, total, last = parse.concessions(fetch('ncr', url, dict.fromkeys(NCR_FIELDS, '')))
    for page in range(2, last + 1):
        more, count, page_count = parse.concessions(fetch('ncr', url + f'?page={page}'))
        if count != total or page_count != last:
            raise ShapeError('NCR counter/pagination changed mid-pagination')
        rows.extend(more)
    store.reconcile(rows, total)
    return rows, dict(pages=last, source_count=total)


def read_enterprise(fetch, row):
    """The profile of one enterprise, every page of its report links, and each report."""
    url = row['source_url']
    data = parse.profile(fetch('appk', url))
    links = set(data.pop('report_links'))
    waiting = data.pop('report_pages')
    seen = {url}
    while waiting:
        page = waiting.pop()
        if page in seen:
            continue
        seen.add(page)
        part = parse.profile(fetch('appk', page))
        links.update(part['report_links'])
        waiting.extend(p for p in part['report_pages'] if p not in seen)
    reports = []
    for link in sorted(links):
        report = parse.report(fetch('appk', link), link)
        report['appk_id'] = row['id']
        reports.append(report)
    data.update(id=row['id'], source_url=url, reports=len(reports))
    return data, reports


def read_concession(fetch, row):
    """The page of one concession and each notice it links."""
    data = parse.concession_detail(fetch('ncr', row['source_url']))
    notices = []
    for link in data.pop('notice_links'):
        notice = parse.assigned_notice(fetch('ncr', link))
        notice['source_url'] = link
        notices.append(notice)
    data.update(id=row['id'], source_url=row['source_url'], notices=notices)
    return data


# ---------------------------------------------------------------- live reads

def fetcher(conn, r, client, resume=False):
    """fetch() of a live read: every answer is kept under its sha256 and recorded as a fetch of this run. With resume,
    an answer to the same address kept within a day is used again (an interrupted backfill only)."""
    def fetch(source, url, data=None, keep=False):
        if client.deadline:
            client.deadline.require()
        if resume and data is None:
            found = conn.execute('''SELECT f.raw_file_id, f.sha256, f.path FROM ops.fetch x JOIN ops.raw_file f USING (raw_file_id)
                WHERE x.tool = %s AND x.source = %s AND x.ref = %s AND x.fetched_at > now() - interval '1 day'
                ORDER BY x.fetched_at DESC LIMIT 1''', (TOOL, source, url)).fetchone()
            if found:
                r.count('reused')
                raw = pipeline.read_raw(conn, found[0])
                return (raw, pipeline.RawFile(*found)) if keep else raw
        raw = client.get(url, data)
        file = pipeline.save_raw(conn, r, source, url, raw, store.RAW, name=url.rsplit('/', 1)[-1][-60:])
        return (raw, file) if keep else raw
    return fetch


def refused(conn, r, source, ref, read):
    """A catalogue whose answer does not hold together is recorded as refused, and the step stops."""
    try:
        return read()
    except ShapeError as e:
        pipeline.reject(conn, r, source, ref, e)
        raise


def appk(conn, r, fetch):
    rows, manifest, ministries, mmanifest = refused(conn, r, 'appk', 'catalogue', lambda: read_appk_catalogue(fetch))
    with conn.transaction():   # both lists or neither
        store.load_catalogue(conn, r, 'appk', 'ministries', ministries, mmanifest)
        loaded = store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest)
    return dict(status=loaded.status, rows=len(rows), active=manifest['source_count'], ministries=len(ministries),
                active_ministries=mmanifest['source_count'], pages=manifest['pages'], ministry_pages=mmanifest['pages'])


def ncr(conn, r, fetch):
    rows, manifest = refused(conn, r, 'ncr', 'catalogue', lambda: read_ncr_catalogue(fetch))
    loaded = store.load_catalogue(conn, r, 'ncr', 'catalogue', rows, manifest)
    return dict(status=loaded.status, rows=len(rows), source_count=manifest['source_count'], pages=manifest['pages'])


DETAILS = {
    'appk': ('appk-details', 'appk-full', 'Профилите и отчетните метаданни'),
    'ncr': ('ncr-details', 'ncr-full', 'Партидите и обявленията'),
}


def details(conn, r, fetch, source, limit=None, continue_pending=False):
    """Every enterprise (or concession) of the current catalogue, a checkpoint after each. One whose answer is refused
    is recorded and the pass goes on; the pass counts as complete only when none of its reads was refused."""
    entities, full, _label = DETAILS[source]
    queue = Queue(source, 'details', store.catalogue(conn, source), continue_pending)
    done = queue.done
    for row in queue.rows[done:]:
        if limit is not None and done >= limit:
            break
        try:
            if source == 'appk':
                store.load_enterprise(conn, r, *read_enterprise(fetch, row))
            else:
                store.load_concession(conn, r, read_concession(fetch, row))
        except (ShapeError, pipeline.CorruptRaw) as e:
            pipeline.reject(conn, r, entities, row['id'], e)
        queue.advance()
        done = queue.done
        print(json.dumps(dict(progress=entities, completed=done, total=len(queue.rows))), flush=True)
    result = dict(completed=done, total=len(queue.rows), partial=done != len(queue.rows))
    if done == len(queue.rows):
        refused = store.rejected_since(conn, entities, queue.data['started_at'])
        if refused:
            result['refused'] = refused
        else:
            store.load_full(conn, r, full, 'details', queue.data['started_at'], done)
        queue.complete()
    return result


def refresh(conn, r, fetch, continue_pending=False):
    continuing = continue_pending and (pending('appk', 'details') or pending('ncr', 'details'))
    result = {} if continuing else dict(appk=appk(conn, r, fetch), ncr=ncr(conn, r, fetch))
    if not continuing or pending('appk', 'details'):
        result['appk_details'] = details(conn, r, fetch, 'appk', continue_pending=continue_pending)
    result['ncr_details'] = details(conn, r, fetch, 'ncr', continue_pending=continue_pending)
    return result


def finish(conn, r):
    """The clean tables, the rules, and the publication when every gating rule holds."""
    same, n = store.build(conn, r)
    pipeline.record_check(conn, r, 'landing_keeps_the_record', n, same)
    failed = pipeline.run_checks(conn, r)
    return failed or pipeline.publish(conn, r, ROOT).problems


# ---------------------------------------------------------------- freshness and the summary

def freshness(conn):
    problems = pipeline.freshness(conn, TOOL) + store.source_problems(conn)
    for source, (_e, _f, label) in DETAILS.items():
        if pending(source, 'details'):
            problems.append(f'{source}: профили: partial: {label} още не са прочетени докрай')
        if pending(source, 'pdfs'):
            problems.append(f'{source}: файлови оригинали: partial: Файловите оригинали още не са прочетени докрай')
    return problems


def summary(conn):
    def one(sql, *params):
        return conn.execute(sql, params).fetchone()[0]
    counts = {
        'Предприятия': one('SELECT count(*) FROM sobstvenost.enterprise') if one(
            "SELECT count(*) FROM ops.load WHERE tool = %s AND source = 'appk' AND is_current", TOOL) else None,
        'Концесии, уникални партиди': one('SELECT count(*) FROM sobstvenost.concession') if one(
            "SELECT count(*) FROM ops.load WHERE tool = %s AND source = 'ncr' AND is_current", TOOL) else None,
        'Прочетени профили': one('SELECT count(*) FROM sobstvenost.profile') + one('SELECT count(*) FROM sobstvenost.concession_profile'),
        'Отчетни метаданни': one('SELECT count(*) FROM sobstvenost.report'),
        'Архивирани PDF версии': one("SELECT count(*) FROM sobstvenost.attachment WHERE media_type = 'application/pdf'"),
        'Други архивирани файлови версии': one("SELECT count(*) FROM sobstvenost.attachment WHERE media_type <> 'application/pdf'"),
    }
    last_ok = {s: str(t) if t else None for s, t in conn.execute(
        "SELECT source, last_success FROM sobstvenost.source_state WHERE scope = 'details' ORDER BY source")}
    return dict(tool='Държавна собственост', counts=counts, last_ok=last_ok,
                changes=one("SELECT count(*) FROM ops.change_log WHERE tool = %s AND detected_at > now() - interval '7 days'", TOOL),
                held=one('SELECT count(*) FROM ops.hold WHERE tool = %s', TOOL), problems=freshness(conn))


# ---------------------------------------------------------------- the command

def safe_budget(requested, now=None):
    """The seconds a heavy read may take, counted after the shared lock: never into 05:45-10:15 or 17:45-22:15 UTC."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cuts = [now.replace(hour=5, minute=45, second=0, microsecond=0), now.replace(hour=17, minute=45, second=0, microsecond=0)]
    cuts.append(cuts[0] + dt.timedelta(days=1))
    safe = int((min(cut for cut in cuts if cut > now) - now).total_seconds())
    minute = now.hour * 60 + now.minute
    if 345 <= minute < 615 or 1065 <= minute < 1335:
        safe = 0
    return min(requested, safe) if requested is not None else safe


STEPS = ['migrate', 'appk', 'ncr', 'appk-details', 'ncr-details', 'refresh', 'pdfs', 'build', 'check', 'replay', 'parity',
         'freshness', 'summary', 'municipal']


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', required=True, choices=STEPS)
    p.add_argument('--resume', action='store_true', help='use answers kept within a day again (an interrupted backfill only)')
    p.add_argument('--limit', type=int, help='at most this many profiles; the step then reports an incomplete read')
    p.add_argument('--budget-seconds', type=int)
    p.add_argument('--continue-pending', action='store_true')
    p.add_argument('--pending-only', action='store_true')
    a = p.parse_args(argv)
    if a.budget_seconds is not None and a.budget_seconds <= 0:
        p.error('budget must be positive')
    if a.pending_only:
        if a.step != 'pdfs':
            p.error('pending-only is supported only for attachment archive')
        if not (pending('appk', 'pdfs') or pending('ncr', 'pdfs')):
            return pipeline.report(dict(skipped=True, reason='no pending attachment checkpoint'))
    if a.step == 'municipal':
        return pipeline.report(dict(step='municipal', problems=[
            'municipal dataset inventory and individual reuse permissions are not yet validated; no fabricated import']))
    with pipeline.connect(DSN) as conn:
        if a.step == 'migrate':
            pipeline.require_core(conn, '0007')
            return pipeline.report({'migrations': pipeline.migrate(conn, TOOL, ROOT)})
        if a.step == 'freshness':
            return pipeline.report({'step': 'freshness', 'problems': freshness(conn)})
        if a.step == 'summary':
            return pipeline.report(summary(conn))
        client = None
        try:
            with pipeline.run(conn, TOOL, a.step, params={k: v for k, v in vars(a).items() if v not in (None, False)},
                              sha=pipeline.code_sha(ROOT)) as r:
                problems, extra = [], {}
                if a.step in ('appk', 'ncr', 'appk-details', 'ncr-details', 'refresh', 'pdfs'):
                    client = Client(deadline=Deadline(safe_budget(a.budget_seconds)))
                    fetch = fetcher(conn, r, client, a.resume)
                    try:
                        if a.step == 'refresh':
                            extra = refresh(conn, r, fetch, a.continue_pending)
                        elif a.step == 'appk':
                            extra = appk(conn, r, fetch)
                        elif a.step == 'ncr':
                            extra = ncr(conn, r, fetch)
                        elif a.step == 'pdfs':
                            from . import documents
                            extra, problems = documents.archive(conn, r, lambda s, u: fetch(s, u, keep=True), a.continue_pending)
                        else:
                            extra = details(conn, r, fetch, a.step.split('-')[0], a.limit, a.continue_pending)
                    except BudgetReached:
                        extra = dict(pending=True, reason='budget', step=a.step, **({'read': extra} if extra else {}))
                    for part in [extra, *[v for v in extra.values() if isinstance(v, dict)]]:
                        if part.get('refused'):
                            problems.append(f"{len(part['refused'])} отказани записа: {', '.join(part['refused'][:10])}")
                        if part.get('partial') and a.limit is not None:
                            problems.append('--limit: прочитът не е пълен')
                elif a.step == 'replay':
                    from .move import replay
                    problems = replay(conn, r)
                if a.step == 'parity':
                    from .move import parity
                    parity(conn, r)
                else:
                    problems += finish(conn, r)
                result = {'step': a.step, 'stats': r.stats, **extra, 'problems': problems}
        except pipeline.Busy as e:
            result = {'step': a.step, 'status': 'skipped', 'note': str(e)}
        except Exception as e:   # the run is recorded as failed; the report says why
            result = {'step': a.step, 'problems': [str(e)]}
        finally:
            if client:
                client.session.close()
        if a.step == 'parity' and 'stats' in result:
            result['problems'] = [f'{title}: старо {e}, ново {n}' for title, e, n in conn.execute('''SELECT title_bg, expected, actual
                FROM ops.check_latest WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name''', (TOOL,))]
    return pipeline.report(result)


if __name__ == '__main__':
    sys.exit(main())

"""The steps of Одити и контрол: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    nao        the five lists of the National Audit Office; adfi: the two registers of АДФИ; cpc: the decisions of КЗК
    refresh    all eight lists
    documents  the documents of the records (--source cpc for КЗК); --limit and --max-seconds bound the work
    build      the clean tables again from the current reads
    check      every quality rule, then the publication
    replay     the reads and documents of the old database, from its raw files and through the same parser (the move);
               --source replays one list
    parity     the old database of its own against the shared one
    freshness  the list of problems, empty when all is well
    summary    the weekly report

Ingest and serving are apart. The steps fill kontrol_src and kontrol; the pages read pub_kontrol, which changes only by
a publication at the end of a step whose rules all hold. A read of a list is complete or it is not stored.
"""
import argparse
import json
import sys

from . import pipeline, sources, store
from .config import DSN, ROOT, SOURCES, TOOL
from .http import Client
from .parse import ShapeError


def collect(source, client, save, progress):
    if source.startswith('nao') or source.startswith('recommendations'):
        return sources.nao(client, source, save, progress)
    if source.startswith('adfi'):
        return sources.adfi(client, source, save, progress)
    return sources.cpc(client, save, progress)


def selected(step, source=None):
    if source:
        return [source]
    return [s for s in SOURCES if step == 'refresh' or step == 'nao' and s.startswith(('nao', 'recommendations'))
            or step == 'adfi' and s.startswith('adfi') or step == 'cpc' and s == 'cpc']


def read(conn, r, client, names):
    """Each list in full; a list that fails is recorded and the others go on."""
    problems = []

    def progress(source, year, page, count):
        print(json.dumps(dict(source=source, year=year, pages=page, rows=count, status='наваксване, още не е публикувано'),
                         ensure_ascii=False), flush=True)
    def save(src, url, data):
        pipeline.save_raw(conn, r, src, url, data, store.RAW, name=url.rsplit('/', 1)[-1][-60:])
    for source in names:
        try:
            rows, manifest = collect(source, client, save, progress)
            if store.load(conn, r, source, rows, manifest).status == 'held':
                problems.append(source + ': задържан до второ четене')
        except Exception as e:
            pipeline.reject(conn, r, source, SOURCES[source][1], e)
            problems.append(source + ': ' + str(e))
            print(json.dumps(dict(source=source, error=str(e)), ensure_ascii=False), flush=True)
    return problems


def finish(conn, r):
    """The clean tables, the rules, and the publication when every gating rule holds."""
    same, n = store.build(conn, r)
    pipeline.record_check(conn, r, 'landing_keeps_the_record', n, same)
    failed = pipeline.run_checks(conn, r)
    return failed or pipeline.publish(conn, r, ROOT).problems


def source_problems(conn):
    """A list whose last read failed or is held, in the words of the sources page."""
    return [f'{label}: {status}' + (f': {error}' if error else '') for label, status, error in conn.execute(
        "SELECT label, status, error FROM kontrol.source_state WHERE status NOT IN ('наред') ORDER BY source")]


def freshness(conn):
    return pipeline.freshness(conn, TOOL) + [p for p in source_problems(conn) if 'Няма доказан' not in p]


def summary(conn):
    from .documents import pending
    counts = {SOURCES[s][0]: n for s, n in conn.execute('''SELECT r.source, r.unique_count FROM kontrol_src.read r
        JOIN ops.load l USING (load_id) WHERE l.tool = %s AND l.is_current ORDER BY r.source''', (TOOL,))}
    counts['Документи: чакащи архивиране'] = pending(conn)
    return dict(tool='Одити и контрол', counts=counts,
                last_ok={s: str(t) if t else None for s, t in conn.execute(
                    "SELECT source, last_ok_at FROM ops.freshness WHERE tool = %s AND source <> 'document' ORDER BY source", (TOOL,))},
                changes=conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = %s AND detected_at >= now() - interval '7 days'",
                                     (TOOL,)).fetchone()[0],
                held=conn.execute('SELECT count(*) FROM ops.hold WHERE tool = %s', (TOOL,)).fetchone()[0],
                problems=freshness(conn))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', required=True, choices=['migrate', 'refresh', 'nao', 'adfi', 'cpc', 'documents', 'build', 'check',
                                                     'replay', 'parity', 'freshness', 'summary'])
    p.add_argument('--source', choices=list(SOURCES))
    p.add_argument('--limit', type=int, help='Explicit document work budget; pending work is reported as incomplete')
    p.add_argument('--max-seconds', type=int, help='Document runtime checkpoint; pending work remains visible')
    a = p.parse_args(argv)
    if a.max_seconds is not None and (a.max_seconds <= 0 or a.step != 'documents'):
        p.error('--max-seconds requires documents and a positive budget')
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
            with pipeline.run(conn, TOOL, a.step, params={'source': a.source, 'limit': a.limit, 'max_seconds': a.max_seconds},
                              sha=pipeline.code_sha(ROOT)) as r:
                problems, extra = [], {}
                if a.step in ('refresh', 'nao', 'adfi', 'cpc'):
                    client = Client()
                    problems = read(conn, r, client, selected(a.step, a.source))
                elif a.step == 'documents':
                    from .documents import refresh
                    client = Client()
                    extra = refresh(conn, r, client, a.source, a.limit, a.max_seconds)
                    problems = extra.pop('problems')
                elif a.step == 'replay':
                    from .move import replay
                    problems = replay(conn, r, only=a.source)
                if a.step == 'parity':
                    from .move import parity
                    parity(conn, r)
                else:
                    problems += finish(conn, r)
                result = {'step': a.step, 'stats': r.stats, **extra, 'problems': problems}
        except pipeline.Busy as e:
            result = {'step': a.step, 'status': 'skipped', 'note': str(e)}
        except (ShapeError, pipeline.CorruptRaw) as e:
            result = {'step': a.step, 'problems': [str(e)]}
        finally:
            if client:
                client.close()
        if a.step == 'parity' and 'stats' in result:
            result['problems'] = [f'{title}: старо {e}, ново {n}' for title, e, n in conn.execute('''SELECT title_bg, expected, actual
                FROM ops.check_latest WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name''', (TOOL,))]
    return pipeline.report(result)


if __name__ == '__main__':
    sys.exit(main())

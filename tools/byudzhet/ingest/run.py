"""The steps of Бюджет: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    refresh    the catalogues of the five datasets of МФ and every resource in them, into the landing zone and the
               clean tables; then the rules and the publication
    grao       nothing any more: the registrations per municipality are Население's (kept for the n8n line)
    build      the clean tables again from the landing zone, every version in its order
    replay     the resources the old database has, from its raw files and through the same parser (the move)
    parity     the old database of its own against the shared one, number by number
    check      every quality rule, then the publication
    freshness  the list of problems, empty when all is well; the sources page gets it with a publication

Ingest and serving are apart. The steps fill byudzhet_src and byudzhet; the pages read pub_byudzhet (and the
registrations in pub_naselenie), which change only by a publication at the end of a step whose rules all hold.
"""
import argparse
import json
from decimal import Decimal

import psycopg

from . import parse, pipeline, reference, store
from .config import DSN, OLD_DSN, RAW, ROOT, SETS, TOOL
from .http import Client
from .parse import ShapeError, period, table

API = 'https://data.egov.bg/api/'


def sync_gaps(conn):
    """db/ref/source_gaps.json: the resources whose invalid bytes were checked by hand (byudzhet.known_gap)."""
    gaps = json.loads((ROOT / 'db/ref/source_gaps.json').read_text(encoding='utf-8'))
    with conn.transaction():
        conn.execute('DELETE FROM byudzhet.known_gap')
        for uri, g in gaps.items():
            conn.execute('INSERT INTO byudzhet.known_gap (uri, sha256, kind, name, reason) VALUES (%s,%s,%s,%s,%s)',
                         (uri, g['sha256'], g['kind'], g['name'], g['reason']))
    return {(u, g['sha256']) for u, g in gaps.items()}


def resources(client, ds, save=lambda raw, page: None):
    """The catalogue of one dataset; save(raw, page) stores every page raw and its result of the last page is returned
    with the list. A changed or cut catalogue stops before anything is loaded."""
    out, page, files = [], 1, []
    while True:
        raw = client.api('listResources', dict(criteria={'dataset_uri': ds}, records_per_page=100, page_number=page))
        files.append(save(raw, page))
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ShapeError('Каталогът не е JSON') from e
        if set(d) != {'success', 'resources', 'total_records'} or d['success'] is not True or not isinstance(d['resources'], list):
            raise ShapeError('Променен каталог')
        out += d['resources']
        if len(out) >= int(d['total_records']):
            break
        if not d['resources']:
            raise ShapeError('Отрязан каталог')
        page += 1
    if len(out) != int(d['total_records']) or len({x['uri'] for x in out}) != len(out):
        raise ShapeError('Невалиден брой ресурси')
    for x in out:
        if not {'uri', 'name', 'version', 'updated_at'} <= x.keys() or not all(isinstance(x[k], str) for k in ('uri', 'name')):
            raise ShapeError('Променени полета на ресурс')
    return out, files[-1]


def write_catalog(conn, load_id, rows):
    with conn.cursor() as cur:
        cur.executemany('''INSERT INTO byudzhet_src.egov_catalog (load_id, row_no, uri, name, version, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)''', [(load_id, i, uri, x['name'], x['version'], x['updated_at'])
                                            for i, (uri, x) in enumerate(sorted(rows.items()))])


def read_catalog(conn, load_id):
    return {u: dict(name=n, version=v, updated_at=t) for u, n, v, t in conn.execute(
        'SELECT uri, name, version, updated_at FROM byudzhet_src.egov_catalog WHERE load_id = %s', (load_id,))}


def load_resource(conn, r, kind, meta, raw, raw_file_id, now=None):
    """Parse one resource and load it. Raises ShapeError before anything is written when the table is not sound."""
    cells = table(raw)
    data = parse.parse_rows(cells, kind, meta['name'])
    return pipeline.load(conn, r, kind, meta['uri'], store.rows(data), store.writer(data, meta, cells, now),
                         raw_file_id=raw_file_id, current=store.current, hold_when=store.hold_when, now=now)


def refresh(conn, r, client, latest_only=False):
    problems, gaps = [], sync_gaps(conn)
    for kind, ds in SETS.items():
        rs, catalog_file = resources(client, ds, lambda raw, page: pipeline.save_raw(
            conn, r, 'egov-catalog', ds, raw, RAW, name=f'{ds}-{page}.json', media_type='application/json'))
        pipeline.load(conn, r, 'egov-catalog', ds, {x['uri']: {k: x[k] for k in ('name', 'version', 'updated_at')} for x in rs},
                      write_catalog, raw_file_id=catalog_file.raw_file_id, current=read_catalog)
        known = {x for (x,) in conn.execute('SELECT DISTINCT ref FROM ops.load WHERE tool = %s AND source = %s', (TOOL, kind))}
        problems += [f'{kind}: липсва {ref}' for ref in sorted(known - {x['uri'] for x in rs})]
        if latest_only:
            rs = sorted(rs, key=lambda x: period(x['name']), reverse=True)[:1]
        for x in sorted(rs, key=lambda x: period(x['name'])):
            file = None
            try:
                raw = client.api('getResourceData', {'resource_uri': x['uri']})
                file = pipeline.save_raw(conn, r, kind, x['uri'], raw, RAW, name=x['uri'] + '.json', media_type='application/json')
                load_resource(conn, r, kind, x, raw, file.raw_file_id)
            except ShapeError as e:
                pipeline.reject(conn, r, kind, x['uri'], e, file.raw_file_id if file else None)
                if not file or (x['uri'], file.sha256) not in gaps:
                    problems.append(f"{kind}: {x['name']}: {e}")
    return problems


def rebuild(conn, r):
    """The clean tables again from the landing zone: every accepted version of every resource in the order it was read,
    so the history (superseded_at) comes out as it was. One transaction; a full rebuild may delete the clean rows."""
    with conn.transaction():
        for t in ('state_line', 'kfp_value', 'reserve_line', 'municipal_value', 'report'):
            conn.execute(f'DELETE FROM byudzhet.{t}')
        for load_id, uri, version, at in conn.execute('''SELECT l.load_id, f.uri, f.version, l.loaded_at FROM ops.load l
                JOIN byudzhet_src.egov_file f USING (load_id) WHERE l.tool = %s AND l.status IN ('loaded', 'confirmed')
                ORDER BY l.loaded_at, l.load_id''', (TOOL,)).fetchall():
            store.build(conn, load_id, store.parsed(conn, load_id), uri, version, at)
            r.count('reports')


def replay(conn, r, old_dsn=OLD_DSN, old=('src', 'ops')):
    """Load the resources the old database holds, from their raw files and through the same parser, at the time the old
    database first fetched those bytes. Nothing is copied from its tables except the catalogue (it kept no raw file
    of it) and its log of events."""
    gaps = sync_gaps(conn)
    src, ops = old
    with psycopg.connect(old_dsn, autocommit=True) as o:
        o.execute('SET default_transaction_read_only = on')
        res = o.execute(f'''SELECT s.ref, s.kind, s.name, s.version, s.metadata, s.status, f.path, f.sha256, f.fetched_at
            FROM {src}.resource s JOIN {ops}.raw_file f ON f.ref = s.ref AND f.sha256 = s.sha256
            WHERE s.kind <> 'population' ORDER BY f.fetched_at, s.ref''').fetchall()
        log = o.execute(f'''SELECT detected_at, source, ref, field, old, new, cause FROM {ops}.change_log
            WHERE cause <> 'new-record' AND source <> 'population' ORDER BY id''').fetchall()
        first = o.execute(f"SELECT min(fetched_at) FROM {ops}.raw_file WHERE source <> 'population'").fetchone()[0]
    problems = []
    for kind, ds in SETS.items():
        cat = {x[0]: dict(name=x[4].get('name', x[2]), version=str(x[4].get('version', x[3])), updated_at=x[4].get('updated_at'))
               for x in res if x[1] == kind and x[5] != 'липсва при източника'}
        pipeline.load(conn, r, 'egov-catalog', ds, cat, write_catalog, current=read_catalog, now=first)
    for ref, kind, name, version, meta, status, path, sha, fetched_at in res:
        file, raw = pipeline.register_raw(conn, r, kind, ref, path, media_type='application/json', fetched_at=fetched_at, expect_sha=sha)
        m = dict(uri=ref, name=meta.get('name', name), version=str(version), updated_at=meta.get('updated_at'))
        try:
            load_resource(conn, r, kind, m, raw, file.raw_file_id, now=fetched_at)
        except ShapeError as e:
            pipeline.reject(conn, r, kind, ref, e, file.raw_file_id, now=fetched_at)
            if (ref, sha) not in gaps:
                problems.append(f'{kind}: {name}: {e}')
    if not conn.execute("SELECT 1 FROM ops.change_log WHERE tool = %s AND cause IN ('rewritten', 'held', 'confirmed') LIMIT 1", (TOOL,)).fetchone():
        for detected_at, source, ref_, field, old_value, new_value, cause in log:
            pipeline.log_change(conn, r, source, ref_, field, old_value, new_value, cause, detected_at)
    r.stats['resources'] = len(res)
    return problems


def _norm(doc):
    """The rows of a report document in a comparable shape: what the pages show, original and euro."""
    def z(x):   # PostgreSQL has no negative zero: the source's "-0.0" is 0.0 in the shared database
        return x[1:] if x and x.startswith('-') and Decimal(x) == 0 else x

    def m(v):
        return (z(v['original']), v['unit'], z(v['eur'])) if v else None
    k = doc['kind']
    if k == 'state':
        return [(x['line'], m(x['law']), m(x['actual']), x['pct']) for x in doc['rows']]
    if k == 'kfp':
        # the old database kept a row as a JSON object, which orders its keys by length; the order of the columns
        # is the source's now, so the items are compared as a set
        return [(x['budget_type'], sorted((i, m(v)) for i, v in x['values'].items())) for x in doc['rows']]
    if k == 'reserve':
        return [(x['line'], m(x['value'])) for x in doc['rows']]
    return sorted((x['code'], sorted((i, m(v)) for i, v in x['values'].items())) for x in doc['rows'])


def _values(doc):
    k = doc['kind']
    if k == 'state':
        return [v for x in doc['rows'] for v in (x['law'], x['actual'])]
    if k == 'reserve':
        return [x['value'] for x in doc['rows']]
    return [v for x in doc['rows'] for v in x['values'].values()]


def parity(conn, r, old_dsn=OLD_DSN, old_live='live'):
    """The old database (JSON documents) against the shared one (the serving layer, read the way the pages read it):
    every report, every number, the euro to the last digit. Each comparison is a result of a rule of kind `parity`."""
    from app.queries import documents
    with psycopg.connect(old_dsn, autocommit=True) as o:
        o.execute('SET default_transaction_read_only = on')
        old_docs = [p for (p,) in o.execute(f'SELECT payload FROM {old_live}.snapshot ORDER BY period, ref')]
    new_docs = documents(conn)
    t, n = dict(reports=0, rows=0, orig=Decimal(0), eur=Decimal(0)), dict(reports=0, rows=0, orig=Decimal(0), eur=Decimal(0))
    for side, docs in ((t, old_docs), (n, new_docs)):
        for d in docs:
            if d['kind'] == 'population':
                continue
            side['reports'] += 1
            side['rows'] += len(d['rows'])
            for v in _values(d):
                side['orig'] += Decimal(v['original'] or 0)
                side['eur'] += Decimal(v['eur'] or 0)
    by = {(d['kind'], d['period']): d for d in new_docs}
    differ, detail = 0, []
    for d in old_docs:
        if d['kind'] == 'population':
            continue
        other = by.get((d['kind'], d['period']))
        if other is None or _norm(d) != _norm(other) or d['url'] != other['url'] or d.get('reconciliation') != other.get('reconciliation'):
            differ += 1
            if other is None:
                detail.append(f"{d['kind']} {d['period']}: липсва")
            else:
                first = next(((a, b) for a, b in zip(_norm(d), _norm(other)) if a != b), (len(d['rows']), len(other['rows'])))
                detail.append(f"{d['kind']} {d['period']}: {str(first)[:400]}")
    old_pop = {d['period']: sorted((x['code'], x['permanent'], x['current']) for x in d['rows']) for d in old_docs if d['kind'] == 'population'}
    new_pop = {d['period']: sorted((x['code'], x['permanent'], x['current']) for x in d['rows']) for d in new_docs if d['kind'] == 'population'}
    pop_differ = [p for p in old_pop if p in new_pop and old_pop[p] != new_pop[p]]
    pipeline.record_check(conn, r, 'parity_reports', t['reports'], n['reports'])
    pipeline.record_check(conn, r, 'parity_rows', t['rows'], n['rows'])
    pipeline.record_check(conn, r, 'parity_original_sum', t['orig'], n['orig'])
    pipeline.record_check(conn, r, 'parity_eur_sum', round(t['eur'], 2), round(n['eur'], 2))
    pipeline.record_check(conn, r, 'parity_rows_differ', 0, differ, '; '.join(detail)[:2000] or None)
    pipeline.record_check(conn, r, 'parity_population_dates', len(old_pop), len(set(old_pop) & set(new_pop)),
                          'нови дати: ' + ', '.join(sorted(set(new_pop) - set(old_pop)))[:1900])
    pipeline.record_check(conn, r, 'parity_population_differ', 0, len(pop_differ), ', '.join(pop_differ)[:2000] or None)
    r.stats.update(old={k: str(v) for k, v in t.items()}, new={k: str(v) for k, v in n.items()}, rows_differ=differ,
                   population=dict(old=len(old_pop), new=len(new_pop), differ=len(pop_differ)))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', choices=['migrate', 'refresh', 'grao', 'build', 'replay', 'parity', 'check', 'freshness'], required=True)
    p.add_argument('--latest-only', action='store_true')
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == 'migrate':
            pipeline.require_core(conn, '0007')
            result = {'migrations': pipeline.migrate(conn, TOOL, ROOT)}
        elif a.step == 'grao':
            result = {'step': 'grao', 'note': 'регистрациите по общини са на Население (pub_naselenie); Бюджет не чете ГРАО', 'problems': []}
        else:
            client = None
            try:
                with pipeline.run(conn, TOOL, a.step, params={'latest_only': a.latest_only}, sha=pipeline.code_sha(ROOT)) as r:
                    problems = []
                    if a.step in ('refresh', 'build', 'replay'):
                        parse.use(reference.load(conn))
                    if a.step == 'refresh':
                        client = Client()
                        problems = refresh(conn, r, client, a.latest_only)
                    elif a.step == 'build':
                        rebuild(conn, r)
                    elif a.step == 'replay':
                        problems = replay(conn, r)
                    elif a.step == 'parity':
                        parity(conn, r)
                    if a.step == 'freshness':
                        sync_gaps(conn)
                        pipeline.run_checks(conn, r, ['drop'])
                        problems = pipeline.freshness(conn, TOOL) + [f'{title}: {actual}' for title, actual in conn.execute(
                            '''SELECT title_bg, actual FROM ops.check_latest WHERE tool = %s AND kind = 'drop' AND NOT gating
                               AND ok = false ORDER BY name''', (TOOL,))]
                        # the sources page shows this list as of the last publication
                        pipeline.publish(conn, r, ROOT)
                    elif a.step != 'parity':
                        failed = pipeline.run_checks(conn, r)
                        # The serving layer follows only when every rule holds. A resource that one read refused
                        # is not a reason to hold the rest: what is stored is still sound.
                        problems += failed or pipeline.publish(conn, r, ROOT).problems
                    result = {'step': a.step, 'stats': r.stats, 'problems': problems}
            except pipeline.Busy as e:
                result = {'step': a.step, 'status': 'skipped', 'note': str(e)}
            except (ShapeError, pipeline.CorruptRaw) as e:
                result = {'step': a.step, 'problems': [str(e)]}
            finally:
                if client:
                    client.close()
            if a.step == 'parity' and 'stats' in result:
                bad = conn.execute('''SELECT title_bg, expected, actual FROM ops.check_latest
                    WHERE tool = %s AND kind = 'parity' AND gating AND ok = false ORDER BY name''', (TOOL,)).fetchall()
                result['problems'] = [f'{title}: старо {e}, ново {n}' for title, e, n in bad]
    return pipeline.report(result)


if __name__ == '__main__':
    raise SystemExit(main())

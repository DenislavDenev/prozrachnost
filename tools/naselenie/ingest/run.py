"""The steps of Население: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    grao       the catalogue of ГРАО and every table it links, into the landing zone and the clean tables
    build      the clean tables again from the landing zone (after the places of core changed)
    replay     the tables already in raw/, as the old database published them (the move to the shared database)
    parity     the old database of its own against the shared one, number by number
    check      every quality rule
    freshness  the list of problems, empty when all is well

Ingest and serving are apart. The steps above fill naselenie_src and naselenie; the pages read pub_naselenie,
which changes only by a publication at the end of a step whose rules all hold (db/publish/*.sql).
"""
import argparse
import datetime as dt

import psycopg

from . import http, pipeline, reference, store
from .config import DSN, OLD_DSN, RAW, ROOT, TOOL
from .parse import ShapeError, catalog, parse

INDEX = 'https://www.grao.bg/tables.html'
OLD_KIND = {'municipalities': 'monthly', 'places': 'detailed'}


def write_catalog(conn, load_id, rows):
    with conn.cursor().copy('COPY naselenie_src.grao_catalog (load_id, url) FROM STDIN') as copy:
        for url in rows:
            copy.write_row((load_id, url))


def read_catalog(conn, load_id):
    return {u: {'url': u} for (u,) in conn.execute('SELECT url FROM naselenie_src.grao_catalog WHERE load_id = %s', (load_id,))}


def load_table(conn, r, ref, url, raw, raw_file_id, today=None, now=None):
    """Parse, match and load one table. Raises ShapeError before anything is written when the table is not sound."""
    data = reference.enrich(parse(raw, today), ref)
    if len(data['municipalities']) < 260:
        raise ShapeError('Непълен национален файл')
    # Old places keep the names of the source; the current EKATTE cannot establish a historic identity.
    recent = ((today or dt.date.today()) - dt.date.fromisoformat(data['date'])).days < 120
    if data['kind'] == 'places' and recent and data['unmatched'] / len(data['places']) > 0.005:
        raise ShapeError('Над 0,5% несъпоставени редове с ЕКАТТЕ')
    return pipeline.load(conn, r, 'grao', url, store.rows(data), store.writer(data, now), raw_file_id=raw_file_id,
                         current=store.current, now=now)


def refresh(conn, r, get=http.get, backfill=False, today=None):
    problems = []
    ref = reference.load(conn)
    raw = get(INDEX)
    file = pipeline.save_raw(conn, r, 'grao-catalog', INDEX, raw, RAW, name='tables.html', media_type='text/html')
    try:
        urls = catalog(raw)
    except ShapeError as e:
        pipeline.reject(conn, r, 'grao-catalog', INDEX, e, file.raw_file_id)
        raise
    pipeline.load(conn, r, 'grao-catalog', INDEX, {u: {'url': u} for u in urls}, write_catalog,
                  raw_file_id=file.raw_file_id, current=read_catalog)
    # Stage one starts in 2011. The 2010 source has inconsistent regional totals.
    urls = [u for u in urls if 'tadr' not in u or int(u[-8:-4]) >= 2011]
    current = [u for u in urls if 'tadr' not in u]
    if not backfill:
        known = {x[0] for x in conn.execute("SELECT ref FROM ops.load WHERE tool = %s AND source = 'grao' AND is_current", (TOOL,))}
        urls = [u for u in urls if u not in known or u in current]
    for url in urls:
        file = None
        try:
            raw = get(url)
            file = pipeline.save_raw(conn, r, 'grao', url, raw, RAW, name=url.rsplit('/', 1)[-1], media_type='text/plain')
            load_table(conn, r, ref, url, raw, file.raw_file_id, today)
        except Exception as e:   # one bad table does not stop the others; it is recorded and reported
            pipeline.reject(conn, r, 'grao', url, e, file.raw_file_id if file else None)
            problems.append(f'{url}: {e}')
    return problems


def rebuild(conn, r):
    """The clean tables again from the landing zone, with the places of core as they are now."""
    ref = reference.load(conn)
    loads = conn.execute('''SELECT l.load_id, l.raw_file_id FROM ops.load l
        WHERE l.tool = %s AND l.source = 'grao' AND l.is_current ORDER BY l.loaded_at, l.load_id''', (TOOL,)).fetchall()
    for load_id, raw_file_id in loads:
        data = reference.enrich(parse(pipeline.read_raw(conn, raw_file_id)), ref)
        with conn.transaction():
            store.build(conn, load_id, data)
        r.count('tables')


def replay(conn, r, old_dsn=OLD_DSN, old_ops='ops'):
    """Load the tables the old database has published, from their raw files and through the same parser, with the
    time the old database published them. Nothing is copied from its tables."""
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute('SET default_transaction_read_only = on')
        files = old.execute(f'''SELECT s.url, s.sha, f.path, f.fetched_at, s.published_at
            FROM live.source s JOIN {old_ops}.raw_file f ON f.url = s.url AND f.sha = s.sha
            WHERE s.kind <> 'reference' ORDER BY s.published_at, s.url''').fetchall()
        log = old.execute(f'''SELECT detected_at, source, ref, field, old, new, cause FROM {old_ops}.change_log
            WHERE cause <> 'new-record' ORDER BY id''').fetchall()
    ref, problems = reference.load(conn), []
    for url, sha, path, fetched_at, published_at in files:
        file, raw = pipeline.register_raw(conn, r, 'grao', url, path, media_type='text/plain', fetched_at=fetched_at,
                                          expect_sha=sha)
        try:
            load_table(conn, r, ref, url, raw, file.raw_file_id, now=published_at)
        except ShapeError as e:
            pipeline.reject(conn, r, 'grao', url, e, file.raw_file_id)
            problems.append(f'{url}: {e}')
    # the events of the old log (holds and the like); its line for every first row is not carried over
    if not conn.execute("SELECT 1 FROM ops.change_log WHERE tool = %s AND cause IN ('held', 'confirmed') LIMIT 1", (TOOL,)).fetchone():
        for detected_at, source, ref_, field, old_value, new_value, cause in log:
            pipeline.log_change(conn, r, 'grao', ref_, field, old_value, new_value, cause, detected_at)
    r.stats['files'] = len(files)
    return problems


def parity(conn, r, old_dsn=OLD_DSN):
    """The old database (JSON documents) against the shared one (tables): every published table, every number.
    Each comparison is a result of a rule of kind `parity` in ops.check_result."""
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute('SET default_transaction_read_only = on')
        sources = old.execute("SELECT url, as_of, kind, payload FROM live.source WHERE kind <> 'reference' ORDER BY as_of, url").fetchall()
    eik = dict(conn.execute('SELECT municipality_code, eik FROM core.municipality'))
    t = dict(files=0, m_rows=0, m_perm=0, m_cur=0, m_both=0, s_rows=0, s_perm=0, s_cur=0, s_both=0, sub=0)
    n = dict(t)
    row_diff, ekatte_diff, detail = 0, 0, []
    for url, as_of, kind, payload in sources:
        t['files'] += 1
        load = conn.execute("SELECT load_id FROM ops.load WHERE tool = %s AND source = 'grao' AND ref = %s AND is_current",
                            (TOOL, url)).fetchone()
        if not load:
            detail.append(f'{url}: липсва')
            row_diff += 1
            continue
        n['files'] += 1
        want = sorted((x['code'], x['permanent'], x['current'], x['both']) for x in payload['municipalities'])
        got = sorted((eik[c], p, cu, b) for c, p, cu, b in conn.execute(
            '''SELECT municipality_code, permanent, current, both_here FROM naselenie.population
               WHERE as_of = %s AND table_kind = %s AND superseded_at IS NULL''', (as_of, OLD_KIND[kind])))
        for side, rows_ in ((t, want), (n, got)):
            side['m_rows'] += len(rows_)
            for i, k in enumerate(('m_perm', 'm_cur', 'm_both')):
                side[k] += sum(x[i + 1] for x in rows_)
        if want != got:
            row_diff += len(set(want) ^ set(got)) or 1
            detail.append(f'{url}: общини')
        want = [(x['code'], x['name'], x['permanent'], x['current'], x['both'], x['ekatte']) for x in payload['places']]
        got = [(eik[c], nm, p, cu, b, e) for c, nm, p, cu, b, e in conn.execute(
            '''SELECT municipality_code, source_name, permanent, current, both_here, ekatte FROM naselenie.settlement_population
               WHERE as_of = %s AND superseded_at IS NULL AND %s = 'detailed' ORDER BY row_no''', (as_of, OLD_KIND[kind]))]
        for side, rows_ in ((t, want), (n, got)):
            side['s_rows'] += len(rows_)
            for i, k in enumerate(('s_perm', 's_cur', 's_both')):
                side[k] += sum(x[i + 2] for x in rows_)
        if [x[:5] for x in want] != [x[:5] for x in got]:
            row_diff += 1
            detail.append(f'{url}: населени места')
        else:
            changed = [(a[1], a[5], b[5]) for a, b in zip(want, got) if a[5] != b[5]]
            ekatte_diff += len(changed)
            detail += [f'{url}: {name} {a} -> {b}' for name, a, b in changed[:20]]
        t['sub'] += len(payload['subdivisions'])
        n['sub'] += conn.execute("SELECT count(*) FROM naselenie_src.grao_row WHERE load_id = %s AND row_kind = 'subdivision'",
                                 load).fetchone()[0]
    names = dict(files='parity_files', m_rows='parity_municipality_rows', m_perm='parity_municipality_permanent',
                 m_cur='parity_municipality_current', m_both='parity_municipality_both', s_rows='parity_settlement_rows',
                 s_perm='parity_settlement_permanent', s_cur='parity_settlement_current', s_both='parity_settlement_both',
                 sub='parity_subdivision_rows')
    for k, name in names.items():
        pipeline.record_check(conn, r, name, t[k], n[k])
    pipeline.record_check(conn, r, 'parity_rows_differ', 0, row_diff, '; '.join(detail)[:2000] or None)
    pipeline.record_check(conn, r, 'parity_ekatte_differ', 0, ekatte_diff, '; '.join(detail)[:2000] or None)
    r.stats.update(old=t, new=n, rows_differ=row_diff, ekatte_differ=ekatte_diff)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', choices=['migrate', 'grao', 'build', 'replay', 'parity', 'check', 'freshness'], required=True)
    p.add_argument('--backfill', action='store_true')
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == 'migrate':
            pipeline.require_core(conn, '0004')
            result = {'migrations': pipeline.migrate(conn, TOOL, ROOT)}
        elif a.step == 'freshness':
            result = {'problems': pipeline.freshness(conn, TOOL)}
        else:
            try:
                with pipeline.run(conn, TOOL, a.step, params={'backfill': a.backfill}, sha=pipeline.code_sha(ROOT)) as r:
                    problems = []
                    if a.step == 'grao':
                        problems = refresh(conn, r, backfill=a.backfill)
                    elif a.step == 'build':
                        rebuild(conn, r)
                    elif a.step == 'replay':
                        problems = replay(conn, r)
                    elif a.step == 'parity':
                        parity(conn, r)
                    if a.step != 'parity':
                        failed = pipeline.run_checks(conn, r)
                        # The serving layer follows only when every rule holds. A table that one read refused
                        # is not a reason to hold the rest: what is stored is still sound.
                        problems += failed or pipeline.publish(conn, r, ROOT).problems
                    result = {'step': a.step, 'stats': r.stats, 'problems': problems}
            except pipeline.Busy as e:
                result = {'step': a.step, 'status': 'skipped', 'note': str(e)}
            except (ShapeError, pipeline.CorruptRaw) as e:
                result = {'step': a.step, 'problems': [str(e)]}
            if a.step == 'parity' and 'stats' in result:
                bad = conn.execute('''SELECT title_bg, expected, actual FROM ops.check_latest
                    WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name''', (TOOL,)).fetchall()
                result['problems'] = [f'{title}: старо {e}, ново {n}' for title, e, n in bad]
    return pipeline.report(result)


if __name__ == '__main__':
    raise SystemExit(main())

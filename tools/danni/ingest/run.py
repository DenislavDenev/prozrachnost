"""The steps of the shared foundation: python -m ingest.run --step <step>

    migrate    the schemas ops, core, api_v1 and the registers; the licence list
    place      EKATTE from the archive of Наблюдател and the municipality reference -> core.nuts, oblast, municipality, settlement
    org        the organisations with a valid ЕИК from Тендер -> core.org
    check      every quality rule of the foundation
    freshness  the list of problems, empty when all is well
"""
import argparse
import csv
import io
import tomllib

import psycopg

import pipeline
from pipeline import ShapeError

from . import ekatte
from .config import ARCHIVE, DSN, RAW, ROOT, TENDER_DSN, TOOL

EKATTE_TABLE = {'ek_reg1.json': 'ekatte_region', 'ek_reg2.json': 'ekatte_region', 'ek_obl.json': 'ekatte_oblast',
                'ek_obst.json': 'ekatte_municipality', 'ek_atte.json': 'ekatte_settlement'}
REF_COLUMNS = ('municipality_code', 'eik', 'name_bg', 'name_en', 'drawn_as_eik')
ORG_COLUMNS = ('eik', 'name', 'legal_form', 'seat', 'registered_on', 'register_status', 'is_public_body', 'is_buyer')


def eik_valid(e):
    """The same rule as core.eik_valid() in SQL; a test compares the two."""
    if not e or not e.isascii() or not e.isdigit() or len(e) not in (9, 13) or set(e) == {'0'}:
        return False
    d = [int(c) for c in e]
    s = sum((i + 1) * d[i] for i in range(8)) % 11
    if s == 10:
        s = sum((i + 3) * d[i] for i in range(8)) % 11
        if s == 10:
            s = 0
    if s != d[8]:
        return False
    if len(e) == 9:
        return True
    s = (2 * d[8] + 7 * d[9] + 3 * d[10] + 5 * d[11]) % 11
    if s == 10:
        s = (4 * d[8] + 9 * d[9] + 5 * d[10] + 7 * d[11]) % 11
        if s == 10:
            s = 0
    return s == d[12]


def writer(table, columns):
    """write() for pipeline.load(): COPY the rows into danni_src.<table> under the load id."""
    def write(conn, load_id, rows):
        with conn.cursor().copy(f"COPY danni_src.{table} (load_id, {', '.join(columns)}) FROM STDIN") as copy:
            for row in rows.values():
                copy.write_row((load_id, *(row[c] for c in columns)))
    return write


def reader(table, columns):
    """current() for pipeline.load(): the rows of a load, keyed like the parser keys them."""
    def current(conn, load_id):
        cur = conn.execute(f"SELECT {', '.join(columns)} FROM danni_src.{table} WHERE load_id = %s", (load_id,))
        return {row[0]: dict(zip(columns, row)) for row in cur}
    return current


def drop_previous(conn, table, loaded):
    """Landing policy `latest`: the rows of the replaced version leave the landing zone; the ledger row stays."""
    if loaded.previous:
        conn.execute(f'DELETE FROM danni_src.{table} WHERE load_id = %s', (loaded.previous,))


def build(conn, name):
    with conn.transaction():
        conn.execute((ROOT / 'db' / 'build' / f'{name}.sql').read_text(encoding='utf-8'))


def newest_ekatte(archive):
    files = sorted((archive / 'ekatte').glob('*.zip'))
    if not files:
        raise ShapeError('В архива няма файл на ЕКАТТЕ')
    return files[-1]


def municipality_ref(raw):
    rows = {}
    for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if tuple(row) != REF_COLUMNS or not all(row[c] for c in REF_COLUMNS if c != 'name_en'):
            raise ShapeError('municipality.csv: променени колони или празна стойност')
        row['name_en'] = row['name_en'] or None
        if row['municipality_code'] in rows or not eik_valid(row['eik']) or not eik_valid(row['drawn_as_eik']):
            raise ShapeError(f"municipality.csv: дубликат или невалиден ЕИК за {row['municipality_code']}")
        rows[row['municipality_code']] = row
    if len({r['eik'] for r in rows.values()}) != len(rows):
        raise ShapeError('municipality.csv: един ЕИК за две общини')
    return rows


def place(conn, r, archive=ARCHIVE, raw_dir=RAW):
    """EKATTE and the reference of the municipalities into the landing zone, then core.*"""
    path = newest_ekatte(archive)
    file, raw = pipeline.register_raw(conn, r, 'ekatte', path.name, path, media_type='application/zip')
    try:
        parsed = ekatte.parse(raw)
    except ShapeError as e:
        pipeline.reject(conn, r, 'ekatte', 'archive', e, file.raw_file_id)
        raise
    ref_raw = (ROOT / 'db' / 'ref' / 'municipality.csv').read_bytes()
    ref = municipality_ref(ref_raw)
    unknown = sorted(parsed['ek_obst.json'].keys() ^ ref.keys())
    if unknown:
        raise ShapeError(f'Общини без съответствие между ЕКАТТЕ и municipality.csv: {unknown[:10]}')
    result = {}
    for name, rows in parsed.items():
        cols = ekatte.FILES[name][3]
        result[name] = pipeline.load(conn, r, 'ekatte', name, rows, writer(EKATTE_TABLE[name], cols),
                                     raw_file_id=file.raw_file_id, current=reader(EKATTE_TABLE[name], cols)).status
    ref_file = pipeline.save_raw(conn, r, 'municipality-ref', 'municipality.csv', ref_raw, raw_dir, name='municipality.csv',
                                 media_type='text/csv')
    result['municipality.csv'] = pipeline.load(conn, r, 'municipality-ref', 'municipality.csv', ref,
                                               writer('municipality_ref', REF_COLUMNS), raw_file_id=ref_file.raw_file_id,
                                               current=reader('municipality_ref', REF_COLUMNS)).status
    build(conn, 'place')
    r.stats.update(file=path.name, loads=result)
    return result


def tender_orgs(tender):
    """Every organisation Тендер holds under an ЕИК: the companies and the buyers. Persons are not read.
    Returns the rows with a valid ЕИК, and the counts the reconciliation needs."""
    rows, seen, invalid, unnamed = {}, 0, 0, 0
    for eik, name, legal_form, seat, registered_on, status, public in tender.execute(
            '''SELECT eik, name, legal_form, seat, registered_on, tr_status, is_public_body
               FROM live.company WHERE eik IS NOT NULL ORDER BY eik'''):
        seen += 1
        if not eik_valid(eik):
            invalid += 1
        elif not (name or '').strip():
            unnamed += 1
        else:
            rows[eik] = dict(eik=eik, name=name.strip(), legal_form=legal_form or None, seat=seat or None,
                             registered_on=registered_on, register_status=status or None,
                             is_public_body=bool(public), is_buyer=False)
    buyers = buyers_valid = tender_says_valid = 0
    for eik, name, says_valid in tender.execute('SELECT eik, name, eik_valid FROM live.buyer ORDER BY eik'):
        buyers += 1
        tender_says_valid += bool(says_valid)
        if not eik_valid(eik):
            continue
        buyers_valid += 1
        if eik in rows:
            rows[eik]['is_buyer'] = True
        elif (name or '').strip():
            rows[eik] = dict(eik=eik, name=name.strip(), legal_form=None, seat=None, registered_on=None,
                             register_status=None, is_public_body=False, is_buyer=True)
    return rows, dict(seen=seen, invalid=invalid, unnamed=unnamed, buyers=buyers, buyers_valid=buyers_valid,
                      tender_says_valid=tender_says_valid)


def org(conn, r, tender_dsn=TENDER_DSN):
    with psycopg.connect(tender_dsn, autocommit=True) as tender:
        tender.execute('SET default_transaction_read_only = on')
        with tender.transaction():   # one snapshot for both tables
            tender.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            rows, n = tender_orgs(tender)
            municipal = tender.execute('SELECT eik FROM live.buyer WHERE name ILIKE %s', ('%община%',)).fetchall()
    loaded = pipeline.load(conn, r, 'org-tender', 'live.company+live.buyer', rows, writer('org_tender', ORG_COLUMNS))
    drop_previous(conn, 'org_tender', loaded)
    build(conn, 'org')
    in_core = conn.execute('SELECT count(*) FROM core.org WHERE gone_at IS NULL').fetchone()[0]
    pipeline.record_check(conn, r, 'org_count_matches_tender', len(rows), in_core)
    pipeline.record_check(conn, r, 'org_invalid_eik', 0, round(10000 * n['invalid'] / max(n['seen'], 1)),
                          f"{n['invalid']} от {n['seen']} реда с ЕИК; без име: {n['unnamed']}")
    pipeline.record_check(conn, r, 'org_buyer_valid_matches_tender', n['tender_says_valid'], n['buyers_valid'])
    known = {e for (e,) in municipal}
    municipalities = [e for (e,) in conn.execute('SELECT eik FROM core.municipality WHERE gone_at IS NULL')]
    if municipalities:
        pipeline.record_check(conn, r, 'municipality_eik_is_buyer', len(municipalities),
                              sum(e in known for e in municipalities),
                              'липсват: ' + ', '.join(sorted(e for e in municipalities if e not in known)[:20]))
    r.stats.update(n, status=loaded.status, orgs=len(rows), in_core=in_core)
    return loaded.status


def licences(conn):
    """db/licences.toml is the only thing that makes a dataset distributable. A dataset that left the list is closed."""
    listed = tomllib.loads((ROOT / 'db' / 'licences.toml').read_text(encoding='utf-8')).get('licence', [])
    with conn.transaction():
        conn.execute('UPDATE core.dataset SET is_distributable = false WHERE is_distributable AND NOT (dataset = ANY(%s))',
                     ([x['dataset'] for x in listed],))
        for x in listed:
            done = conn.execute('''UPDATE core.dataset SET is_distributable = true, licence_checked_on = %s
                WHERE dataset = %s AND personal_data <> 'names' RETURNING 1''', (x['checked_on'], x['dataset'])).fetchone()
            if not done and conn.execute('SELECT 1 FROM core.dataset WHERE dataset = %s', (x['dataset'],)).fetchone():
                raise RuntimeError(f"{x['dataset']} holds names of persons and cannot be distributed")
    return len(listed)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--step', choices=['migrate', 'place', 'org', 'check', 'freshness'], required=True)
    a = p.parse_args(argv)
    sha = pipeline.code_sha(ROOT)
    with pipeline.connect(DSN) as conn:
        if a.step == 'migrate':
            result = {'migrations': pipeline.migrate(conn, TOOL, ROOT), 'licences': licences(conn)}
        elif a.step == 'freshness':
            result = {'problems': pipeline.freshness(conn, TOOL)}
        else:
            try:
                with pipeline.run(conn, TOOL, a.step, sha=sha) as r:
                    if a.step == 'place':
                        place(conn, r)
                    elif a.step == 'org':
                        org(conn, r)
                    problems = pipeline.run_checks(conn, r)
                    result = {'step': a.step, 'stats': r.stats, 'problems': problems}
            except pipeline.Busy as e:
                result = {'step': a.step, 'status': 'skipped', 'note': str(e)}
            except (ShapeError, pipeline.CorruptRaw) as e:
                result = {'step': a.step, 'problems': [str(e)]}
    return pipeline.report(result)


if __name__ == '__main__':
    raise SystemExit(main())

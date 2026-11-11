"""One table of МФ into the landing zone (byudzhet_src) and, in the same transaction, into the clean tables
(byudzhet). The ledger, the hold and the change log are pipeline.load()."""
from collections import Counter

from .parse import parse_rows
from .state import structure

PERIOD_KIND = {'state': 'ytd', 'kfp': 'ytd', 'reserve': 'day', 'debt': 'day', 'indicators': 'day'}


def rows(data):
    """{key: row} for pipeline.load(): what the table says, each row under its place in the table's own structure
    (the category of a budget line, the type of budget, the municipality), not under its line number. Values are the
    numbers as the source writes them, so a version that only rewrites the file is the same content."""
    kind = data['kind']
    out = {'file': {'period': data['period'], 'currency': data['currency']}}
    if kind == 'state':
        for r in structure(data['rows']):
            out['s/' + r['key']] = dict(line=r['line'], law=r['law']['original'], actual=r['actual']['original'],
                                        unit=r['actual']['unit'], pct=r['pct'])
    elif kind == 'kfp':
        for r in data['rows']:
            out['k/' + r['budget_type']] = {item: v['original'] for item, v in r['values'].items()}
    elif kind == 'reserve':
        seen = Counter()
        for r in data['rows']:
            seen[r['line']] += 1
            out[f"r/{r['line']}/{seen[r['line']]}"] = dict(value=r['value']['original'], unit=r['value']['unit'])
    else:
        for r in data['rows']:
            out['m/' + r['code']] = {k: v['original'] for k, v in r['values'].items()}
    return out


def hold_when(old, new):
    """A read is held when a row of the current version is missing or one of its numbers became empty: a shorter or
    emptier table may be a fault of the portal, not a correction. It replaces the current version only when a second
    read a day later says the same (pipeline.load)."""
    emptied = sum(1 for k in old.keys() & new.keys() for f, v in old[k].items() if v is not None and new[k].get(f) is None)
    return len(old.keys() - new.keys()) + emptied


def cells_of(conn, load_id):
    return [c for (c,) in conn.execute('SELECT cells FROM byudzhet_src.egov_row WHERE load_id = %s ORDER BY row_no', (load_id,))]


def parsed(conn, load_id):
    """The report of a load again, parsed from its landing rows."""
    kind, name = conn.execute('SELECT kind, name FROM byudzhet_src.egov_file WHERE load_id = %s', (load_id,)).fetchone()
    return parse_rows(cells_of(conn, load_id), kind, name)


def current(conn, load_id):
    """The rows of a load back in the shape of rows(), for the hold and the field by field difference."""
    return rows(parsed(conn, load_id))


def land(conn, load_id, kind, meta, cells):
    conn.execute('''INSERT INTO byudzhet_src.egov_file (load_id, kind, uri, name, version, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)''', (load_id, kind, meta['uri'], meta['name'], str(meta['version']), meta.get('updated_at')))
    with conn.cursor().copy('COPY byudzhet_src.egov_row (load_id, row_no, cells) FROM STDIN') as copy:
        copy.set_types(['int8', 'int4', 'text[]'])
        for i, c in enumerate(cells):
            copy.write_row((load_id, i, c))


def _mult(unit):
    return 1000000 if unit.startswith('млн.') else 1


CLEAN = {'state': ('state_line', 'period_end'), 'kfp': ('kfp_value', 'period_end'), 'reserve': ('reserve_line', 'as_of'),
         'debt': ('municipal_value', 'as_of'), 'indicators': ('municipal_value', 'as_of')}


def build(conn, load_id, data, uri, version, at=None):
    """The clean rows of one report. The current rows of the same period are superseded, not deleted, so every
    earlier version can still be read. Amounts are computed here, in SQL: the unit unfolded from millions, rounded to
    the cent, and the euro by core.to_eur."""
    kind, period, currency = data['kind'], data['period'], data['currency']
    table, col = CLEAN[kind]
    conn.execute('''UPDATE byudzhet.report SET superseded_at = COALESCE(%s, now())
        WHERE kind = %s AND period_end = %s AND superseded_at IS NULL''', (at, kind, period))
    conn.execute(f'''UPDATE byudzhet.{table} SET superseded_at = COALESCE(%s, now())
        WHERE {col} = %s AND superseded_at IS NULL''' + (' AND kind = %s' if table == 'municipal_value' else ''),
                 (at, period, kind) if table == 'municipal_value' else (at, period))
    conn.execute('''INSERT INTO byudzhet.report (kind, period_kind, period_end, uri, version, currency, reconciliation, load_id,
        first_seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now()))''',
                 (kind, PERIOD_KIND[kind], period, uri, str(version), currency, data['reconciliation'], load_id, at))
    with conn.cursor() as cur:
        if kind == 'state':
            cur.executemany('''INSERT INTO byudzhet.state_line (period_end, row_no, row_key, line, law_original, actual_original,
                source_unit, currency, law_amount, actual_amount, law_eur, actual_eur, pct, load_id, first_seen_at)
                SELECT p, n, k, l, lo, ao, u, c, round(lo * m, 2), round(ao * m, 2), core.to_eur(round(lo * m, 2), c, p),
                       core.to_eur(round(ao * m, 2), c, p), pct, ld, COALESCE(at, now())
                FROM (VALUES (%s::date, %s::int, %s, %s, %s::numeric, %s::numeric, %s, %s::char(3), %s::numeric, %s::numeric,
                              %s::bigint, %s::timestamptz)) v(p, n, k, l, lo, ao, u, c, m, pct, ld, at)''',
                            [(period, r['order'], r['key'], r['line'], r['law']['original'], r['actual']['original'], r['actual']['unit'],
                              currency, _mult(r['actual']['unit']), r['pct'], load_id, at) for r in structure(data['rows'])])
        elif kind == 'kfp':
            cur.executemany('''INSERT INTO byudzhet.kfp_value (period_end, row_no, col_no, budget_type, item, original, source_unit,
                currency, amount, amount_eur, load_id, first_seen_at)
                SELECT p, n, cn, b, i, o, u, c, round(o * m, 2), core.to_eur(round(o * m, 2), c, p), ld, COALESCE(at, now())
                FROM (VALUES (%s::date, %s::int, %s::int, %s, %s, %s::numeric, %s, %s::char(3), %s::numeric, %s::bigint,
                              %s::timestamptz)) v(p, n, cn, b, i, o, u, c, m, ld, at)''',
                            [(period, n, cn, r['budget_type'], item, v['original'], v['unit'], currency, _mult(v['unit']), load_id, at)
                             for n, r in enumerate(data['rows']) for cn, (item, v) in enumerate(r['values'].items())])
        elif kind == 'reserve':
            cur.executemany('''INSERT INTO byudzhet.reserve_line (as_of, row_no, line, original, source_unit, currency, amount,
                amount_eur, load_id, first_seen_at)
                SELECT p, n, l, o, u, c, round(o * m, 2), core.to_eur(round(o * m, 2), c, p), ld, COALESCE(at, now())
                FROM (VALUES (%s::date, %s::int, %s, %s::numeric, %s, %s::char(3), %s::numeric, %s::bigint, %s::timestamptz))
                v(p, n, l, o, u, c, m, ld, at)''',
                            [(period, n, r['line'], r['value']['original'], r['value']['unit'], currency, _mult(r['value']['unit']),
                              load_id, at) for n, r in enumerate(data['rows'])])
        else:
            cur.executemany('''INSERT INTO byudzhet.municipal_value (as_of, kind, municipality_code, measure, original, source_unit,
                currency, amount, amount_eur, load_id, first_seen_at)
                SELECT p, kd, mc, ms, o, u, c, round(o * m, 2), core.to_eur(round(o * m, 2), c, p), ld, COALESCE(at, now())
                FROM (VALUES (%s::date, %s, %s, %s, %s::numeric, %s, %s::char(3), %s::numeric, %s::bigint, %s::timestamptz))
                v(p, kd, mc, ms, o, u, c, m, ld, at)''',
                            [(period, kind, r['municipality_code'], k, v['original'], v['unit'], currency, _mult(v['unit']), load_id, at)
                             for r in data['rows'] for k, v in r['values'].items()])


def writer(data, meta, cells, at=None):
    """write() for pipeline.load(): the landing rows and the clean rows in the transaction of the load."""
    def write(conn, load_id, _rows):
        land(conn, load_id, data['kind'], meta, cells)
        build(conn, load_id, data, meta['uri'], meta['version'], at)
    return write

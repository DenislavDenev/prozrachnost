"""One table of ГРАО into the landing zone (naselenie_src) and, in the same transaction, into the clean tables
(naselenie). The ledger, the hold and the change log are pipeline.load()."""
from collections import Counter

KIND = {'municipalities': 'monthly', 'places': 'detailed'}
NUMBERS = ('permanent', 'current', 'both')


def rows(data):
    """{key: row} for pipeline.load(): the rows as the source has them, without anything we matched.
    The key of a row is its place in the table's own hierarchy, not its line number: a row added above
    does not rename the rows below it."""
    out = {'file': {'as_of': data['date'], 'table_kind': KIND[data['kind']]}}
    for r in data['municipalities']:
        out[f"m/{r['oblast']}/{r['municipality']}"] = {k: r[k] for k in ('oblast', 'municipality', *NUMBERS)}
    for kind, items in (('s', data['places']), ('d', data['subdivisions'])):
        seen = Counter()
        for r in items:
            name = (r['oblast'], r['municipality'], r['name'])
            seen[name] += 1
            out[f"{kind}/{r['oblast']}/{r['municipality']}/{r['name']}/{seen[name]}"] = {
                k: r.get(k) for k in ('oblast', 'municipality', 'name', *(NUMBERS if kind == 's' else ()))}
    return out


LANDING = {'m': 'municipality', 's': 'settlement', 'd': 'subdivision'}


def current(conn, load_id):
    """The rows of a load back in the shape of rows(), for the hold and the field by field difference."""
    as_of, kind = conn.execute('SELECT as_of, table_kind FROM naselenie_src.grao_file WHERE load_id = %s', (load_id,)).fetchone()
    out, seen = {'file': {'as_of': as_of.isoformat(), 'table_kind': kind}}, Counter()
    for row_kind, oblast, municipality, name, permanent, cur, both in conn.execute(
            '''SELECT row_kind, oblast, municipality, name, permanent, current, both_here FROM naselenie_src.grao_row
               WHERE load_id = %s ORDER BY row_kind, row_no''', (load_id,)):
        if row_kind == 'municipality':
            out[f'm/{oblast}/{municipality}'] = dict(oblast=oblast, municipality=municipality, permanent=permanent,
                                                     current=cur, both=both)
            continue
        k = 's' if row_kind == 'settlement' else 'd'
        seen[(k, oblast, municipality, name)] += 1
        row = dict(oblast=oblast, municipality=municipality, name=name)
        if k == 's':
            row.update(permanent=permanent, current=cur, both=both)
        out[f'{k}/{oblast}/{municipality}/{name}/{seen[(k, oblast, municipality, name)]}'] = row
    return out


def land(conn, load_id, data):
    conn.execute('INSERT INTO naselenie_src.grao_file (load_id, as_of, table_kind) VALUES (%s, %s, %s)',
                 (load_id, data['date'], KIND[data['kind']]))
    with conn.cursor().copy('''COPY naselenie_src.grao_row (load_id, row_kind, row_no, oblast, municipality, name,
            permanent, current, both_here) FROM STDIN''') as copy:
        for kind, items in (('municipality', data['municipalities']), ('settlement', data['places']),
                            ('subdivision', data['subdivisions'])):
            for i, r in enumerate(items):
                copy.write_row((load_id, kind, i, r['oblast'], r['municipality'], r.get('name') if kind != 'municipality' else None,
                                r.get('permanent'), r.get('current'), r.get('both')))


def build(conn, load_id, data, at=None):
    """The clean rows of one table. A row that did not change stays as it is; a changed or vanished one gets
    superseded_at and the new value is added, so every earlier state can still be read."""
    as_of, kind = data['date'], KIND[data['kind']]
    conn.execute('''CREATE TEMP TABLE new_population (municipality_code text, permanent int, current int, both_here int)
                    ON COMMIT DROP''')
    with conn.cursor().copy('COPY new_population FROM STDIN') as copy:
        for r in data['municipalities']:
            copy.write_row((r['code'], r['permanent'], r['current'], r['both']))
    conn.execute('''UPDATE naselenie.population p SET superseded_at = COALESCE(%(at)s, now())
        WHERE p.as_of = %(as_of)s AND p.table_kind = %(kind)s AND p.superseded_at IS NULL AND NOT EXISTS (
          SELECT FROM new_population n WHERE n.municipality_code = p.municipality_code
            AND (n.permanent, n.current, n.both_here) = (p.permanent, p.current, p.both_here))''', dict(at=at, as_of=as_of, kind=kind))
    conn.execute('''INSERT INTO naselenie.population (municipality_code, as_of, table_kind, permanent, current, both_here, load_id, first_seen_at)
        SELECT n.municipality_code, %(as_of)s, %(kind)s, n.permanent, n.current, n.both_here, %(load)s, COALESCE(%(at)s, now())
        FROM new_population n WHERE NOT EXISTS (
          SELECT FROM naselenie.population p WHERE p.as_of = %(as_of)s AND p.table_kind = %(kind)s
            AND p.superseded_at IS NULL AND p.municipality_code = n.municipality_code)
        ORDER BY n.municipality_code''', dict(at=at, as_of=as_of, kind=kind, load=load_id))
    conn.execute('DROP TABLE new_population')
    if kind != 'detailed':
        return
    conn.execute('''CREATE TEMP TABLE new_settlement (municipality_code text, source_name text, name_no smallint, row_no int,
                    ekatte text, permanent int, current int, both_here int) ON COMMIT DROP''')
    seen = Counter()
    with conn.cursor().copy('COPY new_settlement FROM STDIN') as copy:
        for i, r in enumerate(data['places']):
            seen[(r['code'], r['name'])] += 1
            copy.write_row((r['code'], r['name'], seen[(r['code'], r['name'])], i, r['ekatte'],
                            r['permanent'], r['current'], r['both']))
    conn.execute('''UPDATE naselenie.settlement_population p SET superseded_at = COALESCE(%(at)s, now())
        WHERE p.as_of = %(as_of)s AND p.superseded_at IS NULL AND NOT EXISTS (
          SELECT FROM new_settlement n WHERE (n.municipality_code, n.source_name, n.name_no) = (p.municipality_code, p.source_name, p.name_no)
            AND (n.permanent, n.current, n.both_here) = (p.permanent, p.current, p.both_here) AND n.ekatte IS NOT DISTINCT FROM p.ekatte)''',
                 dict(at=at, as_of=as_of))
    # the line number is only the order of the source; it follows the newest file without making a new version
    conn.execute('''UPDATE naselenie.settlement_population p SET row_no = n.row_no FROM new_settlement n
        WHERE p.as_of = %(as_of)s AND p.superseded_at IS NULL AND p.row_no <> n.row_no
          AND (n.municipality_code, n.source_name, n.name_no) = (p.municipality_code, p.source_name, p.name_no)''', dict(as_of=as_of))
    conn.execute('''INSERT INTO naselenie.settlement_population (municipality_code, as_of, source_name, name_no, row_no, ekatte,
            permanent, current, both_here, load_id, first_seen_at)
        SELECT n.municipality_code, %(as_of)s, n.source_name, n.name_no, n.row_no, n.ekatte, n.permanent, n.current, n.both_here,
               %(load)s, COALESCE(%(at)s, now())
        FROM new_settlement n WHERE NOT EXISTS (
          SELECT FROM naselenie.settlement_population p WHERE p.as_of = %(as_of)s AND p.superseded_at IS NULL
            AND (p.municipality_code, p.source_name, p.name_no) = (n.municipality_code, n.source_name, n.name_no))
        ORDER BY n.row_no''', dict(at=at, as_of=as_of, load=load_id))
    conn.execute('DROP TABLE new_settlement')


def writer(data, at=None):
    """write() for pipeline.load(): the landing rows and the clean rows in the transaction of the load."""
    def write(conn, load_id, _rows):
        land(conn, load_id, data)
        build(conn, load_id, data, at)
    return write

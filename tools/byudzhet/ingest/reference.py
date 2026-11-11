"""The municipalities of core in the shape the tables of МФ are matched against. Nothing is read from a file of our
own: the shared database is the reference."""
from .pipeline import ShapeError

SQL = '''SELECT m.eik, m.municipality_code, m.name_bg, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
    FROM {s}.municipality m JOIN {s}.oblast o USING (oblast_code)
    JOIN {s}.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN {s}.nuts n2 ON n2.nuts_code = n3.parent_code
    WHERE {gone} ORDER BY m.name_bg, m.municipality_code'''


def load(conn, schema='core'):
    """[{id: ЕИК, code: code by НСИ, name_bg, oblast, nuts3, nuts2, nuts1}] of the 265 municipalities.
    The ingest reads core; the pages read the same from pub."""
    gone = 'm.gone_at IS NULL' if schema == 'core' else 'NOT m.is_gone'
    ref = [dict(id=a, code=b, name_bg=c, oblast=d, nuts3=e, nuts2=f, nuts1=g)
           for a, b, c, d, e, f, g in conn.execute(SQL.format(s=schema, gone=gone))]
    if len(ref) != 265:
        raise ShapeError(f'Общите измерения ({schema}) не са заредени: {len(ref)} общини')
    return ref

"""The places of core (municipalities and settlements of EKATTE) in the shape the tables of ГРАО are matched against.
Nothing is read from a file of our own: the shared database is the reference."""
from collections import Counter, defaultdict

from .parse import ShapeError, norm


def oblast_key(s):
    s = norm(s)
    return {'СОФИЙСКА': 'СОФИЯ ОБЛАСТ', 'СОФИЯ (СТОЛИЦА)': 'СОФИЯ-ГРАД', 'СОФИЯ': 'СОФИЯ-ГРАД'}.get(s, s)


def municipality_key(ob, name):
    name = norm(name).replace('Ь', 'Ъ')
    name = {'ДОБРИЧ-СЕЛСКА': 'ДОБРИЧКА', 'ВЪЛЧИДОЛ': 'ВЪЛЧИ ДОЛ', 'ГЕНЕРАЛ-ТОШЕВО': 'ГЕНЕРАЛ ТОШЕВО'}.get(name, name)
    if oblast_key(ob) == 'СОФИЯ-ГРАД':
        name = 'СТОЛИЧНА'
    if oblast_key(ob) == 'ДОБРИЧ' and name == 'ДОБРИЧ-ГРАД':
        name = 'ДОБРИЧ'
    return oblast_key(ob), name


class Reference:
    """municipalities: {(oblast key, municipality key): code by НСИ}; places: {(code, kind and name): [ЕКАТТЕ]}"""

    def __init__(self, municipalities, places):
        self.municipalities, self.places = municipalities, places


def load(conn):
    municipalities = {}
    for code, oblast_code, oblast_name, name in conn.execute(
            '''SELECT m.municipality_code, o.oblast_code, o.name_bg, m.name_bg
               FROM core.municipality m JOIN core.oblast o USING (oblast_code) WHERE m.gone_at IS NULL'''):
        # in EKATTE both Sofia oblasts are called София; ГРАО writes СОФИЙСКА for the oblast around the capital
        municipalities[municipality_key('СОФИЙСКА' if oblast_code == 'SFO' else oblast_name, name)] = code
    places = defaultdict(list)
    for code, prefix, name, ekatte in conn.execute(
            'SELECT municipality_code, kind_prefix, name_bg, ekatte FROM core.settlement WHERE gone_at IS NULL ORDER BY ekatte'):
        places[(code, norm(prefix + name))].append(ekatte)
    if len(municipalities) < 260 or len(places) < 5000:
        raise ShapeError('Общите измерения (core) не са заредени')
    return Reference(municipalities, dict(places))


def enrich(data, ref):
    """Give every row of a parsed table its municipality code, and every settlement row its ЕКАТТЕ when exactly one
    settlement of that municipality has that kind and name. An unknown municipality stops the load; an ambiguous
    or unknown settlement keeps its name from the source and no code."""
    counts = Counter((*municipality_key(r['oblast'], r['municipality']), r['name']) for r in data['places'])
    for r in data['municipalities'] + data['places']:
        key = municipality_key(r['oblast'], r['municipality'])
        code = ref.municipalities.get(key)
        if not code:
            raise ShapeError('Несъпоставена община: ' + repr(key))
        r['code'] = code
        if 'name' in r:
            candidates = ref.places.get((code, r['name']), [])
            if not candidates and '(' in r['name']:
                candidates = ref.places.get((code, r['name'].split('(')[0].strip()), [])
            r['ekatte'] = candidates[0] if len(candidates) == 1 and counts[(*key, r['name'])] == 1 else None
    data['unmatched'] = sum(r['ekatte'] is None for r in data['places'])
    return data

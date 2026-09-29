import csv
import io
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from .parse import ShapeError, norm

ROOT = Path(__file__).resolve().parent.parent


def oblast_key(s):
    s = norm(s)
    return {'СОФИЙСКА': 'СОФИЯ ОБЛАСТ', 'СОФИЯ (СТОЛИЦА)': 'СОФИЯ-ГРАД', 'СОФИЯ': 'СОФИЯ-ГРАД'}.get(s, s)


def municipality_key(ob, name):
    name = norm(name).replace('Ь', 'Ъ')
    name = {'ДОБРИЧ-СЕЛСКА':'ДОБРИЧКА','ВЪЛЧИДОЛ':'ВЪЛЧИ ДОЛ','ГЕНЕРАЛ-ТОШЕВО':'ГЕНЕРАЛ ТОШЕВО'}.get(name,name)
    if oblast_key(ob) == 'СОФИЯ-ГРАД':
        name = 'СТОЛИЧНА'
    if oblast_key(ob) == 'ДОБРИЧ' and name == 'ДОБРИЧ-ГРАД':
        name = 'ДОБРИЧ'
    return oblast_key(ob), name


def municipalities():
    with (ROOT / 'db/ref/municipality.csv').open(encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def ekatte(raw):
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        rows = json.loads(z.read('ek_atte.json'))
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        raise ShapeError('Невалиден архив ЕКАТТЕ') from e
    required = {'ekatte','t_v_m','name','oblast','obshtina','nuts1','nuts2','nuts3','oblast_name','obshtina_name'}
    allowed = required | {'kmetstvo','kind','category','altitude','document','abc','name_en','text'}
    result = []
    for row in rows:
        if set(row) == {'Дата и час на изготвяне на справката', 'Данните са актуални към'}:
            continue
        if not required <= row.keys() or row.keys() - allowed or not all(isinstance(row[k], str) for k in required):
            raise ShapeError('Променена схема на ЕКАТТЕ')
        result.append(row)
    if not result or len({r['ekatte'] for r in result}) != len(result):
        raise ShapeError('Празен или дублиран ЕКАТТЕ')
    return result


def enrich(data, ekatte_rows):
    refs = {municipality_key(r['oblast'], r['name_bg']): r for r in municipalities()}
    places = defaultdict(list)
    for r in ekatte_rows:
        places[(*municipality_key('СОФИЙСКА' if r['oblast'] == 'SFO' else r['oblast_name'], r['obshtina_name']), norm(r['t_v_m'] + r['name']))].append(r['ekatte'])
    counts = Counter((*municipality_key(r['oblast'], r['municipality']), r['name']) for r in data['places'])
    for r in data['municipalities'] + data['places']:
        key = municipality_key(r['oblast'], r['municipality'])
        ref = refs.get(key)
        if not ref:
            raise ShapeError('Несъпоставена община: ' + repr(key))
        r['code'] = ref['id']
        if 'name' not in r:
            r['name'] = ref['name_bg']
            r['nuts3'], r['nuts2'], r['nuts1'] = ref['nuts3'], ref['nuts2'], ref['nuts1']
        else:
            candidates = places.get((*key, r['name']), [])
            if not candidates and '(' in r['name']:
                candidates = places.get((*key, r['name'].split('(')[0].strip()), [])
            r['ekatte'] = candidates[0] if len(candidates) == 1 and counts[(*key, r['name'])] == 1 else None
    data['unmatched'] = sum(r['ekatte'] is None for r in data['places'])
    return data

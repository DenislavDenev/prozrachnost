"""EKATTE of НСИ (the JSON archive): regions, oblasts, municipalities, settlements. Every file is checked for its
shape before any row is returned; an unknown or missing field stops the load."""
import io
import json
import zipfile

from pipeline import ShapeError

FOOTER = {'Дата и час на изготвяне на справката', 'Данните са актуални към'}

# file -> (key field, required fields, optional fields, the columns kept in the landing zone)
FILES = {
    'ek_reg1.json': ('region', {'region', 'nuts1', 'name', 'name_en'}, {'document'}, ('region', 'name', 'name_en')),
    'ek_reg2.json': ('region', {'region', 'nuts1', 'nuts2', 'name', 'name_en'}, {'document'}, ('region', 'name', 'name_en')),
    'ek_obl.json': ('oblast', {'oblast', 'ekatte', 'name', 'name_en', 'nuts3', 'nuts2', 'nuts1'},
                    {'region', 'document', 'full_name_bul'},
                    ('oblast', 'ekatte', 'name', 'name_en', 'nuts3', 'nuts2', 'nuts1')),
    'ek_obst.json': ('obshtina', {'obshtina', 'ekatte', 'name', 'name_en', 'nuts3'},
                     {'nuts1', 'nuts2', 'category', 'document', 'full_name_bul'},
                     ('obshtina', 'ekatte', 'name', 'name_en', 'nuts3', 'category')),
    'ek_atte.json': ('ekatte', {'ekatte', 't_v_m', 'name', 'oblast', 'obshtina', 'nuts3'},
                     {'kmetstvo', 'kind', 'category', 'altitude', 'document', 'abc', 'name_en', 'text', 'nuts1', 'nuts2',
                      'oblast_name', 'obshtina_name'},
                     ('ekatte', 't_v_m', 'name', 'name_en', 'oblast', 'obshtina', 'kmetstvo', 'kind', 'category',
                      'altitude', 'document', 'nuts3')),
}
# fewer rows than this is a truncated file, not a reform of the country
MINIMUM = {'ek_reg1.json': 2, 'ek_reg2.json': 6, 'ek_obl.json': 28, 'ek_obst.json': 260, 'ek_atte.json': 5000}
INTEGERS = {'kind', 'category', 'altitude', 'document'}


def parse(raw):
    """{file name: {key: row}} for the five files. Raises ShapeError on anything unexpected."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        loaded = {name: json.loads(z.read(name)) for name in FILES}
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        raise ShapeError('Невалиден архив ЕКАТТЕ') from e
    out = {}
    for name, (key, required, optional, keep) in FILES.items():
        rows = {}
        if not isinstance(loaded[name], list):
            raise ShapeError(f'{name}: не е списък')
        for row in loaded[name]:
            if not isinstance(row, dict):
                raise ShapeError(f'{name}: ред, който не е запис')
            if set(row) == FOOTER:
                continue
            if not required <= row.keys() or row.keys() - required - optional:
                raise ShapeError(f'{name}: променена схема ({sorted(set(row) ^ required)[:5]})')
            if not all(isinstance(row[k], str) and row[k].strip() for k in required):
                raise ShapeError(f'{name}: празно или нетекстово задължително поле в {row.get(key)!r}')
            for k in INTEGERS & row.keys():
                if row[k] is not None and (isinstance(row[k], bool) or not isinstance(row[k], int)):
                    raise ShapeError(f'{name}: {k} не е цяло число в {row[key]!r}')
            if row[key] in rows:
                raise ShapeError(f'{name}: дублиран ключ {row[key]!r}')
            rows[row[key]] = {k: row.get(k) for k in keep}
        if len(rows) < MINIMUM[name]:
            raise ShapeError(f'{name}: {len(rows)} реда, очаквани поне {MINIMUM[name]}')
        out[name] = rows
    municipalities, oblasts = out['ek_obst.json'], out['ek_obl.json']
    for r in out['ek_atte.json'].values():
        if len(r['ekatte']) != 5 or not r['ekatte'].isdigit():
            raise ShapeError(f"ek_atte.json: невалиден ЕКАТТЕ {r['ekatte']!r}")
        if r['obshtina'] not in municipalities or r['oblast'] not in oblasts or r['obshtina'][:3] != r['oblast']:
            raise ShapeError(f"ek_atte.json: {r['ekatte']} сочи към непозната община или област")
    for r in municipalities.values():
        if r['obshtina'][:3] not in oblasts or r['ekatte'] not in out['ek_atte.json']:
            raise ShapeError(f"ek_obst.json: {r['obshtina']} е без област или без център в ek_atte.json")
    return out

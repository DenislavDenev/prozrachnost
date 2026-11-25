"""The parsers of the five sources: bytes in, rows out. A different shape stops the read with ShapeError before
anything is written (pipeline.reject() records it); an item that is only incomplete (a station without coordinates,
a crash without a date) is skipped and counted, as the pages have always done."""
import csv
import datetime as dt
import io
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from .pipeline import ShapeError

TRAFFIC = 'https://bgtoll.bg/index.php/traffic_passes/data'
WEATHER = 'https://bgtoll.bg/index.php/mto/data'
CATALOG = 'https://datasheet.api.bg/?lang=bg&g={group}&c={code}'
LIMA = [   # (source, group, code, kind as the pages name it)
    ('lima_r01', 'roadworks', 'r01', 'Затворен път'),
    ('lima_r02', 'roadworks', 'r02', 'Затворена лента'),
    ('lima_d01', 'danger', 'd01', 'Опасност'),
]
ARCGIS = 'https://services6.arcgis.com/GLbanbtRQ5XHYhZ4/arcgis/rest/services/Network_Wide_Road_Safety_Assessment/FeatureServer'
RISK_LAYERS = range(6)
MVR = 'https://www.mvr.bg/PTPShapeResult/ptp.zip'

class Garbled(ShapeError):
    """The answer is not the format of the source at all: not JSON, not XML, not a ZIP (an error page, an empty or
    cut body). Servers do this now and then, so it counts as a failed read, not as a changed format."""


INSIDE = (40.5, 44.5, 21.5, 29.0)   # south, north, west, east: what is not inside is not on the map of Bulgaria


def inside(lat, lon):
    return INSIDE[0] <= lat <= INSIDE[1] and INSIDE[2] <= lon <= INSIDE[3]


def first_bytes(raw):
    return repr(raw[:80])


# ---------------------------------------------------------------- БГТОЛ: traffic and weather

TOLL_FIELDS = {
    'traffic': ('scp', 'name', 'lat', 'lon', 'count15min', 'count1Hour', 'average_speed_15min', 'time'),
    'weather': ('scp', 'name', 'lat', 'lon', 'air', 'surface', 'humidity', 'wind', 'time'),
}


def toll_time(value):
    """БГТОЛ documents its timestamps without a zone as UTC."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError) as e:
        raise ShapeError(f'БГТОЛ: непознат час {value!r}') from e


def _number(value, name, whole=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShapeError(f'БГТОЛ: {name} не е число ({value!r})')
    if whole:
        if float(value) != int(value):
            raise ShapeError(f'БГТОЛ: {name} не е цяло число ({value!r})')
        return int(value)
    return float(value)


def parse_toll(raw, kind):
    """The stations of one answer in the order of the source, and how many were left out for their coordinates."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise Garbled(f'невалиден отговор: не е JSON ({first_bytes(raw)})') from e
    if not isinstance(data, list) or not data:
        raise ShapeError(f'невалиден отговор: празен или не е списък ({first_bytes(raw)})')
    rows, skipped, seen = [], 0, set()
    for item in data:
        if not isinstance(item, dict):
            raise ShapeError('БГТОЛ: елемент, който не е обект')
        missing = [f for f in TOLL_FIELDS[kind] if f not in item]
        if missing:
            raise ShapeError(f'БГТОЛ: липсват полета {missing}')
        try:
            lat, lon = float(item['lat']), float(item['lon'])
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not inside(lat, lon):
            skipped += 1
            continue
        scp = str(item['scp'])
        if scp in seen:
            raise ShapeError(f'БГТОЛ: станция {scp} е два пъти')
        seen.add(scp)
        row = dict(scp=scp, name=item['name'], lat=lat, lon=lon, observed=toll_time(item['time']))
        if kind == 'traffic':
            row.update(count15=_number(item['count15min'], 'count15min', True), count60=_number(item['count1Hour'], 'count1Hour', True),
                       speed15=_number(item['average_speed_15min'], 'average_speed_15min'))
        else:
            row.update({k: _number(item[k], k) for k in ('air', 'surface', 'humidity', 'wind')})
        rows.append(row)
    if not rows:
        raise ShapeError('БГТОЛ: нито една станция с координати')
    return rows, skipped


# ---------------------------------------------------------------- АПИ ЛИМА: closed roads, closed lanes, incidents

DATEX = '{http://datex2.eu/schema/2/2_0}'
NS = {'d': DATEX[1:-1]}


def catalog_xml(html):
    """The address of the day's DATEX II file in the catalogue page of ЛИМА."""
    match = re.search(r'/files/[^"\'<> ]+\.xml', html.decode('utf-8', 'replace'))
    if not match:
        raise ShapeError('ЛИМА: няма връзка към XML в каталога')
    return urllib.parse.urljoin('https://datasheet.api.bg', match.group(0))


def parse_datex(raw, kind):
    """The situations of one file that have a point. Some files declare UTF-16 and are UTF-8."""
    source = re.sub(r'^<\?xml[^>]+\?>', '', raw.decode('utf-8-sig', 'replace'), count=1)
    try:
        root = ET.fromstring(source)
    except ET.ParseError as e:
        raise Garbled(f'ЛИМА: невалиден XML ({e}; {first_bytes(raw)})') from e
    if root.tag != DATEX + 'd2LogicalModel':
        raise ShapeError(f'ЛИМА: непознат корен {root.tag}')
    rows, seen = [], {}
    for situation in root.findall('.//d:situation', NS):
        rec = situation.find('d:situationRecord', NS)
        if rec is None:
            continue
        coords = rec.find('.//d:pointCoordinates', NS)
        if coords is None:
            continue
        try:
            lat = float(coords.findtext('d:latitude', namespaces=NS))
            lon = float(coords.findtext('d:longitude', namespaces=NS))
        except (TypeError, ValueError):
            continue
        values = [v.text.strip() for v in rec.findall('.//d:generalPublicComment/d:comment/d:values/d:value', NS)
                  if v.text and v.attrib.get('lang') == 'bg']
        title = values[0] if values else kind
        row = dict(id=str(rec.attrib.get('id') or situation.attrib.get('id')), kind=kind, title=title,
                   detail=values[1] if len(values) > 1 else title, lat=lat, lon=lon,
                   valid_from=rec.findtext('.//d:overallStartTime', namespaces=NS),
                   valid_until=rec.findtext('.//d:overallEndTime', namespaces=NS))
        if row['id'] in seen:
            if seen[row['id']] != row:
                raise ShapeError(f'ЛИМА: запис {row["id"]} е два пъти с различно съдържание')
            continue
        seen[row['id']] = row
        rows.append(row)
    return rows


# ---------------------------------------------------------------- ДАБДП: the assessed road network (ArcGIS)

RISK_FIELDS = ('FID', 'National_r', 'Section_ID', 'Risk_level', 'Referance_', 'District')
# what each layer had on 24.11.2026 beyond those (road.db: layer 0 without condition or length, 2 and 3 without
# length). Layer 0 names its length L_km; the map has only ever used L__km.
LAYER_FIELDS = {0: (), 1: ('Road_Condi', 'L__km'), 2: ('Road_Condi',), 3: ('Road_Condi',), 4: ('Road_Condi', 'L__km'),
                5: ('Road_Condi', 'L__km')}


def risk_query(layer, offset):
    return f'{ARCGIS}/{layer}/query?' + urllib.parse.urlencode({
        'where': '1=1', 'outFields': '*', 'returnGeometry': 'true', 'outSR': 4326, 'resultOffset': offset,
        'resultRecordCount': 1000, 'f': 'json'})


def risk_count_query(layer):
    return f'{ARCGIS}/{layer}/query?' + urllib.parse.urlencode({'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'})


def _arcgis(raw):
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise Garbled(f'ArcGIS: не е JSON ({first_bytes(raw)})') from e
    if not isinstance(data, dict):
        raise ShapeError('ArcGIS: не е обект')
    if 'error' in data:
        raise ShapeError(f'ArcGIS: {data["error"]}')
    return data


def parse_risk_count(raw):
    count = _arcgis(raw).get('count')
    if not isinstance(count, int):
        raise ShapeError('ArcGIS: няма брояч')
    return count


def parse_risk_page(raw, layer):
    """(sections with a geometry, features on the page). The text fields are written as the pages have always
    shown them; the geometry is the paths of the feature as compact JSON."""
    data = _arcgis(raw)
    names = {f.get('name') for f in data.get('fields') or []}
    missing = [f for f in (*RISK_FIELDS, *LAYER_FIELDS.get(layer, ())) if f not in names]
    if missing:
        raise ShapeError(f'ArcGIS: липсват полета {missing}')
    features = data.get('features')
    if not isinstance(features, list):
        raise ShapeError('ArcGIS: няма features')
    rows = []
    for feature in features:
        attr = feature.get('attributes') or {}
        paths = (feature.get('geometry') or {}).get('paths') or []
        points = [point for path in paths for point in path]
        if not points:
            continue
        lats, lons = [p[1] for p in points], [p[0] for p in points]
        length = attr.get('L__km')
        rows.append(dict(id=f'{layer}:{attr.get("FID")}', layer=layer, fid=attr.get('FID'),
                         road=str(attr.get('National_r') or attr.get('Section_ID') or 'Пътен участък'),
                         risk_level=str(attr.get('Risk_level') or ''), condition=str(attr.get('Road_Condi') or ''),
                         reference=str(attr.get('Referance_') or ''), district=str(attr.get('District') or ''),
                         minlat=min(lats), maxlat=max(lats), minlon=min(lons), maxlon=max(lons),
                         geometry=json.dumps(paths, separators=(',', ':')),
                         length_km=None if length is None else float(length)))
    return rows, len(features)


# ---------------------------------------------------------------- МВР: crashes (ptp.zip)

MVR_FIELDS = {'latitude', 'longitude', 'crashdatetime', 'crashtype', 'diedcount', 'injuredcount', 'region', 'municipality'}


def parse_mvr(raw, min_rows=10000):
    """(crashes with coordinates and a date, the rows left out, the rows of the file). row_no is the place of the
    row in the file, counting every row: it is the number the pages have always shown."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise Garbled(f'МВР: не е ZIP ({first_bytes(raw)})') from e
    with archive:
        name = next((n for n in archive.namelist() if n.lower().endswith('.csv')), None)
        if not name:
            raise ShapeError('МВР: в архива няма CSV')
        try:
            text = archive.read(name).decode('utf-8-sig')
        except (UnicodeDecodeError, zipfile.BadZipFile) as e:
            raise ShapeError(f'МВР: CSV не се чете ({e})') from e
    reader = csv.DictReader(io.StringIO(text, newline=''))
    if not reader.fieldnames or not MVR_FIELDS.issubset(reader.fieldnames):
        raise ShapeError(f'МВР: непознати колони {reader.fieldnames}')
    rows, skipped, total = [], 0, 0
    for index, item in enumerate(reader, 1):
        total += 1
        try:
            lat, lon = float(item['latitude']), float(item['longitude'])
            if not inside(lat, lon):
                raise ValueError('извън България')
            at = item['crashdatetime'].strip()
            if not re.match(r'^20\d\d-\d\d-\d\d', at):
                raise ValueError('невалидна дата')
            rows.append(dict(row_no=index, at=at, lat=lat, lon=lon,
                             type=(item.get('crashtype') or 'Пътнотранспортно произшествие').strip(),
                             killed=int(item['diedcount'] or 0), injured=int(item['injuredcount'] or 0),
                             region=(item['region'] or '').strip() or None,
                             municipality=(item['municipality'] or '').strip() or None))
        except (TypeError, ValueError, KeyError, AttributeError):
            skipped += 1
    if len(rows) + skipped != total:   # every row of the file is in exactly one of the two
        raise ShapeError(f'МВР: {len(rows)} + {skipped} не е {total}')
    if len(rows) < min_rows:
        raise ShapeError(f'МВР: непълен архив, {len(rows)} записа с координати')
    return rows, skipped, total

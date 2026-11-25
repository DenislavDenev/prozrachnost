"""The parsers on the real answers in tests/fixtures (shortened, not written by hand), and their edge cases."""
import datetime as dt
import io
import json
import urllib.error
import zipfile

import pytest

from ingest import http, sources
from ingest.pipeline import ShapeError
from tests.conftest import fixture


def test_toll_answers_as_the_source_gives_them():
    traffic, skipped = sources.parse_toll(fixture('bgtoll', 'traffic.json'), 'traffic')
    assert (len(traffic), skipped) == (4, 0)
    first = traffic[0]
    assert isinstance(first['lat'], float) and isinstance(first['count15'], int) and isinstance(first['speed15'], float)
    # БГТОЛ writes its clock without a zone and documents it as UTC: 15:27 UTC is 17:27 in Sofia
    assert first['observed'].tzinfo == dt.timezone.utc
    weather, _ = sources.parse_toll(fixture('bgtoll', 'weather.json'), 'weather')
    assert [w['scp'] for w in weather] == ['1001', '1004', '1005', '1006']
    assert weather[1]['surface'] == 0.0 and isinstance(weather[1]['surface'], float)


def test_a_body_that_is_not_json_is_garbled_a_missing_field_is_a_new_shape():
    for body in (b'\r\n<!DOCTYPE html><html>', b'', b'\xef\xbb\xbf[\r\n    {\r\n'):
        with pytest.raises(sources.Garbled):
            sources.parse_toll(body, 'weather')
    data = json.loads(fixture('bgtoll', 'weather.json'))
    del data[0]['air']
    with pytest.raises(ShapeError) as e:
        sources.parse_toll(json.dumps(data).encode(), 'weather')
    assert not isinstance(e.value, sources.Garbled)
    data = json.loads(fixture('bgtoll', 'traffic.json'))
    data[1]['count15min'] = 'много'
    with pytest.raises(ShapeError):
        sources.parse_toll(json.dumps(data).encode(), 'traffic')
    data = json.loads(fixture('bgtoll', 'traffic.json'))
    data[1]['scp'] = data[0]['scp']
    with pytest.raises(ShapeError):
        sources.parse_toll(json.dumps(data).encode(), 'traffic')


def test_a_station_outside_bulgaria_or_without_coordinates_is_left_out_and_counted():
    data = json.loads(fixture('bgtoll', 'weather.json'))
    data[0]['lat'], data[1]['lon'] = None, '30.5'
    rows, skipped = sources.parse_toll(json.dumps(data).encode(), 'weather')
    assert (len(rows), skipped) == (2, 2)


def test_lima_files_declare_utf16_and_are_utf8():
    rows = sources.parse_datex(fixture('lima', '20261124_roadworks_r01.xml'), 'Затворен път')
    assert len(rows) == 12 and all(r['kind'] == 'Затворен път' for r in rows)
    assert rows[0]['valid_from'].endswith('+0200') or rows[0]['valid_from'].endswith('+0300')
    assert len({r['id'] for r in rows}) == 12
    assert len(sources.parse_datex(fixture('lima', '20261124_danger_d01.xml'), 'Опасност')) == 1
    with pytest.raises(sources.Garbled):
        sources.parse_datex(b'<html><body>502 Bad Gateway', 'Опасност')
    with pytest.raises(ShapeError):
        sources.parse_datex(b'<?xml version="1.0"?><other/>', 'Опасност')
    assert sources.catalog_xml(fixture('lima', 'catalog-r01.html')) == 'https://datasheet.api.bg/files/20261124_roadworks_r01.xml'


def test_arcgis_page_and_count():
    rows, n = sources.parse_risk_page(fixture('arcgis', 'layer5.json'), 5)
    assert n == 3 and len(rows) == 3
    assert rows[0]['id'] == '5:1' and rows[0]['road'] == 'I-5' and rows[0]['reference'] == '2023' and rows[0]['district'] == 'RSE'
    assert rows[0]['minlat'] <= json.loads(rows[0]['geometry'])[0][0][1] <= rows[0]['maxlat']
    assert sources.parse_risk_count(b'{"count":9569}') == 9569
    with pytest.raises(ShapeError):
        sources.parse_risk_page(b'{"error":{"code":400,"message":"Invalid query"}}', 5)
    data = json.loads(fixture('arcgis', 'layer5.json'))
    data['fields'] = [f for f in data['fields'] if f['name'] != 'L__km']
    with pytest.raises(ShapeError):
        sources.parse_risk_page(json.dumps(data).encode(), 5)    # the length of layer 5 is what the chart sums
    rows, n = sources.parse_risk_page(fixture('arcgis', 'layer0.json'), 0)   # layer 0 never had a condition or a length
    assert n == 3 and rows[0]['condition'] == '' and rows[0]['length_km'] is None


def test_every_row_of_the_crash_file_is_a_crash_or_left_out():
    rows, skipped, total = sources.parse_mvr(fixture('mvr', 'ptp.zip'), min_rows=1)
    assert (len(rows), skipped, total) == (44, 1, 45)
    assert rows[0]['row_no'] == 1 and rows[0]['region'] == 'ПЕРНИК' and rows[0]['type'] == 'блъскане в крайпътно съоръжение'
    assert all(r['type'] == r['type'].strip() for r in rows)
    assert rows[1]['type'] == 'сблъскване между МПС странично'   # the source pads it with spaces
    with pytest.raises(ShapeError):
        sources.parse_mvr(fixture('mvr', 'ptp.zip'))    # a short archive is not the archive
    with pytest.raises(sources.Garbled):
        sources.parse_mvr(b'<html>403</html>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('TrafficAccidents.csv', 'latitude,longitude\n42,23\n')
    with pytest.raises(ShapeError):
        sources.parse_mvr(out.getvalue(), min_rows=1)


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def sleep(self, s):
        self.slept.append(round(s, 1))
        self.now += s


def test_the_server_of_api_gets_a_pause_and_a_refused_connection_is_asked_again(monkeypatch):
    """datasheet.api.bg refuses connections for a few seconds after a quick series of requests (24.11.2026: refused
    at once, answered 10 s later). The reader waits between requests to it and asks again after a refusal."""
    clock, calls = Clock(), []
    monkeypatch.setattr(http.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(http, '_last', {})

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 2:
            raise urllib.error.URLError(OSError(113, 'No route to host'))
        return Answer(b'ok')
    monkeypatch.setattr(http.urllib.request, 'urlopen', urlopen)
    assert http.get('https://datasheet.api.bg/?c=r01', sleep=clock.sleep) == b'ok'
    assert http.get('https://datasheet.api.bg/files/a.xml', sleep=clock.sleep, delay=20) == b'ok'
    assert len(calls) == 3
    # the second request kept its distance (8 s), the refusal waited (20 s) and the request after it went at once
    assert clock.slept == [0.0, 8.0, 20, 0.0]
    with pytest.raises(http.Refused):
        monkeypatch.setattr(http.urllib.request, 'urlopen', lambda req, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, None)))
        http.get('https://www.mvr.bg/PTPShapeResult/ptp.zip', sleep=clock.sleep)


def test_the_read_every_15_minutes_does_not_load_the_geometry():
    """It runs beside the heavy jobs without their lock and must stay under 100 MB: shapely, numpy and pyproj (about
    20 MB) come only when a station of БГТОЛ is new or moved."""
    import subprocess
    import sys
    code = '''import sys
from ingest import run, derive
class Conn:
    def execute(self, *a):
        return self
    def fetchall(self):
        return []
assert derive.bearings_from_sections(Conn()) == 0
print(sorted(m for m in ('shapely', 'numpy', 'pyproj') if m in sys.modules))'''
    from pathlib import Path
    out = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parent.parent,
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == '[]'

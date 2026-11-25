"""The test database: the foundation of tools/danni migrated with its places loaded from the real EKATTE archive by
danni's own step, then the tables of Пътна обстановка. Built once for the session; every database test starts with
the tables of this tool empty. The sources are the real answers in tests/fixtures, served by a fake get()."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from ingest import pipeline, run, sources

ROOT = Path(__file__).resolve().parent.parent
DANNI = ROOT.parent / 'danni'           # the foundation is not copied into the tool: the tests need the sibling folder
FIX = ROOT / 'tests' / 'fixtures'
SCHEMAS = ('api_v1', 'pub_patna_obstanovka', 'pub', 'patna_obstanovka', 'patna_obstanovka_src', 'danni_src', 'core', 'ops')
TOOL = 'patna-obstanovka'


def rebuild(dsn, tmp):
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        for schema in SCHEMAS:
            c.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
    (tmp / 'archive' / 'ekatte').mkdir(parents=True)
    shutil.copy(DANNI / 'tests' / 'fixtures' / 'ekatte.zip', tmp / 'archive' / 'ekatte' / '2026-09-29.002862bf0655.zip')
    env = {**os.environ, 'DANNI_DSN': dsn, 'DANNI_ARCHIVE': str(tmp / 'archive'), 'DANNI_DATA': str(tmp / 'danni')}
    for step in ('migrate', 'place'):
        subprocess.run([sys.executable, '-m', 'ingest.run', '--step', step], cwd=DANNI, env=env, check=True, capture_output=True)
    with pipeline.connect(dsn) as c:
        pipeline.require_core(c, '0007')
        pipeline.migrate(c, TOOL, ROOT)
    # danni again, now that the datasets of this tool are registered: their right to be shown comes from its list
    subprocess.run([sys.executable, '-m', 'ingest.run', '--step', 'check'], cwd=DANNI, env=env, capture_output=True)
    with pipeline.connect(dsn) as c:
        assert c.execute('SELECT bool_and(is_displayable) FROM core.dataset WHERE tool = %s', (TOOL,)).fetchone()[0]


@pytest.fixture(scope='session')
def dsn(tmp_path_factory):
    dsn = os.environ.get('PATNA_OBSTANOVKA_TEST_DSN')
    if not dsn:
        pytest.skip('PATNA_OBSTANOVKA_TEST_DSN required')
    assert 'test' in psycopg.conninfo.conninfo_to_dict(dsn).get('dbname', '')
    rebuild(dsn, tmp_path_factory.mktemp('foundation'))
    return dsn


@pytest.fixture
def conn(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'RAW', tmp_path / 'raw')
    with pipeline.connect(dsn) as c:
        pipeline.require_test_db(c)
        c.execute('''TRUNCATE patna_obstanovka.crash_match, patna_obstanovka.station_bearing, patna_obstanovka.crash,
            patna_obstanovka.risk_section, patna_obstanovka_src.toll_answer, patna_obstanovka_src.traffic_reading,
            patna_obstanovka_src.weather_reading, patna_obstanovka_src.lima_event, patna_obstanovka_src.lima_file,
            patna_obstanovka_src.risk_layer, patna_obstanovka_src.risk_section, patna_obstanovka_src.crash_file,
            patna_obstanovka_src.crash, pub_patna_obstanovka.traffic, pub_patna_obstanovka.weather, pub_patna_obstanovka.event,
            pub_patna_obstanovka.crash_match, pub_patna_obstanovka.crash, pub_patna_obstanovka.risk_section,
            pub_patna_obstanovka.source_state RESTART IDENTITY''')
        c.execute('DELETE FROM pub.publication WHERE tool = %s', (TOOL,))
        c.execute('UPDATE ops.check_rule SET is_active = true WHERE tool = %s', (TOOL,))
        for table in ('check_result', 'change_log', 'hold', 'fetch', 'load', 'run'):
            c.execute(f'DELETE FROM ops.{table} WHERE tool = %s', (TOOL,))
        yield c


def fixture(*parts):
    return (FIX.joinpath(*parts)).read_bytes()


def empty_layer():
    """A layer page with no features: the real answer of layer 5 without its features."""
    data = json.loads(fixture('arcgis', 'layer5.json'))
    data['features'], data['exceededTransferLimit'] = [], False
    return json.dumps(data).encode()


def answers(**override):
    """{url: bytes} of every source as the real fixtures have it. ArcGIS gives layers 0 and 5 (three sections each)."""
    out = {
        sources.TRAFFIC: fixture('bgtoll', 'traffic.json'),
        sources.WEATHER: fixture('bgtoll', 'weather.json'),
        sources.MVR: fixture('mvr', 'ptp.zip'),
    }
    for source, group, code, kind in sources.LIMA:
        name = {'r01': '20261124_roadworks_r01.xml', 'r02': '20261124_roadworks_r01.xml', 'd01': '20261124_danger_d01.xml'}[code]
        out[sources.CATALOG.format(group=group, code=code)] = f'<a href="/files/{code}-{name}">XML</a>'.encode()
        out[f'https://datasheet.api.bg/files/{code}-{name}'] = fixture('lima', name)
    for layer in sources.RISK_LAYERS:
        page = fixture('arcgis', f'layer{layer}.json') if layer in (0, 5) else empty_layer()
        n = len(json.loads(page)['features'])
        out[sources.risk_count_query(layer)] = json.dumps({'count': n}).encode()
        out[sources.risk_query(layer, 0)] = page
        out[sources.risk_query(layer, n)] = empty_layer()
    out.update(override)
    return out


def getter(table):
    """A get() that answers from the table; an exception in it is raised, a missing address is a refusal."""
    from ingest import http

    def get(url, **_):
        if url not in table:
            raise http.Refused(f'HTTP 404 {url}')
        value = table[url]
        if isinstance(value, Exception):
            raise value
        return value
    return get


OLD_SCHEMA = '''
CREATE TABLE traffic(scp TEXT PRIMARY KEY,name TEXT,lat REAL,lon REAL,count15 INTEGER,count60 INTEGER,speed15 REAL,observed TEXT,bearing REAL);
CREATE TABLE weather(scp TEXT PRIMARY KEY,name TEXT,lat REAL,lon REAL,air REAL,surface REAL,humidity REAL,wind REAL,observed TEXT);
CREATE TABLE events(id TEXT PRIMARY KEY,kind TEXT,title TEXT,detail TEXT,lat REAL,lon REAL,valid_from TEXT,valid_until TEXT,source_url TEXT);
CREATE TABLE risk(id TEXT PRIMARY KEY,layer INTEGER,road TEXT,risk_level TEXT,condition TEXT,reference TEXT,district TEXT,minlat REAL,maxlat REAL,minlon REAL,maxlon REAL,geometry TEXT,length_km REAL);
CREATE TABLE crashes(id TEXT PRIMARY KEY,date TEXT,lat REAL,lon REAL,description TEXT,killed INTEGER,injured INTEGER);
CREATE TABLE crash_matches(crash_id TEXT PRIMARY KEY,risk_id TEXT NOT NULL,distance_m REAL NOT NULL);
CREATE TABLE sources(key TEXT PRIMARY KEY,label TEXT,url TEXT,updated TEXT,count INTEGER,error TEXT);
'''


@pytest.fixture
def road_db(tmp_path):
    """A road.db as the SQLite version wrote it (its schema of 27.09.2026), with a few rows of each table."""
    path = tmp_path / 'road.db'
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    db.executemany('INSERT INTO traffic VALUES(?,?,?,?,?,?,?,?,?)', [
        ('20912', 'Рудник', 42.960022, 27.768303, 51, 228, 18.0, '2026-11-24T15:27:02+00:00', 344.6),
        ('10132', 'Долна Градешница', 41.684632, 23.181644, 80, 324, 25.9, '2026-11-24T15:27:02+00:00', None)])
    db.executemany('INSERT INTO weather VALUES(?,?,?,?,?,?,?,?,?)', [
        ('1001', 'Мотел Ихтиман', 42.426981, 23.854053, -0.23523, 1.958613, 58.676414, 28.906227, '2026-11-24T15:30:00+00:00')])
    db.executemany('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)', [
        ('e1', 'Затворен път', 'Ремонт', 'Ремонт на път III-868', 41.49, 24.84, '2025-11-19T12:00:00+0200', '2026-12-31T23:59:59+0200',
         'https://datasheet.api.bg/files/20261124_roadworks_r01.xml'),
        ('e2', 'Затворена лента', 'Лента', 'Лента', 42.1, 23.4, None, None, 'https://datasheet.api.bg/files/20261124_roadworks_r02.xml')])
    db.executemany('INSERT INTO risk VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [
        ('5:1', 5, 'I-1', 'HIGH', 'GOOD', '2023', 'SOF', 42.4, 42.6, 23.5, 23.5, '[[[23.5,42.4],[23.5,42.6]]]', 10.0),
        ('0:7', 0, 'I-2', 'LOW', '', '2023', 'VID', 42.5, 42.5, 23.4, 23.6, '[[[23.4,42.5],[23.6,42.5]]]', None)])
    db.executemany('INSERT INTO crashes VALUES(?,?,?,?,?,?,?)', [
        ('1', '2026-09-26 10:00:00', 42.45, 23.5, 'Сблъсък', 1, 2),
        ('3', '2026-09-26 10:00:00', 42.45, 23.5, 'Сблъсък', 0, 0),
        ('4', '2026-09-27 11:00:00', 42.5, 23.55, 'ПТП', 0, 1)])
    db.execute("INSERT INTO crash_matches VALUES('1','5:1',2.0)")
    db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?)', [
        ('traffic', 'БГТОЛ · измерен трафик', sources.TRAFFIC, '2026-11-24T15:30:03+00:00', 2, None),
        ('weather', 'БГТОЛ · метеостанции', sources.WEATHER, '2026-11-24T15:30:03+00:00', 1, None),
        ('lima_r01', 'АПИ ЛИМА · затворени пътища', 'https://datasheet.api.bg/files/20261124_roadworks_r01.xml', '2026-11-24T15:30:17+00:00', 1, None),
        ('lima_r02', 'АПИ ЛИМА · затворени ленти', 'https://datasheet.api.bg/?lang=bg&g=roadworks&c=r02', '2026-11-24T15:18:18+00:00', 1, '<urlopen error [Errno 113] No route to host>'),
        ('lima_d01', 'АПИ ЛИМА · инциденти', 'https://datasheet.api.bg/files/20261124_danger_d01.xml', '2026-11-24T15:30:18+00:00', 0, None),
        ('risk', 'ДАБДП · оценка на пътния риск', sources.ARCGIS, '2026-11-07T12:35:24+00:00', 2, 'database is locked'),
        ('crashes', 'МВР · катастрофи', sources.MVR, '2026-11-23T01:17:05+00:00', 3, 'HTTP Error 403: Forbidden'),
        ('matches', 'Свързани ПТП с пътни участъци', sources.MVR, '2026-11-23T01:17:32+00:00', 1, None)])
    db.commit()
    db.close()
    return path

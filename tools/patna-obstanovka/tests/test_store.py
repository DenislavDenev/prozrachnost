"""The steps against the test database: reads, holds, refusals, history, the links to core, the copy from road.db
and the comparison with it. The sources are the real answers in tests/fixtures."""
import csv
import datetime as dt
import io
import json
import sqlite3
import zipfile

import pytest

from ingest import derive, http, pipeline, run, sources, store
from tests.conftest import TOOL, answers, fixture, getter


@pytest.fixture(autouse=True)
def old_measurements_allowed(monkeypatch):
    # the fixtures were measured on 24.11.2026; the rule on old measurements has its own test
    monkeypatch.setattr(run, 'OBSERVATION_MAX_AGE', dt.timedelta(days=36500))


def count(conn, table):
    return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def status(conn):
    return {r[0]: r[1:] for r in conn.execute('SELECT key, url, updated, count, error FROM pub_patna_obstanovka.source_state')}


def test_current_reads_every_source_and_publishes(conn):
    result = run.perform(conn, 'current', get=getter(answers()))
    assert (result['problems'], result['rejected']) == ([], [])
    assert result['stats']['publication']
    assert (count(conn, 'pub_patna_obstanovka.traffic'), count(conn, 'pub_patna_obstanovka.weather')) == (4, 4)
    assert count(conn, 'pub_patna_obstanovka.event') == 12 + 12 + 1
    s = status(conn)
    assert s['traffic'][2:] == (4, None) and s['lima_d01'][2:] == (1, None)
    assert s['lima_r01'][0].endswith('r01-20261124_roadworks_r01.xml')    # the file of the day, as before
    assert pipeline.freshness(conn, TOOL) == ['МВР · катастрофи: няма успешно четене',
                                              'ДАБДП · оценка на пътния риск: няма успешно четене']
    assert run.freshness(conn, 'current') == []
    # the same answers again: nothing new is written; only the time of the last good read moves, as /api/status
    # has always shown it
    conn.execute("UPDATE ops.load SET loaded_at = loaded_at - interval '1 minute' WHERE tool = %s", (TOOL,))
    again = run.perform(conn, 'current', get=getter(answers()))
    assert again['stats']['unchanged'] == 5
    assert conn.execute('SELECT changes FROM pub.publication WHERE publication_id = %s',
                        (again['stats']['publication'],)).fetchone()[0].keys() == {'pub_patna_obstanovka.source_state'}


def test_a_failed_read_keeps_the_last_answer_until_the_source_is_stale(conn):
    run.perform(conn, 'current', get=getter(answers()))
    before = status(conn)['weather']
    catalog = sources.CATALOG.format(group='danger', code='d01')
    result = run.perform(conn, 'current', get=getter(answers(**{
        sources.WEATHER: b'\r\n<!DOCTYPE html><html><body>Bad gateway</body></html>',
        catalog: RuntimeError('No route to host след 4 опита: ' + catalog)})))
    # neither is a problem yet: the map keeps the last good answers and says what failed
    assert result['problems'] == [] and len(result['rejected']) == 2
    assert count(conn, 'pub_patna_obstanovka.weather') == 4
    after = status(conn)
    assert after['weather'][1:3] == before[1:3] and after['weather'][3].startswith('невалиден отговор: не е JSON')
    assert after['lima_d01'][0] == catalog and 'No route to host' in after['lima_d01'][3]
    # the refused answer is kept, to show what came
    assert conn.execute("SELECT raw_file_id IS NOT NULL FROM ops.load WHERE tool = %s AND source = 'weather' AND status = 'rejected'",
                        (TOOL,)).fetchone()[0]
    assert run.freshness(conn, 'current') == []
    # the source stays silent past its threshold: now it is a problem
    conn.execute("UPDATE ops.load SET loaded_at = loaded_at - interval '30 minutes' WHERE tool = %s AND source = 'weather'", (TOOL,))
    assert [p for p in run.freshness(conn, 'current') if p.startswith('БГТОЛ · метеостанции: последно успешно четене')]


def test_an_answer_in_a_new_shape_is_a_problem_at_once(conn):
    data = json.loads(fixture('bgtoll', 'weather.json'))
    for item in data:
        del item['air']
    result = run.perform(conn, 'current', get=getter(answers(**{sources.WEATHER: json.dumps(data).encode()})))
    assert result['problems'] == ["weather: БГТОЛ: липсват полета ['air']"]
    assert count(conn, 'pub_patna_obstanovka.traffic') == 4


def test_a_short_answer_is_held_and_the_map_keeps_the_full_one(conn):
    run.perform(conn, 'current', get=getter(answers()))
    short = json.loads(fixture('bgtoll', 'traffic.json'))[:1]
    for _ in range(2):
        run.perform(conn, 'current', get=getter(answers(**{sources.TRAFFIC: json.dumps(short).encode()})))
    assert conn.execute("SELECT status FROM ops.load WHERE tool = %s AND source = 'traffic' ORDER BY load_id DESC LIMIT 1",
                        (TOOL,)).fetchone()[0] == 'held'
    assert count(conn, 'pub_patna_obstanovka.traffic') == 4
    assert status(conn)['traffic'][3].startswith('задържан до второ четене')
    conn.execute("UPDATE ops.hold SET first_at = first_at - interval '2 hours' WHERE tool = %s", (TOOL,))
    assert [p for p in run.freshness(conn, 'current') if p.startswith('traffic ') and 'задържан' in p]
    # one station less is how the source is: accepted, and logged as its own line
    run.perform(conn, 'current', get=getter(answers(**{sources.TRAFFIC: json.dumps(json.loads(fixture('bgtoll', 'traffic.json'))[:3]).encode()})))
    assert count(conn, 'pub_patna_obstanovka.traffic') == 3
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = %s AND source = 'traffic' AND cause = 'removed'", (TOOL,)).fetchone()[0] == 1
    # the landing zone keeps only the current answer (policy `latest`)
    assert conn.execute('SELECT count(DISTINCT load_id) FROM patna_obstanovka_src.traffic_reading').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM patna_obstanovka_src.toll_answer').fetchone()[0] == 2


def test_old_measurements_are_a_problem(conn, monkeypatch):
    run.perform(conn, 'current', get=getter(answers()))
    monkeypatch.setattr(run, 'OBSERVATION_MAX_AGE', dt.timedelta(seconds=1))   # the fixtures are older than that
    problems = run.freshness(conn, 'current')
    assert any(p.startswith('БГТОЛ · измерен трафик: последното измерване е от') for p in problems)


def test_a_refused_crash_file_is_recorded_not_fatal(conn):
    result = run.perform(conn, 'mvr', get=getter(answers(**{sources.MVR: http.Refused('HTTP 403 ' + sources.MVR)})), min_rows=1)
    assert result['problems'] == [] and result['rejected'] == ['crashes: HTTP 403 ' + sources.MVR]
    assert status(conn)['crashes'] == (sources.MVR, None, 0, 'HTTP 403 ' + sources.MVR)
    # three days without a file is a problem of the scheduled step
    assert run.freshness(conn, 'mvr') == ['МВР · катастрофи: няма успешно четене']


def changed_zip(change):
    text = zipfile.ZipFile(io.BytesIO(fixture('mvr', 'ptp.zip'))).read('TrafficAccidents.csv').decode('utf-8-sig')
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = change(rows)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('TrafficAccidents.csv', out.getvalue().encode('utf-8'))
    return data.getvalue()


def test_crashes_keep_their_history_and_link_to_the_municipality(conn):
    assert run.perform(conn, 'mvr', get=getter(answers()), min_rows=1)['problems'] == []
    assert count(conn, 'pub_patna_obstanovka.crash') == 44
    codes = dict(conn.execute('''SELECT c.municipality, k.municipality_code FROM patna_obstanovka_src.crash c
        JOIN patna_obstanovka.crash k ON k.row_no = c.row_no AND k.superseded_at IS NULL'''))
    # the capital is СОФИЯ and the oblast СОФИЙСКА in the file of МВР; ДОБРИЧ-ГРАД is the municipality Добрич
    assert (codes['СТОЛИЧНА'], codes['КОСТИНБРОД'], codes['ДОБРИЧ-ГРАД'], codes['ДОБРИЧКА'], codes['ПЕРНИК']) == \
        ('SOF46', 'SFO26', 'DOB28', 'DOB15', 'PER32')
    assert None not in codes.values()

    def more_killed(rows):
        rows[2]['diedcount'] = str(int(rows[2]['diedcount']) + 1)
        return rows
    run.perform(conn, 'mvr', get=getter(answers(**{sources.MVR: changed_zip(more_killed)})), min_rows=1)
    # the earlier version stays, superseded; the new one is shown
    row_no = 3
    versions = conn.execute('SELECT killed, superseded_at IS NULL FROM patna_obstanovka.crash WHERE row_no = %s ORDER BY crash_id',
                            (row_no,)).fetchall()
    assert len(versions) == 2 and versions[0][1] is False and versions[1] == (versions[0][0] + 1, True)
    assert conn.execute("SELECT killed FROM pub_patna_obstanovka.crash WHERE id = '3'").fetchone()[0] == versions[1][0]
    assert conn.execute("SELECT count(*) FROM ops.change_log WHERE tool = %s AND source = 'crashes' AND cause = 'rewritten'",
                        (TOOL,)).fetchone()[0] == 1
    # a file with a crash fewer than the current one is held: the archive only grows
    run.perform(conn, 'mvr', get=getter(answers(**{sources.MVR: changed_zip(lambda rows: rows[:-1])})), min_rows=1)
    assert conn.execute("SELECT status FROM ops.load WHERE tool = %s AND source = 'crashes' ORDER BY load_id DESC LIMIT 1",
                        (TOOL,)).fetchone()[0] == 'held'
    assert count(conn, 'pub_patna_obstanovka.crash') == 44


def test_the_sections_are_reconciled_with_the_count_of_the_source(conn):
    wrong = answers(**{sources.risk_count_query(5): b'{"count":4}'})
    result = run.perform(conn, 'risk', get=getter(wrong), min_rows=1)
    assert result['problems'] == ['risk: ArcGIS: слой 5 има 4 обекта, прочетени 3']
    assert count(conn, 'patna_obstanovka.risk_section') == 0
    assert run.perform(conn, 'risk', get=getter(answers()), min_rows=1)['problems'] == []
    assert count(conn, 'pub_patna_obstanovka.risk_section') == 6
    assert conn.execute("SELECT oblast_code FROM patna_obstanovka.risk_section WHERE section_id = '5:1'").fetchone()[0] == 'RSE'
    assert conn.execute('SELECT sum(features), sum(counted) FROM patna_obstanovka_src.risk_layer').fetchone() == (6, 6)


def test_arrows_and_crashes_linked_to_sections(conn):
    """Synthetic geometry: a north-south section and an east-west one that cross."""
    sections = [dict(id='5:1', layer=5, fid=1, road='I-1', risk_level='HIGH', condition='GOOD', reference='2023', district='SOF',
                     minlat=42.4, maxlat=42.6, minlon=23.5, maxlon=23.5, geometry='[[[23.5,42.4],[23.5,42.6]]]', length_km=10.0),
                dict(id='5:2', layer=5, fid=2, road='I-2', risk_level='LOW', condition='BAD', reference='2023', district='SOF',
                     minlat=42.5, maxlat=42.5, minlon=23.4, maxlon=23.6, geometry='[[[23.4,42.5],[23.6,42.5]]]', length_km=1.0)]
    stations = [dict(scp=s, name='Станция', lat=lat, lon=lon, count15=4, count60=16, speed15=None,
                     observed=dt.datetime(2026, 11, 24, 8, tzinfo=dt.timezone.utc))
                for s, lat, lon in (('12341', 42.45, 23.5), ('12342', 42.45, 23.5), ('56781', 42.5, 23.55))]
    crashes = [dict(row_no=i, at='2026-11-20 10:00:00', lat=lat, lon=lon, type='ПТП', killed=k, injured=0)
               for i, (lat, lon, k) in enumerate([(42.45, 23.5, 1), (42.4501, 23.5001, 0), (42.5, 23.5, 0)], 1)]
    with pipeline.run(conn, TOOL, 'test') as r:
        pipeline.load(conn, r, 'risk', sources.ARCGIS, store.risk_rows(sections), store.risk_writer(sections, {5: (2, 2)}))
        pipeline.load(conn, r, 'traffic', sources.TRAFFIC, store.toll_rows(stations), store.toll_writer('traffic', stations, 0))
        pipeline.load(conn, r, 'crashes', sources.MVR, store.crash_rows(crashes), store.crash_writer(crashes, 0, 3))
    assert derive.bearings_from_sections(conn) == 3
    bearings = dict(conn.execute('SELECT scp, bearing FROM patna_obstanovka.station_bearing'))
    assert min(bearings['12341'], 360 - bearings['12341']) < 15     # increasing kilometre: north
    assert abs(bearings['12342'] - 180) < 15                        # the other direction
    assert abs(bearings['56781'] - 90) < 15
    matches = derive.crash_matches(conn)
    # the third crash is on the crossing: two sections at the same distance, not linked to either
    assert [m[1] for m in matches] == ['5:1', '5:1'] and matches[0][2] < 1 and 0 < matches[1][2] < 15


def test_copy_from_road_db_and_parity(conn, road_db):
    result = run.perform(conn, 'copy', old_db=road_db)
    assert result['problems'] == [] and result['stats']['errors'] == 3
    s = status(conn)
    assert s['risk'] == (sources.ARCGIS, '2026-11-07T12:35:24+00:00', 2, 'database is locked')
    assert s['lima_r02'] == ('https://datasheet.api.bg/?lang=bg&g=roadworks&c=r02', '2026-11-24T15:18:18+00:00', 1,
                             '<urlopen error [Errno 113] No route to host>')
    assert s['lima_d01'][2:] == (0, None)
    parity = run.perform(conn, 'parity', old_db=road_db)
    assert parity['problems'] == [] and all(v['differ'] == 0 for k, v in parity['stats'].items() if isinstance(v, dict))
    with pytest.raises(RuntimeError):
        run.perform(conn, 'copy', old_db=road_db)    # once only
    # a difference in one field of one row is seen
    db = sqlite3.connect(road_db)
    db.execute("UPDATE crashes SET description = 'друго' WHERE id = '4'")
    db.commit()
    db.close()
    assert run.perform(conn, 'parity', old_db=road_db)['problems'] == ['Редове, различни в поне едно поле, във всички таблици: старо 0, ново 1']

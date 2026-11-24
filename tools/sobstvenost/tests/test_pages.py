"""The pages, rendered from the serving layer of a real publication."""
import csv
import io

from app import queries as Q
from ingest import parse, pipeline, run, store

from . import seed


def publish(conn, pages=None, places=None):
    pages = pages or seed.answers()
    if places is not None:
        pages[seed.NOTICE] = pages[seed.NOTICE].replace('Област: Пазарджик, Община: Пазарджик'.encode(), places.encode())
    with pipeline.run(conn, 'sobstvenost', 'refresh') as r:
        seed.load_all(conn, r, pages)
        assert run.finish(conn, r) == []
    Q._cache.clear()
    return r.stats['publication']


def test_all_pages_answer_with_their_publication(conn, site):
    pid = publish(conn)
    for url in ['/', '/?view=concessions', '/predpriyatiya', '/predpriyatiya/id/105', '/predpriyatiya/831646048', '/koncesii',
                '/koncesii/' + seed.CONCESSION, '/karta', '/api/karta.json', '/export-map.csv', '/export-enterprises.csv',
                '/export-concessions.csv', '/sources', '/how', '/rights', '/healthz']:
        response = site.get(url)
        assert response.status_code == 200, url + ' ' + response.text[:400]
        assert response.headers['X-Publication'] == str(pid), url
        if url.endswith(('.csv', '.json')) or url == '/healthz':
            continue
        assert 'Обратна връзка' in response.text and 'Подкрепи проекта' in response.text, url
    assert site.get('/predpriyatiya/831646049').status_code == 404
    assert site.get('/export-map.csv?l=invalid').status_code == 400
    page = site.get('/predpriyatiya/id/105').text
    assert 'companies/831646048' in page and 'не дати на назначение' in page
    assert 'Иван Атанасов Кунев' in page        # the board in its public role
    version = conn.execute("SELECT sha256 FROM pub_sobstvenost.report_version WHERE appk_id = '105' LIMIT 1").fetchone()[0]
    assert version in page
    concession = site.get('/koncesii/' + seed.CONCESSION).text
    assert 'КАРИЕРИ ПАЗАРДЖИК' in concession and '180 месеца' in concession


def test_filters_summary_csv_match(conn, site):
    publish(conn)
    params = dict(participation='direct', q='АВТО', status='Активно')
    rows = list(csv.DictReader(io.StringIO(site.get('/export-enterprises.csv', params=params).text.lstrip('﻿'))))
    assert len(rows) == 1 and rows[0]['id'] == '105'
    assert '1 предприятия' in site.get('/predpriyatiya', params=params).text
    assert '1 предприятия' in site.get('/', params=params).text
    direct = list(csv.DictReader(io.StringIO(site.get('/export-enterprises.csv?participation=direct').text.lstrip('﻿'))))
    assert len(direct) == 2
    missing = next(r for r in csv.DictReader(io.StringIO(site.get('/export-enterprises.csv').text.lstrip('﻿'))) if r['id'] == '309')
    assert missing['share_pct'] == ''


def test_map_verified_dedup_ambiguous_and_filter(conn, site):
    # Бяла is a municipality in two oblasti: without its oblast in the notice it is not placed
    publish(conn, places='Област: Пазарджик, Община: Пазарджик, Община: Бяла')
    data = Q.mapdata()
    assert data['mapped'] == 1 and data['unmatched'] == 1
    assert sum(r['v'] or 0 for r in data['items']) == 1
    assert all(r['v'] is None for r in data['items'] if r['name'] == 'Бяла')
    country = Q.mapdata('darzhava')
    assert country['bg'] == 1 and country['items'][0]['v'] == 1
    none = Q.mapdata(kind='Държавна', q='does-not-exist')
    assert none['count'] == 0 and none['bg'] is None
    assert all(r['v'] is None for r in none['items'])
    assert Q.mapdata(subject='does-not-exist')['mapped'] == 0
    assert Q.mapdata(subject='Концесия за услуги')['filters']['subject'] == 'Концесия за услуги'


def test_multi_municipality_same_national_concession(conn, site):
    publish(conn, places='Област: Пазарджик, Община: Пазарджик, Област: Пазарджик, Община: Велинград')
    municipalities = Q.mapdata()
    assert sum(r['v'] or 0 for r in municipalities['items']) == 2
    country = Q.mapdata('darzhava')
    assert country['bg'] == 1 and country['items'][0]['v'] == 1
    assert Q.mapdata('oblasti')['items'][0]['v'] == 1


def test_csv_never_executes_source_formula():
    output = Q.csv_response([dict(name='=HYPERLINK("danger")', zero=0, missing=None)], ['name', 'zero', 'missing'])
    row = next(csv.DictReader(io.StringIO(output.lstrip('﻿'))))
    assert row['name'].startswith("'=") and row['zero'] == '0' and row['missing'] == ''


def test_the_records_the_pages_read_are_the_parser_records(conn, site):
    """The serving layer gives back each record exactly as the parser made it (the pages of the old database showed
    the parser's records), so the move changes no page."""
    publish(conn)
    rows, _ = seed.enterprises()
    assert Q.records('appk', 'catalogue') == sorted(rows, key=lambda x: x['id'])
    pages = seed.answers()
    profile, reports = run.read_enterprise(seed.fetch_from(pages), dict(id='105', source_url=seed.PROFILE))
    shown = Q.profiles('appk')['105']
    assert {k: v for k, v in shown.items() if k != 'board'} == {k: v for k, v in profile.items() if k not in ('board', 'board_original')}
    assert shown['board'] == profile['board']
    assert Q.current()['reports']['105'] == sorted(reports, key=lambda x: x['id'])
    concession = run.read_concession(seed.fetch_from(pages), next(x for x in seed.concessions()[0] if x['id'] == seed.CONCESSION))
    assert Q.profiles('ncr')[seed.CONCESSION] == concession
    assert store.version(Q.profiles('ncr')[seed.CONCESSION]) == store.version(concession)
    assert parse.APPK in Q.records('appk', 'catalogue')[0]['source_url']

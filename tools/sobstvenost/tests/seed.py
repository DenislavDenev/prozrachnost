"""Records from the real fixtures, read through the same parser and traversal as a live read, for the database tests."""
from pathlib import Path

from ingest import parse, pipeline, run, store

P = Path(__file__).parent / 'fixtures'
PROFILE = parse.APPK + '/Public/Public/CompanyDetails/105'
CONCESSION = 'f0648cf4-8c12-4cf8-9c57-4f739e9b6396'
NOTICE = parse.NCR + '/Preview/AssignedConcession/example'


def detail_page(notices=(NOTICE,), documents=('/File/Download/abc',)):
    """The page of a concession: the parser keeps only its notice and document links (no such page is a fixture)."""
    links = ''.join(f'<a href="{n.replace(parse.NCR, "")}">Обявление</a>' for n in notices)
    links += ''.join(f'<a href="{d}">Документ</a>' for d in documents)
    return f'<html><body><h1>Информация за партида</h1>{links}</body></html>'.encode()


def answers():
    """{address: bytes}: the profile of АВТОМАГИСТРАЛИ (105) with both pages of its reports, every report it links
    (annual from the annual fixture, quarterly from the quarterly one), and one concession with one notice."""
    out = {PROFILE: (P / 'appk-105.html').read_bytes()}
    profile = parse.profile(out[PROFILE])
    for page in profile['report_pages']:
        out[page] = out[PROFILE]
    for link in profile['report_links']:
        out[link] = (P / ('appk-annual-9848.html' if 'Annual' in link else 'appk-quarterly-10603.html')).read_bytes()
    out[parse.NCR + '/ConcessionaireProcedures/ConcessionaireProcedureInfo/' + CONCESSION] = detail_page()
    out[NOTICE] = (P / 'ncr-mining.html').read_bytes()
    return out


def fetch_from(pages):
    def fetch(source, url, data=None):
        if url not in pages:
            raise parse.ShapeError('no answer for ' + url)
        return pages[url]
    return fetch


def enterprises():
    rows = parse.companies((P / 'appk-companies-1.html').read_bytes())[0]
    return rows, dict(pages=1, source_count=sum(r['status'] == 'Активно' for r in rows))


def ministries():
    rows = [dict(id='7', name='МИНИСТЕРСТВО НА РЕГИОНАЛНОТО РАЗВИТИЕ И БЛАГОУСТРОЙСТВОТО', status='Активно'),
            dict(id='8', name='МИНИСТЕРСТВО НА ФИНАНСИТЕ', status='Активно')]
    return rows, dict(pages=1, source_count=2)


def concessions():
    rows = parse.concessions((P / 'ncr-search-1.html').read_bytes())[0]
    return rows, dict(pages=1, source_count=len(rows))


def load_all(conn, r, pages=None):
    """The catalogues, enterprise 105 and one concession."""
    fetch = fetch_from(pages or answers())
    store.load_catalogue(conn, r, 'appk', 'ministries', *ministries())
    rows, manifest = enterprises()
    store.load_catalogue(conn, r, 'appk', 'catalogue', rows, manifest)
    ncr, nmanifest = concessions()
    store.load_catalogue(conn, r, 'ncr', 'catalogue', ncr, nmanifest)
    store.load_enterprise(conn, r, *run.read_enterprise(fetch, next(x for x in rows if x['id'] == '105')))
    store.load_concession(conn, r, run.read_concession(fetch, next(x for x in ncr if x['id'] == CONCESSION)))
    return rows, ncr


def published(conn):
    with pipeline.run(conn, 'sobstvenost', 'check') as r:
        problems = run.finish(conn, r)
    return problems, r

import csv
import datetime as dt
import io
import os
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from markupsafe import Markup
from fastapi import Depends, FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import feedback, queries
from ingest.config import DATA

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='Население', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
app.include_router(feedback.router('DenislavDenev/prozrachnost', Path(os.environ.get('NASELENIE_FEEDBACK', str(DATA / 'feedback'))), 'Население'))
templates = Jinja2Templates(directory=ROOT / 'templates')


def number(v):
    return 'няма данни' if v is None else f'{v:,.0f}'.replace(',', ' ')


def date(v):
    if not v:
        return 'няма данни'
    if hasattr(v, 'astimezone'):
        return v.astimezone(ZoneInfo('Europe/Sofia')).strftime('%d.%m.%Y, %H:%M')
    s = str(v)
    return s[8:10] + '.' + s[5:7] + '.' + s[:4]


templates.env.filters.update(num=number, date=date)
LEVELS = [('obshtini', 'Общини'), ('oblasti', 'Области'), ('rayoni', 'Райони'), ('makrorayoni', 'Макрорайони')]
NUTS = {'obshtini': 4, 'oblasti': 3, 'rayoni': 2, 'makrorayoni': 1}
SINGLE = {'obshtini': 'Община', 'oblasti': 'Област', 'rayoni': 'Район', 'makrorayoni': 'Макрорайон'}
INDEX = 'https://www.grao.bg/tables.html'


def snap(request: Request):
    """The one snapshot of the serving layer a request reads, and the publication it shows.
    Every query of the request goes through it, so the answer is of one version."""
    with queries.snapshot() as c:
        request.state.publication = queries.version(c)
        yield c


@app.middleware('http')
async def publication_headers(request: Request, call_next):
    """Every answer built from the data says which publication it is of. A cache in front of the site keeps an
    answer until this number moves; the number moves only when the published content does."""
    response = await call_next(request)
    publication = getattr(request.state, 'publication', None)
    if publication:
        response.headers['X-Publication'] = str(publication[0])
        response.headers['ETag'] = f'W/"pub-{publication[0]}"'
        response.headers['Last-Modified'] = format_datetime(publication[1].astimezone(dt.timezone.utc), usegmt=True)
    return response


def render(request, c, name, **context):
    hub = os.environ.get('HUB_URL', '/')
    fresh = queries.latest(c)
    return templates.TemplateResponse(request=request, name=name, context=dict(
        v='1', hub_url=hub, feedback_button=Markup(feedback.BUTTON), support_link=Markup(feedback.support_link(hub)),
        fresh=fresh[0] if fresh else None, **context))


@app.get('/healthz')
def health(request: Request, c=Depends(snap)):
    publication = request.state.publication
    return {'ok': True, 'publication': publication[0] if publication else None}


@app.get('/favicon.svg')
def favicon():
    return Response((ROOT / 'static/favicon.svg').read_text(), media_type='image/svg+xml')


def mapdata(c, m='current', l='obshtini', y=None):
    """The numbers of one published date at one level. The same function feeds the map, the tables and the CSV."""
    if m not in ('current', 'permanent') or l not in NUTS:
        raise HTTPException(400, 'Невалиден показател или ниво')
    ref, regions = queries.reference(c)
    ss = queries.snapshots(c)
    periods = sorted(ss, reverse=True)
    y = y or (periods[0] if periods else None)
    if y and y not in ss:
        raise HTTPException(404, 'Няма данни за тази дата')
    rows = queries.municipal(c, y, ss[y][0]) if y else []
    items = {}
    for r in ref.values():
        code = r['id'] if l == 'obshtini' else r['nuts' + str(NUTS[l])]
        name = r['name_bg'] if l == 'obshtini' else r['oblast'] if l == 'oblasti' else regions[code]
        items[code] = dict(code=code, name=name, v=None, change=None, rank=None,
                           href='/obshtini/' + r['id'] if l == 'obshtini' else None)
    column = 2 if m == 'current' else 1
    for row in rows:
        code = row[0] if l == 'obshtini' else ref[row[0]]['nuts' + str(NUTS[l])]
        items[code]['v'] = (items[code]['v'] or 0) + row[column]
    data = sorted(items.values(), key=lambda r: (r['v'] is None, -(r['v'] or 0), r['name']))
    for rank, r in enumerate(data, 1):
        r['rank'] = rank if r['v'] is not None else None
    return dict(m=m, l=l, o='bg', nuts=NUTS[l], title='Настоящ адрес' if m == 'current' else 'Постоянен адрес',
                plural=dict(LEVELS)[l], single=SINGLE[l], unit='регистрирани лица', digits=0, items=data, y=y, years=periods,
                levels=LEVELS, bg=sum(row[column] for row in rows) if rows else None, base=None, updated=None, countries={},
                dataset='ГРАО · ' + date(y), url=ss[y][1] if y else INDEX,
                csv=f'/export.csv?l={l}&m={m}' + (f'&y={y}' if y else ''))


@app.get('/', response_class=HTMLResponse)
def home(request: Request, c=Depends(snap)):
    return render(request, c, 'home.html', nav='Табло', d=mapdata(c))


@app.get('/karta', response_class=HTMLResponse)
def karta(request: Request, m: str = 'current', l: str = 'obshtini', y: str | None = None, c=Depends(snap)):
    return render(request, c, 'karta.html', nav='Карта', d=mapdata(c, m, l, y), scopes={'bg': ['България']},
                  measures={'current': [0, 0, 'Настоящ адрес'], 'permanent': [0, 0, 'Постоянен адрес']})


@app.get('/api/karta.json')
def karta_json(m: str = 'current', l: str = 'obshtini', y: str | None = None, c=Depends(snap)):
    return mapdata(c, m, l, y)


@app.get('/obshtini', response_class=HTMLResponse)
def list_municipalities(request: Request, c=Depends(snap)):
    return render(request, c, 'list.html', nav='Общини', d=mapdata(c))


def known(c, code):
    ref, _ = queries.reference(c)
    if code not in ref:
        raise HTTPException(404, 'Няма такава община')
    return ref[code]


@app.get('/obshtini/{code}', response_class=HTMLResponse)
def municipality(request: Request, code: str, c=Depends(snap)):
    ref = known(c, code)
    d = mapdata(c)
    item = next(r for r in d['items'] if r['code'] == code)
    s = queries.latest(c, 'detailed')
    places = queries.places(c, s[0], code) if s else []
    current = queries.detailed_current(c, s[0], code) if s else None
    # the two tables of ГРАО for one date can differ; both are shown, neither is corrected with the other
    difference = current - item['v'] if current is not None and item['v'] is not None and s[0] == d['y'] else None
    return render(request, c, 'municipality.html', nav='Общини', ref=ref, d=d, item=item, places=places,
                  detail=dict(date=s[0], url=s[1]) if s else None, difference=difference)


def series_data(c, code=None):
    if code:
        known(c, code)
    rows = queries.series(c, code)
    result = [dict(name='Постоянен адрес', color='#121417', points=[[str(r[0]), r[3]] for r in rows]),
              dict(name='Настоящ адрес', color='#0b7a5e', points=[[str(r[0]), r[4]] for r in rows])]
    return {'unit': 'регистрирани лица', 'series': result,
            'sources': [{'date': str(r[0]), 'url': r[2], 'kind': queries.OLD_KIND[r[1]]} for r in rows]}


@app.get('/api/series.json')
@app.get('/api/obshtini/{code}.json')
def series(code: str | None = None, c=Depends(snap)):
    return series_data(c, code)


def csv_response(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return Response('﻿' + out.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="naselenie.csv"'})


@app.get('/export.csv')
def export(m: str = 'current', l: str = 'obshtini', y: str | None = None, code: str | None = None, c=Depends(snap)):
    if code:
        known(c, code)
        s = queries.latest(c, 'detailed')
        if not s:
            return csv_response([['Няма данни']])
        return csv_response([['Населено място / ред на ГРАО', 'ЕКАТТЕ', 'Постоянен адрес', 'Настоящ адрес', 'Дата', 'Източник']] + [
            [r['name'], r['ekatte'] or '', r['permanent'], r['current'], s[0], s[1]] for r in queries.places(c, s[0], code)])
    d = mapdata(c, m, l, y)
    return csv_response([[d['single'], d['title'], 'Дата', 'Източник']] + [[r['name'], r['v'], d['y'], d['url']] for r in d['items']])


@app.get('/history.csv')
def history_csv(code: str | None = None, c=Depends(snap)):
    d = series_data(c, code)
    return csv_response([['Дата', 'Постоянен адрес', 'Настоящ адрес', 'Източник']] + [
        [a[0], a[1], b[1], s['url']] for a, b, s in zip(d['series'][0]['points'], d['series'][1]['points'], d['sources'])])


def source_rows(c):
    return [dict(url=u, last_read=t, last_ok=ok, status=s, error=e, rows=n) for u, t, ok, s, e, n in queries.source_state(c)]


@app.get('/sources', response_class=HTMLResponse)
def source_page(request: Request, c=Depends(snap)):
    s = queries.latest(c, 'detailed')
    unmatched = queries.places(c, s[0], unmatched=True) if s else []
    return render(request, c, 'sources.html', nav='Източници', rows=source_rows(c), unmatched=unmatched,
                  detail=dict(date=s[0], url=s[1]) if s else None)


@app.get('/how', response_class=HTMLResponse)
def how(request: Request, c=Depends(snap)):
    return render(request, c, 'how.html', nav='')


@app.get('/sources.csv')
def source_csv(c=Depends(snap)):
    return csv_response([['Източник', 'Последно четене', 'Статус', 'Проблем', 'Редове']] + [
        [r['url'], r['last_read'], r['status'], r['error'], r['rows']] for r in source_rows(c)])


@app.get('/unmatched.csv')
def unmatched_csv(c=Depends(snap)):
    s = queries.latest(c, 'detailed')
    return csv_response([['Община', 'Ред на ГРАО']] + ([[r['municipality'], r['name']] for r in queries.places(c, s[0], unmatched=True)] if s else []))


@app.get('/sources.json')
def source_json(c=Depends(snap)):
    return [dict(source=r['url'], label='ГРАО' if 'grao.bg' in r['url'] else 'ЕКАТТЕ',
                 last_ok=r['last_ok'].isoformat() if r['last_ok'] else None, status=r['status'], rows=r['rows'], url=r['url'])
            for r in source_rows(c)]

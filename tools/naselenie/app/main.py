import csv
import io
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from markupsafe import Markup
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import feedback
from ingest.config import DSN, DATA
from ingest.reference import municipalities

ROOT=Path(__file__).resolve().parent
app=FastAPI(title='Население',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
app.include_router(feedback.router('DenislavDenev/prozrachnost',Path(os.environ.get('NASELENIE_FEEDBACK',str(DATA/'feedback'))),'Население'))
templates=Jinja2Templates(directory=ROOT/'templates')
def number(v): return 'няма данни' if v is None else f'{v:,.0f}'.replace(',',' ')
def date(v):
    if not v:return 'няма данни'
    if hasattr(v,'astimezone'):return v.astimezone(ZoneInfo('Europe/Sofia')).strftime('%d.%m.%Y, %H:%M')
    s=str(v);return s[8:10]+'.'+s[5:7]+'.'+s[:4]
templates.env.filters.update(num=number,date=date)
REF={r['id']:r for r in municipalities()}
LEVELS=[('obshtini','Общини'),('oblasti','Области'),('rayoni','Райони'),('makrorayoni','Макрорайони')]
NUTS={'obshtini':4,'oblasti':3,'rayoni':2,'makrorayoni':1}
REGIONS={'BG31':'Северозападен','BG32':'Северен централен','BG33':'Североизточен','BG34':'Югоизточен','BG41':'Югозападен','BG42':'Южен централен','BG3':'Северна и Югоизточна България','BG4':'Югозападна и Южна централна България'}
_cache={};_lock=threading.Lock()


def sources():
    with _lock:
        if time.monotonic()-_cache.get('time',-1e9)<15:return _cache['data']
        with psycopg.connect(DSN) as c:
            data=[dict(url=u,date=str(d),kind=k,payload=p) for u,d,k,p in c.execute("SELECT url,as_of,kind,payload FROM live.source WHERE kind<>'reference' ORDER BY as_of")]
        _cache.update(time=time.monotonic(),data=data)
        return data


def snapshots():
    # Annual detailed observations and monthly aggregates remain labelled by source.
    # Monthly wins if both exist on the same date; settlement tables always use their own source.
    by={}
    for s in sorted(sources(),key=lambda x:(x['date'],x['kind']=='municipalities')):by[s['date']]=s
    return by


def latest(kind=None):
    data=[s for s in sources() if kind is None or s['kind']==kind]
    return max(data,key=lambda s:s['date']) if data else None


def render(request,name,**context):
    hub=os.environ.get('HUB_URL','/')
    return templates.TemplateResponse(request=request,name=name,context=dict(v='1',hub_url=hub,
      feedback_button=Markup(feedback.BUTTON),support_link=Markup(feedback.support_link(hub)),fresh=latest()['date'] if latest() else None,**context))


@app.get('/healthz')
def health():
    with psycopg.connect(DSN) as c:c.execute('SELECT 1')
    return {'ok':True}


@app.get('/favicon.svg')
def favicon():return Response((ROOT/'static/favicon.svg').read_text(),media_type='image/svg+xml')


def mapdata(m='current',l='obshtini',y=None):
    if m not in ('current','permanent') or l not in NUTS:raise HTTPException(400,'Невалиден показател или ниво')
    ss=snapshots();periods=sorted(ss,reverse=True)
    y=y or (periods[0] if periods else None)
    if y and y not in ss:raise HTTPException(404,'Няма данни за тази дата')
    src=ss.get(y); rows=src['payload']['municipalities'] if src else []
    items={}
    for r in REF.values():
        code=r['id'] if l=='obshtini' else r['nuts'+str(NUTS[l])]
        name=r['name_bg'] if l=='obshtini' else r['oblast'] if l=='oblasti' else REGIONS[code]
        items[code]=dict(code=code,name=name,v=None,change=None,rank=None,href='/obshtini/'+r['id'] if l=='obshtini' else None)
    for r in rows:
        code=r['code'] if l=='obshtini' else r['nuts'+str(NUTS[l])]
        items[code]['v']=(items[code]['v'] or 0)+r[m]
    data=sorted(items.values(),key=lambda r: (r['v'] is None,-(r['v'] or 0),r['name']))
    for rank,r in enumerate(data,1):r['rank']=rank if r['v'] is not None else None
    return dict(m=m,l=l,o='bg',nuts=NUTS[l],title='Настоящ адрес' if m=='current' else 'Постоянен адрес',plural=dict(LEVELS)[l],single={'obshtini':'Община','oblasti':'Област','rayoni':'Район','makrorayoni':'Макрорайон'}[l],unit='регистрирани лица',digits=0,items=data,y=y,years=periods,levels=LEVELS,bg=sum(r[m] for r in rows) if rows else None,base=None,updated=None,countries={},dataset='ГРАО · '+date(y),url=src['url'] if src else 'https://www.grao.bg/tables.html',csv=f'/export.csv?l={l}&m={m}'+(f'&y={y}' if y else ''))


@app.get('/',response_class=HTMLResponse)
def home(request:Request):
    d=mapdata();return render(request,'home.html',nav='Табло',d=d)


@app.get('/karta',response_class=HTMLResponse)
def karta(request:Request,m:str='current',l:str='obshtini',y:str|None=None):
    return render(request,'karta.html',nav='Карта',d=mapdata(m,l,y),scopes={'bg':['България']},measures={'current':[0,0,'Настоящ адрес'],'permanent':[0,0,'Постоянен адрес']})


@app.get('/api/karta.json')
def karta_json(m:str='current',l:str='obshtini',y:str|None=None):return mapdata(m,l,y)


@app.get('/obshtini',response_class=HTMLResponse)
def list_municipalities(request:Request):return render(request,'list.html',nav='Общини',d=mapdata())


@app.get('/obshtini/{code}',response_class=HTMLResponse)
def municipality(request:Request,code:str):
    if code not in REF:raise HTTPException(404,'Няма такава община')
    d=mapdata();item=next(r for r in d['items'] if r['code']==code)
    s=latest('places');places=[r for r in s['payload']['places'] if r['code']==code] if s else []
    detail=next((r for r in s['payload']['municipalities'] if r['code']==code),None) if s else None
    difference=detail['current']-item['v'] if detail and item['v'] is not None and s['date']==d['y'] else None
    return render(request,'municipality.html',nav='Общини',ref=REF[code],d=d,item=item,places=places,detail=s,difference=difference)


@app.get('/api/series.json')
@app.get('/api/obshtini/{code}.json')
def series(code:str|None=None):
    if code and code not in REF:raise HTTPException(404,'Няма такава община')
    result=[dict(name='Постоянен адрес',color='#121417',points=[]),dict(name='Настоящ адрес',color='#0b7a5e',points=[])]
    for day,s in sorted(snapshots().items()):
        rows=[r for r in s['payload']['municipalities'] if code is None or r['code']==code]
        for i,key in enumerate(('permanent','current')):
            result[i]['points'].append([day,sum(r[key] for r in rows) if rows else None])
    return {'unit':'регистрирани лица','series':result,'sources':[{'date':d,'url':s['url'],'kind':s['kind']} for d,s in sorted(snapshots().items())]}


def csv_response(rows):
    out=io.StringIO();csv.writer(out).writerows(rows)
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="naselenie.csv"'})


@app.get('/export.csv')
def export(m:str='current',l:str='obshtini',y:str|None=None,code:str|None=None):
    if code:
        if code not in REF:raise HTTPException(404)
        s=latest('places')
        return csv_response([['Населено място / ред на ГРАО','ЕКАТТЕ','Постоянен адрес','Настоящ адрес','Дата','Източник']]+[[r['name'],r['ekatte'] or '',r['permanent'],r['current'],s['date'],s['url']] for r in s['payload']['places'] if r['code']==code] if s else [['Няма данни']])
    d=mapdata(m,l,y)
    return csv_response([[d['single'],d['title'],'Дата','Източник']]+[[r['name'],r['v'],d['y'],d['url']] for r in d['items']])


@app.get('/history.csv')
def history_csv(code:str|None=None):
    d=series(code); ss=d['sources']
    return csv_response([['Дата','Постоянен адрес','Настоящ адрес','Източник']]+[[a[0],a[1],b[1],s['url']] for a,b,s in zip(d['series'][0]['points'],d['series'][1]['points'],ss)])


@app.get('/sources',response_class=HTMLResponse)
def source_page(request:Request):
    with psycopg.connect(DSN) as c:
        rows=[dict(url=u,last_read=t,status=s,error=e,rows=n) for u,t,s,e,n in c.execute('SELECT url,last_read,status,error,rows FROM ops.source_state ORDER BY url')]
    s=latest('places');unmatched=[r for r in s['payload']['places'] if r['ekatte'] is None] if s else []
    return render(request,'sources.html',nav='Източници',rows=rows,unmatched=unmatched,detail=s)


@app.get('/how',response_class=HTMLResponse)
def how(request:Request):return render(request,'how.html',nav='')


@app.get('/sources.csv')
def source_csv():
    with psycopg.connect(DSN) as c:
        rows=list(c.execute('SELECT url,last_read,status,error,rows FROM ops.source_state ORDER BY url'))
    return csv_response([['Източник','Последно четене','Статус','Проблем','Редове']]+[list(r) for r in rows])


@app.get('/unmatched.csv')
def unmatched_csv():
    s=latest('places')
    return csv_response([['Община','Ред на ГРАО']]+([[r['municipality'],r['name']] for r in s['payload']['places'] if r['ekatte'] is None] if s else []))


@app.get('/sources.json')
def source_json():
    with psycopg.connect(DSN) as c:
        return [dict(source=u,label='ГРАО' if 'grao.bg' in u else 'ЕКАТТЕ',last_ok=t.isoformat() if t else None,status=s,rows=n,url=u) for u,t,s,n in c.execute('SELECT url,last_ok,status,rows FROM ops.source_state ORDER BY url')]

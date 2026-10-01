import os
import hashlib
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from ingest import db
from ingest.config import DATA, SOURCES
from . import queries as Q, feedback

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='Одити и контрол', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
app.include_router(feedback.router('DenislavDenev/prozrachnost', Path(os.environ.get('KONTROL_FEEDBACK', str(DATA/'feedback'))), 'Одити и контрол'))
t=Jinja2Templates(directory=ROOT/'templates')
t.env.filters['date']=lambda v: str(v)[8:10]+'.'+str(v)[5:7]+'.'+str(v)[:4] if v else 'не е посочена'
t.env.filters['urlencode']=lambda v:quote(str(v),safe='')

HELP = {
 'count':dict(title='Какво броим',text='Документ, инспекция и решение са различни събития. Един документ в две категории се брои веднъж.',example='Условен пример: един одит и два последващи доклада не са три нарушения.',source='/how'),
 'date':dict(title='Дата на публикуване',text='Това е датата, на която документът е публикуван. Датата на приключване и отчетният период се пазят отделно.',example='Условен пример: публикуван през април доклад може да проверява предходна година.',source='/sources'),
 'recommendation':dict(title='Изпълнение на препоръките',text='Показваме точно публикуваното състояние. Възрастта на одита не определя дали препоръката е изпълнена.',example='Условен пример: „изпълнена частично“ не означава „изпълнена“.',source=SOURCES['recommendations'][1]),
 'result':dict(title='Констатация и съдебен резултат',text='Констатацията в доклад не е присъда. Решение на КЗК може да бъде обжалвано; окончателен съдебен резултат не е установен тук.',example='Условен пример: отменено решение не е действащ извод за нарушение.',source='/how'),
}

def asset(path):
    content=(ROOT/path.lstrip('/')).read_bytes()
    return path+'?v='+hashlib.sha256(content).hexdigest()[:12]

def render(request,name,**kw):
    hub=os.environ.get('HUB_URL','https://prozrachnost.denev.work')
    return t.TemplateResponse(request=request,name=name,context=dict(hub_url=hub,asset=asset,feedback_button=Markup(feedback.BUTTON),support_link=Markup(feedback.support_link(hub)),help={'columns':HELP,'rows':[]},link=Q.link,source_names={k:v[0] for k,v in SOURCES.items()},**kw))

@app.get('/healthz')
def health():
    with db.connect() as c:c.execute('SELECT 1')
    return {'ok':True}

@app.get('/favicon.svg')
def favicon():
    return Response('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="25" fill="#0b7a5e"/><path d="m18 32 9 9 19-22" fill="none" stroke="white" stroke-width="5"/></svg>',media_type='image/svg+xml')

@app.get('/',response_class=HTMLResponse)
def home(request:Request,year='',kind='',sector='',q='',source='',period=''):
    rows,options=Q.select('all',year,kind,sector,q,source=source,period=period)
    return render(request,'home.html',nav='Табло',rows=rows,options=options,stats=Q.summary(rows),timeline=Q.timeline(rows),filters=dict(year=year,kind=kind,sector=sector,q=q,source=source,period=period),view='all')

@app.get('/oditi',response_class=HTMLResponse)
@app.get('/preporaki',response_class=HTMLResponse)
@app.get('/inspekcii',response_class=HTMLResponse)
@app.get('/kzk',response_class=HTMLResponse)
def listing(request:Request,year='',kind='',sector='',q='',source='',sort='published',page:int=1,period=''):
    view=request.url.path.strip('/')
    labels={'oditi':'Одити','preporaki':'Препоръки','inspekcii':'Инспекции','kzk':'КЗК'}
    rows,options=Q.select(view,year,kind,sector,q,sort,source,period)
    page=max(1,page);start=(page-1)*100
    filters=dict(year=year,kind=kind,sector=sector,q=q,source=source,sort=sort,period=period)
    return render(request,'list.html',nav=labels[view],view=view,rows=rows[start:start+100],stats=Q.summary(rows),options=options,filters=filters,page=page,pages=(len(rows)+99)//100)

@app.get('/export.csv')
def export(view='all',year='',kind='',sector='',q='',source='',sort='published',period=''):
    if view not in ['all','oditi','preporaki','inspekcii','kzk']:raise HTTPException(400)
    rows,_=Q.select(view,year,kind,sector,q,sort,source,period)
    return Response(Q.csv_response(rows),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="kontrol.csv"'})

@app.get('/oditi/{id:path}',response_class=HTMLResponse)
def detail(request:Request,id:str):
    r=next((r for r in Q.allrows() if r['id']==id),None)
    if not r:raise HTTPException(404)
    versions=[]
    document_versions=[]
    with db.connect() as c:
        versions=[dict(sha256=x[0],detected_at=x[1]) for x in c.execute('SELECT sha256,detected_at FROM ops.version WHERE ref=%s ORDER BY detected_at',(id,))]
        document_versions=[dict(sha256=x[0],url=x[1],fetched_at=x[2]) for x in c.execute('SELECT sha256,url,fetched_at FROM ops.document_version WHERE ref=%s ORDER BY fetched_at',(id,))]
    return render(request,'detail.html',nav='Одити',r=r,versions=versions,document_versions=document_versions)

@app.get('/sources',response_class=HTMLResponse)
def source_page(request:Request):
    return render(request,'sources.html',nav='Източници',resources=Q.resources(),snapshots=Q.snapshots(),documents=Q.document_coverage())

@app.get('/how',response_class=HTMLResponse)
def how(request:Request):
    return render(request,'how.html',nav='Как работи')


@app.get('/legal',response_class=HTMLResponse)
def legal(request:Request):
    return render(request,'legal.html',nav='Данни и корекции')

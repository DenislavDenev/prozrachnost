import os,json,hashlib
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from ingest import db
from ingest.config import DATA
from . import queries as Q,feedback

ROOT=Path(__file__).parent
ASSET_VERSION=hashlib.sha256(b''.join((ROOT/'static'/name).read_bytes() for name in ('app.css','app.js','karta.js','state-help.js'))).hexdigest()[:12]
app=FastAPI(title='Държавна собственост',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
app.include_router(feedback.router('DenislavDenev/prozrachnost',Path(os.environ.get('SOBSTVENOST_FEEDBACK',str(DATA/'feedback'))),'Държавна собственост'))
t=Jinja2Templates(directory=ROOT/'templates')
HELP={'share':dict(title='Публикуван дял',text='Процентът е публикуваният в АППК; той не доказва сам по себе си държавен дял. Непосредственият собственик и принципалът са различни неща. Липсващ процент остава неизвестен.',example='Пример (условен): ако държавата притежава холдинг и той държи дъщерно дружество, участието е косвено. Тук не доказваме веригата само от име.',source='https://reports.appk.government.bg/Public/Public/Companies'),'principal':dict(title='Орган, упражняващ правата',text='Това е принципалът, посочен от предприятието. Той не се приема автоматично за акционер.',example='Пример (условен): министерство упражнява правата на държавата, а собственик на дъщерното дружество е холдинг.',source='https://reports.appk.government.bg/Public/Public/Companies'),'board':dict(title='Борд по наблюдение',text='Показваме списъка, видян на конкретната дата. Имената не са идентификатори за връзки между предприятия.',example='Пример (условен): име се появява в прочита на 1 октомври. Това не доказва назначение на 1 октомври.',source='https://reports.appk.government.bg/Public/Public/Companies'),'period':dict(title='Период и подаване',text='Годината и тримесечието са отчетният период. Датата на подаване е отделна. Не събираме натрупани тримесечни стойности.',example='Пример (условен): отчет за 2025, подаден през 2026, може да е в лева. Валутата се установява от документа.',source='https://reports.appk.government.bg/Public/Public/Companies'),'concession':dict(title='Партида на концесия',text='Националният брой е по уникален източников идентификатор. Териториалният брой отчита доказаните места на изпълнение.',example='Пример (условен): една концесия в две общини участва в двата реда на картата, но е една национална партида.',source='https://ncr.government.bg/Concessions')}

def render(request,name,**context):
    hub=os.environ.get('HUB_URL','https://prozrachnost.denev.work')
    return t.TemplateResponse(request=request,name=name,context=dict(v=ASSET_VERSION,hub_url=hub,feedback_button=Markup(feedback.BUTTON),support_link=Markup(feedback.support_link(hub)),help=dict(rows=[],columns=HELP),**context))
def csv(data,name):return Response(data,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{name}.csv"'})
def serial(data):return JSONResponse(json.loads(json.dumps(data,default=str,ensure_ascii=False)))

@app.get('/healthz')
def health():
    with db.connect() as c:c.execute('SELECT 1')
    return dict(ok=True)
@app.get('/favicon.svg')
def icon():return Response((ROOT/'static/favicon.svg').read_text(),media_type='image/svg+xml')
@app.get('/',response_class=HTMLResponse)
def home(request:Request,view='enterprises',principal='',participation='',status='',q='',kind='',conceder=''):
    if view not in ('enterprises','concessions'):raise HTTPException(400,'Невалиден изглед')
    data=Q.enterprises(principal,participation,status,q) if view=='enterprises' else Q.concessions(kind=kind,conceder=conceder,status=status,q=q)
    return render(request,'home.html',nav='Табло',view=view,d=data,states=Q.states())
@app.get('/predpriyatiya',response_class=HTMLResponse)
def enterprises(request:Request,principal='',participation='',status='',q='',sort='name',direction='asc'):
    return render(request,'enterprises.html',nav='Предприятия',d=Q.enterprises(principal,participation,status,q,sort,direction))
@app.get('/export-enterprises.csv')
def enterprises_csv(principal='',participation='',status='',q='',sort='name',direction='asc'):
    data=Q.enterprises(principal,participation,status,q,sort,direction)
    return csv(Q.csv_response(data['rows'],['id','name','eik','eik_original','principal','participation','share_pct','owner','share_text','status','source_url']),'sobstvenost-enterprises')
@app.get('/predpriyatiya/id/{id}',response_class=HTMLResponse)
def enterprise_id(request:Request,id):
    data=Q.enterprise(id)
    if data is None:raise HTTPException(404,'Няма такова предприятие')
    return render(request,'enterprise.html',nav='Предприятия',d=data)
@app.get('/predpriyatiya/{eik}',response_class=HTMLResponse)
def enterprise_eik(request:Request,eik):
    matches=[id for id,p in Q.profiles('appk').items() if p.get('eik')==eik]
    if len(matches)!=1:raise HTTPException(404,'ЕИК не е проверен и еднозначен. Използвай профила по идентификатор от АППК.')
    return enterprise_id(request,matches[0])
@app.get('/koncesii',response_class=HTMLResponse)
def concessions(request:Request,kind='',subject='',conceder='',status='',q='',municipality='',sort='reg_number',direction='desc'):
    data=Q.concessions(kind,subject,conceder,status,q,municipality,sort,direction)
    allrows=Q.records('ncr','catalogue')
    return render(request,'concessions.html',nav='Концесии',d=data,options={key:sorted({r[key] for r in allrows}) for key in ['kind','subject','conceder','status']})
@app.get('/export-concessions.csv')
def concessions_csv(kind='',subject='',conceder='',status='',q='',municipality='',sort='reg_number',direction='desc'):
    data=Q.concessions(kind,subject,conceder,status,q,municipality,sort,direction)
    return csv(Q.csv_response(data['rows'],['id','reg_number','name','kind','subject','conceder','status','procedure_status','municipality_names','source_url']),'sobstvenost-concessions')
@app.get('/koncesii/{id}',response_class=HTMLResponse)
def concession(request:Request,id):
    data=Q.concession(id)
    if data is None:raise HTTPException(404,'Няма такава партида')
    return render(request,'concession.html',nav='Концесии',d=data)
@app.get('/karta',response_class=HTMLResponse)
def karta(request:Request,l='obshtini',kind='',status='',conceder='',q='',subject='',municipality=''):
    try:data=Q.mapdata(l,kind,status,conceder,q,subject,municipality)
    except ValueError:raise HTTPException(400,'Невалидно ниво')
    return render(request,'karta.html',nav='Карта',d=data)
@app.get('/api/karta.json')
def map_api(l='obshtini',kind='',status='',conceder='',q='',subject='',municipality=''):
    try:return serial(Q.mapdata(l,kind,status,conceder,q,subject,municipality))
    except ValueError:raise HTTPException(400,'Невалидно ниво')
@app.get('/export-map.csv')
def map_csv(l='obshtini',kind='',status='',conceder='',q='',subject='',municipality=''):
    try:data=Q.mapdata(l,kind,status,conceder,q,subject,municipality)
    except ValueError:raise HTTPException(400,'Невалидно ниво')
    return csv(Q.csv_response([dict(r,observed_on=data['y'],unit='партиди',coverage=data['coverage']) for r in data['items']],['code','name','v','unit','observed_on','coverage']),'sobstvenost-map')
@app.get('/sources',response_class=HTMLResponse)
def sources(request:Request):return render(request,'sources.html',nav='Източници',states=Q.states())
@app.get('/how',response_class=HTMLResponse)
def how(request:Request):return render(request,'how.html',nav='Как работи')

@app.get('/rights',response_class=HTMLResponse)
def rights(request:Request):
    policy=(Path(__file__).parents[1]/'docs/legal.md').read_text(encoding='utf-8')
    return render(request,'rights.html',nav='',paragraphs=policy.split('\n\n')[1:])

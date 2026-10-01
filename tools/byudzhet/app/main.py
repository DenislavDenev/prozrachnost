import os
from pathlib import Path
from decimal import Decimal
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import HTMLResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from ingest import db
from ingest.config import DATA,ROOT as TOOLROOT,SOURCE_NAMES
from ingest.checks import freshness
from . import feedback,queries as Q
ROOT=Path(__file__).resolve().parent
app=FastAPI(title='Бюджет',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
app.include_router(feedback.router('DenislavDenev/prozrachnost',Path(os.environ.get('BYUDZHET_FEEDBACK',str(DATA/'feedback'))),'Бюджет'))
t=Jinja2Templates(directory=ROOT/'templates')
def number(v,d=2):return 'няма данни' if v is None else f'{Decimal(str(v)):,.{d}f}'.replace(',',' ').replace('.',',')
def money(v):
 if v is None:return 'няма данни'
 v=Decimal(str(v));k=Decimal(1000000000) if abs(v)>=1000000000 else Decimal(1000000) if abs(v)>=1000000 else Decimal(1)
 return number(v/k)+(' млрд. €' if k==1000000000 else ' млн. €' if k==1000000 else ' €')
def date(v):
 if not v:return 'няма данни'
 s=str(v);return s[8:10]+'.'+s[5:7]+'.'+s[:4]
t.env.filters.update(num=number,money=money,date=date)
def render(request,name,**context):
 hub=os.environ.get('HUB_URL','https://prozrachnost.denev.work')
 return t.TemplateResponse(request=request,name=name,context=dict(v='4',hub_url=hub,feedback_button=Markup(feedback.BUTTON),support_link=Markup(feedback.support_link(hub)),fresh=max((s['period'] for s in Q.snapshots()),default=None),labels=Q.LABELS,source_names=SOURCE_NAMES,is_summary=Q.is_summary,**context))
def serial(data):
 import json
 return JSONResponse(json.loads(json.dumps(data,default=lambda v:float(v) if isinstance(v,Decimal) else str(v))))
def csv(data):return Response(data,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="byudzhet.csv"'})
@app.get('/healthz')
def health():
 with db.connect() as c:c.execute('SELECT 1')
 return {'ok':True}
@app.get('/favicon.svg')
def favicon():return Response((ROOT/'static/favicon.svg').read_text(),media_type='image/svg+xml')
@app.get('/',response_class=HTMLResponse)
def home(request:Request):
 state=Q.choose('state');summary=Q.state_summary(state);mun=Q.mapdata()
 return render(request,'home.html',nav='Табло',state=state,summary=summary,mun=mun)
@app.get('/karta',response_class=HTMLResponse)
def karta(request:Request,m='debt',l='obshtini',y=None,denominator='current'):
 measures={k:(None,None,name) for k,name in Q.LABELS.items()};measures.update({k+'_per_person':(None,None,name+' на регистрирано лице') for k,name in Q.LABELS.items()})
 return render(request,'karta.html',nav='Карта',d=Q.mapdata(m,l,y,denominator),scopes={'bg':('България',)},measures=measures)
@app.get('/api/karta.json')
def map_api(m='debt',l='obshtini',y=None,denominator='current'):return serial(Q.mapdata(m,l,y,denominator))
@app.get('/export-map.csv')
def map_csv(m='debt',l='obshtini',y=None,denominator='current'):
 d=Q.mapdata(m,l,y,denominator);rows=[dict(code=r['code'],name=r['name'],value=r['v'],period=d['y'],unit=d['unit'],population_date=d['population_date']) for r in d['items']]
 return csv(Q.csv_response(rows,['code','name','value','period','unit','population_date']))
@app.get('/darzhaven',response_class=HTMLResponse)
def state_page(request:Request,y=None,q=''):
 s=Q.choose('state',y);previous=None
 if s:
  import calendar
  y=s['period'];py=int(y[:4])-1;month=int(y[5:7]);previous=Q.choose('state',f'{py}-{month:02d}-{calendar.monthrange(py,month)[1]:02d}')
 return render(request,'state.html',nav='Държавен бюджет',s=s,rows=Q.state_rows(s,q),selected_period=s['period'] if s else y or '',years=sorted(Q.bykind('state'),reverse=True),q=q,previous=Q.state_previous(previous),helps=[Q.state_help(r,s) for r in Q.state_rows(s,q)],column_help=Q.COLUMN_HELP)
@app.get('/kfp',response_class=HTMLResponse)
def kfp_page(request:Request,y=None,q='',budget_type=''):
 s=Q.choose('kfp',y)
 return render(request,'kfp.html',nav='КФП',s=s,selected_period=s['period'] if s else y or '',years=sorted(Q.bykind('kfp'),reverse=True),q=q,budget_type=budget_type,summary=Q.kfp_summary(s,budget_type))
@app.get('/obshtini',response_class=HTMLResponse)
def municipalities(request:Request,y=None,oblast='',municipality='',q='',sort='debt',direction='desc',denominator='current'):
 d=Q.municipality_rows(y,oblast,municipality,q,sort,direction,denominator)
 return render(request,'list.html',nav='Общини',d=d,refs=Q.REF,oblasti=sorted({r['oblast'] for r in Q.REF}))
@app.get('/api/obshtini.json')
def municipalities_api(y=None,oblast='',municipality='',q='',sort='debt',direction='desc',denominator='current'):return serial(Q.municipality_rows(y,oblast,municipality,q,sort,direction,denominator))
@app.get('/export.csv')
def export(y=None,oblast='',municipality='',q='',sort='debt',direction='desc',denominator='current'):
 d=Q.municipality_rows(y,oblast,municipality,q,sort,direction,denominator)
 rows=[]
 for r in d['rows']:
  x=dict(r,unit='EUR')
  for k in Q.LABELS:x[k+'_original']=r['original'].get(k,{}).get('original');x[k+'_original_unit']=r['original'].get(k,{}).get('unit')
  rows.append(x)
 return csv(Q.csv_response(rows,['code','name','oblast','period','unit','debt','overdue','liabilities','commitments','debt_per_person','overdue_per_person','liabilities_per_person','commitments_per_person','denominator','population','population_date']+[k+suffix for k in Q.LABELS for suffix in ('_original','_original_unit')]))
@app.get('/api/obshtini/{code}.json')
def profile_api(code,y=None,denominator='current'):
 if code not in Q.REFBY:raise HTTPException(404,'Няма такава община')
 return serial(dict(row=Q.municipality_rows(y,municipality=code,denominator=denominator)['rows'][0],series=Q.chart('municipalities',code,list(Q.LABELS))))
@app.get('/obshtini/{code}',response_class=HTMLResponse)
def profile(request:Request,code,y=None,denominator='current'):
 if code not in Q.REFBY:raise HTTPException(404,'Няма такава община')
 d=Q.municipality_rows(y,municipality=code,denominator=denominator)
 return render(request,'municipality.html',nav='Общини',r=d['rows'][0],d=d)
@app.get('/api/chart.json')
def chart(kind='state',code=None,metrics=None,budget_type='',y=None):
 if kind not in ('state','kfp','municipalities','reserve'):raise HTTPException(400,'Невалидна графика')
 return serial(Q.chart(kind,code,metrics.split(',') if metrics else None,budget_type,y))
@app.get('/export-budget.csv')
def budget_csv(kind='state',y=None,q='',budget_type=''):
 if kind not in ('state','kfp','reserve'):raise HTTPException(400,'Невалиден набор')
 s=Q.choose(kind,y);rows=[]
 for r in (Q.state_rows(s,q) if kind=='state' else s['rows'] if s else []):
  if kind=='state':
   rows.append(dict(period=s['period'],line=r['line'],law_eur=Q.euros(r['law']),actual_eur=Q.euros(r['actual']),pct=r['pct'],row_key=r['key'],parent=r['section'],depth=r['depth'],unit=r['actual']['unit'],law_original=r['law']['original'],actual_original=r['actual']['original']))
  else:
   if budget_type and r.get('budget_type')!=budget_type:continue
   for k,v in (r['values'].items() if kind=='kfp' else [(r['line'],r['value'])]):
    if q.casefold() not in (k+' '+r.get('budget_type','')).casefold():continue
    rows.append(dict(period=s['period'],budget_type=r.get('budget_type',''),item=k,value_eur=Q.euros(v),unit=v['unit'],original=v['original']))
 headers=['period','line','law_eur','actual_eur','pct','unit','law_original','actual_original','row_key','parent','depth'] if kind=='state' else ['period','budget_type','item','value_eur','unit','original']
 return csv(Q.csv_response(rows,headers))
def source_rows(q=''):
 with db.connect() as c:
  rows=[dict(ref=a,kind=b,name=d,period=e,read_at=f,status=g,error=h,version=i) for a,b,d,e,f,g,h,i in c.execute('SELECT ref,kind,name,period,read_at,status,error,version FROM src.resource ORDER BY kind,name')]
 return [r for r in rows if q.casefold() in ' '.join(str(v or '') for v in r.values()).casefold()]
@app.get('/export-sources.csv')
def sources_csv(q=''):return csv(Q.csv_response(source_rows(q),['kind','ref','name','period','read_at','version','status','error']))
@app.get('/sources',response_class=HTMLResponse)
def sources(request:Request,q=''):
 import json
 with db.connect() as c:problems=freshness(c)
 return render(request,'sources.html',nav='Източници',rows=source_rows(q),q=q,problems=problems,meta=json.loads((TOOLROOT/'db/ref/datasets.json').read_text(encoding='utf-8')))
@app.get('/how',response_class=HTMLResponse)
def how(request:Request):return render(request,'how.html',nav='Източници',definitions=Q.DEFINITIONS)

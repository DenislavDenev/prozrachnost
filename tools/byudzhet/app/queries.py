import calendar,csv,datetime as dt,io,threading,time
from decimal import Decimal
from urllib.parse import urlencode
from fastapi import HTTPException
from ingest import db
from ingest.parse import REF
LABELS={'debt':'Остатъчна главница по общинския дълг','overdue':'Просрочени задължения','liabilities':'Задължения за разходи','commitments':'Поети ангажименти за разходи'}
LEVELS=[('obshtini','Общини'),('oblasti','Области'),('rayoni','Райони'),('makrorayoni','Макрорайони'),('darzhava','България')]
NUTS={'obshtini':4,'oblasti':3,'rayoni':2,'makrorayoni':1,'darzhava':0}
REGIONS={'BG31':'Северозападен','BG32':'Северен централен','BG33':'Североизточен','BG34':'Югоизточен','BG41':'Югозападен','BG42':'Южен централен','BG3':'Северна и Югоизточна България','BG4':'Югозападна и Южна централна България'}
REFBY={r['id']:r for r in REF};_cache={};_lock=threading.Lock()
def snapshots():
 with _lock:
  if time.monotonic()-_cache.get('time',-1e9)<15:return _cache['data']
  with db.connect() as c:data=[r[0] for r in c.execute('SELECT payload FROM live.snapshot ORDER BY period,ref')]
  _cache.update(time=time.monotonic(),data=data);return data
def bykind(kind):return {r['period']:r for r in snapshots() if r['kind']==kind}
def choose(kind,y=None):
 ss=bykind(kind)
 if y and y not in ss:return None
 return ss.get(y or max(ss,default=''))
def quarters():return sorted(set(bykind('debt'))|set(bykind('indicators')),reverse=True)
def euros(value):return None if value is None or value.get('eur') is None else Decimal(value['eur'])
def validate_filters(oblast='',municipality='',q=''):
 if oblast and oblast not in {r['oblast'] for r in REF}:raise HTTPException(400,'Невалидна област')
 if municipality and (municipality not in REFBY or oblast and REFBY[municipality]['oblast']!=oblast):municipality=''
 return oblast,municipality,q.strip()
def municipality_rows(y=None,oblast='',municipality='',q='',sort='debt',direction='desc',denominator='current'):
 oblast,municipality,q=validate_filters(oblast,municipality,q)
 if denominator not in ('permanent','current'):raise HTTPException(400,'Невалиден знаменател')
 if sort not in ['name','oblast','debt','overdue','liabilities','commitments','debt_per_person']:raise HTTPException(400,'Невалидно сортиране')
 if direction not in ('asc','desc'):raise HTTPException(400,'Невалидна посока')
 y=y or (quarters()[0] if quarters() else None)
 if y:
  try:dt.date.fromisoformat(y)
  except ValueError:raise HTTPException(400,'Невалидно тримесечие')
 sources=[choose(k,y) for k in ('debt','indicators')]
 pop=[s for p,s in bykind('population').items() if y and p<=y and (dt.date.fromisoformat(y)-dt.date.fromisoformat(p)).days<=400]
 pop=max(pop,key=lambda s:s['period']) if pop else None
 by={r['code']:r for s in sources if s for r in s['rows']};pby={r['code']:r for r in pop['rows']} if pop else {}
 # Both source snapshots contribute separate fields without falling back across quarters.
 values={}
 for s in sources:
  if s:
   for r in s['rows']:values.setdefault(r['code'],{}).update(r['values'])
 rows=[]
 for ref in REF:
  if oblast and ref['oblast']!=oblast or municipality and ref['id']!=municipality or q and q.casefold() not in (ref['name_bg']+' '+ref['oblast']+' '+ref['id']).casefold():continue
  v=values.get(ref['id'],{});population=pby.get(ref['id'],{}).get(denominator)
  row=dict(code=ref['id'],name=ref['name_bg'],oblast=ref['oblast'],period=y,population=population,population_date=pop['period'] if pop else None,denominator=denominator,original=v)
  row.update({k:euros(v.get(k)) for k in LABELS})
  for k in LABELS:row[k+'_per_person']=row[k]/population if row[k] is not None and population else None
  row['sources']=[dict(kind=s['kind'],url=s['url']) for s in sources if s]
  rows.append(row)
 present=[r for r in rows if r[sort] is not None];missing=[r for r in rows if r[sort] is None]
 present.sort(key=lambda r:r[sort],reverse=direction=='desc');rows=present+missing
 return dict(rows=rows,y=y,years=quarters(),sources=[s for s in sources if s],population_source=pop,oblast=oblast,municipality=municipality,q=q,sort=sort,direction=direction,denominator=denominator)
def mapdata(m='debt',l='obshtini',y=None,denominator='current'):
 per=m.endswith('_per_person');metric=m.removesuffix('_per_person')
 if metric not in LABELS or l not in NUTS:raise HTTPException(400,'Невалиден показател или ниво')
 d=municipality_rows(y,denominator=denominator);n=NUTS[l];items={}
 for row in d['rows']:
  ref=REFBY[row['code']];code=row['code'] if n==4 else ref['nuts'+str(n)] if n else 'BG'
  name=row['name'] if n==4 else row['oblast'] if n==3 else REGIONS[code] if n else 'България'
  x=items.setdefault(code,dict(code=code,name=name,v=Decimal(0),population=0,valid=True,rank=None,change=None,href='/obshtini/'+row['code']+'?'+urlencode(dict(y=d['y'] or '',denominator=denominator)) if n==4 else None))
  if row[metric] is None or per and not row['population']:x['valid']=False
  if row[metric] is not None:x['v']+=row[metric]
  if row['population']:x['population']+=row['population']
 for x in items.values():x['v']=float(x['v']/x['population'] if per else x['v']) if x['valid'] else None
 rows=sorted(items.values(),key=lambda r:(r['v'] is None,-(r['v'] or 0),r['name']))
 for i,r in enumerate(rows,1):r['rank']=i if r['v'] is not None else None
 s=choose('debt' if metric=='debt' else 'indicators',d['y']);allvalid=all(r[metric] is not None for r in d['rows'])
 bg=sum(r[metric] for r in d['rows']) if allvalid else None
 if per:bg=bg/sum(r['population'] for r in d['rows']) if bg is not None and all(r['population'] for r in d['rows']) else None
 return dict(m=m,l=l,o='bg',nuts=n,title=LABELS[metric],plural=dict(LEVELS)[l],single={'obshtini':'Община','oblasti':'Област','rayoni':'Район','makrorayoni':'Макрорайон','darzhava':'Държава'}[l],unit='€ на лице, регистрирано по '+('настоящ' if denominator=='current' else 'постоянен')+' адрес' if per else '€',digits=2,items=rows,y=d['y'],years=d['years'],levels=LEVELS,bg=None if bg is None else float(bg),base=None,updated=None,countries={},dataset='МФ · '+str(d['y']),url=s['url'] if s else 'https://data.egov.bg',csv='/export-map.csv?'+urlencode(dict(m=m,l=l,y=d['y'] or '',denominator=denominator)),denominator=denominator,population_date=d['population_source']['period'] if d['population_source'] else None)
def state_summary(s):
 if not s:return {}
 out={}
 for r in s['rows']:
  line=r['line']
  key='revenue' if line.startswith('I. ПРИХОДИ') else 'spending' if line.startswith('II.') else 'balance' if 'БЮДЖЕТНО САЛДО' in line.upper() else None
  if key:out[key]=euros(r['actual']);out[key+'_law']=euros(r['law']);out[key+'_pct']=r['pct']
 return out
def times(kind):
 periods=sorted(bykind(kind))
 if not periods:return []
 first,last=dt.date.fromisoformat(periods[0]),dt.date.fromisoformat(periods[-1]);step=3 if kind in ('debt','indicators') else 1
 y,m=first.year,first.month;out=[]
 while (y,m)<=(last.year,last.month):
  out.append(dt.date(y,m,calendar.monthrange(y,m)[1]).isoformat());m+=step
  while m>12:y+=1;m-=12
 return sorted(set(out)|set(periods))
def kfp_summary(s):
 if not s:return dict(revenue=None,spending=None,balance=None)
 rows=s['rows'];total=[r for r in rows if r['budget_type']=='Консолидирана фискална програма'];rows=total or rows
 def add(predicate):
  vals=[euros(v) for r in rows for k,v in r['values'].items() if predicate(k)]
  return sum(vals) if vals and all(v is not None for v in vals) else None
 revenue=add(lambda k:k in ['Данъчни приходи','Неданъчни приходи','Помощи'])
 balance=add(lambda k:'Бюджетно салдо' in k)
 # Payments excluding inter-budget transfers; national + EU budgets appear once each.
 spending=add(lambda k:any(k.startswith(z) for z in ['Персонал','Заплати и възнаграждения','Социални и здравно-осигурителни','Издръжка','Лихви','Социални разходи','Субсидии','Предоставени текущи','Капиталови разходи','Прираст на държавния','Вноска в общия']))
 return dict(revenue=revenue,spending=spending,balance=balance)
def chart(kind,code=None,metrics=None):
 if kind in ('state','kfp'):
  ss=bykind(kind);keys=metrics or ['revenue','spending'];label={'revenue':'Приходи','spending':'Разходи и трансфери' if kind=='state' else 'Разходи и вноска в ЕС','balance':'Бюджетно салдо','revenue_pct':'Приходи спрямо закона','spending_pct':'Разходи спрямо закона'}
  series=[]
  for k in keys:
   series.append(dict(name=label[k],points=[[p,None if (v:=(state_summary(ss.get(p)) if kind=='state' else kfp_summary(ss.get(p))).get(k)) is None else float(v)] for p in times(kind)]))
  return dict(unit='%' if keys[0].endswith('_pct') else '€',series=series)
 if kind=='reserve':return dict(unit='€',series=[dict(name='Фискален резерв',points=[[p,float(euros(s['rows'][0]['value'])) if (s:=choose(kind,p)) else None] for p in times(kind)])])
 series=[]
 for k in metrics or ['debt','overdue','liabilities']:
  source='debt' if k=='debt' else 'indicators';ss=bykind(source);points=[]
  for p in times(source):
   s=ss.get(p);rows=[r for r in s['rows'] if not code or r['code']==code] if s else []
   vs=[euros(r['values'].get(k)) for r in rows];v=sum(vs) if vs and all(x is not None for x in vs) else None
   points.append([p,None if v is None else float(v)])
  series.append(dict(name=LABELS[k],points=points))
 return dict(unit='€',series=series)
def csv_response(rows,headers):
 f=io.StringIO(newline='');w=csv.writer(f);w.writerow(headers)
 for r in rows:w.writerow([('' if r.get(k) is None else str(r[k])) for k in headers])
 return '\ufeff'+f.getvalue()

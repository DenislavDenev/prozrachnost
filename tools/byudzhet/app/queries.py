import calendar,csv,datetime as dt,io,re,threading,time
from decimal import Decimal
from urllib.parse import urlencode
from fastapi import HTTPException
from ingest import db
from ingest.parse import REF
from ingest.state import structure
LABELS={'debt':'Остатъчна главница по общинския дълг','overdue':'Просрочени задължения','liabilities':'Задължения за разходи','commitments':'Поети ангажименти за разходи'}
DEFINITIONS={
 'debt':'Парите, които общината още трябва да върне по заемите си и други форми на дълг към края на тримесечието. Показваме оставащата за връщане сума, без бъдещите лихви.',
 'overdue':'Плащания, с които общината вече закъснява: срокът е минал, а сумата още не е платена. Например приет ремонт с неплатена навреме фактура.',
 'liabilities':'Суми, които общината вече дължи за разходи, но още не е платила. Например ремонтът е извършен и приет, а плащането предстои. Тук МФ отчита определен кръг разходи; заплати, пенсии, лихви по дълга и данъци са извън този показател.',
 'commitments':'Разходи, за които общината вече се е обвързала, например с договор за ремонт на улица, строеж на детска градина или сметосъбиране. Тук показваме оставащата част от тези договори и други поети финансови договорености, която още предстои да се реализира. Например ремонтът е договорен, но работата предстои.'}
LEVELS=[('obshtini','Общини'),('oblasti','Области'),('rayoni','Райони'),('makrorayoni','Макрорайони'),('darzhava','Държава')]
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
 return dict(definition=DEFINITIONS[metric],m=m,l=l,o='bg',nuts=n,title=LABELS[metric],plural=dict(LEVELS)[l],single={'obshtini':'Община','oblasti':'Област','rayoni':'Район','makrorayoni':'Макрорайон','darzhava':'Държава'}[l],unit='€ на лице, регистрирано по '+('настоящ' if denominator=='current' else 'постоянен')+' адрес' if per else '€',digits=2,items=rows,y=d['y'],years=d['years'],levels=LEVELS,bg=None if bg is None else float(bg),base=None,updated=None,countries={},dataset='МФ · '+str(d['y']),url=s['url'] if s else 'https://data.egov.bg',csv='/export-map.csv?'+urlencode(dict(m=m,l=l,y=d['y'] or '',denominator=denominator)),denominator=denominator,population_date=d['population_source']['period'] if d['population_source'] else None)
def state_summary(s):
 if not s:return {}
 out={}
 for r in s['rows']:
  line=r['line']
  key='revenue' if line.startswith('I. ПРИХОДИ') else 'spending' if line.startswith('II.') else 'balance' if 'БЮДЖЕТНО САЛДО' in line.upper() else None
  if key:out[key]=euros(r['actual']);out[key+'_law']=euros(r['law']);out[key+'_pct']=r['pct']
  if line.startswith('III.'):out['eu_contribution']=euros(r['actual'])
 out['spending_total']=out['spending']+out['eu_contribution'] if out.get('spending') is not None and out.get('eu_contribution') is not None else None
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
def kfp_summary(s,budget_type=''):
 if not s:return dict(revenue=None,spending=None,balance=None)
 rows=[r for r in s['rows'] if not budget_type or r['budget_type']==budget_type];total=[r for r in rows if r['budget_type']=='Консолидирана фискална програма'];rows=total or rows
 def add(predicate):
  vals=[euros(v) for r in rows for k,v in r['values'].items() if predicate(k)]
  return sum(vals) if vals and all(v is not None for v in vals) else None
 revenue=add(lambda k:k in ['Данъчни приходи','Неданъчни приходи','Помощи'])
 balance=add(lambda k:'Бюджетно салдо' in k)
 # Payments excluding inter-budget transfers; national + EU budgets appear once each.
 spending=add(lambda k:any(k.startswith(z) for z in ['Персонал','Заплати и възнаграждения','Социални и здравно-осигурителни','Издръжка','Лихви','Социални разходи','Субсидии','Предоставени текущи','Капиталови разходи','Прираст на държавния','Вноска в общия']))
 return dict(revenue=revenue,spending=spending,balance=balance)
def chart(kind,code=None,metrics=None,budget_type='',y=None):
 if kind in ('state','kfp'):
  ss=bykind(kind);keys=metrics or ['revenue','spending_total' if kind=='state' else 'spending'];label={'spending_total':'Разходи, трансфери и вноска в ЕС','revenue':'Приходи','spending':'Разходи и трансфери' if kind=='state' else 'Разходи и вноска в ЕС','balance':'Бюджетно салдо','revenue_pct':'Приходи спрямо закона','spending_pct':'Разходи спрямо закона'}
  series=[]
  for k in keys:
   series.append(dict(name=label[k],points=[[p,None if (v:=(state_summary(ss.get(p)) if kind=='state' else kfp_summary(ss.get(p),budget_type)).get(k)) is None else float(v)] for p in times(kind) if not y or p<=y]))
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

def is_summary(line):
 return bool(re.match(r'^[IVXLCDM]+\.\s',line))

def state_rows(s,q=''):
 rows=structure(s['rows']) if s else []
 for row in rows:
  row['source_pct']=row['pct']
  if row['actual']['eur'] is None:row['pct']=None
  if not euros(row['law']):row['pct']=None
  row['checks']=[];children=[r for r in rows if r['parent']==row['key']]
  # "in this number" is a partial detail, not an exhaustive subtotal.
  if not children or row['ident']=='privatization':continue
  for field,label in [('law','План'),('actual','Отчет')]:
   parts=[r for r in children if not(field=='actual' and r['ident']=='contingency')]
   values=[euros(r[field]) for r in parts];total=euros(row[field])
   if total is None:continue
   if not values or any(v is None for v in values):
    missing=', '.join(r['display_line'] for r,v in zip(parts,values) if v is None);known=sum(v for v in values if v is not None)
    row['checks'].append(label+': разбивката е непълна в източника. Липсва стойност за '+missing+'. Общо '+money_text(total)+', публикувани подсуми '+money_text(known)+', разлика '+money_text(total-known)+'. Празна клетка не е нула; не приписваме остатъка на никое подперо.')
    continue
   calculated=sum((-v if row['ident']=='transfers' and r['ident']=='received' else v) for r,v in zip(parts,values))
   diff=total-calculated
   if abs(diff)>Decimal('0.01'):
    row['checks'].append(label+': публикувано общо '+money_text(total)+', сбор на подредовете '+money_text(calculated)+', разлика '+money_text(diff)+'. Запазваме оригиналните числа; не изравняваме разбивката.')
   else:row['checks'].append(label+': подредовете се събират до публикуваното обобщение.' if row['ident']!='transfers' else label+': дадени минус получени се равнява на нетните трансфери.')
 for row in rows:row['warning']=any('непълна' in c or 'разлика' in c for c in row['checks'])
 return [r for r in rows if q.casefold() in (r['line']+' '+r['display_line']).casefold()]

def money_text(v):
 return format(v,',.2f').replace(',',' ').replace('.',',')+' €'

def state_previous(s):
 return {r['key']:r for r in structure(s['rows'])} if s else {}

def state_help(row,s):
 ident=row['ident']
 sign='При „нето“ обратните движения вече са приспаднати. Минусът показва отрицателния нетен резултат на това перо, а не автоматично грешка или нарушение.'
 if ident=='balance':sign='Плюс означава излишък; минус означава дефицит. Крайният месец е важен: отчет към август не е отчет за цялата година.'
 elif '/received' in row['key']:sign='Получените трансфери са показани като получена сума. При изчисляване на „Трансфери (нето)“ тя се изважда от дадените.'
 elif '/provided' in row['key']:sign='Дадените трансфери увеличават нетно предоставената сума; получените се приспадат отделно. Подперо с „нето“ вече включва обратните движения.'
 elif row['key'].startswith('income'):sign='Плюсът е отчетено постъпление. Отрицателен нетен приход може да отразява възстановявания или корекции; конкретната операция не е посочена в тази таблица.'
 elif row['key'].startswith('financing'):sign='Плюсът е нетен източник на средства, а минусът е нетно използване или погасяване в тази финансова операция. Това е финансиране, не събран данък.'
 elif ident!='transfers':sign='Положителната сума участва в разходите. Отрицателната намалява този сбор; при „нето“ обратните движения вече са приспаднати. Таблицата не посочва конкретната операция зад всеки минус.'
 text=row['description']
 if row['line'].endswith('**'):text+=' Бележката на МФ уточнява, че получените трансфери включват и преводи от НЗОК за одобрени разходи на държавни болници.'
 return dict(title=row['display_line'],text=text,example='Пример (условен): '+row['example'],sign=sign,checks=row['checks'],source=s['url'],parent=row['section'])

COLUMN_HELP={
 'line':dict(title='Перо и подперо',text='Отстъпът показва към кой общ ред принадлежи сумата. Един и същ надпис, например „Общини“, може да се среща при дадени и при получени трансфери.',example='Пример: дадени на общини и получени от общини са два различни реда.'),
 'law_eur':dict(title='Годишен план (закон)',text='Планът по закона за държавния бюджет, приет от Народното събрание. Това е годишният план, публикуван от МФ, не план за избрания месец.',example='Пример: план 100 € за годината и отчет 60 € до август.'),
 'actual_eur':dict(title='Отчет',text='Реално събраното или платеното от началото на годината до избрания месец. Сумите са показани в евро; оригиналната единица остава в CSV.',example='Пример: отчет за декември включва цялата година; отчет за август включва януари–август.'),
 'pct':dict(title='Изпълнение',text='Отчетът, разделен на годишния план, умножен по 100. Липсващ отчет или липсващ/нулев план означава, че процент не може да се изчисли.',example='Пример: 60 € отчет при 100 € план = 60%. При отрицателни нетни операции процентът следва знаците на сумите.'),
 'previous':dict(title='Същият период преди година',text='Сравняваме същото перо в същата категория и за същия краен месец. Дадени и получени трансфери не се смесват. При различен обхват няма съпоставим ред.',example='Пример: дадените на общини през 2026 г. се сравняват с дадените на общини през 2025 г., не с получените от тях.'),
 'chart':dict(title='Приходи, плащания и салдо',text='Плащанията в тази графика включват раздел II и вноската в ЕС от раздел III. Приходи минус тази обща сума дават касовото салдо на държавния бюджет. Държавният бюджет е само част от КФП; статистическият дефицит на страната има друг обхват и метод.',example='Пример: 100 € приходи и 110 € плащания означават салдо −10 €.'),
 'reserve':dict(title='Фискален резерв',text='Финансови средства и вземания, публикувани от МФ към края на месеца. Това е наличност, а не разход или резервът за непредвидени разходи в бюджетната таблица.',example='Пример: салдото на сметка е наличност; платеното от нея през годината е поток.'),
}

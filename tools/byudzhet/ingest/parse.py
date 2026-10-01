"""Source values remain decimal strings; euro comparisons never round before display."""
import calendar,csv,datetime as dt,json,re
from decimal import Decimal,InvalidOperation,getcontext
from .config import ROOT
from .grao import ShapeError
RATE=Decimal('1.95583')
getcontext().prec=60
SCHEMAS=json.loads((ROOT/'db/ref/schemas.json').read_text(encoding='utf-8'))
MONTHS=['януари','февруари','март','април','май','юни','юли','август','септември','октомври','ноември','декември']
def clean(s):
 if not isinstance(s,str):raise ShapeError('Клетката трябва да е текст')
 return ' '.join(s.replace('\ufeff','').strip().strip('"').split())
def norm(s):
 return clean(s).upper().translate(str.maketrans('ABCEHKMOPTXY','АВСЕНКМОРТХУ'))
def key(ob,name):
 ob=norm(ob).removeprefix('ОБЛАСТ ').replace('СОФИЙСКА','СОФИЯ ОБЛАСТ')
 ob={'СОФИЯ (СТОЛИЦА)':'СОФИЯ-ГРАД','СОФИЯ ГРАД':'СОФИЯ-ГРАД','СОФИЯ-СТОЛИЧНА':'СОФИЯ-ГРАД'}.get(ob,ob)
 name=norm(name).replace('Ь','Ъ')
 name={'ДОБРИЧ-СЕЛСКА':'ДОБРИЧКА','ДОБРИЧ-ГРАД':'ДОБРИЧ','ВЪЛЧИДОЛ':'ВЪЛЧИ ДОЛ','БОБОВДОЛ':'БОБОВ ДОЛ','ГЕНЕРАЛ-ТОШЕВО':'ГЕНЕРАЛ ТОШЕВО','СОФИЯ':'СТОЛИЧНА'}.get(name,name)
 name={'ЛЕСИЧЕВО':'ЛЕСИЧОВО','ДОЛНА МИТРОПОЛИ':'ДОЛНА МИТРОПОЛИЯ','СТОЛИЧНА ОБЩИНА':'СТОЛИЧНА'}.get(name,name)
 if name=='СТОЛИЧНА':ob='СОФИЯ-ГРАД'
 return ob,name
def reference():
 with (ROOT/'db/ref/municipality.csv').open(encoding='utf-8-sig') as f:return list(csv.DictReader(f))
REF=reference(); MATCH={key(r['oblast'],r['name_bg']):r for r in REF}
def number(s):
 s=clean(s).replace('\u00a0','').replace(' ','')
 if re.fullmatch(r'-?\d{1,3}(?:,\d{3})+\.\d+',s):s=s.replace(',','')
 if re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+,\d+',s):s=s.replace('.','')
 if re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+\.\d{1,2}',s):
  a,b=s.rsplit('.',1);s=a.replace('.','')+'.'+b
 s=s.replace(',','.')
 if s in ('','-','..'):return None
 if re.fullmatch(r'\d{1,3}(?:\.\d{3}){2,}',s):s=s.replace('.','')
 try:v=Decimal(s)
 except InvalidOperation as e:raise ShapeError('Невалидно число: '+s) from e
 if not v.is_finite():raise ShapeError('Невалидно число')
 return v
def money(s,currency,mult=1):
 v=number(s)
 return {'original':None if v is None else str(v),'unit':('млн. ' if mult==1000000 else '')+currency,'eur':None if v is None else str(v*mult/(RATE if currency=='BGN' else 1))}
def period(name):
 s=clean(name).lower()
 m=re.search(r'(20\d{2})-(\d{2})-(\d{2})',s)
 if m:d=dt.date(*map(int,m.groups()))
 else:
  m=re.search(r'(\d{2})\.(\d{2})\.(20\d{2})',s)
  if m:d=dt.date(int(m[3]),int(m[2]),int(m[1]))
  else:
   y=re.search(r'20\d{2}',s); month=next((i for i,n in enumerate(MONTHS,1) if n in s),None)
   if not y or not month:raise ShapeError('Непознат период: '+name)
   y=int(y[0]);d=dt.date(y,month,calendar.monthrange(y,month)[1])
 if d>dt.date.today():raise ShapeError('Бъдещ период')
 return d.isoformat()
def currency(p):return 'EUR' if p>='2026-01-01' else 'BGN'
def table(raw):
 try:d=json.loads(raw)
 except (ValueError,UnicodeError) as e:raise ShapeError('Невалиден JSON/HTML') from e
 if not isinstance(d,dict) or set(d)!={'success','data'} or d['success'] is not True or not isinstance(d['data'],list) or not d['data']:raise ShapeError('Променен отговор на API')
 rows=d['data']
 if not all(isinstance(r,list) and r and (all(isinstance(c,str) for c in r) or r==[None]) for r in rows):raise ShapeError('Променени редове')
 return [r for r in rows if r!=[None]]
def near(a,b,tol='0.01'):
 if a is None or b is None or abs(a-b)>Decimal(tol):raise ShapeError('Неуспешна сверка: '+str(a)+' != '+str(b))
def municipalities(rows,kind,p):
 head=[clean(x) for x in rows[0]]
 if kind=='indicators' and p=='2022-03-31' and len(head)==59 and head in [[clean(x) for x in h] for h in SCHEMAS[kind]]:
  if rows[1][40]!='Q1-2022 г.' or rows[1][43]!='Q1-2022 г.' or rows[1][46]!='Q1-2022 г.':raise ShapeError('Променени периоди в многоредовата таблица')
  converted=[['ОБЛАСТ','ОБЩИНА','Просрочени задължения по бюджет','Задължения за разходи по бюджет','Поети ангажименти за разходи по бюджет']];other=[]
  prefixes={}
  for row in rows[2:]:
   candidates=[r for r in REF if key('',r['name_bg'])[1]==key('',row[1])[1]]
   if len(candidates)==1:
    prefix=row[0][:2];oblast=candidates[0]['oblast']
    if prefix in prefixes and prefixes[prefix]!=oblast:raise ShapeError('Противоречив областен код')
    prefixes[prefix]=oblast
  for row in rows[2:]:
   if len(row)!=59:raise ShapeError('Отрязана многоредова таблица')
   if not any(clean(x) for x in row):continue
   if not row[1] and not any(row[j] for j in (40,43,46)):other.append(row);continue
   if row[1]=='sum':
    converted.append(['За страната','Всичко',row[40],row[43],row[46]]);continue
   candidates=[r for r in REF if key('',r['name_bg'])[1]==key('',row[1])[1] and r['oblast']==prefixes.get(row[0][:2])]
   if len(candidates)!=1:raise ShapeError('Нееднозначна община в историческата таблица: '+row[1])
   converted.append([candidates[0]['oblast'],row[1],row[40],row[43],row[46]])
  result=municipalities(converted,kind,p);result['notes']+=other;return result
 expected=['ОБЛАСТ','ОБЩИНА','РАЗМЕР НА ОСТАТЪЧНИЯ ДЪЛГ']
 if kind=='debt' and head!=expected:raise ShapeError('Файлът не съдържа общински дълг')
 if kind=='indicators' and head not in [[clean(x) for x in h] for h in SCHEMAS[kind]]:raise ShapeError('Непознати колони на финансовите показатели')
 fields=['debt'] if kind=='debt' else ['overdue','liabilities','commitments']
 out=[]; totals=None; notes=[]
 for row in rows[1:]:
  if len(row)!=len(head):raise ShapeError('Отрязан общински ред')
  if kind=='debt' and p=='2018-12-31':row=row[:2]+[x.replace(',','') for x in row[2:]]
  nums=[number(x) for x in row[2:2+len(fields)]]
  if not any(x is not None for x in nums):notes.append(row);continue
  if norm(row[0]) in ('ЗА СТРАНАТА','ВСИЧКО') or norm(row[1])=='ВСИЧКО' or not any(clean(x) for x in row[:2]):
   if totals is not None:raise ShapeError('Дублиран национален сбор')
   totals=nums[:len(fields)];continue
  r=MATCH.get(key(*row[:2]))
  if not r:raise ShapeError('Несъпоставена община: '+repr(row[:2]))
  out.append(dict(code=r['id'],name=r['name_bg'],oblast=r['oblast'],nuts3=r['nuts3'],nuts2=r['nuts2'],nuts1=r['nuts1'],values={k:money(v,currency(p)) for k,v in zip(fields,row[2:])},extra=dict(zip(head[2+len(fields):],row[2+len(fields):]))))
 if len(out)!=265 or len({r['code'] for r in out})!=265:raise ShapeError('Не са съпоставени 265/265 общини')
 if totals:
  for i,k in enumerate(fields):near(sum(Decimal(r['values'][k]['original']) for r in out),totals[i])
 # Every municipality belongs to exactly one oblast; aggregate the same decimals independently.
 for k in fields:
  by={}
  for r in out:
   v=r['values'][k]['original']
   if v is None:continue
   by[r['nuts3']]=by.get(r['nuts3'],Decimal(0))+Decimal(v)
  near(sum(by.values()),sum(Decimal(r['values'][k]['original']) for r in out if r['values'][k]['original'] is not None),'0')
 return dict(rows=out,notes=notes,reconciliation='265/265; национален сбор' if totals else '265/265; сбор по области',unit_basis='МФ: евро от 2026 г.; лева до 2025 г.')
def state(rows,p):
 h=[clean(x) for x in rows[0]]
 if len(h)==2 and not h[0] and h[1].startswith('Изпълнение'):
  rows=[[r[0],'',r[1]] for r in rows];h=['','Закон',h[1]]
 h[1]=h[1].lstrip("'")
 if len(h) not in (3,4) or h[0] or not h[1].startswith('Закон') or not h[2].startswith('Изпълнение') or (len(h)==4 and h[3] not in ('%','')):raise ShapeError('Променени колони на държавния бюджет')
 unit=' '.join(h[1:3]);c='EUR' if 'евро' in unit else 'BGN' if 'лв' in unit else None
 if c!=currency(p) or 'млн' not in unit or ('лв' in unit and c!='BGN'):raise ShapeError('Непозната или противоречива единица')
 out=[];notes=[]
 for i,row in enumerate(rows[1:]):
  if len(row)!=len(h):raise ShapeError('Отрязан бюджетен ред')
  if all(number(v) is None for v in row[1:3]):notes.append(row);continue
  law,actual=[number(v) for v in row[1:3]]
  pct=None if len(row)==4 and row[3]=='#DIV/0!' and not law else number(row[3]) if len(row)==4 else actual/law if law and actual is not None else None
  if law is not None and actual is not None and law!=0 and pct is not None:near(pct,actual/law,'0.000001')
  out.append(dict(line=clean(row[0]),order=i,law=money(row[1],c,1000000),actual=money(row[2],c,1000000),pct=None if pct is None else str(pct*100)))
 for field in ['law','actual']:
  vals={r['line']:number(r[field]['original'] or '') for r in out}
  total=next((v for k,v in vals.items() if k.startswith('I. ПРИХОДИ')),None)
  parts=[vals.get(k) for k in ['Данъчни приходи','Неданъчни приходи','Помощи']]
  if None in parts and field=='law':continue
  if None in parts:raise ShapeError('Липсва съставно перо на приходите')
  near(total,sum(parts))
 return dict(rows=out,notes=notes,reconciliation='Приходи = данъчни + неданъчни + помощи; процент = отчет / закон',cumulative=True)
def kfp(rows,p):
 h=[clean(x) for x in rows[0]]
 if h not in [[clean(x) for x in z] for z in SCHEMAS['kfp']]:raise ShapeError('Непознати колони на КФП')
 out=[];notes=[]
 for r in rows[1:]:
  if len(r)!=len(h):raise ShapeError('Отрязан ред на КФП')
  if not any(number(x) is not None for x in r[1:]):notes.append(r);continue
  out.append(dict(budget_type=clean(r[0]),values={k:money(v,currency(p),1000000) for k,v in zip(h[1:],r[1:])}))
 if len({r['budget_type'] for r in out})!=len(out):raise ShapeError('Дублиран вид бюджет')
 total=next((r for r in out if r['budget_type']=='Консолидирана фискална програма'),None)
 if total:
  for k in h[1:]:near(number(total['values'][k]['original'] or ''),sum(number(r['values'][k]['original'] or '') for r in out if r is not total))
 return dict(rows=out,notes=notes,columns=h[1:],reconciliation='Сбор по видове бюджети = КФП' if total else 'Няма публикуван общ ред КФП; сверка със сбор не е възможна',cumulative=True)
def reserve(rows,p):
 if rows[0][0]=='':
  if len(rows[0])!=2 or 'млн.' not in rows[0][1] or 'лв' not in rows[0][1] or currency(p)!='BGN':raise ShapeError('Променена единица на резерва')
  rows=rows[1:]
 out=[];notes=[]
 for r in rows:
  if len(r)!=2:raise ShapeError('Променена таблица на резерва')
  if number(r[1]) is None:notes.append(r);continue
  out.append(dict(line=clean(r[0]),value=money(r[1],currency(p),1000000)))
 if not out or not out[0]['line'].startswith('Общ размер на фискалния резерв'):raise ShapeError('Липсва фискален резерв')
 parts=[number(r['value']['original']) for r in out if r['line'].startswith(('I.','ІІ.','II.'))]
 if len(parts)!=2:raise ShapeError('Липсват съставните части на резерва')
 near(number(out[0]['value']['original']),sum(parts))
 return dict(rows=out,notes=notes,reconciliation='Фискален резерв = средства + вземания')
def parse(raw,kind,name):
 p=period(name);rows=table(raw)
 data=municipalities(rows,kind,p) if kind in ('debt','indicators') else globals()[kind](rows,p)
 data.update(period=p,kind=kind,input_rows=len(rows),currency=currency(p))
 return data
def population(raw):
 from .grao import parse as grao
 d=grao(raw);out=[]
 for row in d['municipalities']:
  r=MATCH.get(key(row['oblast'],row['municipality']))
  if not r:raise ShapeError('Несъпоставена община на ГРАО: '+str(row))
  out.append(dict(code=r['id'],name=r['name_bg'],permanent=row['permanent'],current=row['current']))
 if len(out)!=265 or len({r['code'] for r in out})!=265:raise ShapeError('ГРАО: не са 265/265 общини')
 return dict(period=d['date'],kind='population',rows=out,input_rows=len(out),reconciliation='265/265; областни сборове на ГРАО',notes=[])

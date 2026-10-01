import argparse,datetime as dt,json,sys
from pathlib import Path
from . import db,store
from .config import SETS,ROOT
from .http import Client
from .parse import ShapeError,period
from .grao import catalog
from .checks import freshness
def resources(client,ds):
 out=[];page=1
 while True:
  raw=client.api('listResources',dict(criteria={'dataset_uri':ds},records_per_page=100,page_number=page))
  try:d=json.loads(raw)
  except ValueError as e:raise ShapeError('Каталогът не е JSON') from e
  if set(d)!={'success','resources','total_records'} or d['success'] is not True or not isinstance(d['resources'],list):raise ShapeError('Променен каталог')
  out+=d['resources']
  if len(out)>=int(d['total_records']):break
  if not d['resources']:raise ShapeError('Отрязан каталог')
  page+=1
 if len(out)!=int(d['total_records']) or len({r['uri'] for r in out})!=len(out):raise ShapeError('Невалиден брой ресурси')
 for r in out:
  if not {'uri','name','version','updated_at'}<=r.keys() or not all(isinstance(r[k],str) for k in ('uri','name')):raise ShapeError('Променени полета на ресурс')
 return out
def refresh(c,client,archive=None,latest_only=False):
 result={'stored':0,'unchanged':0,'held':0,'gaps':[],'problems':[]}
 known_gaps=json.loads((ROOT/'db/ref/source_gaps.json').read_text(encoding='utf-8'))
 for kind,ds in SETS.items():
  rs=resources(client,ds)
  known={r[0] for r in c.execute('SELECT ref FROM src.resource WHERE kind=%s',(kind,))}
  missing=known-{r['uri'] for r in rs}
  for ref in sorted(missing):
   logstate=c.execute('SELECT status FROM src.resource WHERE ref=%s',(ref,)).fetchone()[0]
   if logstate!='липсва при източника':store.log(c,kind,ref,None,None,None,'gone')
   c.execute("UPDATE src.resource SET status='липсва при източника',error='Ресурсът липсва в актуалния каталог; последните валидни данни са запазени' WHERE ref=%s",(ref,))
   result['problems'].append(kind+': липсва '+ref)
  if latest_only:rs=sorted(rs,key=lambda r:period(r['name']),reverse=True)[:1]
  for r in sorted(rs,key=lambda r:period(r['name'])):
   # An explicitly supplied archive is a one-off seed; daily runs always read source bytes anew.
   files=sorted(Path(archive,ds,r['uri']).glob(str(r['version'])+'.*.json')) if archive else []
   raw=files[-1].read_bytes() if files else client.api('getResourceData',{'resource_uri':r['uri']})
   try:state=store.put(c,kind,r['uri'],r['name'],r['version'],raw,{k:r[k] for k in ('uri','name','version','updated_at')});result[state]+=1
   except ShapeError as e:
    result['gaps'].append(dict(kind=kind,name=r['name'],ref=r['uri'],error=str(e)))
    if known_gaps.get(r['uri'],{}).get('sha256')!=store.digest(raw):result['problems'].append(kind+': '+r['name']+': '+str(e))
 return result
def grao(c,client,latest_only=False):
 urls=catalog(client.get('https://www.grao.bg/tables.html'))
 urls=[u for u in urls if '/t41ob-' in u or ('/tadr' in u and int(u.rsplit('/',1)[-1].removeprefix('tadr').strip('-').removesuffix('.txt'))>=2021)]
 if latest_only:urls=sorted(urls,key=lambda u:('t41ob' in u,u))[-1:]
 count=0
 for url in urls:
  count+=store.put(c,'population',url.rsplit('/',1)[-1],url,'1',client.get(url))=='stored'
 return dict(stored=count,problems=[])
def main():
 p=argparse.ArgumentParser();p.add_argument('--step',choices=['migrate','refresh','grao','freshness'],required=True);p.add_argument('--archive');p.add_argument('--latest-only',action='store_true');a=p.parse_args()
 c=db.connect(autocommit=True);client=Client();report={};lock=8003000+['migrate','refresh','grao','freshness'].index(a.step)
 if not c.execute('SELECT pg_try_advisory_lock(%s)',(lock,)).fetchone()[0]:print(json.dumps(dict(status='пропуснато',problems=[])));return
 try:
  if a.step=='migrate':db.migrate(c);report={'problems':[]}
  else:
   job=c.execute("INSERT INTO ops.job_run(step,status) VALUES (%s,'работи') RETURNING id",(a.step,)).fetchone()[0]
   try:
    report=refresh(c,client,a.archive,a.latest_only) if a.step=='refresh' else grao(c,client,a.latest_only) if a.step=='grao' else {'problems':freshness(c)}
   except Exception as e:report={'problems':[str(e)]}
   c.execute('UPDATE ops.job_run SET finished_at=now(),status=%s,report=%s WHERE id=%s',('неуспешно' if report.get('problems') else 'наред',json.dumps(report,ensure_ascii=False),job))
 finally:
  client.close();c.execute('SELECT pg_advisory_unlock(%s)',(lock,));c.close()
 print(json.dumps(report,ensure_ascii=False,default=str))
 if report.get('problems'):sys.exit(1)
if __name__=='__main__':main()

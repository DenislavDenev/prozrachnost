import datetime as dt,hashlib,json
from psycopg.types.json import Jsonb
from .config import DATA
from .parse import parse,population,ShapeError
def digest(raw):return hashlib.sha256(raw).hexdigest()
def log(c,kind,ref,field,old,new,cause):
 c.execute('INSERT INTO ops.change_log(source,ref,field,old,new,cause) VALUES (%s,%s,%s,%s,%s,%s)',(kind,ref,field,None if old is None else str(old),None if new is None else str(new),cause))
def flatten(data):
 out={}
 for i,r in enumerate(data['rows']):
  key=r.get('code',r.get('line',r.get('budget_type',str(i))))
  def walk(v,path):
   if isinstance(v,dict):
    for k,x in sorted(v.items()):walk(x,path+'/'+k)
   else:out[path]=str(v) if v is not None else None
  walk(r,str(key))
 return out
def should_hold(old,new):
 a,b=flatten(old),flatten(new)
 return len(new['rows'])<len(old['rows']) or any(k not in b or (a[k] is not None and b[k] is None) for k in a)
def confirmed(held,sha,now):return bool(held and held[0]==sha and now-held[1]>=dt.timedelta(days=1))
def put(c,kind,ref,name,version,raw,metadata=None,now=None):
 now=now or dt.datetime.now(dt.timezone.utc);sha=digest(raw)
 path=DATA/'raw'/kind/ref/(sha+'.'+('txt' if kind=='population' else 'json'))
 path.parent.mkdir(parents=True,exist_ok=True)
 if not path.exists():path.write_bytes(raw)
 c.execute('INSERT INTO ops.raw_file(source,ref,path,sha256,bytes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(kind,ref,str(path),sha,len(raw)))
 old_meta=c.execute('SELECT sha256,status,version FROM src.resource WHERE ref=%s',(ref,)).fetchone()
 c.execute("INSERT INTO src.resource(ref,kind,name,version,read_at,status,metadata) VALUES (%s,%s,%s,%s,%s,'работи',%s) ON CONFLICT(ref) DO UPDATE SET read_at=excluded.read_at,metadata=excluded.metadata",(ref,kind,name,str(version),now,Jsonb(metadata or {})))
 try:data=population(raw) if kind=='population' else parse(raw,kind,name)
 except ShapeError as e:
  # Invalid new bytes never replace the last valid snapshot.
  if not old_meta or old_meta[0]!=sha or old_meta[1]!='невалиден отговор':log(c,kind,ref,None,old_meta[0] if old_meta else None,str(e),'invalid')
  c.execute("UPDATE src.resource SET status='невалиден отговор',error=%s,sha256=%s WHERE ref=%s",(str(e),sha,ref));raise
 old=c.execute('SELECT payload FROM live.snapshot WHERE ref=%s',(ref,)).fetchone()
 old=old[0] if old else None
 data['url']=name if kind=='population' else 'https://data.egov.bg/data/resourceView/'+ref
 data['resource']=ref;data['version']=str(version)
 if old and old==data:
  if old_meta and old_meta[0]!=sha:log(c,kind,ref,'sha256',old_meta[0],sha,'rewritten')
  c.execute("UPDATE src.resource SET status='наред',error=NULL,sha256=%s,version=%s,period=%s WHERE ref=%s",(sha,str(version),data['period'],ref))
  c.execute('DELETE FROM ops.held WHERE ref=%s',(ref,));return 'unchanged'
 held=c.execute('SELECT sha256,first_seen FROM ops.held WHERE ref=%s',(ref,)).fetchone()
 if old and should_hold(old,data) and not confirmed(held,sha,now):
  if not held or held[0]!=sha:
   c.execute('INSERT INTO ops.held VALUES (%s,%s,%s) ON CONFLICT(ref) DO UPDATE SET sha256=excluded.sha256,first_seen=excluded.first_seen',(ref,sha,now));log(c,kind,ref,None,None,sha,'held')
  c.execute("UPDATE src.resource SET status='задържан до второ четене',error=NULL WHERE ref=%s",(ref,));return 'held'
 with c.transaction():
  if old_meta and old and old_meta[0]!=sha:log(c,kind,ref,'sha256',old_meta[0],sha,'rewritten')
  if old_meta and old and old_meta[2]!=str(version):log(c,kind,ref,'version',old_meta[2],version,'rewritten')
  c.execute('INSERT INTO stage.snapshot VALUES (%s,%s,%s,%s) ON CONFLICT(ref) DO UPDATE SET payload=excluded.payload,period=excluded.period',(ref,kind,data['period'],Jsonb(data)))
  a=flatten(old) if old else {};b=flatten(data)
  for k in sorted(a.keys()|b.keys()):
   if a.get(k)!=b.get(k):log(c,kind,ref,k,a.get(k),b.get(k),'confirmed' if held else 'rewritten' if old else 'new-record')
  c.execute('INSERT INTO live.snapshot SELECT * FROM stage.snapshot WHERE ref=%s ON CONFLICT(ref) DO UPDATE SET payload=excluded.payload,period=excluded.period',(ref,))
  c.execute('DELETE FROM stage.snapshot WHERE ref=%s',(ref,))
  c.execute('DELETE FROM ops.held WHERE ref=%s',(ref,))
  c.execute("UPDATE src.resource SET status='наред',error=NULL,sha256=%s,version=%s,period=%s WHERE ref=%s",(sha,str(version),data['period'],ref))
 return 'stored'

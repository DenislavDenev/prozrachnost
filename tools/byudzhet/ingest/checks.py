import datetime as dt
import json
from .config import ROOT
def freshness(c,today=None):
 today=today or dt.date.today();out=[]
 for kind,days in [('state',45),('kfp',45),('reserve',45),('debt',150),('indicators',150),('population',45)]:
  p=c.execute('SELECT max(period) FROM live.snapshot WHERE kind=%s',(kind,)).fetchone()[0]
  if p is None or (today-p).days>days:out.append(f'{kind}: няма данни' if p is None else f'{kind}: остаряло, последно валидно {p}')
  read=c.execute('SELECT max(read_at) FROM src.resource WHERE kind=%s',(kind,)).fetchone()[0]
  if read is None or (dt.datetime.now(dt.timezone.utc)-read).total_seconds()>172800:out.append(f'{kind}: няма успешно четене в последните 48 часа')
 out += [f'{r}: задържан над ден' for r, in c.execute("SELECT ref FROM ops.held WHERE first_seen < now()-interval '1 day'")]
 known=json.loads((ROOT/'db/ref/source_gaps.json').read_text(encoding='utf-8'))
 for ref,sha,status,error in c.execute("SELECT ref,sha256,status,error FROM src.resource WHERE status IN ('невалиден отговор','липсва при източника')"):
  if status=='невалиден отговор' and known.get(ref,{}).get('sha256')==sha:continue
  out.append(f'{ref}: {status}: {error}')
 return out

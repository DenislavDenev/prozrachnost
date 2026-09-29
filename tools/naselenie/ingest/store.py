import datetime as dt
import hashlib
import json

from psycopg.types.json import Jsonb

from .config import RAW
from .db import log_change
from .parse import ShapeError


def state(conn, url, status, error=None, rows=None):
    conn.execute('''INSERT INTO ops.source_state(url,last_read,last_ok,status,error,rows)
      VALUES (%s,now(),CASE WHEN %s='ok' THEN now() END,%s,%s,%s)
      ON CONFLICT(url) DO UPDATE SET last_read=now(),
      last_ok=CASE WHEN EXCLUDED.status='ok' THEN now() ELSE ops.source_state.last_ok END,
      status=EXCLUDED.status,error=EXCLUDED.error,rows=EXCLUDED.rows''', (url,status,status,error,rows))


def save_raw(conn, url, raw):
    sha = hashlib.sha256(raw).hexdigest()
    path = RAW / (sha + '-' + url.rsplit('/',1)[-1].split('?')[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    conn.execute('INSERT INTO ops.raw_file(url,sha,path) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (url,sha,str(path)))
    return sha


def keyed(data):
    result = {}
    for i, r in enumerate(data.get('municipalities', [])):
        result['municipality/' + r['code']] = r
    # Source order disambiguates two identically named places; no identity across sources is claimed.
    for i, r in enumerate(data.get('places', [])):
        result[f"place/{r['code']}/{r['name']}/{i}"] = r
    if not result:
        result = {r['ekatte']: r for r in data.get('reference', [])}
    return result


def apply(conn, url, sha, data, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    kind = data.get('kind','reference')
    new = keyed(data)
    if not new:
        raise ShapeError('Празен импорт')
    with conn.transaction():
        cur = conn.execute('SELECT payload FROM live.source WHERE url=%s', (url,)).fetchone()
        old = keyed(cur[0]) if cur else {}
        removed = old.keys() - new.keys()
        if removed:
            held = conn.execute('SELECT sha,first_at FROM ops.held WHERE url=%s', (url,)).fetchone()
            if not held or held[0] != sha:
                conn.execute('INSERT INTO ops.held(url,sha,first_at) VALUES(%s,%s,%s) ON CONFLICT(url) DO UPDATE SET sha=EXCLUDED.sha,first_at=EXCLUDED.first_at',(url,sha,now))
            if not held or held[0] != sha or now - held[1] < dt.timedelta(days=1):
                state(conn,url,'held','По-малък отговор, чака второ четене',len(new))
                if not held or held[0] != sha:
                    log_change(conn,'grao',url,'rows',len(old),len(new),'held')
                return 'held'
            log_change(conn,'grao',url,'rows',len(old),len(new),'confirmed')
        for key in old.keys() | new.keys():
            before, after = old.get(key), new.get(key)
            if before == after:
                continue
            if before is None or after is None:
                log_change(conn,'grao',url,key,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),'new-record' if before is None else 'removed')
            else:
                for field in before.keys() | after.keys():
                    if before.get(field) != after.get(field):
                        log_change(conn,'grao',url,key+'/'+field,before.get(field),after.get(field),'rewritten')
        if old != new:
            conn.execute('''INSERT INTO live.source(url,sha,as_of,kind,payload) VALUES(%s,%s,%s,%s,%s)
              ON CONFLICT(url) DO UPDATE SET sha=EXCLUDED.sha,as_of=EXCLUDED.as_of,
              kind=EXCLUDED.kind,payload=EXCLUDED.payload,published_at=now()''',(url,sha,data.get('date'),kind,Jsonb(data)))
        conn.execute('DELETE FROM ops.held WHERE url=%s',(url,))
        state(conn,url,'ok',rows=len(new))
    return 'unchanged' if old == new else 'stored'

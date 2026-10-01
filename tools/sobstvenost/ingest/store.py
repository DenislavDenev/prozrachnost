import datetime as dt
import hashlib,json
from psycopg.types.json import Jsonb
from .parse import ShapeError
from .config import DATA

def canonical(rows): return json.dumps(sorted(rows,key=lambda r:str(r['id'])),sort_keys=True,ensure_ascii=False,separators=(',',':'))
def digest(rows): return hashlib.sha256(canonical(rows).encode()).hexdigest()
def reconcile(rows,expected):
    if len({r['id'] for r in rows})!=len(rows): raise ShapeError('duplicate source identity')
    if len(rows)!=expected: raise ShapeError(f'reconciliation: {len(rows)} != {expected}')

def save_raw(conn,source,url,raw):
    sha=hashlib.sha256(raw).hexdigest()
    path=DATA/'raw'/source/str(dt.date.today())/sha
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        existing=path.read_bytes()
        if len(existing)!=len(raw) or hashlib.sha256(existing).hexdigest()!=sha:raise ShapeError('immutable raw archive is corrupted '+str(path))
    else: path.write_bytes(raw)
    conn.execute('INSERT INTO ops.raw_file(source,url,path,sha256,bytes) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(source,url,str(path),sha,len(raw)))
    return sha

def state(conn,source,scope,status,error=None,rows=None,sha=None):
    conn.execute('''INSERT INTO ops.source_state(source,scope,status,last_read,last_success,rows,error,sha256) VALUES (%s,%s,%s,now(),CASE WHEN %s='ok' THEN now() END,%s,%s,%s)
    ON CONFLICT(source,scope) DO UPDATE SET status=EXCLUDED.status,last_read=EXCLUDED.last_read,last_success=COALESCE(EXCLUDED.last_success,ops.source_state.last_success),rows=COALESCE(EXCLUDED.rows,ops.source_state.rows),error=EXCLUDED.error,sha256=COALESCE(EXCLUDED.sha256,ops.source_state.sha256)''',(source,scope,status,status,rows,error,sha))

def log(conn,source,ref,field,old,new,cause):
    conn.execute('INSERT INTO ops.change_log(source,ref,field,old,new,cause) VALUES (%s,%s,%s,%s,%s,%s)',(source,ref,field,json.dumps(old,ensure_ascii=False) if old is not None else None,json.dumps(new,ensure_ascii=False) if new is not None else None,cause))

def apply(conn,source,scope,rows,expected,answer_sha=None):
    reconcile(rows,expected)
    sha=answer_sha or digest(rows)
    with conn.transaction():
        old={ref:payload for ref,payload in conn.execute('SELECT ref,payload FROM live.record WHERE source=%s AND scope=%s AND gone_at IS NULL',(source,scope))}
        new={str(r['id']):r for r in rows}
        removed=old.keys()-new.keys()
        nested_loss=any(
            {json.dumps(x,sort_keys=True) for x in old[ref].get(field,[])} - {json.dumps(x,sort_keys=True) for x in new[ref].get(field,[])}
            for ref in old.keys()&new.keys() for field in ('board','documents','report_links','municipalities')
        )
        scalar_loss=any(old[ref].get(field) is not None and new[ref].get(field) is None for ref in old.keys()&new.keys() for field in ('eik','share_pct','principal','filed_on','owner','share_text','participation','year','quarter','unit'))
        held=conn.execute("SELECT sha256,first_at<=now()-interval '1 day' FROM ops.held WHERE source=%s AND scope=%s",(source,scope)).fetchone()
        if removed or nested_loss or scalar_loss:
            if not held or held[0]!=sha:
                conn.execute('INSERT INTO ops.held(source,scope,sha256) VALUES (%s,%s,%s) ON CONFLICT(source,scope) DO UPDATE SET sha256=EXCLUDED.sha256,first_at=now()',(source,scope,sha))
                log(conn,source,scope,'rows',len(old),len(new),'held')
                state(conn,source,scope,'held',f'{len(removed)} missing records await an identical read after one day',len(old),sha)
                return 'held'
            if not held[1]: return 'held'
            log(conn,source,scope,'rows',len(old),len(new),'confirmed')
        conn.execute('DELETE FROM ops.held WHERE source=%s AND scope=%s',(source,scope))
        conn.execute('DELETE FROM stage.record WHERE source=%s AND scope=%s',(source,scope))
        for ref,row in new.items():
            conn.execute('INSERT INTO stage.record VALUES (%s,%s,%s,%s)',(source,scope,ref,Jsonb(row)))
            vsha=digest([row])
            conn.execute('INSERT INTO ops.version(source,scope,ref,sha256,payload) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(source,scope,ref,vsha,Jsonb(row)))
            if ref not in old: log(conn,source,f'{scope}/{ref}',None,None,row,'new-record')
            else:
                for field in sorted(old[ref].keys()|row.keys()):
                    if old[ref].get(field)!=row.get(field):log(conn,source,f'{scope}/{ref}',field,old[ref].get(field),row.get(field),'rewritten')
        for ref in removed:
            conn.execute('UPDATE live.record SET gone_at=now() WHERE source=%s AND scope=%s AND ref=%s',(source,scope,ref))
            log(conn,source,f'{scope}/{ref}',None,old[ref],None,'removed')
        conn.execute('''INSERT INTO live.record(source,scope,ref,payload) SELECT source,scope,ref,payload FROM stage.record WHERE source=%s AND scope=%s
        ON CONFLICT(source,scope,ref) DO UPDATE SET payload=EXCLUDED.payload,last_seen=now(),gone_at=NULL''',(source,scope))
        state(conn,source,scope,'ok',rows=len(rows),sha=sha)
        return 'unchanged' if old==new else 'stored'

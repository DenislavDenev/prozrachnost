"""Original attachment archive; no financial extraction or inferred currency."""
import hashlib,json
from collections import defaultdict
from psycopg.types.json import Jsonb
from . import store,parse
from .progress import Queue

def media_type(raw):
    if raw.startswith(b'%PDF-'):return 'application/pdf'
    if raw.startswith(b'PK\x03\x04'):return 'application/zip'
    if raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):return 'application/x-ole-storage'
    raise parse.ShapeError('unrecognized original attachment format; raw retained, not called PDF')

def selected(conn,source):
    state=conn.execute("SELECT status FROM ops.source_state WHERE source=%s AND scope='details'",(source,)).fetchone()
    if not state or state[0]!='ok':raise parse.ShapeError(source+' attachment archive requires complete verified metadata')
    result=[]
    if source=='appk':
        reports=[r[0] for r in conn.execute("SELECT payload FROM live.record WHERE source='appk' AND scope LIKE 'reports:%%' AND gone_at IS NULL")]
        annual=[r for r in reports if r['kind']=='annual'];latest=defaultdict(int)
        for row in annual:latest[row['appk_id']]=max(latest[row['appk_id']],row['year'])
        for row in annual:
            if row['year']!=latest[row['appk_id']]:continue
            for doc in row['documents']:
                result.append(dict(id=f"{row['appk_id']}:{row['id']}:{doc['id']}",url=doc['url'],title=doc['title'],report_id=row['id'],year=row['year'],appk_id=row['appk_id']))
    else:
        for payload, in conn.execute("SELECT payload FROM live.record WHERE source='ncr' AND scope LIKE 'profile:%%' AND gone_at IS NULL"):
            for doc in payload['documents']:
                result.append(dict(id=payload['id']+':'+hashlib.sha256(doc['url'].encode()).hexdigest(),url=doc['url'],title=doc['title'],ncr_id=payload['id']))
    # The same source attachment may be linked more than once inside a profile.
    unique={row['id']:row for row in result}
    return sorted(unique.values(),key=lambda row:row['id'])

def archive(conn,client,fetch,resume=False,continue_pending=False):
    from .progress import pending
    results={}
    continuing=continue_pending and (pending('appk','pdfs') or pending('ncr','pdfs'))
    for source in ('appk','ncr'):
        if continuing and not pending(source,'pdfs') and source=='appk':continue
        queue=Queue(source,'pdfs',selected(conn,source),continue_pending)
        store.state(conn,source,'pdfs','partial','Файловите оригинали още не са прочетени докрай',queue.done)
        for row in queue.rows[queue.done:]:
            client.raws.clear()
            raw=fetch(conn,client,source,row['url'],resume=resume)
            kind=media_type(raw);sha=hashlib.sha256(raw).hexdigest()
            conn.execute('''INSERT INTO ops.document_file(source,ref,sha256,url,media_type,bytes,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source,ref,sha256) DO UPDATE SET last_seen=now()''',(source,row['id'],sha,row['url'],kind,len(raw),Jsonb(row)))
            queue.advance()
            store.state(conn,source,'pdfs','partial','Файловите оригинали още не са прочетени докрай',queue.done)
            print(json.dumps(dict(progress=source+'-documents',completed=queue.done,total=len(queue.rows))),flush=True)
        store.state(conn,source,'pdfs','ok',rows=queue.done)
        conn.execute('UPDATE ops.source_state SET last_success=%s WHERE source=%s AND scope=%s',(queue.data['started_at'],source,'pdfs'))
        queue.complete();results[source]=dict(completed=queue.done,total=len(queue.rows))
    return results

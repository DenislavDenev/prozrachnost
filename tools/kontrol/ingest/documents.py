import datetime as dt
import time
from psycopg.types.json import Jsonb
from . import store
from .pdf import title_excerpt
from .parse import canonical, ShapeError

def save_document(c,r,raw,metadata,fetch_url=None):
    sha=store.archive(c,r['source'],fetch_url or r['url'],raw)
    metadata['url']=r['url']
    old=c.execute('SELECT payload FROM live.document WHERE ref=%s',(r['id'],)).fetchone()
    with c.transaction():
        c.execute('INSERT INTO ops.document_version(ref,sha256,url,payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(r['id'],sha,r['url'],Jsonb(metadata)))
        c.execute('INSERT INTO live.document(ref,payload) VALUES (%s,%s) ON CONFLICT(ref) DO UPDATE SET payload=excluded.payload,read_at=now()',(r['id'],Jsonb(metadata)))
        if not old or old[0]!=metadata:
            store.log(c,r['source'],r['id'],'document_sha256',old[0].get('document_sha256') if old else None,sha,'rewritten' if old else 'new-record')

def cpc(c,client,limit=None,max_seconds=None):
    from . import sources,parse
    from .pdf import cpc_document
    report=dict(stored=0,unchanged=0,pending=0,problems=[],checkpoint=False)
    deadline=time.monotonic()+max_seconds if max_seconds is not None else None
    class Checkpoint(Exception):pass
    def save(source,url,raw):store.archive(c,source,url,raw)
    def callback(rows,raw,url):
        data=parse.postback_form(raw)
        for r in rows:
            if deadline is not None and time.monotonic()>=deadline:
                raise Checkpoint()
            old=c.execute('SELECT read_at FROM live.document WHERE ref=%s',(r['id'],)).fetchone()
            if old and dt.datetime.now(dt.timezone.utc)-old[0]<dt.timedelta(days=7):
                report['unchanged']+=1;continue
            if not r.get('document_event'):
                report['pending']+=1;continue
            if limit is not None and report['stored']>=limit:
                report['pending']+=1;continue
            try:
                form=dict(data,__EVENTTARGET=r['document_event'],__EVENTARGUMENT='')
                document=client.request('POST',url,data=form)
                if document.startswith(b'%PDF-'):
                    metadata=cpc_document(document)
                elif document.startswith((bytes.fromhex('d0cf11e0a1b11ae1'),b'PK')):
                    metadata=dict(document_sha256=store.digest(document),text_available=False,excerpt=None,excerpt_page=None,unp=None,text_status='само документ: стар формат, без надеждно извлечен текст')
                else:
                    raise ShapeError('КЗК: отговорът не е разпознат документ')
                metadata['fetch_url']=url
                metadata['fetch_method']='POST: източников бутон за конкретен акт'
                save_document(c,r,document,metadata,fetch_url=url)
                report['stored']+=1
            except Exception as e:report['problems'].append(r['id']+': '+str(e))
    # Reuses the proven real stateful search/pagination contract, never fabricated URLs.
    try:
        rows,manifest=sources.cpc(client,save,document_callback=callback)
        report['source_count']=manifest['source_count'];report['pages']=manifest['pages']
    except Checkpoint:
        report['checkpoint']=True
        eligible={r['id'] for x in c.execute("SELECT payload FROM live.snapshot WHERE source='cpc'") for r in x[0]['rows']}
        archived={r[0] for r in c.execute('SELECT ref FROM live.document')}
        report['pending']=len(eligible-archived)
        report['scope']='Планирано прекъсване по време; каталогът и последният пълен прочит не се променят'
    if report['pending'] and not report['checkpoint']:report['problems'].append('КЗК: само документ/неприключено архивиране: '+str(report['pending']))
    return report

def refresh(c,client,source=None,limit=None,max_seconds=None):
    if source=='cpc':return cpc(c,client,limit,max_seconds)
    rows=canonical([r for x in c.execute('SELECT payload FROM live.snapshot ORDER BY source') for r in x[0]['rows']])
    rows=[r for r in rows if r['source']!='cpc' and r.get('kind')!='Приключила финансова инспекция' and (source is None or source in r['categories'])]
    now=dt.datetime.now(dt.timezone.utc)
    deadline=time.monotonic()+max_seconds if max_seconds is not None else None
    report=dict(stored=0,unchanged=0,pending=0,problems=[])
    for r in rows:
        if deadline is not None and time.monotonic()>=deadline:
            archived={r[0] for r in c.execute('SELECT ref FROM live.document')}
            report['checkpoint']=True
            report['pending']=len({r['id'] for r in rows}-archived)
            break
        old=c.execute('SELECT payload,read_at FROM live.document WHERE ref=%s',(r['id'],)).fetchone()
        if old and now-old[1]<dt.timedelta(days=7) and old[0]['url']==r['url']:
            report['unchanged']+=1;continue
        if limit is not None and report['stored']>=limit:
            report['pending']+=1;continue
        try:
            raw=client.get(r['url']);sha=store.archive(c,r['source'],r['url'],raw)
            if r['url'].lower().endswith('.pdf'):
                metadata=title_excerpt(raw)
            else:
                if not raw.startswith((bytes.fromhex('d0cf11e0a1b11ae1'),b'PK',b'{\\rtf')):
                    raise ShapeError('Връзката не върна разпознат документен формат')
                metadata=dict(text_available=False,excerpt=None,excerpt_page=None,document_sha256=sha,text_status='само документ: стар формат, текстът не е надеждно прочетен')
            metadata['url']=r['url']
            save_document(c,r,raw,metadata)
            report['stored']+=1
        except Exception as e:
            report['problems'].append(r['id']+': '+str(e))
    if report['pending'] and not report.get('checkpoint'):
        report['problems'].append('Недовършено наваксване на документи: '+str(report['pending']))
    return report

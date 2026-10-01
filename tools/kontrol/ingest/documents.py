import datetime as dt
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

def cpc(c,client,limit=None):
    from . import sources,parse
    from .pdf import cpc_document
    report=dict(stored=0,unchanged=0,pending=0,problems=[])
    def save(source,url,raw):store.archive(c,source,url,raw)
    def callback(rows,raw,url):
        data=parse.postback_form(raw)
        for r in rows:
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
    rows,manifest=sources.cpc(client,save,document_callback=callback)
    if report['pending']:report['problems'].append('КЗК: само документ/неприключено архивиране: '+str(report['pending']))
    report['source_count']=manifest['source_count'];report['pages']=manifest['pages']
    return report

def refresh(c,client,source=None,limit=None):
    if source=='cpc':return cpc(c,client,limit)
    rows=canonical([r for x in c.execute('SELECT payload FROM live.snapshot ORDER BY source') for r in x[0]['rows']])
    rows=[r for r in rows if r['source']!='cpc' and r.get('kind')!='Приключила финансова инспекция' and (source is None or source in r['categories'])]
    now=dt.datetime.now(dt.timezone.utc)
    report=dict(stored=0,unchanged=0,pending=0,problems=[])
    for r in rows:
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
                metadata=dict(text_available=False,excerpt=None,excerpt_page=None,document_sha256=sha,text_status='само документ: стар формат, текстът не е надеждно прочетен')
            metadata['url']=r['url']
            save_document(c,r,raw,metadata)
            report['stored']+=1
        except Exception as e:
            report['problems'].append(r['id']+': '+str(e))
    if report['pending']:
        report['problems'].append('Недовършено наваксване на документи: '+str(report['pending']))
    return report

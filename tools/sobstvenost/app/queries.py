"""What the pages read: the serving layer only (pub_sobstvenost.* and pub.*). Not the clean tables, not the landing
zone, not the ledger: the web role has no right to them.

One request is one snapshot: every query of a request sees the same publication, never a mix. The tables of a
publication are turned into the records the pages have always used (one dict per enterprise, report, concession,
profile and version, as the parser gave them), once per publication, and shared by the requests that show it."""
import contextlib
import contextvars
import csv
import datetime as dt
import io
import threading
from collections import Counter, defaultdict
from urllib.parse import urlencode

import psycopg

from ingest.config import DSN

LEVELS=[('obshtini','Общини'),('oblasti','Области'),('rayoni','Райони'),('makrorayoni','Макрорайони'),('darzhava','Държава')]
NUTS=dict(obshtini=4,oblasti=3,rayoni=2,makrorayoni=1,darzhava=0)
REGIONS={'BG31':'Северозападен','BG32':'Северен централен','BG33':'Североизточен','BG34':'Югоизточен','BG41':'Югозападен','BG42':'Южен централен','BG3':'Северна и Югоизточна България','BG4':'Югозападна и Южна централна България'}

_lock = threading.Lock()
_cache = {}
_state = contextvars.ContextVar('state', default=None)


@contextlib.contextmanager
def snapshot():
    """A read-only connection whose every statement sees the database as of its first one."""
    with psycopg.connect(DSN) as c:
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        c.read_only = True
        yield c


def version(c):
    """(number, time) of the publication this snapshot shows; None before the first one."""
    return c.execute("SELECT publication_id, published_at FROM pub.publication WHERE tool = 'sobstvenost' "
                     'ORDER BY publication_id DESC LIMIT 1').fetchone()


def _dicts(c, sql, params=()):
    cur = c.execute(sql, params)
    names = [d.name for d in cur.description]
    return [dict(zip(names, v)) for v in cur.fetchall()]


def _text(v):
    """A value as the parser gave it: the published percentage and the dates as text."""
    if v is None:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v.isoformat()
    return str(v)


def build(c):
    """Every record of the publication as the pages read it."""
    catalogue = {}
    catalogue['appk'] = [dict(id=x['appk_id'], name=x['name'], status=x['status'], source_url=x['source_url'],
                              source_warning=x['source_warning'], share_pct=_text(x['share_pct']), owner=x['owner'],
                              share_text=x['share_text'], participation=x['participation'])
                         for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.enterprise ORDER BY appk_id COLLATE "C"')]
    catalogue['ncr'] = [dict({k: x[k] for k in ('reg_number', 'status', 'procedure_status', 'name', 'kind', 'subject', 'area_extent',
                                                 'conceder', 'related_id', 'related_reg', 'source_url')},
                             id=x['concession_id'], municipalities=[], concessionaire_eik=None)
                        for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.concession ORDER BY concession_id COLLATE "C"')]
    profiles = {'appk': {}, 'ncr': {}}
    for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.profile ORDER BY appk_id COLLATE "C"'):
        profiles['appk'][x['appk_id']] = dict({k: v for k, v in x.items() if k not in ('appk_id', 'report_count')},
                                              id=x['appk_id'], reports=x['report_count'])
    boards = defaultdict(list)
    for b in _dicts(c, 'SELECT * FROM pub_sobstvenost.board_entry ORDER BY appk_id, sha256, position'):
        boards[(b['appk_id'], b['sha256'])].append(dict(name=b['member_name'], country=b['country']) if b['entry'] is None
                                                   else dict(original=b['entry'], verbatim=True))
    versions = defaultdict(list)
    for v in _dicts(c, 'SELECT * FROM pub_sobstvenost.profile_version ORDER BY observed_at, sha256 COLLATE "C"'):
        versions[v['appk_id']].append(dict(payload=dict(board=boards[(v['appk_id'], v['sha256'])], board_format=v['board_format']),
                                           observed_at=v['observed_at'], sha256=v['sha256']))
    for appk_id, p in profiles['appk'].items():
        if versions[appk_id]:
            p['board'] = versions[appk_id][-1]['payload']['board']
    documents = defaultdict(list)
    for d in _dicts(c, 'SELECT * FROM pub_sobstvenost.report_document ORDER BY appk_id, record_id, position'):
        documents[(d['appk_id'], d['record_id'])].append(dict(id=d['document_id'], title=d['title'], url=d['url']))
    reports = defaultdict(list)
    for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.report ORDER BY appk_id, record_id COLLATE "C"'):
        reports[x['appk_id']].append(dict(id=x['record_id'], report_id=x['report_id'], kind=x['kind'], year=x['year'],
                                          quarter=x['quarter'], filed_on=_text(x['filed_on']), title=x['title'],
                                          documents=documents[(x['appk_id'], x['record_id'])], source_url=x['source_url'],
                                          financials=None, appk_id=x['appk_id']))
    vdocs = defaultdict(list)
    for d in _dicts(c, 'SELECT * FROM pub_sobstvenost.report_version_document ORDER BY appk_id, record_id, sha256, position'):
        vdocs[(d['appk_id'], d['record_id'], d['sha256'])].append(dict(id=d['document_id'], title=d['title'], url=d['url']))
    report_versions = defaultdict(list)
    for v in _dicts(c, 'SELECT * FROM pub_sobstvenost.report_version ORDER BY observed_at DESC, record_id COLLATE "C", sha256 COLLATE "C"'):
        report_versions[v['appk_id']].append(dict(
            payload=dict(id=v['record_id'], report_id=v['report_id'], kind=v['kind'], year=v['year'],
                         documents=vdocs[(v['appk_id'], v['record_id'], v['sha256'])]),
            observed_at=v['observed_at'], sha256=v['sha256']))
    places, names, notices, cdocs = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.notice_place ORDER BY concession_id, notice_position, position'):
        places[(x['concession_id'], x['notice_position'])].append(dict(oblast=x['oblast'], municipality=x['municipality']))
    for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.notice_municipality ORDER BY concession_id, notice_position, position'):
        names[(x['concession_id'], x['notice_position'])].append(x['name'])
    for n in _dicts(c, 'SELECT * FROM pub_sobstvenost.notice ORDER BY concession_id, position'):
        key = (n['concession_id'], n['position'])
        notices[n['concession_id']].append(dict(municipality_names=names[key], location_places=places[key],
                                                location_original=n['location_original'], concessionaire_eik=n['concessionaire_eik'],
                                                concessionaire_name=n['concessionaire_name'], term_months=n['term_months'],
                                                financials=None, source_url=n['source_url']))
    for d in _dicts(c, 'SELECT * FROM pub_sobstvenost.concession_document ORDER BY concession_id, position'):
        cdocs[d['concession_id']].append(dict(url=d['url'], title=d['title']))
    for x in _dicts(c, 'SELECT * FROM pub_sobstvenost.concession_profile ORDER BY concession_id COLLATE "C"'):
        cid = x['concession_id']
        profiles['ncr'][cid] = dict(documents=cdocs[cid], notices=notices[cid], id=cid, source_url=x['source_url'])
    archives = defaultdict(lambda: defaultdict(list))
    for a in _dicts(c, 'SELECT * FROM pub_sobstvenost.attachment ORDER BY first_seen DESC, sha256'):
        owner = a['appk_id'] if a['source'] == 'appk' else a['concession_id']
        archives[(a['source'], owner)][a['url']].append(dict(url=a['url'], sha256=a['sha256'], media_type=a['media_type'],
                                                             first_seen=a['first_seen']))
    saved = _dicts(c, 'SELECT * FROM pub_sobstvenost.source_state ORDER BY source, scope')
    states = [s for s in saved if s['scope'] != 'read_profiles'] + [s for s in saved if s['scope'] == 'read_profiles']
    return dict(catalogue=catalogue, profiles=profiles, versions=versions, reports=reports, report_versions=report_versions,
                archives=archives, states=states, places=_places(c))


def _places(c):
    """The 265 municipalities of the shared serving layer: the ЕИК of the municipality (the code of its contour), its
    name, the oblast and the codes of the oblast, region and macro-region."""
    return [dict(id=r[0], name_bg=r[1], oblast=r[2], nuts3=r[3], nuts2=r[4], nuts1=r[5]) for r in c.execute(
        '''SELECT m.eik, m.name_bg, o.label_bg, o.nuts3_code, n3.parent_code, n2.parent_code
           FROM pub.municipality m JOIN pub.oblast o USING (oblast_code)
           JOIN pub.nuts n3 ON n3.nuts_code = o.nuts3_code JOIN pub.nuts n2 ON n2.nuts_code = n3.parent_code
           WHERE NOT m.is_gone ORDER BY m.municipality_code''')]


def load(c):
    """The records of the publication this snapshot shows, built once per publication."""
    v = version(c)
    key = v[0] if v else None
    with _lock:
        if _cache.get('key', -1) != key or 'data' not in _cache:
            _cache.update(key=key, data=build(c))
        return v, _cache['data']


def use(c, state):
    return _state.set(dict(state, conn=c))


def current():
    """The state of this request; outside a request (a test, a script) the publication of a fresh snapshot."""
    state = _state.get()
    if state is None:
        with snapshot() as c:
            state = load(c)[1]
    return state


def records(source, scope):
    if scope != 'catalogue':
        raise ValueError('only the catalogues are lists of records')
    return current()['catalogue'][source]


def states():
    return current()['states']


def profiles(source):
    return current()['profiles'][source]

def enterprises(principal='',participation='',status='',q='',sort='name',direction='asc'):
    details=profiles('appk');rows=[];catalogue=records('appk','catalogue')
    for record in catalogue:
        row=dict(record,**{k:v for k,v in details.get(record['id'],{}).items() if k not in record})
        row['has_profile']=record['id'] in details
        if principal and row.get('principal')!=principal:continue
        if participation and row.get('participation')!=participation:continue
        if status and row.get('status')!=status:continue
        if q and q.casefold() not in (row['name']+' '+(row.get('eik') or '')).casefold():continue
        rows.append(row)
    allowed={'name','principal','share_pct','status','id'}
    if sort not in allowed:sort='name'
    present=[r for r in rows if r.get(sort) is not None];missing=[r for r in rows if r.get(sort) is None]
    present.sort(key=lambda r:float(r[sort]) if sort=='share_pct' else str(r.get(sort,'')).casefold(),reverse=direction=='desc')
    rows=present+missing
    return dict(rows=rows,count=len(rows) if catalogue else None,principals=sorted({p.get('principal') for p in details.values() if p.get('principal')}),groups=Counter(r.get('principal') or 'Профилът още не е прочетен' for r in rows),filters=dict(principal=principal,participation=participation,status=status,q=q,sort=sort,direction=direction))

def concessions(kind='',subject='',conceder='',status='',q='',municipality='',sort='reg_number',direction='desc'):
    details=profiles('ncr');rows=[];catalogue=records('ncr','catalogue')
    for row in catalogue:
        row=dict(row);detail=details.get(row['id'],{})
        row['has_profile']=bool(detail)
        row['notices']=detail.get('notices',[])
        row['municipality_names']=sorted({name for n in row['notices'] for name in n.get('municipality_names',[])})
        if any(value and row.get(key)!=value for key,value in [('kind',kind),('subject',subject),('conceder',conceder),('status',status)]):continue
        if q and q.casefold() not in (row['name']+' '+row['reg_number']).casefold():continue
        if municipality and municipality not in row['municipality_names']:continue
        rows.append(row)
    if sort not in {'reg_number','name','kind','subject','conceder','status'}:sort='reg_number'
    rows.sort(key=lambda r:str(r.get(sort,'')).casefold(),reverse=direction=='desc')
    return dict(rows=rows,count=len(rows) if catalogue else None,groups=Counter(r['kind'] for r in rows),filters=dict(kind=kind,subject=subject,conceder=conceder,status=status,q=q,municipality=municipality,sort=sort,direction=direction))

def enterprise(id):
    row=next((r for r in records('appk','catalogue') if r['id']==id),None)
    if row is None:return None
    allprofiles=profiles('appk');detail=allprofiles.get(id)
    state=current()
    link_eik=detail.get('eik') if detail and detail.get('eik') and sum(p.get('eik')==detail['eik'] for p in allprofiles.values())==1 else None
    return dict(row=row,detail=detail,link_eik=link_eik,reports=state['reports'].get(id,[]),versions=state['versions'].get(id,[]),report_versions=state['report_versions'].get(id,[]),archives=document_versions('appk',id))

def document_versions(source,id):
    return current()['archives'].get((source,id),{})

def concession(id):
    row=next((r for r in records('ncr','catalogue') if r['id']==id),None)
    if row is None:return None
    return dict(row=row,detail=profiles('ncr').get(id))

def csv_response(rows,fields):
    out=io.StringIO();out.write('﻿');writer=csv.DictWriter(out,fields,extrasaction='ignore');writer.writeheader()
    for row in rows:
        result={k:('; '.join(row[k]) if isinstance(row.get(k),list) else row.get(k)) for k in fields}
        # Spreadsheet formulas from source-controlled text must never execute.
        result={k: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for k,v in result.items()}
        writer.writerow(result)
    return out.getvalue()

def mapdata(l='obshtini',kind='',status='',conceder='',q='',subject='',municipality=''):
    if l not in NUTS:raise ValueError('invalid map level')
    d=concessions(kind=kind,status=status,conceder=conceder,q=q,subject=subject,municipality=municipality)
    REF=current()['places']
    byname=defaultdict(list)
    for ref in REF:byname[ref['name_bg'].casefold()].append(ref)
    members=defaultdict(set);mapped=set();unmatched=set()
    n=NUTS[l]
    items={}
    for ref in REF:
        code=ref['id'] if n==4 else ref['nuts'+str(n)] if n else 'BG'
        name=ref['name_bg'] if n==4 else ref['oblast'] if n==3 else REGIONS[code] if n else 'България'
        items[code]=dict(code=code,name=name,v=None,rank=None,href='/koncesii?'+urlencode(dict(municipality=ref['name_bg'],kind=kind,status=status,conceder=conceder,q=q)) if n==4 else None)
    for row in d['rows']:
        places=[p for notice in row['notices'] for p in notice.get('location_places',[])]
        for name in row['municipality_names']:
            candidates=byname.get(name.casefold(),[])
            source_oblasti={p['oblast'].casefold() for p in places if p['municipality'].casefold()==name.casefold()}
            if source_oblasti:candidates=[r for r in candidates if r['oblast'].casefold() in source_oblasti]
            if len(candidates)!=1:unmatched.add((row['id'],name));continue
            ref=candidates[0]
            code=ref['id'] if n==4 else ref['nuts'+str(n)] if n else 'BG'
            members[code].add(row['id']);mapped.add(row['id'])
    for code,ids in members.items():items[code]['v']=len(ids)
    # No territorial observation is unknown, not a demonstrated zero concession count.
    rows=sorted(items.values(),key=lambda r:(r['v'] is None,-(r['v'] or 0),r['name']))
    for i,r in enumerate(rows,1):r['rank']=i if r['v'] is not None else None
    state=next((s for s in states() if s['source']=='ncr' and s['scope']=='catalogue'),None)
    y=str(state['last_success'].date()) if state and state['last_success'] else None
    coverage=f'{len(mapped)} от {d["count"]} партиди имат проверено и съпоставено място на изпълнение; {len(unmatched)} несъпоставени териториални записа. Една партида може да е в няколко общини. Националният брой не е сборът на общините.'
    return dict(m='concessions',l=l,o='bg',nuts=n,title='Проверени партиди на концесии',plural=dict(LEVELS)[l],single={'obshtini':'Община','oblasti':'Област','rayoni':'Район','makrorayoni':'Макрорайон','darzhava':'Държава'}[l],unit='партиди',digits=0,items=rows,y=y,years=[y] if y else [],levels=LEVELS,bg=len(mapped) if mapped else None,base=None,updated=y,countries={},dataset='НКР, място на изпълнение в обявление',url='https://ncr.government.bg/Concessions',csv='/export-map.csv?'+urlencode(dict(l=l,kind=kind,status=status,conceder=conceder,q=q,subject=subject,municipality=municipality)),denominator='',population_date=None,definition=coverage,coverage=coverage,reading='Избраното ниво е '+dict(LEVELS)[l].lower()+'. По-тъмното зелено означава повече проверени партиди. Сивото е липса на доказано териториално покритие.',count=d['count'],mapped=len(mapped),unmatched=len(unmatched),filters=dict(kind=kind,status=status,conceder=conceder,q=q,subject=subject,municipality=municipality))

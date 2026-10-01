import csv,io,datetime as dt
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlencode
from psycopg.rows import dict_row
from ingest import db
from ingest.config import ROOT

REF=list(csv.DictReader((ROOT/'db/ref/municipality.csv').open(encoding='utf-8')))
LEVELS=[('obshtini','Общини'),('oblasti','Области'),('rayoni','Райони'),('makrorayoni','Макрорайони'),('darzhava','Държава')]
NUTS=dict(obshtini=4,oblasti=3,rayoni=2,makrorayoni=1,darzhava=0)
REGIONS={'BG31':'Северозападен','BG32':'Северен централен','BG33':'Североизточен','BG34':'Югоизточен','BG41':'Югозападен','BG42':'Южен централен','BG3':'Северна и Югоизточна България','BG4':'Югозападна и Южна централна България'}

def records(source,scope):
    with db.connect() as c:return [r[0] for r in c.execute('SELECT payload FROM live.record WHERE source=%s AND scope=%s AND gone_at IS NULL ORDER BY ref',(source,scope))]
def states():
    with db.connect(row_factory=dict_row) as c:
        result=c.execute("SELECT * FROM ops.source_state WHERE scope NOT LIKE 'profile:%%' AND scope NOT LIKE 'reports:%%' ORDER BY source,scope").fetchall()
        for source in ('appk','ncr'):
            row=c.execute("SELECT count(*) AS rows,max(last_success) AS last_success,max(last_read) AS last_read FROM ops.source_state WHERE source=%s AND scope LIKE 'profile:%%' AND status='ok'",(source,)).fetchone()
            if row['rows']:result.append(dict(row,source=source,scope='read_profiles',status='partial',error='Това е броят на отделно прочетените профили. Пълното наваксване има отделен статус.'))
        return result
def profiles(source):
    with db.connect() as c:return {r[0]:r[1] for r in c.execute("SELECT ref,payload FROM live.record WHERE source=%s AND scope LIKE 'profile:%%' AND gone_at IS NULL",(source,))}

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
    with db.connect(row_factory=dict_row) as c:
        versions=c.execute("SELECT payload,observed_at,sha256 FROM ops.version WHERE source='appk' AND scope=%s ORDER BY observed_at",('profile:'+id,)).fetchall()
        report_versions=c.execute("SELECT payload,observed_at,sha256 FROM ops.version WHERE source='appk' AND scope=%s ORDER BY observed_at DESC",('reports:'+id,)).fetchall()
    link_eik=detail.get('eik') if detail and detail.get('eik') and sum(p.get('eik')==detail['eik'] for p in allprofiles.values())==1 else None
    return dict(row=row,detail=detail,link_eik=link_eik,reports=records('appk','reports:'+id),versions=versions,report_versions=report_versions)

def concession(id):
    row=next((r for r in records('ncr','catalogue') if r['id']==id),None)
    if row is None:return None
    return dict(row=row,detail=profiles('ncr').get(id))

def csv_response(rows,fields):
    out=io.StringIO();out.write('\ufeff');writer=csv.DictWriter(out,fields,extrasaction='ignore');writer.writeheader()
    for row in rows:
        result={k:('; '.join(row[k]) if isinstance(row.get(k),list) else row.get(k)) for k in fields}
        # Spreadsheet formulas from source-controlled text must never execute.
        result={k: "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for k,v in result.items()}
        writer.writerow(result)
    return out.getvalue()

def mapdata(l='obshtini',kind='',status='',conceder='',q='',subject='',municipality=''):
    if l not in NUTS:raise ValueError('invalid map level')
    d=concessions(kind=kind,status=status,conceder=conceder,q=q,subject=subject,municipality=municipality)
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

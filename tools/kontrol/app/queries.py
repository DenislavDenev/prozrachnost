import csv
import io
from collections import Counter
from urllib.parse import urlencode
from psycopg.rows import dict_row
from ingest import db, parse
from ingest.config import SOURCES
from ingest.privacy import public_row

def snapshots():
    with db.connect() as c:
        return [r[0] for r in c.execute('SELECT payload FROM live.snapshot ORDER BY source')]

def resources():
    with db.connect(row_factory=dict_row) as c:
        saved = {r['source']: r for r in c.execute('SELECT * FROM src.resource ORDER BY source')}
    return [dict(source=key, label=label, url=url, **{k:v for k,v in saved.get(key, dict(status='Няма доказан пълен прочит')).items() if k not in ('source','url')}) for key,(label,url) in SOURCES.items()]

def document_coverage():
    eligible={r['id'] for s in snapshots() for r in s['rows'] if r.get('kind')!='Приключила финансова инспекция'}
    with db.connect() as c:
        docs={r[0]:r[1] for r in c.execute('SELECT ref,payload FROM live.document')}
    return dict(eligible=len(eligible),processed=len(eligible & docs.keys()),pending=len(eligible-docs.keys()),
                text_available=sum(bool(docs[r].get('text_available')) for r in eligible & docs.keys()),
                excerpts=sum(bool(docs[r].get('excerpt')) for r in eligible & docs.keys()))

def allrows():
    # Canonical source ID deduplicates reports present in multiple category lists.
    rows = [r for s in snapshots() for r in s['rows']]
    rows = parse.canonical(rows)
    with db.connect() as c:
        docs={r[0]:r[1] for r in c.execute('SELECT ref,payload FROM live.document')}
    for row in rows:
        doc=docs.get(row['id'],{})
        if doc.get('url')==row['url']:
            row.update({k:v for k,v in doc.items() if k!='url'})
    return [public_row(row) for row in rows]

def select(view, year='', kind='', sector='', q='', sort='published', source=''):
    rows = allrows()
    if view == 'oditi':
        rows = [r for r in rows if any(s.startswith('nao') for s in r['categories']) and not r.get('recommendation_text')]
    elif view == 'preporaki':
        rows = [r for r in rows if any(s.startswith('recommendations') for s in r['categories'])]
    elif view == 'inspekcii':
        rows = [r for r in rows if r['source'].startswith('adfi')]
    elif view == 'kzk':
        rows = [r for r in rows if r['source'] == 'cpc']
    options = {k: sorted({r.get(k) for r in rows if r.get(k)}, reverse=k=='publication_year') for k in ['publication_year','kind','sector']}
    rows = [r for r in rows if (not year or r.get('publication_year') == year)
            and (not kind or r.get('kind') == kind) and (not sector or r.get('sector') == sector)
            and (not source or source in r['categories'])
            and (not q or q.casefold() in ' '.join(str(r.get(k) or '') for k in ['title','auditee','case_no','report_period','recommendation_text','excerpt']).casefold())]
    if sort == 'title':
        rows.sort(key=lambda r:(r['title'].casefold(),r['id']))
    elif sort == 'year':
        rows.sort(key=lambda r:(r.get('publication_year') or '',r['id']), reverse=True)
    else:
        rows.sort(key=lambda r:(r.get('published_on') or '',r.get('publication_year') or '',r['id']), reverse=True)
    return rows, options

def summary(rows):
    audits = {r['id'] for r in rows if any(s.startswith('nao') for s in r['categories'])}
    decisions = {r['id'] for r in rows if r['source']=='cpc'}
    cases = {r.get('source_case_id') for r in rows if r['source']=='cpc' and r.get('source_case_id')}
    inspections = {r['id'] for r in rows if r.get('kind')=='Приключила финансова инспекция'}
    adfi_docs = {r['id'] for r in rows if r['source']=='adfi'}
    recs = {r['id'] for r in rows if any(s.startswith('recommendations') for s in r['categories'])}
    return dict(total=len(rows),audits=len(audits),decisions=len(decisions),cases=len(cases),inspections=len(inspections),adfi_documents=len(adfi_docs),recommendations=len(recs))

def timeline(rows):
    return sorted(Counter(r.get('publication_year') or 'Не е посочена' for r in rows).items())

FIELDS = ['id','source','title','kind','sector','auditee','eik','case_no','act_date','published_on','publication_year','completed_on','report_period','unp','recommendation_text','violations_count','document_page','document_sha256','url']
def csv_response(rows):
    buf=io.StringIO(newline='');writer=csv.writer(buf);writer.writerow(FIELDS)
    for r in rows:
        cells=[]
        for k in FIELDS:
            v='' if r.get(k) is None else str(r[k])
            if v.startswith(('=','+','-','@')):v="'"+v
            cells.append(v)
        writer.writerow(cells)
    return '\ufeff'+buf.getvalue()

def link(path, **kwargs):
    return path+'?'+urlencode({k:v for k,v in kwargs.items() if v is not None and v!=''})

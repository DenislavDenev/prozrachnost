import csv,io
from fastapi.testclient import TestClient
from ingest import db,store,parse
from app.main import app
from app import queries as Q
from .test_sources import P

def seed(conn,monkeypatch):
    monkeypatch.setattr(db,'connect',lambda **kw: __import__('psycopg').connect(conn.info.dsn,**kw))
    companies=parse.companies((P/'appk-companies-1.html').read_bytes())[0]
    store.apply(conn,'appk','catalogue',companies,len(companies))
    detail=parse.profile((P/'appk-105.html').read_bytes());detail.update(id='105',source_url=companies[1]['source_url'])
    store.apply(conn,'appk','profile:105',[detail],1)
    report=parse.report((P/'appk-annual-9848.html').read_bytes(),parse.APPK+'/Public/Public/CompanyDetailsAnnualReport/9848')
    store.apply(conn,'appk','reports:105',[report],1)
    concessions=parse.concessions((P/'ncr-search-1.html').read_bytes())[0]
    store.apply(conn,'ncr','catalogue',concessions,len(concessions))
    store.apply(conn,'ncr','profile:'+concessions[0]['id'],[dict(id=concessions[0]['id'],documents=[],notices=[dict(source_url='https://ncr.government.bg/Preview/AssignedConcession/example',municipality_names=['Симеоновград','Бяла'],location_places=[dict(oblast='Хасково',municipality='Симеоновград')],concessionaire_eik='208499411',concessionaire_name='МЛ МОНТАЖИ ООД',location_original='Област: Хасково, Община: Симеоновград',term_months=300)])],1)

def test_all_pages_profiles_feedback(conn,monkeypatch):
    seed(conn,monkeypatch);client=TestClient(app)
    for url in ['/','/?view=concessions','/predpriyatiya','/predpriyatiya/id/105','/predpriyatiya/831646048','/koncesii','/koncesii/f0648cf4-8c12-4cf8-9c57-4f739e9b6396','/karta','/sources','/how','/rights']:
        response=client.get(url)
        assert response.status_code==200,url+' '+response.text[:400]
        assert 'Обратна връзка' in response.text and 'Подкрепи проекта' in response.text
    assert client.get('/predpriyatiya/831646049').status_code==404
    assert client.get('/export-map.csv?l=invalid').status_code==400
    assert 'companies/831646048' in client.get('/predpriyatiya/id/105').text
    assert 'не дати на назначение' in client.get('/predpriyatiya/id/105').text

def test_filters_summary_csv_match(conn,monkeypatch):
    seed(conn,monkeypatch);client=TestClient(app)
    params=dict(participation='direct',q='АВТО',status='Активно')
    rows=list(csv.DictReader(io.StringIO(client.get('/export-enterprises.csv',params=params).text.lstrip('\ufeff'))))
    assert len(rows)==1 and rows[0]['id']=='105'
    assert '1 предприятия' in client.get('/predpriyatiya',params=params).text
    assert '1 предприятия' in client.get('/',params=params).text
    direct=list(csv.DictReader(io.StringIO(client.get('/export-enterprises.csv?participation=direct').text.lstrip('\ufeff'))))
    assert len(direct)==2
    missing=next(r for r in csv.DictReader(io.StringIO(client.get('/export-enterprises.csv').text.lstrip('\ufeff'))) if r['id']=='309')
    assert missing['share_pct']==''

def test_map_verified_dedup_ambiguous_and_filter(conn,monkeypatch):
    seed(conn,monkeypatch)
    data=Q.mapdata()
    assert data['mapped']==1 and data['unmatched']==1
    assert sum(r['v'] or 0 for r in data['items'])==1
    assert all(r['v'] is None for r in data['items'] if r['name']=='Бяла')
    country=Q.mapdata('darzhava')
    assert country['bg']==1 and country['items'][0]['v']==1
    none=Q.mapdata(kind='Държавна',q='does-not-exist')
    assert none['count']==0 and none['bg'] is None
    assert all(r['v'] is None for r in none['items'])
    assert Q.mapdata(subject='does-not-exist')['mapped']==0
    assert Q.mapdata(subject='Концесия за услуги')['filters']['subject']=='Концесия за услуги'

def test_csv_never_executes_source_formula():
    output=Q.csv_response([dict(name='=HYPERLINK("danger")',zero=0,missing=None)],['name','zero','missing'])
    row=next(csv.DictReader(io.StringIO(output.lstrip('\ufeff'))))
    assert row['name'].startswith("'=") and row['zero']=='0' and row['missing']==''

def test_multi_municipality_same_national_concession(conn,monkeypatch):
    seed(conn,monkeypatch)
    id='f0648cf4-8c12-4cf8-9c57-4f739e9b6396'
    row=Q.profiles('ncr')[id]
    row['notices'][0]['municipality_names']=['Симеоновград','Хасково']
    row['notices'][0]['location_places']=[dict(oblast='Хасково',municipality=name) for name in ['Симеоновград','Хасково']]
    store.apply(conn,'ncr','profile:'+id,[row],1)
    municipalities=Q.mapdata()
    assert sum(r['v'] or 0 for r in municipalities['items'])==2
    country=Q.mapdata('darzhava')
    assert country['bg']==1 and country['items'][0]['v']==1
    assert Q.mapdata('oblasti')['items'][0]['v']==1

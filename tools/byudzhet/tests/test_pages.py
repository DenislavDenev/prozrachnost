import copy,json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app import queries as Q
from app.main import app
from ingest.parse import parse,population
F=Path(__file__).parent/'fixtures'
@pytest.fixture
def client(monkeypatch):
 manifest=json.loads((F/'egov/manifest.json').read_text(encoding='utf-8'));ss=[]
 for name,m in manifest.items():
  d=parse((F/'egov'/name).read_bytes(),m['kind'],m['name']);d.update(url='https://data.egov.bg/data/resourceView/'+m['ref'],resource=m['ref']);ss.append(d)
 d=population((F/'grao/grao-latest.txt').read_bytes());d.update(url='https://www.grao.bg/tables.html');ss.append(d)
 # The fixture date remains explicit: this clone only supplies a dated denominator for quarter tests.
 p=copy.deepcopy(d);p['period']='2026-06-15';ss.append(p)
 monkeypatch.setattr(Q,'snapshots',lambda:ss)
 return TestClient(app)
@pytest.mark.parametrize('url',['/','/karta','/karta?l=darzhava','/darzhaven','/kfp','/obshtini','/obshtini/000351580','/how'])
def test_rendered_pages(client,url):
 r=client.get(url);assert r.status_code==200
 assert 'Обратна връзка' in r.text and 'Подкрепи проекта' in r.text
 header=r.text.split('<header',1)[1].split('</header>',1)[0]
 assert header.index('Табло')<header.index('Карта')<header.index('Държавен бюджет')
def test_filters_csv_api_and_missing_quarter(client):
 r=client.get('/api/obshtini.json?oblast=Благоевград&municipality=000351580').json()
 assert r['municipality']=='' and len(r['rows'])==14
 csv=client.get('/export.csv?oblast=Благоевград&municipality=000351580').text
 assert len(csv.splitlines())==15 and 'Велинград' not in csv and 'Банско' in csv
 r=client.get('/api/obshtini.json?y=2020-06-30').json()
 assert all(row['debt'] is None and row['overdue'] is None for row in r['rows'])
def test_map_all_levels_and_ratio_of_sums(client):
 a=client.get('/api/karta.json?m=debt').json();assert len(a['items'])==265
 assert all('y=2026-06-30&denominator=current' in r['href'] for r in a['items'])
 b=client.get('/api/karta.json?m=debt&l=oblasti').json();assert len(b['items'])==28
 assert abs(sum(r['v'] for r in a['items'])-sum(r['v'] for r in b['items']))<0.001
 d=client.get('/api/karta.json?m=debt_per_person&l=darzhava').json();assert len(d['items'])==1 and d['items'][0]['v'] is not None
 assert client.get('/api/karta.json?m=unknown').status_code==400

def test_reserve_missing_month_is_a_gap(client,monkeypatch):
 s=copy.deepcopy(Q.choose('reserve'));later=copy.deepcopy(s)
 s['period']='2026-06-30';later['period']='2026-08-31'
 monkeypatch.setattr(Q,'snapshots',lambda:[s,later])
 r=client.get('/api/chart.json?kind=reserve')
 assert r.status_code==200 and r.json()['series'][0]['points'][1]==['2026-07-31',None]

def test_csv_keeps_original_units_and_all_denominators(client):
 import csv,io
 rows=list(csv.DictReader(io.StringIO(client.get('/export.csv?municipality=000024663').text.lstrip('\ufeff'))))
 assert len(rows)==1 and rows[0]['debt_original']=='215961.97' and rows[0]['debt_original_unit']=='EUR'
 assert all(k+'_per_person' in rows[0] for k in Q.LABELS)

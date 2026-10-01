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

@pytest.mark.parametrize('page,kind',[('kfp','kfp'),('darzhaven','state')])
def test_missing_budget_period_keeps_gap_in_page_and_export(client,page,kind):
 period='2025-11-30'
 r=client.get(f'/{page}?y={period}')
 assert r.status_code==200 and 'За избрания месец няма данни.' in r.text
 assert f'value="{period}"' in r.text and f'kind={kind}&y={period}' in r.text
 export=client.get(f'/export-budget.csv?kind={kind}&y={period}')
 assert export.status_code==200 and len(export.text.splitlines())==1
 assert Q.kfp_summary(None)==dict(revenue=None,spending=None,balance=None)

def test_kfp_selected_budget_changes_hero_chart_and_csv(client):
 from decimal import Decimal
 from urllib.parse import quote
 s=Q.choose('kfp');row=next(r for r in s['rows'] if r['budget_type']=='Държавен бюджет')
 expected=sum(Decimal(row['values'][k]['eur']) for k in ['Данъчни приходи','Неданъчни приходи','Помощи'])
 selected=quote(row['budget_type']);period=s['period']
 filtered=client.get(f'/api/chart.json?kind=kfp&metrics=revenue,spending,balance&budget_type={selected}&y={period}').json()
 assert filtered['series'][0]['points'][-1]==[period,float(expected)]
 overall=client.get('/api/chart.json?kind=kfp&metrics=revenue').json()
 assert overall['series'][0]['points'][-1][1]!=float(expected)
 page=client.get(f'/kfp?budget_type={selected}&y={period}')
 assert page.status_code==200 and 'Държавен бюджет: ' in page.text
 assert f'budget_type={selected}' in page.text
 export=client.get(f'/export-budget.csv?kind=kfp&budget_type={selected}&y={period}')
 assert 'Европейски средства' not in export.text and 'Държавен бюджет' in export.text
 assert 'С прости думи' in page.text and 'нетните трансфери' in page.text

def test_state_hierarchy_and_empty_original_value(client,monkeypatch):
 ss=copy.deepcopy(Q.snapshots());state=next(s for s in ss if s['kind']=='state')
 row=next(r for r in state['rows'] if not Q.is_summary(r['line']))
 row['actual']['eur']=None;row['actual']['original']=None;row['pct']='0'
 monkeypatch.setattr(Q,'snapshots',lambda:ss)
 page=client.get('/darzhaven?y='+state['period'])
 assert page.status_code==200 and 'budget-summary' in page.text and 'Съставно перо' in page.text
 assert 'Няма отчет в източника' in page.text
 assert next(r for r in Q.state_rows(state) if r['line']==row['line'])['pct'] is None
 import csv,io
 exported=list(csv.DictReader(io.StringIO(client.get('/export-budget.csv?kind=state&y='+state['period']).text.lstrip('\ufeff'))))
 assert next(r for r in exported if r['line']==row['line'])['pct']==''

def test_map_explains_selected_metric_and_omits_single_scope(client):
 page=client.get('/karta?m=commitments_per_person&denominator=permanent')
 assert page.status_code==200 and 'id="k-scope"' not in page.text
 assert 'id="k-definition"' in page.text and 'С прости думи' in page.text
 data=client.get('/api/karta.json?m=commitments_per_person&denominator=permanent').json()
 assert 'неизпълнени' in data['definition'] and data['denominator']=='permanent'
 assert dict(data['levels'])['darzhava']=='Държава'
 assert client.get('/how').status_code==200

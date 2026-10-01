import csv
import io
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from app import main, queries as Q
from ingest import parse

F=Path(__file__).parent/'fixtures'

@pytest.fixture
def client(monkeypatch):
    rs=parse.nao((F/'nao.html').read_bytes(),'nao','https://www.bulnao.government.bg/','2026')
    rs+=parse.adfi((F/'adfi.html').read_bytes(),'https://www.adfi.minfin.bg/bg/34')
    rec=parse.nao((F/'recommendations.html').read_bytes(),'recommendations','https://www.bulnao.government.bg/','2026')
    rs+=rec
    monkeypatch.setattr(Q,'snapshots',lambda:[dict(rows=rs,manifest={})])
    monkeypatch.setattr(Q,'allrows',lambda:parse.canonical(rs))
    monkeypatch.setattr(Q,'resources',lambda:[dict(source='nao',label='Сметна палата',url='https://www.bulnao.government.bg/',status='наред',read_at='2026-10-01',row_count=3,page_count=1,source_count=None,scope='Тестов оригинален фрагмент',license='Не е установен свободен лиценз')])
    monkeypatch.setattr(Q,'document_coverage',lambda:dict(eligible=9,processed=0,pending=9,text_available=0,excerpts=0))
    return TestClient(main.app)

@pytest.mark.parametrize('path',['/','/oditi','/preporaki','/inspekcii','/kzk','/sources','/how'])
def test_rendered_pages(client,path):
    r=client.get(path)
    assert r.status_code==200
    assert 'lang="bg"' in r.text and 'Обратна връзка' in r.text
    assert 'Подкрепи проекта' in r.text
    assert 'em dash' not in r.text

def test_filters_same_scope_csv_counts_chart(client):
    r=client.get('/?year=2026&q=спортна')
    assert 'има 1 уникални записа' in r.text
    csvdata=client.get('/export.csv?year=2026&q=спортна').content.decode('utf-8-sig')
    rows=list(csv.DictReader(io.StringIO(csvdata)))
    assert len(rows)==1 and rows[0]['id']=='nao:17791'
    assert rows[0]['eik']=='' and rows[0]['published_on']==''
    assert client.get('/oditi?year=2026&q=спортна').text.count('/oditi/nao%3A17791')==1

def test_sources_unknown_license_and_count(client):
    text=client.get('/sources').text
    assert 'не е установен; броят е инвентаризация' in text
    assert 'Не е установен свободен лиценз' in text

def test_tooltip_accessible_and_mobile_constraints(client):
    text=client.get('/').text
    assert 'class="help-trigger"' in text and 'aria-label=' in text
    script=client.get('/static/state-help.js').text
    assert "e.key==='Escape'" in script and "addEventListener('focus'" in script
    assert "addEventListener('click'" in script and "document.documentElement.clientWidth" in script
    css=client.get('/static/app.css').text
    assert 'calc(100vw - 16px)' in css and 'overflow:auto' in css

def test_canonical_enrichment_and_order():
    a=parse.base('nao:1','nao','title','https://example.test/1')
    b=dict(a,source='nao-mun',categories=['nao-mun'],sector='Общини',recommendation_text='source text')
    first=parse.canonical([a,b]);second=parse.canonical([b,a])
    assert first==second
    assert first[0]['sector']=='Общини' and first[0]['recommendation_text']=='source text'

def test_cpc_identity_type_date_context():
    rows=parse.cpc((F/'cpc.html').read_bytes(),'https://reg.cpc.bg/')[0]
    assert ':decision:' in rows[0]['id'] and rows[0]['id'].endswith(':2026-09-24')

def test_nested_loss_protected():
    from ingest.store import should_hold
    assert should_hold([dict(id='1',categories=['a','b'])],[dict(id='1',categories=['a'])])
    assert should_hold([dict(id='1',details=dict(documents=['1']))],[dict(id='1',details=dict(documents=[]))])

def test_public_privacy_without_losing_source_identity():
    from ingest.privacy import public_row
    r=public_row(dict(id='source:1',title='ЕТ Иван, личен контакт test@example.test',auditee='object',eik='1234567890'))
    assert r['id']=='source:1' and r['eik'] is None
    assert 'test@example.test' not in r['title'] and 'Иван' not in r['title']


def test_quarter_filter_matches_csv(monkeypatch,client):
    row=parse.adfi((F/'adfi.html').read_bytes(),'https://www.adfi.minfin.bg/bg/34')[0]
    a=dict(row,id='test-a',report_period='2024-Q1'); b=dict(row,id='test-b',report_period='2024-Q2')
    monkeypatch.setattr(Q,'allrows',lambda:[a,b])
    page=client.get('/inspekcii?period=2024-Q1')
    assert page.status_code==200 and '1 уникални записа' in page.text
    rows=list(csv.DictReader(io.StringIO(client.get('/export.csv?view=inspekcii&period=2024-Q1').content.decode('utf-8-sig'))))
    assert [r['id'] for r in rows]==['test-a']

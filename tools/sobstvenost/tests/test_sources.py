from pathlib import Path
import pytest

def test_actual_board_in_single_list_item():
    row=parse.profile((P/'appk-10.html').read_bytes())
    assert len(row['board'])==3
    assert all(member['country']=='БЪЛГАРИЯ' for member in row['board'])
    assert row['board'][1]['name']=='Сергей Кирилов Цочев'

def test_actual_freeform_board_preserved_without_person_guess():
    row=parse.profile((P/'appk-100.html').read_bytes())
    assert row['board_format']=='source_entries'
    assert row['board']==[dict(original='УПРАВИТЕЛ: СТОЯН ПЕТРОВ ЦВЕТАНОВ, Държава: БЪЛГАРИЯ',verbatim=True)]
    raw=(P/'appk-100.html').read_bytes().replace(b'<li>',b'<span>').replace(b'</li>',b'</span>')
    with pytest.raises(parse.ShapeError,match='not parseable'):parse.profile(raw)

def test_disabled_terminal_next_is_not_an_extra_page():
    raw=b'<ul class="pagination"><li class="active"><a href="?page=29">29</a></li><li class="disabled"><a href="?page=30">Next</a></li></ul>'
    assert parse.pages(parse.soup(raw))==29
    rows,last=parse.companies((P/'appk-companies-29.html').read_bytes())
    assert len(rows)==8 and last==29

def test_legacy_maritime_notice():
    from pathlib import Path
    row=parse.assigned_notice((Path(__file__).parent/'fixtures/ncr-arapya.html').read_bytes())
    assert row['location_places']==[{'oblast':'Бургас','municipality':'Царево'}]
    assert row['concessionaire_eik']=='115325125' and row['term_months']==240
    assert row['financials'] is None

def test_actual_legacy_mining_notice():
    row=parse.assigned_notice((P/'ncr-mining.html').read_bytes())
    assert row['location_places']==[dict(oblast='Пазарджик',municipality='Пазарджик')]
    assert row['concessionaire_eik']=='112612045' and row['term_months']==180
    opened=parse.assigned_notice((P/'ncr-mining-opening.html').read_bytes())
    assert opened['location_places']==[dict(oblast='Ловеч',municipality='Угърчин')]
    assert opened['concessionaire_eik']=='110550933' and opened['term_months']==420
    assert 'Срок' not in opened['location_original']

def test_resume_rejects_corrupted_original(tmp_path):
    import hashlib
    from ingest.run import archive
    original=tmp_path/'original';original.write_bytes(b'changed')
    class Result:
        def fetchone(self):return (str(original),hashlib.sha256(b'original').hexdigest(),8)
    class Conn:
        def execute(self,*args):return Result()
    class Client:
        raws={}
    with pytest.raises(parse.ShapeError,match='Corrupted original'):
        archive(Conn(),Client(),'appk','https://example.test',resume=True)
from ingest import parse,store
P=Path(__file__).parent/'fixtures'

def test_real_catalogue_three_records():
    # Read and manually compared on 2026-10-01: APPK list first page, IDs 51,105,281.
    rows,pages=parse.companies((P/'appk-companies-1.html').read_bytes())
    assert len(rows)==10 and pages==29
    assert [(r['id'],r['name']) for r in rows[:3]]==[('51','АВИОНАМС'),('105','АВТОМАГИСТРАЛИ'),('281','АГЕНЦИЯ ДИПЛОМАТИЧЕСКИ ИМОТИ  В СТРАНАТА')]
    assert rows[0]['participation']=='owner_reported' and rows[1]['participation']=='direct'
    assert rows[4]['share_pct'] is None

def test_preserve_original_share_discrepancy():
    # Original page9, APPK70, read2026-10-01. Resource SHA in methodology.
    rows,_=parse.companies((P/'appk-companies-9.html').read_bytes())
    row=next(r for r in rows if r['id']=='70')
    assert row['share_pct']=='67.88' and '33,21%' in row['share_text']
    assert row['source_warning'] and 'надхвърлят 100%' in row['source_warning']

def test_real_profile_boards_eik():
    p=parse.profile((P/'appk-105.html').read_bytes())
    assert p['eik']=='831646048' and len(p['board'])==5
    assert p['board'][0]['name']=='Иван Атанасов Кунев'
    assert p['board'][-1]['name']=='Митко Михайлов Михайлов'
    assert 'РЕГИОНАЛНОТО' in p['principal']
    assert len(p['report_links'])==16 and len(p['report_pages'])==2

def test_report_period_original_documents():
    p=parse.report((P/'appk-annual-9848.html').read_bytes(),parse.APPK+'/Public/Public/CompanyDetailsAnnualReport/9848')
    assert p['year']==2025 and p['filed_on']=='2026-04-27' and len(p['documents'])==10
    assert p['financials'] is None
    q=parse.report((P/'appk-quarterly-10603.html').read_bytes(),parse.APPK+'/Public/Public/CompanyDetailsQuarterlyReport/10603')
    assert q['year']==2026 and q['quarter']==2

def test_real_ncr():
    # Original actual POST with blank dates; first three entries manually checked against NCR on 2026-10-01.
    rows,total,pages=parse.concessions((P/'ncr-search-1.html').read_bytes())
    assert len(rows)==20 and total==1441 and pages==73
    assert [r['reg_number'] for r in rows[:3]]==['222-458/29.09.2026','221-456/10.09.2026','222-455/03.09.2026']
    assert all(not r['municipalities'] for r in rows)

def test_missing_percentage_and_eik():
    assert parse.share('\u2014')['share_pct'] is None
    assert parse.share('220882 бр. акции с номинал 5 лв.')['share_pct'] is None
    assert parse.share('0%')['share_pct']=='0'
    assert parse.share('89,11 държавно участие')['share_pct'] is None
    assert parse.valid_eik('831646048') and not parse.valid_eik('831646049')
    with pytest.raises(parse.ShapeError):parse.share('101%')

def test_invalid_and_duplicate():
    for raw in [b'',b'<title>Error</title>',b'<form>search</form>']:
        with pytest.raises(parse.ShapeError):parse.companies(raw)
        with pytest.raises(parse.ShapeError):parse.concessions(raw)
    with pytest.raises(parse.ShapeError):store.reconcile([{'id':'1'},{'id':'1'}],2)
    with pytest.raises(parse.ShapeError):store.reconcile([{'id':'1'}],2)
    raw=(P/'appk-105.html').read_bytes().replace(b'831646048',b'831646049')
    assert parse.profile(raw)['eik'] is None

def test_national_identity_and_periods():
    rows=[dict(id='one',municipalities=['A','B'])]
    assert parse.national_count(rows)==1
    report=parse.report((P/'appk-quarterly-10603.html').read_bytes(),parse.APPK+'/Public/Public/CompanyDetailsQuarterlyReport/10603')
    assert report['financials'] is None
    assert report['id']=='quarterly:10603:2026:2'
    assert store.digest([dict(id='b',x=2),dict(id='a',x=1)])==store.digest([dict(x=1,id='a'),dict(x=2,id='b')])

def test_future_filing_and_unknown_profile_schema():
    raw=(P/'appk-annual-9848.html').read_bytes().replace(b'27.04.2026',b'27.04.9999')
    with pytest.raises(parse.ShapeError):parse.report(raw,parse.APPK+'/Public/Public/CompanyDetailsAnnualReport/9848')
    raw=(P/'appk-105.html').read_bytes().replace(b'831646048',b'')
    assert parse.profile(raw)['eik'] is None

def test_http_retry_and_rate(monkeypatch):
    from ingest import http
    waits=[]
    monkeypatch.setattr(http.time,'sleep',waits.append)
    class Response:
        status_code=429;headers={'Retry-After':'7'};content=b'bytes'
        def raise_for_status(self):pass
    class Session:
        def __init__(self):self.calls=0
        def get(self,*args,**kw):
            self.calls+=1
            response=Response()
            if self.calls==2:response.status_code=200
            return response
    client=http.Client();client.session=Session()
    assert client.get('https://data.egov.bg/resource')==b'bytes'
    assert client.session.calls==2 and 7 in waits and any(w>7.5 for w in waits)

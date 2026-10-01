import datetime as dt
import json
from pathlib import Path
import httpx
import pytest
from ingest import parse, sources, store
from ingest.http import Client, Failed

F = Path(__file__).parent / 'fixtures'

def test_real_nao_rows_and_dates():
    # Manually read against original source response archived 01.10.2026.
    rows = parse.nao((F / 'nao.html').read_bytes(), 'nao', 'https://www.bulnao.government.bg/', '2026')
    assert len(rows) == 3
    assert [r['id'] for r in rows] == ['nao:17790', 'nao:17791', 'nao:17740']
    assert 'Административен съд София-град' in rows[0]['title']
    assert 'Национална спортна база' in rows[1]['title']
    assert 'големи данъкоплатци' in rows[2]['title']
    assert all(r['published_on'] is None for r in rows)
    assert rows[1]['report_period'] == '01.01.2023 до 31.12.2024'
    assert rows[1]['eik'] is None

def test_recommendations_source_text_not_inferred_status():
    rows = parse.nao((F / 'recommendations.html').read_bytes(), 'recommendations', 'https://www.bulnao.government.bg/')
    assert len(rows) == 3
    assert rows[1]['id'] == 'nao:17656'
    assert '5 са изпълнени и 2 са изпълнени частично' in rows[1]['recommendation_text']
    assert rows[2]['recommendation_text'] == 'Двете неизпълнени препоръки от първата проверка са изпълнени'

def test_real_adfi_publication_not_completion():
    rows = parse.adfi((F / 'adfi.html').read_bytes(), 'https://www.adfi.minfin.bg/bg/34')
    assert len(rows) == 3
    assert [r['id'] for r in rows] == ['adfi:3619', 'adfi:3621', 'adfi:3623']
    assert [r['published_on'] for r in rows] == ['2024-04-10', '2024-04-10', '2024-04-11']
    assert all(r['completed_on'] is None and r['report_period'] is None for r in rows)
    assert rows[2]['auditee'] == 'Община Неделино - гр. Неделино'

def test_adfi_history_is_list_not_inspection_count():
    rows = parse.adfi_history((F / 'adfi-history.html').read_bytes(), 'https://www.adfi.minfin.bg/bg/18')
    assert len(rows) == 3
    assert [r['report_period'] for r in rows] == ['2011-Q3', '2011-Q4', '2012-Q1']
    assert all(r['kind'] == 'Тримесечен списък' for r in rows)

def test_cpc_real_postback_and_source_counter():
    rows, bounds, nxt = parse.cpc((F / 'cpc.html').read_bytes(), 'https://reg.cpc.bg/Search.aspx')
    assert len(rows) == 30
    assert bounds == (1, 30, 15446)  # Historical fixture observation, never production constant.
    assert rows[0]['case_no'] == 'КЗК/705/2026'
    assert rows[0]['act_date'] == '2026-09-24'
    assert rows[0]['published_on'] == '2026-09-30'
    assert rows[1]['case_no'] == 'КЗК/682/2026'
    assert rows[2]['case_no'] == 'КЗК/603/2026'
    assert nxt.endswith('lnkButtonNext')
    assert all(r['eik'] is None for r in rows)

def test_empty_initial_cpc_get_never_publishes():
    with pytest.raises(parse.ShapeError, match='брояч'):
        parse.cpc((F / 'cpc-initial.html').read_bytes(), 'https://reg.cpc.bg/')

def test_cpc_original_final_padded_range_does_not_relax_total():
    raw=(F/'cpc-final.html').read_bytes()
    rows,bounds,nxt=parse.cpc(raw,'https://reg.cpc.bg/Search.aspx')
    assert len(rows)==26 and bounds==(15421,15446,15446) and nxt is None
    assert rows[0]['source_pager_discrepancy']==dict(first=15421,printed_end=15450,total=15446,actual_rows=26,next_link_style='color: black')
    # Even this documented pager display error cannot excuse one missing act.
    from bs4 import BeautifulSoup
    s=BeautifulSoup(raw.decode('utf-8'),'html.parser');a=s.find('a',id=lambda v:v and v.endswith('lblDecNumber'));a.decompose()
    with pytest.raises(parse.ShapeError,match='брояч'):
        parse.cpc(str(s).encode(),'https://reg.cpc.bg/Search.aspx')

@pytest.mark.parametrize('raw', [b'', b'<html>403 Forbidden</html>', b'\xef\xbb\xbf<html>CSRF verification failed</html>', b'<html>not a registry</html>'])
def test_error_answers(raw):
    with pytest.raises(parse.ShapeError):
        parse.nao(raw, 'nao', 'https://www.bulnao.government.bg/')

def test_canonical_category_dedup_and_versions():
    r = parse.nao((F / 'nao.html').read_bytes(), 'nao', 'https://www.bulnao.government.bg/')[0]
    second = dict(r, categories=['nao-mun'])
    assert len(parse.canonical([r, second])) == 1
    assert parse.canonical([r, second])[0]['categories'] == ['nao', 'nao-mun']
    with pytest.raises(parse.ShapeError):
        parse.canonical([r, dict(r, title='different')])

def test_identifier_links_only():
    assert parse.verified_unp('00123-2024-0045') == '00123-2024-0045'
    assert parse.verified_unp('Еднакво име на възложител') is None
    assert parse.verified_unp('00123-2024-0045 и 00123-2024-0046') is None

def test_hold_both_directions_and_one_day():
    now = dt.datetime.now(dt.timezone.utc)
    assert store.should_hold([dict(id='1', eik='1')], [dict(id='1', eik=None)])
    assert store.should_hold([dict(id='1'), dict(id='2')], [dict(id='1')])
    assert not store.should_hold([dict(id='1')], [dict(id='1'), dict(id='2')])
    assert not store.confirmed(('a', now), 'a', now + dt.timedelta(hours=23))
    assert store.confirmed(('a', now), 'a', now + dt.timedelta(days=1))
    assert not store.confirmed(('a', now), 'b', now + dt.timedelta(days=2))

def test_retry_after_and_fail_closed():
    delays, calls = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '7'}) if len(calls) == 1 else httpx.Response(200, content=b'ok')
    c = Client(transport=httpx.MockTransport(handler), pause=0, sleep=delays.append)
    assert c.get('https://example.test') == b'ok'
    assert 7 in delays
    c.close()
    c = Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)), pause=0, sleep=lambda _: None)
    with pytest.raises(Failed):
        c.get('https://example.test')
    c.close()

def test_bytes_and_determinism():
    raw = (F / 'nao.html').read_bytes()
    rows = parse.nao(raw, 'nao', 'https://www.bulnao.government.bg/')
    assert store.digest(store.encode(parse.canonical(rows))) == store.digest(store.encode(parse.canonical(list(reversed(rows)))))
    assert raw == raw.decode('utf-8').encode('utf-8')

def test_archive_corruption_never_overwritten(tmp_path):
    raw=b'original source answer'
    sha=store.archive(None,'nao','https://example.test',raw,tmp_path)
    path=tmp_path/'raw/nao'/(sha+'.bin');path.write_bytes(b'corrupt')
    with pytest.raises(parse.ShapeError,match='архив'):
        store.archive(None,'nao','https://example.test',raw,tmp_path)
    assert path.read_bytes()==b'corrupt'

def test_cpc_repeated_page_never_counted_as_next(monkeypatch):
    # Sanitized public fixtures intentionally remove ASP.NET viewstate, which can
    # serialize personal fields. Isolate the page sequence guard from that form.
    monkeypatch.setattr(parse,'postback_form',lambda raw:{})
    class C:
        def get(self,url):return (F/'cpc-initial.html').read_bytes()
        def request(self,*args,**kw):return (F/'cpc.html').read_bytes()
    with pytest.raises(parse.ShapeError,match='страница'):
        sources.cpc(C(),lambda *a:None)

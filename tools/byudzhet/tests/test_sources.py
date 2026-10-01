import copy,datetime as dt,json
from decimal import Decimal
from pathlib import Path
import httpx,pytest
from ingest import parse
from ingest.grao import ShapeError
from ingest.http import Client,Failed
from ingest.store import confirmed,should_hold
from ingest.run import resources
F=Path(__file__).parent/'fixtures'
MANIFEST=json.loads((F/'egov/manifest.json').read_text(encoding='utf-8'))
@pytest.mark.parametrize('name',list(MANIFEST))
def test_real_sources(name):
 m=MANIFEST[name];d=parse.parse((F/'egov'/name).read_bytes(),m['kind'],m['name'])
 assert d['rows'] and d['reconciliation']
 if m['kind'] in ('debt','indicators'):assert len(d['rows'])==len({r['code'] for r in d['rows']})==265
def test_manual_three_and_currency():
 # Original getResourceData, 01 October 2026: rows 2, 3, 4, quarter 2026-06-30.
 d=parse.parse((F/'egov/debt-latest.json').read_bytes(),'debt','2026-06-30')
 assert [(r['name'],r['values']['debt']['original']) for r in d['rows'][:3]]==[('Банско','215961.97'),('Белица','0'),('Благоевград','11963583.01')]
 assert d['rows'][0]['values']['debt']['eur']=='215961.97'
 assert parse.money('1,95583','BGN')['eur']=='1'
 assert parse.money('', 'EUR')['eur'] is None
 assert parse.money('0','EUR')['eur']=='0'
 assert parse.norm('БAHCKO')=='БАНСКО'
def test_reconcile_unknown_duplicate_and_units():
 raw=json.loads((F/'egov/debt-latest.json').read_bytes())
 for change in [lambda x:x['data'].pop(),lambda x:x['data'].append(x['data'][1]),lambda x:x['data'][1].__setitem__(1,'НЕПОЗНАТА')]:
  x=copy.deepcopy(raw);change(x)
  with pytest.raises(ShapeError):parse.parse(json.dumps(x),'debt','2026-06-30')
 raw=json.loads((F/'egov/state-latest.json').read_bytes())
 raw['data'][1][2]='1'
 with pytest.raises(ShapeError):parse.parse(json.dumps(raw),'state','31.08.2026')
 raw=json.loads((F/'egov/state-latest.json').read_bytes());raw['data'][0][2]=raw['data'][0][2].replace('евро','лв')
 with pytest.raises(ShapeError):parse.parse(json.dumps(raw),'state','31.08.2026')
@pytest.mark.parametrize('raw',[b'',b'<html>denied</html>',b'{',b'{"success":true,"data":[]}',b'{"success":true,"data":[[123]]}'])
def test_invalid(raw):
 with pytest.raises(ShapeError):parse.table(raw)
def test_grao():
 d=parse.population((F/'grao/grao-latest.txt').read_bytes());assert len(d['rows'])==265 and d['period']=='2026-09-15'
def test_hold_requires_same_bytes_and_a_day():
 now=dt.datetime.now(dt.timezone.utc)
 assert confirmed(('a',now-dt.timedelta(days=1)),'a',now)
 assert not confirmed(('a',now-dt.timedelta(hours=23)),'a',now)
 assert not confirmed(('a',now-dt.timedelta(days=2)),'b',now)
 a={'rows':[{'code':'a','v':'1'},{'code':'b','v':'2'}]}
 assert should_hold(a,{'rows':a['rows'][:1]})
 assert should_hold(a,{'rows':[{'code':'a','v':None},a['rows'][1]]})
def test_http_retry_after_403_and_html():
 calls=[];wait=[]
 def handler(req):
  calls.append(req)
  return httpx.Response(429,headers={'Retry-After':'7'}) if len(calls)==1 else httpx.Response(200,content=b'ok')
 c=Client(httpx.MockTransport(handler),pause=0,sleep=wait.append)
 assert c.get('https://example.org')==b'ok' and 7 in wait and len(calls)==2
 c.close()
 c=Client(httpx.MockTransport(lambda r:httpx.Response(403)),pause=0,sleep=wait.append)
 with pytest.raises(Failed):c.get('https://example.org')
 c.close()
def test_catalog_pagination():
 class API:
  def api(self,method,body):
   page=body['page_number'];return json.dumps(dict(success=True,total_records=2,resources=[dict(uri=str(page),name='2026-06-30',version='1',updated_at='2026-09-04')])).encode()
 assert len(resources(API(),'a'))==2

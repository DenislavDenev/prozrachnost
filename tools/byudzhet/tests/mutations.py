"""Each selected guard must fail its real regression test when deliberately broken."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parent.parent
cases=[('ingest/parse.py',".translate(str.maketrans('ABCEHKMOPTXY','АВСЕНКМОРТХУ'))",'', 'tests/test_sources.py::test_manual_three_and_currency'),('ingest/parse.py',"RATE=Decimal('1.95583')","RATE=Decimal('2')",'tests/test_sources.py::test_manual_three_and_currency'),('ingest/parse.py',"if a is None or b is None or abs(a-b)>Decimal(tol):","if False:",'tests/test_sources.py::test_reconcile_unknown_duplicate_and_units'),('ingest/store.py','now-held[1]>=dt.timedelta(days=1)','now-held[1]>=dt.timedelta(0)','tests/test_sources.py::test_hold_requires_same_bytes_and_a_day')]
cases.extend([
 ('app/queries.py',"if not budget_type or r['budget_type']==budget_type","if True",'tests/test_pages.py::test_kfp_selected_budget_changes_hero_chart_and_csv'),
 ('app/queries.py',"if row['actual']['eur'] is None:row['pct']=None","if False:row['pct']=None",'tests/test_pages.py::test_state_hierarchy_and_empty_original_value'),
 ('app/main.py',"r['pct'] if r['actual']['eur'] is not None else None","r['pct']",'tests/test_pages.py::test_state_hierarchy_and_empty_original_value')
])
for file,old,new,test in cases:
 p=root/file;original=p.read_bytes();s=original.decode('utf-8')
 if old not in s:raise SystemExit('mutation target missing: '+old)
 try:
  p.write_bytes(s.replace(old,new,1).encode('utf-8'))
  r=subprocess.run([sys.executable,'-B','-m','pytest','-q',test],cwd=root,capture_output=True,text=True,encoding="utf-8")
  if r.returncode==0:raise SystemExit('SURVIVED '+file+' '+old)
  print('caught',old)
 finally:p.write_bytes(original)

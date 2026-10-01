"""Deliberately break real guarding rules and require the targeted regression to fail."""
from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parent.parent
cases=[
 ('ingest/parse.py','share_pct=None,owner=None,share_text=original,participation=\'unknown\'','share_pct=\'0\',owner=None,share_text=original,participation=\'unknown\'','tests/test_sources.py::test_missing_percentage_and_eik'),
 ('ingest/parse.py','return check==digits[8]','return True','tests/test_sources.py::test_missing_percentage_and_eik'),
 ('ingest/store.py','if len(rows)!=expected:','if False:','tests/test_store.py::test_schema_and_reconciliation_keep_live'),
 ('ingest/store.py','if not held[1]: return \'held\'','if False: return \'held\'','tests/test_store.py::test_hold_identical_day_and_idempotent'),
 ('ingest/store.py','if removed or nested_loss or scalar_loss:','if removed:','tests/test_store.py::test_board_loss_versions_and_context'),
 ('app/queries.py','if len(candidates)!=1:unmatched.add((row[\'id\'],name));continue','if not candidates:unmatched.add((row[\'id\'],name));continue','tests/test_pages.py::test_map_verified_dedup_ambiguous_and_filter'),
 ('app/queries.py','if participation and row.get(\'participation\')!=participation:continue','if False:continue','tests/test_pages.py::test_filters_summary_csv_match'),
 ('app/queries.py',"members[code].add(row['id'])","members[code].add(row['id']+':'+name)",'tests/test_pages.py::test_multi_municipality_same_national_concession'),
 ('ingest/store.py','if len(existing)!=len(raw) or hashlib.sha256(existing).hexdigest()!=sha:','if False:','tests/test_store.py::test_raw_corruption_never_overwrites'),
 ('ingest/store.py','if removed or nested_loss or scalar_loss:','if removed or nested_loss:','tests/test_store.py::test_scalar_identity_loss_is_held'),
]
for file,old,new,test in cases:
    p=root/file;original=p.read_bytes();text=original.decode('utf-8')
    if old not in text:raise SystemExit('missing mutation target '+old)
    try:
        p.write_bytes(text.replace(old,new,1).encode())
        result=subprocess.run([sys.executable,'-B','-m','pytest','-q',test],cwd=root,capture_output=True,text=True)
        if result.returncode==0 or 'skipped' in result.stdout or 'FAILED ' not in result.stdout or 'ERROR collecting' in result.stdout:raise SystemExit('mutation not proved '+test+' '+result.stdout)
        print('caught '+test)
    finally:p.write_bytes(original)

"""Each mutation must make a guarding test fail in an isolated copied tree."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MUTATIONS=[
 ('ingest/parse.py',"r['publication_year'] = str(year) if year else None","r['published_on'] = date(r['title']); r['publication_year'] = str(year) if year else None",'tests/test_sources.py','publication from report period'),
 ('ingest/parse.py',"kind='Решение', published_on=date(pub.get_text()), outcome_text=None","kind='Решение', published_on=date(decision[1]), outcome_text=None",'tests/test_sources.py','act date becomes publication'),
 ('ingest/parse.py','if len(rows) != actual_last - first + 1 or (last > total and not padded_final) or first < 1 or len({r[\'id\'] for r in rows}) != len(rows):','if False:','tests/test_sources.py','CPC count protection'),
 ('ingest/parse.py','eik=None, unp=None',"eik='123456789', unp=None",'tests/test_sources.py','unverified EIK assignment'),
 ('ingest/store.py','return any(key not in b or loss(row, b[key]) for key,row in a.items())','return False','tests/test_sources.py','hold bypass'),
 ('ingest/store.py','now - held[1] >= dt.timedelta(days=1)','now - held[1] >= dt.timedelta(seconds=0)','tests/test_sources.py','confirmation before one day'),
 ('ingest/parse.py','return [out[key] for key in sorted(out)]','return rows','tests/test_sources.py','category duplicate counts twice'),
 ('ingest/pdf.py','if number != len(rows) + 1:','if False:','tests/test_pdf.py','inspection sequence gap'),
]
if os.environ.get('KONTROL_TEST_DSN'):
    MUTATIONS += [
     ('ingest/store.py',"if manifest.get('source_count') is not None and manifest['source_count'] != len(rows):",'if False:','tests/test_store.py','source reconciliation bypass'),
     ('ingest/store.py',"if not manifest.get('complete') or manifest.get('occurrence_count') != len(rows):",'if False:','tests/test_store.py','partial publication'),
    ]

def main():
    baseline=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
    if baseline.returncode:
        print('Baseline must pass before mutations');print(baseline.stdout);return 1
    caught=0
    for file,before,after,test,label in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='kontrol-mutation-') as td:
            p=Path(td)/'tool';shutil.copytree(ROOT,p,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
            target=p/file;text=target.read_text(encoding='utf-8')
            if before not in text:raise RuntimeError('Mutation target changed: '+label)
            target.write_text(text.replace(before,after,1),encoding='utf-8')
            result=subprocess.run([sys.executable,'-m','pytest','-q',test],cwd=p,capture_output=True,text=True,encoding='utf-8')
            if result.returncode==0:
                print('NOT CAUGHT: '+label);print(result.stdout);return 1
            if result.returncode!=1:
                print('INFRASTRUCTURE ERROR: '+label);print(result.stdout);print(result.stderr);return 1
            caught+=1;print('caught: '+label)
    print(f'{caught}/{len(MUTATIONS)} mutations caught');return 0
if __name__=='__main__':sys.exit(main())

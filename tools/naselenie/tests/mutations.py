"""Prove three data guards are exercised by tests. Run against a test database only."""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
CASES=[
 ('municipality-total','ingest/parse.py',"!= v:\n                    raise ShapeError('Общинският сбор", "== v:\n                    raise ShapeError('Общинският сбор",'tests/test_sources.py::test_actual_source_totals'),
 ('ambiguous-name','ingest/reference.py',"len(candidates) == 1 and counts[(*key, r['name'])] == 1", "len(candidates) >= 1",'tests/test_sources.py::test_reference_never_guesses_ambiguous_names'),
 ('hold-delay','ingest/store.py',"now - held[1] < dt.timedelta(days=1)", "now - held[1] < dt.timedelta(seconds=0)",'tests/test_store.py::test_hold_requires_same_answer_after_day'),
]


def main():
    dsn=os.environ.get('NASELENIE_TEST_DSN','')
    if 'test' not in dsn:
        raise SystemExit('NASELENIE_TEST_DSN with test database required')
    for label,file,old,new,test in CASES:
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)
            for name in ('ingest','app','db','tests'):
                shutil.copytree(ROOT/name,dest/name,ignore=shutil.ignore_patterns('__pycache__'))
            src=dest/file
            text=src.read_text(encoding='utf-8')
            if text.count(old)!=1:raise SystemExit(f'{label}: mutation anchor changed')
            src.write_text(text.replace(old,new),encoding='utf-8',newline='\n')
            p=subprocess.run([sys.executable,'-m','pytest','-q',test],cwd=dest,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True)
            if p.returncode==0 or 'FAILED' not in p.stdout:
                print(p.stdout[-1500:],p.stderr[-500:])
                raise SystemExit(f'{label}: test did not catch the mutation')
            print(f'{label}: caught')


if __name__=='__main__':main()

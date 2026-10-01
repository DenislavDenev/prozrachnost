import argparse
import json
import sys
from pathlib import Path
from psycopg.types.json import Jsonb
from . import db, store, sources
from .checks import freshness
from .config import SOURCES
from .http import Client

def collect(source, client, save, progress):
    if source.startswith('nao') or source.startswith('recommendations'):
        return sources.nao(client, source, save, progress)
    if source.startswith('adfi'):
        return sources.adfi(client, source, save, progress)
    return sources.cpc(client, save, progress)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--step', required=True, choices=['migrate', 'refresh', 'nao', 'adfi', 'cpc', 'freshness', 'documents', 'summary'])
    p.add_argument('--source', choices=list(SOURCES))
    p.add_argument('--export', type=Path, help='Private offline raw archive plus verified parsed bundle; does not publish')
    p.add_argument('--bundle', type=Path, help='Publish a complete previously archived bundle with its manifest')
    p.add_argument('--limit', type=int, help='Explicit document work budget; pending work is reported as incomplete')
    p.add_argument('--max-seconds',type=int,help='Document runtime checkpoint; pending work remains visible')
    args = p.parse_args()
    if args.max_seconds is not None and (args.max_seconds<=0 or args.step!='documents' ):p.error('--max-seconds requires documents and a positive budget')
    if args.export:
        args.export.mkdir(parents=True, exist_ok=True)
        c = None
    else:
        c = db.connect(autocommit=True)
    client = Client()
    report = dict(stored=0, unchanged=0, held=0, problems=[])
    lock = 8007000
    locked = c is not None and args.step not in ('freshness','summary')
    if locked and not c.execute('SELECT pg_try_advisory_lock(%s)', (lock,)).fetchone()[0]:
        print(json.dumps(dict(problems=['Друг импорт на Контрол вече работи']))); return 1
    try:
        if args.step == 'migrate':
            db.migrate(c)
        elif args.step == 'freshness':
            report['problems'] = freshness(c)
        elif args.step == 'summary':
            report=dict(tool='Одити и контрол', counts={}, last_ok={}, changes=0, held=0, problems=freshness(c))
            for source,payload in c.execute('SELECT source,payload FROM live.snapshot ORDER BY source'):
                report['counts'][SOURCES[source][0]]=len(payload['rows'])
            report['last_ok']={s:str(read) if read else None for s,read in c.execute("SELECT source,read_at FROM src.resource WHERE status='наред'")}
            report['changes']=c.execute("SELECT count(*) FROM ops.change_log WHERE detected_at>=now()-interval '7 days'").fetchone()[0]
            report['held']=c.execute('SELECT count(*) FROM ops.held').fetchone()[0]
            eligible={r['id'] for x in c.execute('SELECT payload FROM live.snapshot') for r in x[0]['rows'] if r.get('kind')!='Приключила финансова инспекция'}
            archived={r[0] for r in c.execute('SELECT ref FROM live.document')}
            report['counts']['Документи: чакащи архивиране']=len(eligible-archived)
        elif args.step == 'documents':
            from .documents import refresh
            report=refresh(c,client,args.source,args.limit,args.max_seconds)
        else:
            selected = [args.source] if args.source else [s for s in SOURCES if args.step == 'refresh' or args.step == 'nao' and (s.startswith('nao') or s.startswith('recommendations')) or args.step == 'adfi' and s.startswith('adfi') or args.step == 'cpc' and s == 'cpc']
            def progress(source, year, page, count):
                print(json.dumps(dict(source=source, year=year, pages=page, rows=count, status='наваксване, още не е публикувано'), ensure_ascii=False), flush=True)
            for source in selected:
                job = c.execute("INSERT INTO ops.job_run(step,status) VALUES (%s,'работи') RETURNING id", (source,)).fetchone()[0] if c else None
                try:
                    if args.bundle:
                        d = json.loads((args.bundle / (source + '.json')).read_text(encoding='utf-8'))
                        rows, manifest = d['rows'], d['manifest']
                        manifest['answer_sha256'] = store.digest(store.encode([(r['url'],r['sha256']) for r in d['raw']]))
                        # Bundle is private and source originals are required, not a public sample.
                        for raw in d['raw']:
                            data = (args.bundle / raw['path']).read_bytes()
                            if store.digest(data) != raw['sha256']:
                                raise ValueError('Повреден оригинален архив')
                            store.archive(c, source, raw['url'], data)
                    else:
                        archived = []
                        def save(src, url, data):
                            sha = store.archive(c, src, url, data, args.export)
                            archived.append(dict(url=url, sha256=sha, path='raw/' + src + '/' + sha + '.bin'))
                            if args.export:
                                with (args.export / 'archive-index.jsonl').open('a', encoding='utf-8') as f:
                                    f.write(json.dumps(dict(source=src, **archived[-1])) + '\n')
                        rows, manifest = collect(source, client, save, progress)
                        manifest['answer_sha256'] = store.digest(store.encode([(r['url'],r['sha256']) for r in archived]))
                        if args.export:
                            (args.export / (source + '.json')).write_bytes(store.encode(dict(rows=rows, manifest=manifest, raw=archived)))
                    if c:
                        state = store.publish(c, source, rows, manifest)
                        report[state] += 1
                        if state == 'held':
                            report['problems'].append(source + ': задържан до второ четене')
                    else:
                        report['stored'] += 1
                    if c:
                        c.execute("UPDATE ops.job_run SET finished_at=now(),status='наред',report=%s WHERE id=%s", (Jsonb(manifest), job))
                except Exception as e:
                    report['problems'].append(source + ': ' + str(e))
                    print(json.dumps(dict(source=source, error=str(e)), ensure_ascii=False), flush=True)
                    if c:
                        store.failure(c, source, e)
                        c.execute("UPDATE ops.job_run SET finished_at=now(),status='неуспешно',report=%s WHERE id=%s", (Jsonb({'problems': [str(e)]}), job))
    finally:
        client.close()
        if c:
            if locked:c.execute('SELECT pg_advisory_unlock(%s)', (lock,))
            c.close()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report['problems'] else 0

if __name__ == '__main__':
    sys.exit(main())

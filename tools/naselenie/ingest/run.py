import argparse
import datetime as dt
import json

from . import db, http, store
from .parse import parse, catalog, ShapeError
from .reference import ekatte, enrich

INDEX = 'https://www.grao.bg/tables.html'
EKATTE = 'https://www.nsi.bg/nrnm/ekatte/zip/download?files_type=json'


def freshness(conn, today=None):
    today = today or dt.date.today()
    problems = []
    for url,status,error,last_read in conn.execute('SELECT url,status,error,last_read FROM ops.source_state'):
        if status != 'ok':
            problems.append(f'{url}: {error or status}')
    last = conn.execute("SELECT max(as_of) FROM live.source WHERE kind='municipalities'").fetchone()[0]
    if not last or (today-last).days > 45:
        problems.append('ГРАО: няма актуален месечен файл')
    index = conn.execute('SELECT last_ok FROM ops.source_state WHERE url=%s',(INDEX,)).fetchone()
    if not index or not index[0] or (dt.datetime.now(dt.timezone.utc)-index[0]).total_seconds()>36*3600:
        problems.append('Каталогът на ГРАО не е проверяван от 36 часа')
    return problems


def refresh(conn, get=http.get, backfill=False):
    if not conn.execute('SELECT pg_try_advisory_lock(8017001)').fetchone()[0]:
        return {'status':'skipped'}
    report = {'stored':0,'unchanged':0,'problems':[]}
    try:
        raw = get(EKATTE); sha=store.save_raw(conn,EKATTE,raw)
        reference=ekatte(raw)
        result=store.apply(conn,EKATTE,sha,{'reference':reference})
        if result == 'held':
            raise ShapeError('ЕКАТТЕ чака второ четене')
        raw=get(INDEX); store.save_raw(conn,INDEX,raw)
        urls=catalog(raw)
        # Stage one starts in 2011. The 2010 source has inconsistent regional totals.
        urls=[u for u in urls if 'tadr' not in u or int(u[-8:-4])>=2011]
        current = [u for u in urls if 'tadr' not in u]
        if not backfill:
            known={r[0] for r in conn.execute('SELECT url FROM live.source')}
            urls=[u for u in urls if u not in known or u in current]
        for url in urls:
            try:
                raw=get(url);sha=store.save_raw(conn,url,raw)
                data=enrich(parse(raw),reference)
                if len(data['municipalities'])<260:
                    raise ShapeError('Непълен национален файл')
                # Old places retain source names; current EKATTE cannot establish historic identity.
                if data['kind']=='places' and (dt.date.today()-dt.date.fromisoformat(data['date'])).days<120 and data['unmatched']/len(data['places'])>0.005:
                    raise ShapeError('Над 0,5% несъпоставени редове с ЕКАТТЕ')
                result=store.apply(conn,url,sha,data)
                report[result]=report.get(result,0)+1
            except Exception as e:
                store.state(conn,url,'invalid',str(e));report['problems'].append(f'{url}: {e}')
        store.state(conn,INDEX,'ok',rows=len(urls))
    except Exception as e:
        store.state(conn,INDEX,'error',str(e));report['problems'].append(str(e))
    finally:
        conn.execute('SELECT pg_advisory_unlock(8017001)')
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--step',choices=['migrate','grao','freshness'],required=True);p.add_argument('--backfill',action='store_true');a=p.parse_args()
    with db.connect(autocommit=True) as conn:
        if a.step=='migrate': result={'migrations':db.migrate(conn)}
        elif a.step=='grao': result=refresh(conn,backfill=a.backfill)
        else: result={'problems':freshness(conn)}
    print(json.dumps(result,ensure_ascii=False,default=str))
    return 1 if result.get('problems') else 0


if __name__=='__main__':
    raise SystemExit(main())

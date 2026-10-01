"""Complete catalogue reads and resumable profile/report/document backfill."""
import argparse,datetime as dt,json,re,sys
from pathlib import Path
from . import db,parse,store
from .config import DATA,ROOT
from .http import Client
from .checks import freshness,summary
from .progress import Queue,Deadline,BudgetReached,pending

def archive(conn,client,source,url,data=None,resume=False):
    if getattr(client,'deadline',None):client.deadline.require()
    if resume and data is None:
        row=conn.execute("SELECT path,sha256,bytes FROM ops.raw_file WHERE source=%s AND url=%s AND fetched_at>now()-interval '1 day' ORDER BY id DESC LIMIT 1",(source,url)).fetchone()
        if row and Path(row[0]).exists():
            raw=Path(row[0]).read_bytes()
            import hashlib
            if len(raw)!=row[2] or hashlib.sha256(raw).hexdigest()!=row[1]:
                raise parse.ShapeError('Corrupted original archive during resume')
            read_proof(conn,source,url,'GET','archive_reuse',raw)
            client.raws[url]=raw
            return raw
    raw=client.get(url,data)
    store.save_raw(conn,source,url,raw)
    read_proof(conn,source,url,'GET' if data is None else 'POST','live',raw)
    client.raws[url]=raw
    return raw

def read_proof(conn,source,url,method,mode,raw):
    import hashlib
    conn.execute('''INSERT INTO ops.raw_read(job_id,source,url,method,mode,sha256,bytes)
        VALUES((SELECT id FROM ops.job_run WHERE status='running' ORDER BY id DESC LIMIT 1),%s,%s,%s,%s,%s,%s)''',(source,url,method,mode,hashlib.sha256(raw).hexdigest(),len(raw)))

def raw_digest(client):
    import hashlib
    return hashlib.sha256(b''.join(url.encode()+b'\0'+client.raws[url] for url in sorted(client.raws))).hexdigest()

def appk_catalogue(conn,client):
    client.raws.clear()
    home=archive(conn,client,'appk',parse.APPK+'/')
    s=parse.soup(home)
    counters={}
    for slide in s.select('.info-blocks-swiper .swiper-slide'):
        label=parse.text(slide).casefold();number=slide.select_one('p')
        if number and parse.text(number).isdigit():
            counters['enterprises' if label.startswith('предприятия') else 'ministries' if label.startswith('министерства') else 'unknown']=int(parse.text(number))
    if set(counters)!= {'enterprises','ministries'}: raise parse.ShapeError('homepage counters schema changed')
    company_count,ministry_count=counters['enterprises'],counters['ministries']
    url=parse.APPK+'/Public/Public/Companies'
    rows,last=parse.companies(archive(conn,client,'appk',url));company_pages=last
    for page in range(2,last+1):
        more,page_count=parse.companies(archive(conn,client,'appk',url+f'?searchType=company&page={page}'))
        if page_count!=last:raise parse.ShapeError('enterprise pagination changed during crawl')
        rows.extend(more)
    store.reconcile(rows,len(rows))
    if sum(r['status']=='Активно' for r in rows)!=company_count:raise parse.ShapeError('active enterprise counter mismatch')
    url=parse.APPK+'/Public/Public/Ministries'
    ministries,last=parse.ministries(archive(conn,client,'appk',url))
    for page in range(2,last+1):
        more,page_count=parse.ministries(archive(conn,client,'appk',url+f'?page={page}'))
        if page_count!=last:raise parse.ShapeError('ministry pagination changed during crawl')
        ministries.extend(more)
    store.reconcile(ministries,len(ministries))
    if sum(r['status']=='Активно' for r in ministries)!=ministry_count:raise parse.ShapeError('active ministry counter mismatch')
    # One transaction publishes both reconciled scopes.
    with conn.transaction():
        store.apply(conn,'appk','ministries',ministries,len(ministries))
        result=store.apply(conn,'appk','catalogue',rows,len(rows),raw_digest(client))
    return dict(status=result,rows=len(rows),active=company_count,ministries=len(ministries),active_ministries=ministry_count,pages=company_pages,ministry_pages=last)

def ncr_catalogue(conn,client):
    client.raws.clear()
    archive(conn,client,'ncr',parse.NCR+'/Concessions')
    url=parse.NCR+'/Concessions/Search'
    fields=dict.fromkeys(['RegNumber','FromDate','ToDate','LotStatusId','ProcedureStatusId','ConcessionName','ConcessionTypeId','ConcessionSubjectId','AreaExtentId','Conceder','ProcedureTypeId'],'')
    rows,total,last=parse.concessions(archive(conn,client,'ncr',url,fields))
    for page in range(2,last+1):
        more,count,page_count=parse.concessions(archive(conn,client,'ncr',url+f'?page={page}'))
        if count!=total or page_count!=last:raise parse.ShapeError('NCR counter/pagination changed mid-pagination')
        rows.extend(more)
    result=store.apply(conn,'ncr','catalogue',rows,total,raw_digest(client))
    return dict(status=result,rows=len(rows),source_count=total,pages=last)

def appk_details(conn,client,limit=None,resume=False,continue_pending=False):
    rows=[r[0] for r in conn.execute("SELECT payload FROM live.record WHERE source='appk' AND scope='catalogue' AND gone_at IS NULL ORDER BY ref")]
    scope='details'
    queue=Queue('appk',scope,rows,continue_pending);rows=queue.rows;done=queue.done
    store.state(conn,'appk',scope,'partial','Профилите и отчетните метаданни още не са прочетени докрай',0)
    for row in rows[done:]:
        client.raws.clear()
        if limit is not None and done>=limit:break
        url=row['source_url'];raw=archive(conn,client,'appk',url,resume=resume)
        data=parse.profile(raw);links=set(data.pop('report_links'));pending=data.pop('report_pages');seen={url}
        while pending:
            page=pending.pop()
            if page in seen:continue
            seen.add(page)
            part=parse.profile(archive(conn,client,'appk',page,resume=resume))
            links.update(part['report_links']);pending.extend(p for p in part['report_pages'] if p not in seen)
        reports=[]
        for link in sorted(links):
            report=parse.report(archive(conn,client,'appk',link,resume=resume),link)
            report['appk_id']=row['id'];reports.append(report)
        data.update(id=row['id'],source_url=url,reports=len(reports))
        with conn.transaction():
            store.apply(conn,'appk','profile:'+row['id'],[data],1,raw_digest(client))
            store.apply(conn,'appk','reports:'+row['id'],reports,len(reports),raw_digest(client))
        queue.advance();done=queue.done
        store.state(conn,'appk',scope,'partial','Профилите и отчетните метаданни още не са прочетени докрай',done)
        print(json.dumps(dict(progress='appk-details',completed=done,total=len(rows))),flush=True)
    store.state(conn,'appk',scope,'ok' if done==len(rows) else 'partial',None if done==len(rows) else 'profile backfill is incomplete',done)
    if done==len(rows):
        conn.execute('UPDATE ops.source_state SET last_success=%s WHERE source=%s AND scope=%s',(queue.data['started_at'],'appk',scope));queue.complete()
    return dict(completed=done,total=len(rows),partial=done!=len(rows))

def ncr_details(conn,client,limit=None,resume=False,continue_pending=False):
    rows=[r[0] for r in conn.execute("SELECT payload FROM live.record WHERE source='ncr' AND scope='catalogue' AND gone_at IS NULL ORDER BY ref")]
    queue=Queue('ncr','details',rows,continue_pending);rows=queue.rows;done=queue.done
    store.state(conn,'ncr','details','partial','Партидите и обявленията още не са прочетени докрай',0)
    for row in rows[done:]:
        client.raws.clear()
        if limit is not None and done>=limit:break
        data=parse.concession_detail(archive(conn,client,'ncr',row['source_url'],resume=resume))
        notices=[]
        for link in data.pop('notice_links'):
            notice=parse.assigned_notice(archive(conn,client,'ncr',link,resume=resume));notice['source_url']=link;notices.append(notice)
        data.update(id=row['id'],source_url=row['source_url'],notices=notices)
        store.apply(conn,'ncr','profile:'+row['id'],[data],1,raw_digest(client))
        queue.advance();done=queue.done
        store.state(conn,'ncr','details','partial','Партидите и обявленията още не са прочетени докрай',done)
        print(json.dumps(dict(progress='ncr-details',completed=done,total=len(rows))),flush=True)
    store.state(conn,'ncr','details','ok' if done==len(rows) else 'partial',None if done==len(rows) else 'detail backfill is incomplete',done)
    if done==len(rows):
        conn.execute('UPDATE ops.source_state SET last_success=%s WHERE source=%s AND scope=%s',(queue.data['started_at'],'ncr','details'));queue.complete()
    return dict(completed=done,total=len(rows),partial=done!=len(rows))

def main():
    p=argparse.ArgumentParser();p.add_argument('--step',required=True,choices=['migrate','appk','ncr','appk-details','ncr-details','refresh','freshness','summary','municipal','pdfs']);p.add_argument('--resume',action='store_true');p.add_argument('--limit',type=int);p.add_argument('--budget-seconds',type=int);p.add_argument('--continue-pending',action='store_true');p.add_argument('--pending-only',action='store_true');args=p.parse_args()
    if args.budget_seconds is not None and args.budget_seconds<=0:p.error('budget must be positive')
    # Calculate after the shared lock: a queued night job cannot spill into morning.
    now=dt.datetime.now(dt.timezone.utc)
    cuts=[now.replace(hour=5,minute=45,second=0,microsecond=0),now.replace(hour=17,minute=45,second=0,microsecond=0)]
    cuts.append(cuts[0]+dt.timedelta(days=1))
    safe_seconds=int((min(cut for cut in cuts if cut>now)-now).total_seconds())
    minute=now.hour*60+now.minute
    if 345<=minute<615 or 1065<=minute<1335:safe_seconds=0
    args.budget_seconds=min(args.budget_seconds,safe_seconds) if args.budget_seconds is not None else safe_seconds
    if args.pending_only:
        if args.step!='pdfs':p.error('pending-only is supported only for attachment archive')
        if not (pending('appk','pdfs') or pending('ncr','pdfs')):
            print(json.dumps(dict(skipped=True,reason='no pending attachment checkpoint')));return 0
    try:
        if args.step=='migrate':
            with db.connect() as conn:db.migrate(conn)
            result={'ok':True,'step':'migrate'}
        elif args.step=='freshness':
            with db.connect() as conn:result={'problems':freshness(conn)}
        elif args.step=='summary':
            with db.connect() as conn:result=summary(conn)
        elif args.step=='municipal':
            raise RuntimeError('municipal dataset inventory and individual reuse permissions are not yet validated; no fabricated import')
        else:
            with db.job(args.step) as conn:
                client=Client(deadline=Deadline(args.budget_seconds))
                try:
                    if args.step=='refresh':
                        continuing=args.continue_pending and (pending('appk','details') or pending('ncr','details'))
                        result={} if continuing else dict(appk=appk_catalogue(conn,client),ncr=ncr_catalogue(conn,client))
                        if not continuing or pending('appk','details'):
                            result['appk_details']=appk_details(conn,client,resume=args.resume,continue_pending=args.continue_pending)
                        result['ncr_details']=ncr_details(conn,client,resume=args.resume,continue_pending=args.continue_pending)
                    elif args.step=='appk':result=appk_catalogue(conn,client)
                    elif args.step=='ncr':result=ncr_catalogue(conn,client)
                    elif args.step=='pdfs':
                        from . import documents
                        result=documents.archive(conn,client,archive,resume=args.resume,continue_pending=args.continue_pending)
                    elif args.step=='appk-details':result=appk_details(conn,client,args.limit,args.resume,continue_pending=args.continue_pending)
                    else:result=ncr_details(conn,client,args.limit,args.resume,continue_pending=args.continue_pending)
                except BudgetReached:
                    result=dict(pending=True,reason='budget',step=args.step)
        print(json.dumps(result,ensure_ascii=False,default=str),flush=True)
        return 1 if result.get('problems') or result.get('partial') else 0
    except Exception as e:
        print(json.dumps(dict(ok=False,problems=[str(e)]),ensure_ascii=False),flush=True);return 1
if __name__=='__main__':sys.exit(main())

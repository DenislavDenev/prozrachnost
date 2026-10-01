import datetime as dt
def freshness(conn):
    problems=[]
    for source in ('appk','ncr'):
        row=conn.execute("SELECT last_success,status,error FROM ops.source_state WHERE source=%s AND scope='catalogue'",(source,)).fetchone()
        if not row or not row[0]:problems.append(source+': няма успешно пълно четене')
        elif row[1]!='ok':problems.append(source+': '+row[1]+': '+(row[2] or ''))
        elif dt.datetime.now(dt.timezone.utc)-row[0]>dt.timedelta(days=9):problems.append(source+': остаряло четене')
        detail=conn.execute("SELECT last_success,status,error FROM ops.source_state WHERE source=%s AND scope='details'",(source,)).fetchone()
        if not detail or not detail[0]:problems.append(source+': няма завършено четене на профилите и документните метаданни')
        elif detail[1]!='ok':problems.append(source+': профили: '+detail[1]+': '+(detail[2] or ''))
        elif dt.datetime.now(dt.timezone.utc)-detail[0]>dt.timedelta(days=9):problems.append(source+': остаряло четене на профилите')
    for source,status,error in conn.execute("SELECT source,status,error FROM ops.source_state WHERE scope='pdfs' AND status!='ok'"):
        problems.append(source+': файлови оригинали: '+status+': '+(error or ''))
    for source,scope,error in conn.execute("SELECT source,scope,error FROM ops.source_state WHERE status IN ('failed','invalid','held')"):
        problems.append(f'{source}/{scope}: {error}')
    for step,error in conn.execute("SELECT DISTINCT ON(step) step,error FROM ops.job_run ORDER BY step,id DESC"):
        if error:problems.append(step+': '+error)
    return sorted(set(problems))

def summary(conn):
    counts={}
    for label,source,scope in [('Предприятия','appk','catalogue'),('Концесии, уникални партиди','ncr','catalogue')]:
        state=conn.execute('SELECT last_success FROM ops.source_state WHERE source=%s AND scope=%s',(source,scope)).fetchone()
        counts[label]=conn.execute('SELECT count(*) FROM live.record WHERE source=%s AND scope=%s AND gone_at IS NULL',(source,scope)).fetchone()[0] if state and state[0] else None
    counts['Прочетени профили']=conn.execute("SELECT count(*) FROM live.record WHERE scope LIKE 'profile:%%' AND gone_at IS NULL").fetchone()[0]
    counts['Отчетни метаданни']=conn.execute("SELECT count(*) FROM live.record WHERE scope LIKE 'reports:%%' AND gone_at IS NULL").fetchone()[0]
    counts['Архивирани PDF версии']=conn.execute("SELECT count(*) FROM (SELECT DISTINCT source,url,sha256 FROM ops.document_file WHERE media_type='application/pdf') files").fetchone()[0]
    counts['Други архивирани файлови версии']=conn.execute("SELECT count(*) FROM (SELECT DISTINCT source,url,sha256 FROM ops.document_file WHERE media_type!='application/pdf') files").fetchone()[0]
    last_ok={source:date.isoformat() if date else None for source,date in conn.execute("SELECT source,max(last_success) FROM ops.source_state WHERE scope='details' GROUP BY source")}
    return dict(tool='Държавна собственост',counts=counts,last_ok=last_ok,changes=conn.execute("SELECT count(*) FROM ops.change_log WHERE detected_at>now()-interval '7 days'").fetchone()[0],held=conn.execute('SELECT count(*) FROM ops.held').fetchone()[0],problems=freshness(conn))

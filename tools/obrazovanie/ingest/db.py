import hashlib
import json
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from .config import DSN, RAW, ROOT
from .parse import ShapeError


def connect(**kwargs):
    return psycopg.connect(DSN, **kwargs)


def migrate(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    conn.commit()
    done = {row[0] for row in conn.execute("SELECT name FROM public.schema_migrations")}
    applied = []
    for file in sorted((ROOT / "db/migrations").glob("*.sql")):
        if file.name in done:
            continue
        with conn.transaction():
            conn.execute(file.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (file.name,))
        applied.append(file.name)
    return applied


def save_raw(conn, uri, raw):
    sha = hashlib.sha256(raw).hexdigest()
    folder = RAW / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    # Catalog identifiers are full URLs; keep them in the database, not in a filename.
    path = folder / f"{sha[:12]}-{hashlib.sha256(uri.encode()).hexdigest()[:12]}.json"
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            pass
    conn.execute("INSERT INTO ops.raw_file(url, sha, path) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                 (uri, sha, str(path)))
    return sha


def state(conn, uri, status, error=None, rows=None):
    conn.execute("""INSERT INTO ops.source_state(url,last_read,last_ok,status,error,rows)
      VALUES (%s,now(),CASE WHEN %s='ok' THEN now() END,%s,%s,%s)
      ON CONFLICT(url) DO UPDATE SET last_read=now(),
      last_ok=CASE WHEN EXCLUDED.status='ok' THEN now() ELSE ops.source_state.last_ok END,
      status=EXCLUDED.status,error=EXCLUDED.error,rows=EXCLUDED.rows""",
      (uri, status, status, error, rows))


def needs_hold(old_codes, new_codes, old_count, new_count):
    return bool(old_codes - new_codes or new_count < old_count)


def publish(conn, year, exam, register, exam_sha, register_sha, results, schools, checks, now=None,
            verification="code"):
    """Atomically replace one academic year after matching and the 24-hour hold check."""
    now = now or datetime.now(timezone.utc)
    records = {r.neispuo: r for r in results}
    new_codes = set(records)
    if len(results) != 2 * len(new_codes) or len(new_codes) != checks["schools"]:
        raise ShapeError("Result count does not reconcile")
    if verification not in {"code", "no-code"} or (verification == "no-code" and schools):
        raise ShapeError("Invalid register verification status")
    with conn.transaction():
        old = conn.execute("SELECT exam_sha,register_sha,school_count,verification FROM live.publication WHERE school_year=%s", (year,)).fetchone()
        if old and old[:2] == (exam_sha, register_sha) and old[3] == verification:
            state(conn, exam.uri, "ok", rows=len(new_codes))
            state(conn, register.uri, "ok", rows=len(schools))
            return "unchanged"
        old_codes = {row[0] for row in conn.execute("SELECT neispuo FROM live.school WHERE school_year=%s", (year,))}
        combined_sha = hashlib.sha256((exam_sha + register_sha).encode()).hexdigest()
        if old and needs_hold(old_codes, new_codes, old[2], len(new_codes)):
            held = conn.execute("SELECT sha,first_at FROM ops.held WHERE school_year=%s", (year,)).fetchone()
            if not held or held[0] != combined_sha:
                conn.execute("""INSERT INTO ops.held(school_year,sha,first_at) VALUES (%s,%s,%s)
                    ON CONFLICT(school_year) DO UPDATE SET sha=EXCLUDED.sha,first_at=EXCLUDED.first_at""",
                    (year, combined_sha, now))
            if not held or held[0] != combined_sha or now - held[1] < timedelta(days=1):
                state(conn, exam.uri, "held", "По-малък отговор, чака второ четене", len(new_codes))
                conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                    VALUES ('mon',%s,'schools',%s,%s,'held')""", (year, str(old[2]), str(len(new_codes))))
                return "held"
            conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                VALUES ('mon',%s,'schools',%s,%s,'confirmed')""", (year, str(old[2]), str(len(new_codes))))

        previous = {}
        for row in conn.execute("""SELECT s.neispuo,s.name,s.oblast,s.municipality,s.town,s.matched,
                e.subject,e.takers,e.score,e.scale FROM live.school s JOIN live.exam_result e
                ON s.school_year=e.school_year AND s.neispuo=e.neispuo WHERE s.school_year=%s""", (year,)):
            previous[(row[0], row[6])] = tuple(str(x) if x is not None else None for x in row[1:6] + row[7:])

        conn.execute("DELETE FROM live.exam_result WHERE school_year=%s", (year,))
        conn.execute("DELETE FROM live.school WHERE school_year=%s", (year,))
        for code, row in records.items():
            school = schools.get(code)
            conn.execute("""INSERT INTO live.school
                (school_year,neispuo,name,oblast,municipality,town,matched)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (year, code, school.name if school else row.school, school.oblast if school else row.oblast,
                 school.municipality if school else row.municipality, school.town if school else row.town, bool(school)))
        for row in results:
            conn.execute("""INSERT INTO live.exam_result(school_year,neispuo,subject,takers,score,scale)
                VALUES (%s,%s,%s,%s,%s,%s)""",
                (year, row.neispuo, row.subject, row.takers, row.score, row.scale))
            school = schools.get(row.neispuo)
            current = (school.name if school else row.school, school.oblast if school else row.oblast,
                       school.municipality if school else row.municipality, school.town if school else row.town,
                       str(bool(school)), str(row.takers) if row.takers is not None else None,
                       str(row.score) if row.score is not None else None, row.scale)
            before = previous.pop((row.neispuo, row.subject), None)
            if before != current:
                conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                    VALUES ('mon',%s,%s,%s,%s,%s)""",
                    (f"{year}/{row.neispuo}/{row.subject}", "result", json.dumps(before, ensure_ascii=False),
                     json.dumps(current, ensure_ascii=False), "new-record" if before is None else "rewritten"))
        for (code, subject), before in previous.items():
            conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                VALUES ('mon',%s,'result',%s,NULL,'removed')""",
                (f"{year}/{code}/{subject}", json.dumps(before, ensure_ascii=False)))
        conn.execute("""INSERT INTO live.publication(school_year,exam_resource,register_resource,exam_sha,register_sha,
            exam_updated_at,register_updated_at,school_count,matched_count,unmatched,verification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(school_year) DO UPDATE SET exam_resource=EXCLUDED.exam_resource,
            register_resource=EXCLUDED.register_resource,exam_sha=EXCLUDED.exam_sha,register_sha=EXCLUDED.register_sha,
            exam_updated_at=EXCLUDED.exam_updated_at,register_updated_at=EXCLUDED.register_updated_at,
            school_count=EXCLUDED.school_count,matched_count=EXCLUDED.matched_count,
            unmatched=EXCLUDED.unmatched,verification=EXCLUDED.verification,published_at=now()""",
            (year, exam.uri, register.uri, exam_sha, register_sha, exam.updated_at, register.updated_at,
             checks["schools"], checks["matched"], Jsonb(checks["unmatched"]), verification))
        conn.execute("DELETE FROM ops.held WHERE school_year=%s", (year,))
        state(conn, exam.uri, "ok", rows=len(new_codes))
        state(conn, register.uri, "ok", rows=len(schools))
    return "stored"


def publish_dzi(conn, resource, exam_sha, table, register=None, register_sha=None, schools=None, now=None):
    """Atomically publish one DZI resource after code checks and the 24-hour shrink hold."""
    now = now or datetime.now(timezone.utc)
    if not table.results or table.rows != table.schools + table.aggregates:
        raise ShapeError("DZI source row count does not reconcile")
    verification = "code" if schools is not None else "no-code"
    if verification == "code":
        unmatched = sorted(table.codes - schools.keys())
        if len(unmatched) / table.schools > 0.02:
            raise ShapeError(f"Too many unmatched DZI school codes: {len(unmatched)} of {table.schools}")
        matched_count = table.schools - len(unmatched)
    else:
        unmatched, matched_count = [], 0
    keys = {(r.row_number, r.subject) for r in table.results}
    if len(keys) != len(table.results):
        raise ShapeError("Duplicate DZI result key")
    with conn.transaction():
        old = conn.execute("""SELECT sha,register_sha,verification,source_rows,result_count
            FROM live.dzi_publication WHERE resource=%s""", (resource.uri,)).fetchone()
        if old and old[:3] == (exam_sha, register_sha, verification):
            state(conn, resource.uri, "ok", rows=table.rows)
            return "unchanged"
        previous = { (row[0], row[1]): tuple(str(x) if x is not None else None for x in row[2:])
                     for row in conn.execute("""SELECT row_number,subject,neispuo,takers,score,matched
                         FROM live.dzi_result WHERE resource=%s""", (resource.uri,)) }
        combined_sha = hashlib.sha256((exam_sha + (register_sha or "")).encode()).hexdigest()
        if old and (table.rows < old[3] or len(table.results) < old[4] or previous.keys() - keys):
            held = conn.execute("SELECT sha,first_at FROM ops.dzi_held WHERE resource=%s", (resource.uri,)).fetchone()
            if not held or held[0] != combined_sha:
                conn.execute("""INSERT INTO ops.dzi_held(resource,sha,first_at) VALUES (%s,%s,%s)
                    ON CONFLICT(resource) DO UPDATE SET sha=EXCLUDED.sha,first_at=EXCLUDED.first_at""",
                    (resource.uri, combined_sha, now))
            if not held or held[0] != combined_sha or now - held[1] < timedelta(days=1):
                state(conn, resource.uri, "held", "По-малък отговор, чака второ четене", table.rows)
                return "held"
        conn.execute("""INSERT INTO live.dzi_publication(resource,school_year,session,kind,sha,updated_at,
            register_resource,register_sha,verification,source_rows,school_count,aggregate_count,result_count,
            matched_count,unmatched,anomalies)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(resource) DO UPDATE SET school_year=EXCLUDED.school_year,
            session=EXCLUDED.session,kind=EXCLUDED.kind,sha=EXCLUDED.sha,updated_at=EXCLUDED.updated_at,
            register_resource=EXCLUDED.register_resource,register_sha=EXCLUDED.register_sha,
            verification=EXCLUDED.verification,source_rows=EXCLUDED.source_rows,
            school_count=EXCLUDED.school_count,aggregate_count=EXCLUDED.aggregate_count,
            result_count=EXCLUDED.result_count,matched_count=EXCLUDED.matched_count,
            unmatched=EXCLUDED.unmatched,anomalies=EXCLUDED.anomalies,published_at=now()""",
            (resource.uri, table.year, table.session, table.kind, exam_sha, resource.updated_at,
             register.uri if register else None, register_sha, verification, table.rows, table.schools,
             table.aggregates, len(table.results), matched_count, Jsonb(unmatched), Jsonb(table.anomalies)))
        conn.execute("DELETE FROM live.dzi_result WHERE resource=%s", (resource.uri,))
        values = [(resource.uri, r.row_number, r.neispuo, r.school, r.oblast, r.municipality,
                   r.town, r.subject, r.takers, r.score, r.is_school,
                   r.neispuo in schools if schools is not None and r.is_school else None)
                  for r in table.results]
        with conn.cursor() as cursor:
            cursor.executemany("""INSERT INTO live.dzi_result
                (resource,row_number,neispuo,school,oblast,municipality,town,subject,takers,score,is_school,matched)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)
        if not old:
            conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                VALUES ('mon-dzi',%s,'results',NULL,%s,'new-resource')""",
                (resource.uri, str(len(values))))
        else:
            for r, row in zip(table.results, values):
                key = (r.row_number, r.subject)
                current = tuple(str(x) if x is not None else None for x in (r.neispuo, r.takers, r.score, row[-1]))
                before = previous.pop(key, None)
                if before != current:
                    conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                        VALUES ('mon-dzi',%s,'result',%s,%s,%s)""",
                        (f"{resource.uri}/{r.row_number}/{r.subject}", json.dumps(before, ensure_ascii=False),
                         json.dumps(current, ensure_ascii=False), "new-record" if before is None else "rewritten"))
            for (number, subject), before in previous.items():
                conn.execute("""INSERT INTO ops.change_log(source,ref,field,old,new,cause)
                    VALUES ('mon-dzi',%s,'result',%s,NULL,'removed')""",
                    (f"{resource.uri}/{number}/{subject}", json.dumps(before, ensure_ascii=False)))
        conn.execute("DELETE FROM ops.dzi_held WHERE resource=%s", (resource.uri,))
        state(conn, resource.uri, "ok", rows=table.rows)
        if register:
            state(conn, register.uri, "ok", rows=len(schools))
    return "stored"

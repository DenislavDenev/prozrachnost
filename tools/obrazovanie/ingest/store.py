"""One resource of MON into the landing zone (obrazovanie_src) and, in the same transaction, into the clean tables
(obrazovanie). The ledger, the hold and the change log are pipeline.load().

Every resource is one load. Its rows are keyed so that a second read can be compared with the current one:
a read that lacks a key of the current version is held until a second read says the same at least a day later.
The key `publication` carries the hashes of the files the rows came from, so that a new file with the same rows
still replaces the old one, as before the move."""
from decimal import Decimal

from . import pipeline
from .parse import ShapeError

NEVER_HOLD = lambda old, rows: 0   # noqa: E731  a register is read only to match codes; it was never held


def _places(src):
    """The SQL that adds the municipality and the settlement to the rows of one load, matched once per place."""
    # MATERIALIZED: each place is matched once, not once for every row that names it
    return f'''WITH p AS (SELECT DISTINCT oblast, municipality, town FROM {src} WHERE load_id = %(load)s),
        m AS MATERIALIZED (SELECT p.*, obrazovanie.municipality_of(p.oblast, p.municipality) AS municipality_code FROM p),
        place AS MATERIALIZED (SELECT m.*, obrazovanie.ekatte_of(m.oblast, m.municipality_code, m.town) AS ekatte FROM m)'''


def known_codes(conn, register_load_id):
    return {c for (c,) in conn.execute('SELECT neispuo FROM obrazovanie_src.register_row WHERE load_id = %s',
                                       (register_load_id,))}


def check_unmatched(codes, known, total, label):
    """Codes are matched to the register of the same year only by the code, never by a similar name. More than 2%
    of the schools missing from the register stops the read before anything is written."""
    missing = len(set(codes) - set(known))
    if missing / total > 0.02:
        raise ShapeError(f'Too many unmatched {label}school codes: {missing} of {total}')


# ---------------------------------------------------------------- the register of schools

def register_rows(schools):
    return {code: dict(name=s.name, oblast=s.oblast, municipality=s.municipality, town=s.town)
            for code, s in schools.items()}


def current_register(conn, load_id):
    return {code: dict(name=n, oblast=o, municipality=m, town=t) for code, n, o, m, t in conn.execute(
        'SELECT neispuo, name, oblast, municipality, town FROM obrazovanie_src.register_row WHERE load_id = %s', (load_id,))}


def load_register(conn, r, resource, schools, raw_file_id=None, now=None):
    """The register of one year. Returns the load that is current after the read: its rows match the codes."""
    def write(conn, load_id, rows):
        conn.execute('INSERT INTO obrazovanie_src.register (load_id, resource, school_year, updated_at) VALUES (%s,%s,%s,%s)',
                     (load_id, resource.uri, resource.year, resource.updated_at))
        with conn.cursor().copy('''COPY obrazovanie_src.register_row (load_id, neispuo, name, oblast, municipality, town)
                                   FROM STDIN''') as copy:
            for code, s in rows.items():
                copy.write_row((load_id, code, s['name'], s['oblast'], s['municipality'], s['town']))
    loaded = pipeline.load(conn, r, 'register', resource.uri, register_rows(schools), write, raw_file_id=raw_file_id,
                           current=current_register, now=now, hold_when=NEVER_HOLD)
    return loaded.load_id


# ---------------------------------------------------------------- NVO VII

def nvo7_rows(results, exam_sha, register_sha, verification):
    out = {'publication': dict(exam_sha=exam_sha, register_sha=register_sha, verification=verification)}
    for x in results:
        out[f'{x.neispuo}/{x.subject}'] = dict(school=x.school, oblast=x.oblast, municipality=x.municipality, town=x.town,
                                              takers=x.takers, score=x.score, scale=x.scale)
    return out


def current_nvo7(conn, load_id):
    meta = conn.execute('SELECT exam_sha, register_sha, verification FROM obrazovanie_src.nvo7 WHERE load_id = %s',
                        (load_id,)).fetchone()
    out = {'publication': dict(exam_sha=meta[0], register_sha=meta[1], verification=meta[2])}
    for code, subject, school, oblast, mun, town, takers, score, scale in conn.execute(
            '''SELECT neispuo, subject, school, oblast, municipality, town, takers, score, scale
               FROM obrazovanie_src.nvo7_row WHERE load_id = %s''', (load_id,)):
        out[f'{code}/{subject}'] = dict(school=school, oblast=oblast, municipality=mun, town=town, takers=takers,
                                        score=score, scale=scale)
    return out


def build_nvo7(conn, load_id):
    """The clean year of NVO VII from one load: a school takes its name and place from the register when its code is
    there, otherwise from the exam file."""
    (year,) = conn.execute('SELECT school_year FROM obrazovanie_src.nvo7 WHERE load_id = %s', (load_id,)).fetchone()
    for table in ('exam_result', 'school', 'nvo7_publication'):
        conn.execute(f'DELETE FROM obrazovanie.{table} WHERE school_year = %s', (year,))
    q = dict(load=load_id)
    conn.execute('''INSERT INTO obrazovanie.nvo7_publication (school_year, exam_resource, register_resource, exam_updated_on,
          register_updated_on, school_count, matched_count, unmatched, verification, load_id)
        SELECT n.school_year, n.exam_resource, n.register_resource, left(n.exam_updated_at, 10)::date,
               left(n.register_updated_at, 10)::date, c.schools, c.schools - cardinality(c.unmatched), c.unmatched,
               n.verification, n.load_id
        FROM obrazovanie_src.nvo7 n, LATERAL (
          SELECT count(*)::int AS schools,
                 COALESCE(array_agg(x.neispuo ORDER BY x.neispuo COLLATE "C") FILTER (WHERE g.neispuo IS NULL), '{}') AS unmatched
          FROM (SELECT DISTINCT neispuo FROM obrazovanie_src.nvo7_row WHERE load_id = n.load_id) x
          LEFT JOIN obrazovanie_src.register_row g ON g.load_id = n.register_load_id AND g.neispuo = x.neispuo) c
        WHERE n.load_id = %(load)s''', q)
    conn.execute(_places('''(SELECT n.load_id, COALESCE(g.oblast, r.oblast) AS oblast,
                                   COALESCE(g.municipality, r.municipality) AS municipality, COALESCE(g.town, r.town) AS town
                             FROM obrazovanie_src.nvo7_row r JOIN obrazovanie_src.nvo7 n USING (load_id)
                             LEFT JOIN obrazovanie_src.register_row g ON g.load_id = n.register_load_id AND g.neispuo = r.neispuo) s''') + '''
        INSERT INTO obrazovanie.school (school_year, neispuo, name, oblast, municipality, town, matched, municipality_code, ekatte)
        SELECT DISTINCT ON (r.neispuo) n.school_year, r.neispuo, COALESCE(g.name, r.school), COALESCE(g.oblast, r.oblast),
               COALESCE(g.municipality, r.municipality), COALESCE(g.town, r.town), g.neispuo IS NOT NULL,
               place.municipality_code, place.ekatte
        FROM obrazovanie_src.nvo7_row r JOIN obrazovanie_src.nvo7 n USING (load_id)
        LEFT JOIN obrazovanie_src.register_row g ON g.load_id = n.register_load_id AND g.neispuo = r.neispuo
        JOIN place ON (place.oblast, place.municipality, place.town)
                    = (COALESCE(g.oblast, r.oblast), COALESCE(g.municipality, r.municipality), COALESCE(g.town, r.town))
        WHERE r.load_id = %(load)s ORDER BY r.neispuo, r.subject DESC''', q)
    conn.execute('''INSERT INTO obrazovanie.exam_result (school_year, neispuo, subject, takers, score, scale)
        SELECT n.school_year, r.neispuo, r.subject, r.takers, r.score, r.scale
        FROM obrazovanie_src.nvo7_row r JOIN obrazovanie_src.nvo7 n USING (load_id) WHERE r.load_id = %(load)s''', q)


def load_nvo7(conn, r, year, exam, register, exam_sha, register_sha, results, register_load_id, register_rows=None,
              raw_file_id=None, now=None):
    """One year of NVO VII. Each school has exactly two subjects; more than 2% of codes missing from the register of
    the year stops the read before anything is written."""
    codes = {x.neispuo for x in results}
    if not results or len(results) != 2 * len(codes):
        raise ShapeError('Result count does not reconcile')
    verification = 'code' if register_load_id else 'no-code'
    if verification == 'code':
        check_unmatched(codes, known_codes(conn, register_load_id), len(codes), '')

    def write(conn, load_id, rows):
        conn.execute('''INSERT INTO obrazovanie_src.nvo7 (load_id, school_year, exam_resource, exam_sha, exam_updated_at,
              register_resource, register_sha, register_updated_at, register_load_id, register_rows, verification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (load_id, year, exam.uri, exam_sha, exam.updated_at, register.uri, register_sha, register.updated_at,
                      register_load_id, register_rows, verification))
        with conn.cursor().copy('''COPY obrazovanie_src.nvo7_row (load_id, neispuo, subject, school, oblast, municipality,
                                   town, takers, score, scale) FROM STDIN''') as copy:
            for x in results:
                copy.write_row((load_id, x.neispuo, x.subject, x.school, x.oblast, x.municipality, x.town, x.takers,
                                x.score, x.scale))
        build_nvo7(conn, load_id)
    return pipeline.load(conn, r, 'nvo7', year, nvo7_rows(results, exam_sha, register_sha, verification), write,
                         raw_file_id=raw_file_id, current=current_nvo7, now=now)


# ---------------------------------------------------------------- DZI

def dzi_rows(table, sha, register_sha, verification):
    out = {'publication': dict(sha=sha, register_sha=register_sha, verification=verification,
                               school_codes=sorted(table.codes))}
    for x in table.results:
        out[f'{x.row_number}/{x.subject}'] = dict(neispuo=x.neispuo, school=x.school, oblast=x.oblast,
                                                 municipality=x.municipality, town=x.town, takers=x.takers,
                                                 score=x.score, is_school=x.is_school)
    return out


def current_dzi(conn, load_id):
    meta = conn.execute('''SELECT sha, register_sha, CASE WHEN register_load_id IS NULL THEN 'no-code' ELSE 'code' END,
        school_codes FROM obrazovanie_src.dzi WHERE load_id = %s''', (load_id,)).fetchone()
    out = {'publication': dict(sha=meta[0], register_sha=meta[1], verification=meta[2], school_codes=meta[3])}
    for number, subject, code, school, oblast, mun, town, takers, score, is_school in conn.execute(
            '''SELECT row_number, subject, neispuo, school, oblast, municipality, town, takers, score, is_school
               FROM obrazovanie_src.dzi_row WHERE load_id = %s''', (load_id,)):
        out[f'{number}/{subject}'] = dict(neispuo=code, school=school, oblast=oblast, municipality=mun, town=town,
                                         takers=takers, score=score, is_school=is_school)
    return out


def build_dzi(conn, load_id):
    (resource,) = conn.execute('SELECT resource FROM obrazovanie_src.dzi WHERE load_id = %s', (load_id,)).fetchone()
    conn.execute('DELETE FROM obrazovanie.dzi_result WHERE resource = %s', (resource,))
    conn.execute('DELETE FROM obrazovanie.dzi_publication WHERE resource = %s', (resource,))
    q = dict(load=load_id)
    conn.execute('''INSERT INTO obrazovanie.dzi_publication (resource, school_year, session, kind, updated_on,
          register_resource, verification, source_rows, school_count, aggregate_count, result_count, matched_count,
          unmatched, missing_grade, suppressed_takers, source_placeholder, load_id)
        SELECT d.resource, d.school_year, d.session, d.kind, left(d.updated_at, 10)::date, d.register_resource,
               CASE WHEN d.register_load_id IS NULL THEN 'no-code' ELSE 'code' END, d.source_rows, d.school_count,
               d.aggregate_count, (SELECT count(*) FROM obrazovanie_src.dzi_row WHERE load_id = d.load_id),
               CASE WHEN d.register_load_id IS NULL THEN 0 ELSE d.school_count - cardinality(u.codes) END,
               CASE WHEN d.register_load_id IS NULL THEN '{}' ELSE u.codes END,
               d.missing_grade, d.suppressed_takers, d.source_placeholder, d.load_id
        FROM obrazovanie_src.dzi d, LATERAL (
          SELECT COALESCE(array_agg(x.neispuo ORDER BY x.neispuo COLLATE "C"), '{}') AS codes
          FROM unnest(d.school_codes) AS x (neispuo)   -- every school of the file, also one without any result
          WHERE NOT EXISTS (SELECT FROM obrazovanie_src.register_row g WHERE g.load_id = d.register_load_id AND g.neispuo = x.neispuo)) u
        WHERE d.load_id = %(load)s''', q)
    conn.execute(_places('obrazovanie_src.dzi_row') + '''
        INSERT INTO obrazovanie.dzi_result (resource, row_number, subject, neispuo, school, oblast, municipality, town,
          takers, score, is_school, matched, municipality_code, ekatte)
        SELECT d.resource, r.row_number, r.subject, r.neispuo, r.school, r.oblast, r.municipality, r.town, r.takers, r.score,
               r.is_school,
               CASE WHEN d.register_load_id IS NULL OR NOT r.is_school THEN NULL
                    ELSE EXISTS (SELECT FROM obrazovanie_src.register_row g WHERE g.load_id = d.register_load_id AND g.neispuo = r.neispuo) END,
               place.municipality_code, place.ekatte
        FROM obrazovanie_src.dzi_row r JOIN obrazovanie_src.dzi d USING (load_id)
        JOIN place ON (place.oblast, place.municipality, place.town) = (r.oblast, r.municipality, r.town)
        WHERE r.load_id = %(load)s''', q)


def load_dzi(conn, r, resource, sha, table, register=None, register_sha=None, register_load_id=None, raw_file_id=None,
             now=None):
    """One DZI resource, after the checks of the code and the count of its rows."""
    if not table.results or table.rows != table.schools + table.aggregates:
        raise ShapeError('DZI source row count does not reconcile')
    if register_load_id:
        check_unmatched(table.codes, known_codes(conn, register_load_id), table.schools, 'DZI ')
    if len({(x.row_number, x.subject) for x in table.results}) != len(table.results):
        raise ShapeError('Duplicate DZI result key')
    verification = 'code' if register_load_id else 'no-code'

    def write(conn, load_id, rows):
        a = table.anomalies
        conn.execute('''INSERT INTO obrazovanie_src.dzi (load_id, resource, school_year, session, kind, sha, updated_at,
              register_resource, register_sha, register_load_id, source_rows, school_count, aggregate_count,
              missing_grade, suppressed_takers, source_placeholder, school_codes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (load_id, resource.uri, table.year, table.session, table.kind, sha, resource.updated_at,
                      register.uri if register else None, register_sha, register_load_id, table.rows, table.schools,
                      table.aggregates, a['missing_grade'], a['suppressed_takers'], a['source_placeholder'],
                      sorted(table.codes)))
        with conn.cursor().copy('''COPY obrazovanie_src.dzi_row (load_id, row_number, subject, neispuo, school, oblast,
                                   municipality, town, takers, score, is_school) FROM STDIN''') as copy:
            for x in table.results:
                copy.write_row((load_id, x.row_number, x.subject, x.neispuo, x.school, x.oblast, x.municipality, x.town,
                                x.takers, x.score, x.is_school))
        build_dzi(conn, load_id)
    return pipeline.load(conn, r, 'dzi', resource.uri, dzi_rows(table, sha, register_sha, verification), write,
                         raw_file_id=raw_file_id, current=current_dzi, now=now)


# ---------------------------------------------------------------- NVO IV and X

def nvo_rows(table, sha, register_sha, verification):
    out = {'publication': dict(sha=sha, register_sha=register_sha, verification=verification)}
    for x in table.results:
        out[f'{x.neispuo}/{x.subject}'] = dict(school=x.school, oblast=x.oblast, municipality=x.municipality,
                                              town=x.town, takers=x.takers, score=x.score)
    return out


def current_nvo(conn, load_id):
    meta = conn.execute('''SELECT sha, register_sha, CASE WHEN register_load_id IS NULL THEN 'no-code' ELSE 'code' END
        FROM obrazovanie_src.nvo WHERE load_id = %s''', (load_id,)).fetchone()
    out = {'publication': dict(sha=meta[0], register_sha=meta[1], verification=meta[2])}
    for code, subject, school, oblast, mun, town, takers, score in conn.execute(
            '''SELECT neispuo, subject, school, oblast, municipality, town, takers, score
               FROM obrazovanie_src.nvo_row WHERE load_id = %s''', (load_id,)):
        out[f'{code}/{subject}'] = dict(school=school, oblast=oblast, municipality=mun, town=town, takers=takers,
                                        score=score)
    return out


def build_nvo(conn, load_id):
    (resource, exam, year) = conn.execute('SELECT resource, exam, school_year FROM obrazovanie_src.nvo WHERE load_id = %s',
                                          (load_id,)).fetchone()
    for res in [resource] + [x for (x,) in conn.execute(
            'SELECT resource FROM obrazovanie.nvo_publication WHERE exam = %s AND school_year = %s AND resource <> %s',
            (exam, year, resource))]:
        conn.execute('DELETE FROM obrazovanie.nvo_result WHERE resource = %s', (res,))
        conn.execute('DELETE FROM obrazovanie.nvo_publication WHERE resource = %s', (res,))
    q = dict(load=load_id)
    conn.execute('''INSERT INTO obrazovanie.nvo_publication (resource, exam, school_year, updated_on, register_resource,
          verification, school_count, subject_count, result_count, matched_count, unmatched, load_id)
        SELECT d.resource, d.exam, d.school_year, left(d.updated_at, 10)::date, d.register_resource,
               CASE WHEN d.register_load_id IS NULL THEN 'no-code' ELSE 'code' END, d.school_count, d.subject_count,
               (SELECT count(*) FROM obrazovanie_src.nvo_row WHERE load_id = d.load_id),
               CASE WHEN d.register_load_id IS NULL THEN 0 ELSE d.school_count - cardinality(u.codes) END,
               CASE WHEN d.register_load_id IS NULL THEN '{}' ELSE u.codes END, d.load_id
        FROM obrazovanie_src.nvo d, LATERAL (
          SELECT COALESCE(array_agg(x.neispuo ORDER BY x.neispuo COLLATE "C"), '{}') AS codes
          FROM (SELECT DISTINCT neispuo FROM obrazovanie_src.nvo_row WHERE load_id = d.load_id) x
          WHERE NOT EXISTS (SELECT FROM obrazovanie_src.register_row g WHERE g.load_id = d.register_load_id AND g.neispuo = x.neispuo)) u
        WHERE d.load_id = %(load)s''', q)
    conn.execute(_places('obrazovanie_src.nvo_row') + '''
        INSERT INTO obrazovanie.nvo_result (resource, neispuo, subject, school, oblast, municipality, town, takers, score,
          matched, municipality_code, ekatte)
        SELECT d.resource, r.neispuo, r.subject, r.school, r.oblast, r.municipality, r.town, r.takers, r.score,
               CASE WHEN d.register_load_id IS NULL THEN NULL
                    ELSE EXISTS (SELECT FROM obrazovanie_src.register_row g WHERE g.load_id = d.register_load_id AND g.neispuo = r.neispuo) END,
               place.municipality_code, place.ekatte
        FROM obrazovanie_src.nvo_row r JOIN obrazovanie_src.nvo d USING (load_id)
        JOIN place ON (place.oblast, place.municipality, place.town) = (r.oblast, r.municipality, r.town)
        WHERE r.load_id = %(load)s''', q)


def load_nvo(conn, r, resource, sha, table, register=None, register_sha=None, register_load_id=None, raw_file_id=None,
             now=None):
    """One year of NVO IV or X, with the same gates as before the move."""
    if (table.year != resource.year or not table.results or table.rows != len(table.codes)
            or len(table.results) != table.rows * len(table.subjects)):
        raise ShapeError('NVO result count does not reconcile')
    if register_load_id:
        check_unmatched(table.codes, known_codes(conn, register_load_id), table.rows, 'NVO ')
    if len({(x.neispuo, x.subject) for x in table.results}) != len(table.results):
        raise ShapeError('Duplicate NVO result key')
    verification = 'code' if register_load_id else 'no-code'

    def write(conn, load_id, rows):
        conn.execute('''INSERT INTO obrazovanie_src.nvo (load_id, resource, exam, school_year, sha, updated_at,
              register_resource, register_sha, register_load_id, school_count, subject_count)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (load_id, resource.uri, table.exam, table.year, sha, resource.updated_at,
                      register.uri if register else None, register_sha, register_load_id, table.rows, len(table.subjects)))
        with conn.cursor().copy('''COPY obrazovanie_src.nvo_row (load_id, neispuo, subject, school, oblast, municipality,
                                   town, takers, score) FROM STDIN''') as copy:
            for x in table.results:
                copy.write_row((load_id, x.neispuo, x.subject, x.school, x.oblast, x.municipality, x.town, x.takers, x.score))
        build_nvo(conn, load_id)
    return pipeline.load(conn, r, 'nvo', resource.uri, nvo_rows(table, sha, register_sha, verification), write,
                         raw_file_id=raw_file_id, current=current_nvo, now=now)


# ---------------------------------------------------------------- the lists of protected and central schools

def status_rows(rows, sha):
    out = {'publication': dict(sha=sha)}
    for x in rows:
        out[str(x.number)] = dict(neispuo=x.code, name=x.name, town=x.town, scope=x.scope, is_school=x.is_school)
    return out


def current_status(conn, load_id):
    (sha,) = conn.execute('SELECT sha FROM obrazovanie_src.status WHERE load_id = %s', (load_id,)).fetchone()
    out = {'publication': dict(sha=sha)}
    for number, code, name, town, scope, is_school in conn.execute(
            'SELECT row_number, neispuo, name, town, scope, is_school FROM obrazovanie_src.status_row WHERE load_id = %s',
            (load_id,)):
        out[str(number)] = dict(neispuo=code, name=name, town=town, scope=scope, is_school=is_school)
    return out


def build_status(conn, load_id):
    (resource, kind, year) = conn.execute('SELECT resource, kind, school_year FROM obrazovanie_src.status WHERE load_id = %s',
                                          (load_id,)).fetchone()
    for res in [resource] + [x for (x,) in conn.execute(
            'SELECT resource FROM obrazovanie.status_publication WHERE kind = %s AND school_year = %s AND resource <> %s',
            (kind, year, resource))]:
        conn.execute('DELETE FROM obrazovanie.status_row WHERE resource = %s', (res,))
        conn.execute('DELETE FROM obrazovanie.status_publication WHERE resource = %s', (res,))
    q = dict(load=load_id)
    conn.execute('''INSERT INTO obrazovanie.status_publication (resource, kind, school_year, updated_on, row_count,
          school_rows, load_id)
        SELECT s.resource, s.kind, s.school_year, left(s.updated_at, 10)::date,
               (SELECT count(*) FROM obrazovanie_src.status_row WHERE load_id = s.load_id),
               (SELECT count(*) FILTER (WHERE is_school) FROM obrazovanie_src.status_row WHERE load_id = s.load_id), s.load_id
        FROM obrazovanie_src.status s WHERE s.load_id = %(load)s''', q)
    conn.execute('''INSERT INTO obrazovanie.status_row (resource, row_number, neispuo, name, town, scope, is_school)
        SELECT s.resource, r.row_number, r.neispuo, r.name, r.town, r.scope, r.is_school
        FROM obrazovanie_src.status_row r JOIN obrazovanie_src.status s USING (load_id) WHERE r.load_id = %(load)s''', q)


def load_status(conn, r, kind, resource, sha, rows, raw_file_id=None, now=None):
    if not rows or len({x.number for x in rows}) != len(rows):
        raise ShapeError('Invalid status list rows')

    def write(conn, load_id, _rows):
        conn.execute('''INSERT INTO obrazovanie_src.status (load_id, resource, kind, school_year, sha, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)''', (load_id, resource.uri, kind, resource.year, sha, resource.updated_at))
        with conn.cursor().copy('''COPY obrazovanie_src.status_row (load_id, row_number, neispuo, name, town, scope, is_school)
                                   FROM STDIN''') as copy:
            for x in rows:
                copy.write_row((load_id, x.number, x.code, x.name, x.town, x.scope, x.is_school))
        build_status(conn, load_id)
    return pipeline.load(conn, r, 'status', resource.uri, status_rows(rows, sha), write, raw_file_id=raw_file_id,
                         current=current_status, now=now)


# ---------------------------------------------------------------- the national counts by school type

def context_rows(rows, pupils_sha, classes_sha):
    out = {'publication': dict(pupils_sha=pupils_sha, classes_sha=classes_sha)}
    for x in rows:
        out[x.kind] = dict(institutions=x.institutions, grades={str(g): n for g, n in x.grades.items()},
                           preschool=x.preschool, reported_pupils=x.reported_pupils, reported_groups=x.reported_groups)
    return out


def current_context(conn, load_id):
    meta = conn.execute('SELECT pupils_sha, classes_sha FROM obrazovanie_src.context WHERE load_id = %s', (load_id,)).fetchone()
    out = {'publication': dict(pupils_sha=meta[0], classes_sha=meta[1])}
    grades = {}
    for kind, grade, students in conn.execute(
            'SELECT kind, grade, students FROM obrazovanie_src.context_grade WHERE load_id = %s ORDER BY kind, grade', (load_id,)):
        grades.setdefault(kind, {})[str(grade)] = students
    for kind, inst, pre, pupils, groups in conn.execute(
            '''SELECT kind, institutions, preschool, reported_pupils, reported_groups FROM obrazovanie_src.context_row
               WHERE load_id = %s''', (load_id,)):
        out[kind] = dict(institutions=inst, grades=grades.get(kind, {}), preschool=pre, reported_pupils=pupils,
                         reported_groups=groups)
    return out


def _totals(rows):
    kinds = {k: v for k, v in rows.items() if k != 'publication'}
    pupils = sum(sum(n for g, n in v['grades'].items() if 1 <= int(g) <= 12) for v in kinds.values())
    groups = sum((Decimal(v['reported_groups']) for v in kinds.values()), Decimal(0))
    return kinds, pupils, groups


def context_falls(old, rows):
    """hold_when for the national counts: held when the pupils of grades 1-12, the groups or the schools of a type
    fall, as before the move. Returns how many counts fell."""
    old_kinds, old_pupils, old_groups = _totals(old)
    new_kinds, new_pupils, new_groups = _totals(rows)
    fell = (new_pupils < old_pupils) + (new_groups < old_groups)
    fell += sum(v['institutions'] < old_kinds[k]['institutions'] for k, v in new_kinds.items() if k in old_kinds)
    return fell + len(old_kinds.keys() - new_kinds.keys())


def build_context(conn, load_id):
    (year,) = conn.execute('SELECT school_year FROM obrazovanie_src.context WHERE load_id = %s', (load_id,)).fetchone()
    for table in ('context_grade', 'context_kind', 'context_publication'):
        conn.execute(f'DELETE FROM obrazovanie.{table} WHERE school_year = %s', (year,))
    q = dict(load=load_id)
    conn.execute('''INSERT INTO obrazovanie.context_publication (school_year, pupils_resource, classes_resource,
          pupils_updated_on, classes_updated_on, kind_count, grade_1_12_students, reported_groups, load_id)
        SELECT c.school_year, c.pupils_resource, c.classes_resource, left(c.pupils_updated, 10)::date,
               left(c.classes_updated, 10)::date,
               (SELECT count(*) FROM obrazovanie_src.context_row WHERE load_id = c.load_id),
               (SELECT COALESCE(sum(students), 0) FROM obrazovanie_src.context_grade WHERE load_id = c.load_id AND grade BETWEEN 1 AND 12),
               (SELECT COALESCE(sum(reported_groups), 0) FROM obrazovanie_src.context_row WHERE load_id = c.load_id), c.load_id
        FROM obrazovanie_src.context c WHERE c.load_id = %(load)s''', q)
    conn.execute('''INSERT INTO obrazovanie.context_kind (school_year, kind, institutions, preschool, reported_pupils, reported_groups)
        SELECT c.school_year, r.kind, r.institutions, r.preschool, r.reported_pupils, r.reported_groups
        FROM obrazovanie_src.context_row r JOIN obrazovanie_src.context c USING (load_id) WHERE r.load_id = %(load)s''', q)
    conn.execute('''INSERT INTO obrazovanie.context_grade (school_year, kind, grade, students)
        SELECT c.school_year, g.kind, g.grade, g.students
        FROM obrazovanie_src.context_grade g JOIN obrazovanie_src.context c USING (load_id) WHERE g.load_id = %(load)s''', q)


def load_context(conn, r, pupils, classes, pupils_sha, classes_sha, rows, raw_file_id=None, now=None):
    if pupils.year != classes.year or len(rows) != 15 or len({x.kind for x in rows}) != len(rows):
        raise ShapeError('National context resources do not reconcile')

    def write(conn, load_id, _rows):
        conn.execute('''INSERT INTO obrazovanie_src.context (load_id, school_year, pupils_resource, classes_resource,
              pupils_sha, classes_sha, pupils_updated, classes_updated) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                     (load_id, pupils.year, pupils.uri, classes.uri, pupils_sha, classes_sha, pupils.updated_at,
                      classes.updated_at))
        for x in rows:
            conn.execute('''INSERT INTO obrazovanie_src.context_row (load_id, kind, institutions, preschool, reported_pupils,
                  reported_groups) VALUES (%s,%s,%s,%s,%s,%s)''',
                         (load_id, x.kind, x.institutions, x.preschool, x.reported_pupils, x.reported_groups))
            for grade, students in x.grades.items():
                conn.execute('INSERT INTO obrazovanie_src.context_grade (load_id, kind, grade, students) VALUES (%s,%s,%s,%s)',
                             (load_id, x.kind, int(grade), students))
        build_context(conn, load_id)
    return pipeline.load(conn, r, 'context', pupils.year, context_rows(rows, pupils_sha, classes_sha), write,
                         raw_file_id=raw_file_id, current=current_context, now=now, hold_when=context_falls)


BUILD = {'nvo7': build_nvo7, 'dzi': build_dzi, 'nvo': build_nvo, 'status': build_status, 'context': build_context}

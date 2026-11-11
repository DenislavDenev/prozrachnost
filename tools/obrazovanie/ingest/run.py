"""The steps of Образование: python -m ingest.run --step <step>

    migrate    the tables and the registers (the foundation of danni must be there)
    mon        the latest year of NVO VII with the register of the same year
    history    the earlier years of NVO VII that have a register with codes (2021/2022 on)
    legacy     the years of NVO VII before that, whose register has no codes
    dzi        every resource of the DZI (the matura)
    nvo        every year of NVO IV and X
    status     the lists of protected and central schools
    context    the latest national counts by school type
    build      the clean tables again from the landing zone (after the places of core changed)
    replay     what the old database published, from its raw files and through the same parsers (the move)
    parity     the old database of its own against the shared one, number by number
    check      every quality rule
    freshness  the list of problems, empty when all is well

Ingest and serving are apart. The steps above fill obrazovanie_src and obrazovanie; the pages read pub_obrazovanie,
which changes only by a publication at the end of a step whose rules all hold (db/publish/*.sql).
"""
import argparse
from decimal import Decimal

import psycopg

from . import http, pipeline, store
from .config import DSN, OLD_DSN, RAW, ROOT, TOOL
from .context import DATASETS as CONTEXT_DATASETS, latest_pair, parse_context
from .dzi import DZI_DATASET, dzi_catalog, parse_dzi
from .history import parse_nvo7_year
from .nvo import DATASETS as NVO_DATASETS, LAYOUTS as NVO_LAYOUTS, nvo_catalog, parse_nvo
from .parse import ShapeError, parse_nvo7
from .registry import parse_code_free_register, parse_schools_year
from .sources import NVO7_DATASET, SCHOOLS_DATASET, Resource, _payload, catalog, current_pair, parse_schools
from .status import DATASETS as STATUS_DATASETS, parse_status, status_catalog


LEGACY = {
    "2017/2018": (("025f06e2-5676-47d0-a08a-bc8bf56916a7", "2018г."),
                  ("3a4cc873-0431-44ab-88bc-a227a78efd97", "2017/2018")),
    "2018/2019": (("8198a9cf-01da-4f8f-b2f4-5095e33a9972", "2018/2019"),
                  ("1fabe87d-d987-47ee-b2e5-86e8dfe029f7", "06.02.2019")),
    "2019/2020": (("6a7a1a4f-bded-4f4f-9050-b29a653e9e2d", "2019/2020"),
                  ("8067c6bc-115c-427d-a888-897130285d61", "06.02.2020")),
    "2020/2021": (("ccb4db31-e5d0-4717-9476-4fb71dd98b1a", "2020/2021"),
                  ("1f0b73bb-cbd1-40a9-808c-034d2a60e156", "25.02.2021")),
}
LATEST_REGISTER = "2025/2026"   # the register of this year is the JSON layout; earlier ones are tables


def legacy_resource(raw, dataset, uri, year, title_part):
    value = _payload(raw, "legacy catalog")
    rows = value.get("resources")
    if (set(value) != {"success", "resources", "total_records"} or not isinstance(rows, list)
            or type(value["total_records"]) is not int or len(rows) != value["total_records"]
            or any(not isinstance(row, dict) for row in rows)):
        raise ValueError("Incomplete legacy catalog")
    found = [r for r in rows if r.get("uri") == uri and r.get("dataset_uri") == dataset]
    if (len(found) != 1 or not isinstance(found[0].get("name"), str)
            or not isinstance(found[0].get("updated_at"), str) or title_part not in found[0]["name"]):
        raise ValueError(f"Legacy source missing or changed for {year}")
    row = found[0]
    return Resource(uri, year, row["name"], row["updated_at"])


def historical_years(exams, registers):
    """Return only published code-bearing register years before the latest exam."""
    latest = max(exams)
    return [year for year in sorted(exams.keys() & registers.keys())
            if "2021/2022" <= year < latest]


def catalog_url(dataset):
    return "https://data.egov.bg/api/listResources?dataset=" + dataset


def read_catalog(conn, r, post, dataset):
    raw = post("listResources", {"criteria": {"dataset_uri": dataset}, "records_per_page": 100, "page_number": 1})
    pipeline.save_raw(conn, r, "catalog", catalog_url(dataset), raw, RAW, name="listResources.json",
                      media_type="application/json")
    return raw


def read_resource(conn, r, post, source, uri):
    raw = post("getResourceData", {"resource_uri": uri})
    file = pipeline.save_raw(conn, r, source, uri, raw, RAW, name=uri + ".json", media_type="application/json")
    return raw, file


def parse_register(raw, year):
    return parse_schools(raw) if year == LATEST_REGISTER else parse_schools_year(raw)


def read_register(conn, r, post, register, parser=None, cache=None):
    """The register of a year, read once per step. Returns (sha, load_id of its rows)."""
    if cache is not None and register.uri in cache:
        return cache[register.uri]
    raw, file = read_resource(conn, r, post, "register", register.uri)
    try:
        schools = (parser or (lambda b: parse_register(b, register.year)))(raw)
    except ShapeError as e:
        pipeline.reject(conn, r, "register", register.uri, e, file.raw_file_id)
        raise
    out = (file.sha256, store.load_register(conn, r, register, schools, file.raw_file_id))
    if cache is not None:
        cache[register.uri] = out
    return out


def fail(conn, r, problems, source, ref, exc, file=None, label=None):
    """One resource that could not be read: recorded as a rejected load, reported, and the others go on."""
    pipeline.reject(conn, r, source, ref, exc, file.raw_file_id if file else None)
    problems.append(f"Образование: {label or ref}: {exc}")


def held(problems, loaded, label):
    if loaded.status == "held":
        problems.append(f"Образование: {label} чака второ четене")


def refresh(conn, r, post=http.post):
    """The latest year of NVO VII."""
    problems = []
    nvo_raw = read_catalog(conn, r, post, NVO7_DATASET)
    school_raw = read_catalog(conn, r, post, SCHOOLS_DATASET)
    exam, register = current_pair(nvo_raw, school_raw)
    exam_raw, file = read_resource(conn, r, post, "nvo7", exam.uri)
    try:
        register_sha, register_load = read_register(conn, r, post, register, parser=parse_schools)
        results = parse_nvo7(exam_raw)
        loaded = store.load_nvo7(conn, r, exam.year, exam, register, file.sha256, register_sha, results, register_load,
                                 raw_file_id=file.raw_file_id)
    except (ShapeError, pipeline.ShapeError) as e:
        fail(conn, r, problems, "nvo7", exam.year, e, file)
        return problems
    r.stats["year"] = exam.year
    held(problems, loaded, exam.year)
    return problems


def refresh_history(conn, r, post=http.post):
    """Code-reconciled earlier years of NVO VII, each on its own."""
    problems = []
    exams = {item.year: item for item in catalog(read_catalog(conn, r, post, NVO7_DATASET), NVO7_DATASET)}
    registers = {}
    for item in catalog(read_catalog(conn, r, post, SCHOOLS_DATASET), SCHOOLS_DATASET):
        if item.year not in registers or item.updated_at > registers[item.year].updated_at:
            registers[item.year] = item
    # Earlier official register tables have no NEISPUO code. Their display policy is separate.
    for year in historical_years(exams, registers):
        exam, register, file = exams[year], registers[year], None
        try:
            exam_raw, file = read_resource(conn, r, post, "nvo7", exam.uri)
            register_sha, register_load = read_register(conn, r, post, register, parser=parse_schools_year)
            results = parse_nvo7_year(exam_raw, year)
            held(problems, store.load_nvo7(conn, r, year, exam, register, file.sha256, register_sha, results,
                                           register_load, raw_file_id=file.raw_file_id), year)
        except Exception as e:   # one bad year does not stop the others
            fail(conn, r, problems, "nvo7", year, e, file)
    return problems


def refresh_legacy(conn, r, post=http.post):
    """The years of NVO VII before 2021/2022, with an explicit no-code verification."""
    problems = []
    catalogs = {d: read_catalog(conn, r, post, d) for d in (NVO7_DATASET, SCHOOLS_DATASET)}
    for year, ((exam_uri, exam_title), (register_uri, register_title)) in LEGACY.items():
        file = None
        try:
            exam = legacy_resource(catalogs[NVO7_DATASET], NVO7_DATASET, exam_uri, year, exam_title)
            register = legacy_resource(catalogs[SCHOOLS_DATASET], SCHOOLS_DATASET, register_uri, year, register_title)
            exam_raw, file = read_resource(conn, r, post, "nvo7", exam.uri)
            register_raw, register_file = read_resource(conn, r, post, "register", register.uri)
            results = parse_nvo7_year(exam_raw, year)
            register_rows = parse_code_free_register(register_raw, year)
            held(problems, store.load_nvo7(conn, r, year, exam, register, file.sha256, register_file.sha256, results,
                                           None, register_rows=register_rows, raw_file_id=file.raw_file_id), year)
        except Exception as e:
            fail(conn, r, problems, "nvo7", year, e, file)
    return problems


def registers_by_year(raw):
    registers = {}
    for item in catalog(raw, SCHOOLS_DATASET):
        if item.year not in registers or item.updated_at > registers[item.year].updated_at:
            registers[item.year] = item
    return registers


def refresh_dzi(conn, r, post=http.post):
    """Every observed DZI resource, keeping the session and the optional exams apart."""
    problems = []
    resources = dzi_catalog(read_catalog(conn, r, post, DZI_DATASET))
    registers = registers_by_year(read_catalog(conn, r, post, SCHOOLS_DATASET))
    cache = {}
    for resource in resources:
        file = None
        try:
            raw, file = read_resource(conn, r, post, "dzi", resource.uri)
            table = parse_dzi(raw, resource.uri)
            if table.year != resource.year:
                raise ShapeError("DZI catalog and table years differ")
            register, register_sha, register_load = None, None, None
            if table.year >= "2021/2022":
                register = registers.get(table.year)
                if not register:
                    raise ShapeError(f"No same-year school register for {table.year}")
                register_sha, register_load = read_register(conn, r, post, register, cache=cache)
            held(problems, store.load_dzi(conn, r, resource, file.sha256, table, register, register_sha, register_load,
                                          raw_file_id=file.raw_file_id), f"ДЗИ {resource.name}")
        except Exception as e:
            fail(conn, r, problems, "dzi", resource.uri, e, file, f"ДЗИ {resource.uri[:8]}")
    r.stats["resources"] = len(resources)
    return problems


def refresh_nvo(conn, r, post=http.post):
    """Every observed year of NVO IV and X; the years with a register of codes are matched by code."""
    problems = []
    registers = registers_by_year(read_catalog(conn, r, post, SCHOOLS_DATASET))
    cache = {}
    for exam, dataset in NVO_DATASETS.items():
        for resource in nvo_catalog(read_catalog(conn, r, post, dataset), exam):
            file = None
            try:
                answer, file = read_resource(conn, r, post, "nvo", resource.uri)
                table = parse_nvo(answer, resource.uri)
                if table.rows < NVO_LAYOUTS[resource.uri]["observed_rows"] * 0.8:
                    raise ShapeError("NVO response has fewer than 80% of observed schools")
                register, register_sha, register_load = None, None, None
                if table.year >= "2021/2022":
                    register = registers.get(table.year)
                    if not register:
                        raise ShapeError(f"No same-year school register for {table.year}")
                    register_sha, register_load = read_register(conn, r, post, register, cache=cache)
                held(problems, store.load_nvo(conn, r, resource, file.sha256, table, register, register_sha,
                                              register_load, raw_file_id=file.raw_file_id), f"{exam} {table.year}")
            except Exception as e:
                fail(conn, r, problems, "nvo", resource.uri, e, file, f"{exam} {resource.uri[:8]}")
    return problems


def refresh_status(conn, r, post=http.post):
    """Every reviewed list of protected and central schools with NEISPUO codes."""
    problems = []
    for kind, dataset in STATUS_DATASETS.items():
        try:
            resources = status_catalog(read_catalog(conn, r, post, dataset), kind)
        except Exception as e:
            problems.append(f"Образование: каталог {kind}: {e}")
            continue
        for resource in resources:
            file = None
            try:
                answer, file = read_resource(conn, r, post, "status", resource.uri)
                rows = parse_status(answer, resource.uri)
                held(problems, store.load_status(conn, r, kind, resource, file.sha256, rows, raw_file_id=file.raw_file_id),
                     f"списък {resource.uri[:8]}")
            except Exception as e:
                fail(conn, r, problems, "status", resource.uri, e, file, f"списък {resource.uri[:8]}")
    return problems


def refresh_context(conn, r, post=http.post):
    """Only the latest common national snapshot by school type, without school codes."""
    problems = []
    catalogs = {kind: read_catalog(conn, r, post, dataset) for kind, dataset in CONTEXT_DATASETS.items()}
    pupils, classes = latest_pair(catalogs["pupils"], catalogs["classes"])
    file = None
    try:
        pupils_raw, file = read_resource(conn, r, post, "context", pupils.uri)
        classes_raw, classes_file = read_resource(conn, r, post, "context", classes.uri)
        rows = parse_context(pupils_raw, classes_raw, pupils.year)
        held(problems, store.load_context(conn, r, pupils, classes, file.sha256, classes_file.sha256, rows,
                                          raw_file_id=file.raw_file_id), f"националните данни за {pupils.year}")
    except (ShapeError, pipeline.ShapeError) as e:
        fail(conn, r, problems, "context", pupils.year, e, file, "национални данни")
    r.stats["year"] = pupils.year
    return problems


def rebuild(conn, r):
    """The clean tables again from the current loads of the landing zone, with the places of core as they are now."""
    for source, build in store.BUILD.items():
        for (load_id,) in conn.execute('''SELECT load_id FROM ops.load WHERE tool = %s AND source = %s AND is_current
                ORDER BY loaded_at, load_id''', (TOOL, source)).fetchall():
            with conn.transaction():
                build(conn, load_id)
            r.count(source)


# ---------------------------------------------------------------- the move from the database of its own

def _old_file(old, old_ops, uri, sha):
    row = old.execute(f"SELECT path, fetched_at FROM {old_ops}.raw_file WHERE url = %s AND sha = %s ORDER BY id LIMIT 1",
                      (uri, sha)).fetchone()
    if not row:
        raise ShapeError(f"{uri}: the raw file {sha[:12]} is not in the old register")
    return row


def replay(conn, r, old_dsn=OLD_DSN, old_ops="ops"):
    """Load what the old database has published, from its raw files and through the same parsers, with the time
    the old database published it. Nothing is copied from its tables."""
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        nvo7 = old.execute("""SELECT school_year, exam_resource, exam_sha, exam_updated_at, register_resource,
            register_sha, register_updated_at, verification, published_at FROM live.publication ORDER BY school_year""").fetchall()
        dzi = old.execute("""SELECT resource, school_year, sha, updated_at, register_resource, register_sha, published_at
            FROM live.dzi_publication ORDER BY published_at, resource""").fetchall()
        nvo = old.execute("""SELECT resource, exam, school_year, sha, updated_at, register_resource, register_sha, published_at
            FROM live.nvo_publication ORDER BY published_at, resource""").fetchall()
        status = old.execute("""SELECT resource, kind, school_year, sha, updated_at, published_at
            FROM live.status_publication ORDER BY published_at, resource""").fetchall()
        context = old.execute("""SELECT school_year, pupils_resource, classes_resource, pupils_sha, classes_sha,
            pupils_updated, classes_updated, published_at FROM live.context_publication ORDER BY school_year""").fetchall()
        files = {}

        def raw(source, uri, sha):
            if (uri, sha) not in files:
                path, fetched_at = _old_file(old, old_ops, uri, sha)
                files[(uri, sha)] = pipeline.register_raw(conn, r, source, uri, path, media_type="application/json",
                                                          fetched_at=fetched_at, expect_sha=sha)
            return files[(uri, sha)]

        registers = {}

        def register(uri, sha, year, updated_at, at, parser=None):
            if (uri, sha) not in registers:
                file, data = raw("register", uri, sha)
                schools = (parser or (lambda b: parse_register(b, year)))(data)
                registers[(uri, sha)] = store.load_register(conn, r, Resource(uri, year, "", updated_at), schools,
                                                            file.raw_file_id, now=at)
            return registers[(uri, sha)]

        problems = []
        latest = max((row[0] for row in nvo7 if row[7] == "code"), default=None)
        for year, exam_uri, exam_sha, exam_updated, reg_uri, reg_sha, reg_updated, verification, at in nvo7:
            try:
                file, data = raw("nvo7", exam_uri, exam_sha)
                exam, reg = Resource(exam_uri, year, "", exam_updated), Resource(reg_uri, year, "", reg_updated)
                if verification == "no-code":
                    reg_file, reg_data = raw("register", reg_uri, reg_sha)
                    store.load_nvo7(conn, r, year, exam, reg, exam_sha, reg_sha, parse_nvo7_year(data, year), None,
                                    register_rows=parse_code_free_register(reg_data, year), raw_file_id=file.raw_file_id,
                                    now=at)
                elif year == latest:
                    store.load_nvo7(conn, r, year, exam, reg, exam_sha, reg_sha, parse_nvo7(data),
                                    register(reg_uri, reg_sha, year, reg_updated, at, parse_schools),
                                    raw_file_id=file.raw_file_id, now=at)
                else:
                    store.load_nvo7(conn, r, year, exam, reg, exam_sha, reg_sha, parse_nvo7_year(data, year),
                                    register(reg_uri, reg_sha, year, reg_updated, at, parse_schools_year),
                                    raw_file_id=file.raw_file_id, now=at)
            except (ShapeError, pipeline.ShapeError) as e:
                fail(conn, r, problems, "nvo7", year, e)
        for uri, year, sha, updated, reg_uri, reg_sha, at in dzi:
            try:
                file, data = raw("dzi", uri, sha)
                table = parse_dzi(data, uri)
                reg_load = None
                if reg_uri:
                    reg_updated = old.execute("SELECT register_updated_at FROM live.publication WHERE register_resource = %s",
                                              (reg_uri,)).fetchone()
                    reg_load = register(reg_uri, reg_sha, year, reg_updated[0] if reg_updated else updated, at)
                store.load_dzi(conn, r, Resource(uri, year, "", updated), sha, table,
                               Resource(reg_uri, year, "", "") if reg_uri else None, reg_sha, reg_load,
                               raw_file_id=file.raw_file_id, now=at)
            except (ShapeError, pipeline.ShapeError) as e:
                fail(conn, r, problems, "dzi", uri, e)
        for uri, exam, year, sha, updated, reg_uri, reg_sha, at in nvo:
            try:
                file, data = raw("nvo", uri, sha)
                table = parse_nvo(data, uri)
                reg_load = None
                if reg_uri:
                    reg_updated = old.execute("SELECT register_updated_at FROM live.publication WHERE register_resource = %s",
                                              (reg_uri,)).fetchone()
                    reg_load = register(reg_uri, reg_sha, year, reg_updated[0] if reg_updated else updated, at)
                store.load_nvo(conn, r, Resource(uri, year, "", updated), sha, table,
                               Resource(reg_uri, year, "", "") if reg_uri else None, reg_sha, reg_load,
                               raw_file_id=file.raw_file_id, now=at)
            except (ShapeError, pipeline.ShapeError) as e:
                fail(conn, r, problems, "nvo", uri, e)
        for uri, kind, year, sha, updated, at in status:
            try:
                file, data = raw("status", uri, sha)
                store.load_status(conn, r, kind, Resource(uri, year, "", updated), sha, parse_status(data, uri),
                                  raw_file_id=file.raw_file_id, now=at)
            except (ShapeError, pipeline.ShapeError) as e:
                fail(conn, r, problems, "status", uri, e)
        for year, p_uri, c_uri, p_sha, c_sha, p_updated, c_updated, at in context:
            try:
                p_file, p_data = raw("context", p_uri, p_sha)
                _, c_data = raw("context", c_uri, c_sha)
                store.load_context(conn, r, Resource(p_uri, year, "", p_updated), Resource(c_uri, year, "", c_updated),
                                   p_sha, c_sha, parse_context(p_data, c_data, year), raw_file_id=p_file.raw_file_id, now=at)
            except (ShapeError, pipeline.ShapeError) as e:
                fail(conn, r, problems, "context", year, e)
    r.stats["files"] = len(files)
    return problems


# What is compared: the published tables of the old database and of the shared one, row by row. The columns are the
# ones both have; a date of the catalogue is the first ten characters of the old text.
PARITY = {
    "nvo7_publication": ("SELECT school_year, exam_resource, register_resource, left(exam_updated_at, 10), left(register_updated_at, 10), school_count, matched_count, (SELECT array_agg(x ORDER BY x COLLATE \"C\") FROM jsonb_array_elements_text(unmatched) x), verification FROM live.publication",
                         "SELECT school_year, exam_resource, register_resource, exam_updated_on::text, register_updated_on::text, school_count, matched_count, unmatched, verification FROM pub_obrazovanie.nvo7_publication"),
    "school": ("SELECT school_year, neispuo, name, oblast, municipality, town, matched FROM live.school",
               "SELECT school_year, neispuo, name, oblast, municipality, town, matched FROM pub_obrazovanie.school"),
    "exam_result": ("SELECT school_year, neispuo, subject, takers, score, scale FROM live.exam_result",
                    "SELECT school_year, neispuo, subject, takers, score, scale FROM pub_obrazovanie.exam_result"),
    "dzi_publication": ("SELECT resource, school_year, session, kind, left(updated_at, 10), register_resource, verification, source_rows, school_count, aggregate_count, result_count, matched_count, (SELECT array_agg(x ORDER BY x COLLATE \"C\") FROM jsonb_array_elements_text(unmatched) x), (anomalies->>'missing_grade')::int, (anomalies->>'suppressed_takers')::int, (anomalies->>'source_placeholder')::int FROM live.dzi_publication",
                        "SELECT resource, school_year, session, kind, updated_on::text, register_resource, verification, source_rows, school_count, aggregate_count, result_count, matched_count, NULLIF(unmatched, '{}'), missing_grade, suppressed_takers, source_placeholder FROM pub_obrazovanie.dzi_publication"),
    "dzi_result": ("SELECT resource, row_number, subject, neispuo, school, oblast, municipality, town, takers, score, is_school, matched FROM live.dzi_result",
                   "SELECT resource, row_number, subject, neispuo, school, oblast, municipality, town, takers, score, is_school, matched FROM pub_obrazovanie.dzi_result"),
    "nvo_publication": ("SELECT resource, exam, school_year, left(updated_at, 10), register_resource, verification, school_count, subject_count, result_count, matched_count, (SELECT array_agg(x ORDER BY x COLLATE \"C\") FROM jsonb_array_elements_text(unmatched) x) FROM live.nvo_publication",
                        "SELECT resource, exam, school_year, updated_on::text, register_resource, verification, school_count, subject_count, result_count, matched_count, NULLIF(unmatched, '{}') FROM pub_obrazovanie.nvo_publication"),
    "nvo_result": ("SELECT resource, neispuo, subject, school, oblast, municipality, town, takers, score, matched FROM live.nvo_result",
                   "SELECT resource, neispuo, subject, school, oblast, municipality, town, takers, score, matched FROM pub_obrazovanie.nvo_result"),
    "status_publication": ("SELECT resource, kind, school_year, left(updated_at, 10), row_count, school_rows FROM live.status_publication",
                           "SELECT resource, kind, school_year, updated_on::text, row_count, school_rows FROM pub_obrazovanie.status_publication"),
    "status_row": ("SELECT resource, row_number, neispuo, name, town, scope, is_school FROM live.status_row",
                   "SELECT resource, row_number, neispuo, name, town, scope, is_school FROM pub_obrazovanie.status_row"),
    "context_publication": ("SELECT school_year, pupils_resource, classes_resource, left(pupils_updated, 10), left(classes_updated, 10), kind_count, grade_1_12_students, reported_groups FROM live.context_publication",
                            "SELECT school_year, pupils_resource, classes_resource, pupils_updated_on::text, classes_updated_on::text, kind_count, grade_1_12_students, reported_groups FROM pub_obrazovanie.context_publication"),
    "context_kind": ("SELECT school_year, kind, institutions, preschool, reported_pupils, reported_groups FROM live.context_kind",
                     "SELECT school_year, kind, institutions, preschool, reported_pupils, reported_groups FROM pub_obrazovanie.context_kind"),
    "context_grade": ("SELECT school_year, kind, g.key::smallint, g.value::int FROM live.context_kind, jsonb_each_text(grades) g",
                      "SELECT school_year, kind, grade, students FROM pub_obrazovanie.context_grade"),
}
# (name of the rule, table, the column of the period or resource, the summed columns by their place in the row)
SUMS = {"exam_result": (0, (3, 4)), "dzi_result": (0, (8, 9)), "nvo_result": (0, (7, 8)),
        "context_grade": (0, (3,)), "context_kind": (0, (2, 4))}


def _norm(row):
    """One row as a comparable tuple: numbers by value, so 5.0 and 5.00 are the same score."""
    return tuple(x.normalize() if isinstance(x, Decimal) else tuple(x) if isinstance(x, list) else x for x in row)


def parity(conn, r, old_dsn=OLD_DSN):
    """The old database against the serving layer of the shared one: every published table, row by row, and the
    count and the sums of every table by year or resource. Each comparison is a result of a rule of kind `parity`."""
    with psycopg.connect(old_dsn, autocommit=True) as old:
        old.execute("SET default_transaction_read_only = on")
        sides = {t: ([_norm(x) for x in old.execute(a)], [_norm(x) for x in conn.execute(b)])
                 for t, (a, b) in PARITY.items()}
    differ, detail = 0, []
    for table, (a, b) in sides.items():
        pipeline.record_check(conn, r, f"parity_{table}_rows", len(a), len(b))
        missing, extra = set(a) - set(b), set(b) - set(a)
        if missing or extra or len(set(a)) != len(a):
            differ += len(missing) + len(extra) or 1
            detail.append(f"{table}: {len(missing)} само в старата, {len(extra)} само в новата; "
                          + "; ".join(map(str, sorted(missing, key=str)[:3] + sorted(extra, key=str)[:3])))
        if table in SUMS:
            period, columns = SUMS[table]
            for side, rows in (("old", a), ("new", b)):
                totals = {}
                for x in rows:
                    t = totals.setdefault(x[period], [0] * len(columns))
                    for i, c in enumerate(columns):
                        t[i] += x[c] or 0
                r.stats.setdefault("sums", {}).setdefault(table, {})[side] = [sum(t[i] for t in totals.values())
                                                                              for i in range(len(columns))]
            o, n = r.stats["sums"][table]["old"], r.stats["sums"][table]["new"]
            for i, c in enumerate(columns):
                pipeline.record_check(conn, r, f"parity_{table}_sum{i + 1}", o[i], n[i])
    pipeline.record_check(conn, r, "parity_rows_differ", 0, differ, "; ".join(detail)[:2000] or None)
    r.stats.update(rows={t: [len(a), len(b)] for t, (a, b) in sides.items()}, rows_differ=differ)


STEPS = {"mon": refresh, "history": refresh_history, "legacy": refresh_legacy, "dzi": refresh_dzi, "nvo": refresh_nvo,
         "status": refresh_status, "context": refresh_context}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--step", required=True, choices=["migrate", *STEPS, "build", "replay", "parity", "check", "freshness"])
    a = p.parse_args(argv)
    with pipeline.connect(DSN) as conn:
        if a.step == "migrate":
            pipeline.require_core(conn, "0009")
            result = {"migrations": pipeline.migrate(conn, TOOL, ROOT)}
        elif a.step == "freshness":
            result = {"problems": pipeline.freshness(conn, TOOL)}
        else:
            try:
                with pipeline.run(conn, TOOL, a.step, sha=pipeline.code_sha(ROOT)) as r:
                    problems = []
                    if a.step in STEPS:
                        problems = STEPS[a.step](conn, r)
                    elif a.step == "build":
                        rebuild(conn, r)
                    elif a.step == "replay":
                        problems = replay(conn, r)
                    elif a.step == "parity":
                        parity(conn, r)
                    if a.step != "parity":
                        failed = pipeline.run_checks(conn, r)
                        # The serving layer follows only when every rule holds. A resource that one read refused is
                        # not a reason to hold the rest: what is stored is still sound.
                        problems += failed or pipeline.publish(conn, r, ROOT).problems
                    result = {"step": a.step, "stats": r.stats, "problems": problems}
            except pipeline.Busy as e:
                result = {"step": a.step, "status": "skipped", "note": str(e)}
            except (ShapeError, pipeline.ShapeError, pipeline.CorruptRaw, ValueError) as e:
                result = {"step": a.step, "problems": [f"Образование: {e}"]}
            if a.step == "parity" and "stats" in result:
                bad = conn.execute("""SELECT title_bg, expected, actual FROM ops.check_latest
                    WHERE tool = %s AND kind = 'parity' AND ok = false ORDER BY name""", (TOOL,)).fetchall()
                result["problems"] = [f"{title}: старо {e}, ново {n}" for title, e, n in bad]
    return pipeline.report(result)


if __name__ == "__main__":
    raise SystemExit(main())

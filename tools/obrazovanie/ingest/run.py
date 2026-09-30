"""Refresh the latest NVO VII school year from MON."""

import argparse
import datetime as dt
import json

from . import db, http
from .dzi import DZI_DATASET, dzi_catalog, parse_dzi
from .history import parse_nvo7_year
from .nvo import DATASETS as NVO_DATASETS, LAYOUTS as NVO_LAYOUTS, nvo_catalog, parse_nvo
from .parse import parse_nvo7
from .registry import parse_code_free_register, parse_schools_year
from .sources import (NVO7_DATASET, SCHOOLS_DATASET, Resource, _payload, catalog, current_pair,
                      parse_schools, reconcile)
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


def freshness(conn, today=None):
    today = today or dt.date.today()
    problems = []
    for url, status, error, last_ok in conn.execute("SELECT url,status,error,last_ok FROM ops.source_state"):
        if status != "ok":
            problems.append(f"Образование: {url}: {error or status}")
        elif not last_ok or (dt.datetime.now(dt.timezone.utc) - last_ok).days > 8:
            problems.append(f"Образование: източникът {url} не е проверяван от 8 дни")
    latest = conn.execute("SELECT max(school_year) FROM live.publication").fetchone()[0]
    expected_start = today.year - (1 if today.month >= 9 else 2)
    if not latest or int(latest[:4]) < expected_start:
        problems.append("Образование: няма резултати от НВО VII за очакваната учебна година")
    for year, first_at in conn.execute("SELECT school_year,first_at FROM ops.held"):
        if dt.datetime.now(dt.timezone.utc) - first_at > dt.timedelta(days=1):
            problems.append(f"Образование: {year} е задържана над ден")
    dzi = conn.execute("SELECT max(school_year),count(*) FROM live.dzi_publication").fetchone()
    if not dzi[0] or int(dzi[0][:4]) < expected_start:
        problems.append("Образование: няма резултати от ДЗИ за очакваната учебна година")
    for resource, first_at in conn.execute("SELECT resource,first_at FROM ops.dzi_held"):
        if dt.datetime.now(dt.timezone.utc) - first_at > dt.timedelta(days=1):
            problems.append(f"Образование: ДЗИ {resource[:8]} е задържан над ден")
    for exam in ("nvo4", "nvo10"):
        year = conn.execute("SELECT max(school_year) FROM live.nvo_publication WHERE exam=%s", (exam,)).fetchone()[0]
        if not year or int(year[:4]) < expected_start:
            problems.append(f"Образование: няма резултати от {exam} за очакваната учебна година")
    for resource, first_at in conn.execute("SELECT resource,first_at FROM ops.nvo_held"):
        if dt.datetime.now(dt.timezone.utc) - first_at > dt.timedelta(days=1):
            problems.append(f"Образование: НВО {resource[:8]} е задържан над ден")
    for kind in STATUS_DATASETS:
        year = conn.execute("SELECT max(school_year) FROM live.status_publication WHERE kind=%s",
                            (kind,)).fetchone()[0]
        if not year or int(year[:4]) < expected_start:
            problems.append(f"Образование: няма актуален списък {kind}")
    for resource, first_at in conn.execute("SELECT resource,first_at FROM ops.status_held"):
        if dt.datetime.now(dt.timezone.utc) - first_at > dt.timedelta(days=1):
            problems.append(f"Образование: списък {resource[:8]} е задържан над ден")
    return problems


def refresh(conn, post=http.post):
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"problems": []}
    catalog_urls = {NVO7_DATASET: "https://data.egov.bg/api/listResources?dataset=" + NVO7_DATASET,
                    SCHOOLS_DATASET: "https://data.egov.bg/api/listResources?dataset=" + SCHOOLS_DATASET}
    try:
        catalogs = {}
        for dataset, uri in catalog_urls.items():
            raw = post("listResources", {"criteria": {"dataset_uri": dataset}, "records_per_page": 100, "page_number": 1})
            db.save_raw(conn, uri, raw)
            catalogs[dataset] = raw
        exam, register = current_pair(catalogs[NVO7_DATASET], catalogs[SCHOOLS_DATASET])
        exam_raw = post("getResourceData", {"resource_uri": exam.uri})
        exam_sha = db.save_raw(conn, exam.uri, exam_raw)
        register_raw = post("getResourceData", {"resource_uri": register.uri})
        register_sha = db.save_raw(conn, register.uri, register_raw)
        results = parse_nvo7(exam_raw)
        schools = parse_schools(register_raw)
        checks = reconcile(results, schools)
        status = db.publish(conn, exam.year, exam, register, exam_sha, register_sha, results, schools, checks)
        report.update(status=status, year=exam.year, **checks)
        for uri in catalog_urls.values():
            db.state(conn, uri, "ok")
        if status == "held":
            report["problems"].append(f"Образование: {exam.year} чака второ четене")
    except Exception as exc:
        report["problems"].append(f"Образование: {exc}")
        for uri in catalog_urls.values():
            db.state(conn, uri, "error", str(exc)[:1000])
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def historical_years(exams, registers):
    """Return only published code-bearing register years before the latest exam."""
    latest = max(exams)
    return [year for year in sorted(exams.keys() & registers.keys())
            if "2021/2022" <= year < latest]


def refresh_history(conn, post=http.post):
    """Refresh code-reconciled historical NVO VII years independently of the latest year."""
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"years": {}, "problems": []}
    try:
        catalogs = {}
        for dataset in (NVO7_DATASET, SCHOOLS_DATASET):
            uri = "https://data.egov.bg/api/listResources?dataset=" + dataset
            raw = post("listResources", {"criteria": {"dataset_uri": dataset}, "records_per_page": 100, "page_number": 1})
            db.save_raw(conn, uri, raw)
            catalogs[dataset] = catalog(raw, dataset)
            db.state(conn, uri, "ok")
        exams = {item.year: item for item in catalogs[NVO7_DATASET]}
        registers = {}
        for item in catalogs[SCHOOLS_DATASET]:
            if item.year not in registers or item.updated_at > registers[item.year].updated_at:
                registers[item.year] = item
        # Earlier official register tables have no NEISPUO code. Their display policy is separate.
        for year in historical_years(exams, registers):
            exam, register = exams[year], registers[year]
            try:
                exam_raw = post("getResourceData", {"resource_uri": exam.uri})
                exam_sha = db.save_raw(conn, exam.uri, exam_raw)
                register_raw = post("getResourceData", {"resource_uri": register.uri})
                register_sha = db.save_raw(conn, register.uri, register_raw)
                results = parse_nvo7_year(exam_raw, year)
                schools = parse_schools_year(register_raw)
                checks = reconcile(results, schools)
                status = db.publish(conn, year, exam, register, exam_sha, register_sha, results, schools, checks)
                report["years"][year] = {"status": status, **checks}
                if status == "held":
                    report["problems"].append(f"Образование: {year} чака второ четене")
            except Exception as exc:
                db.state(conn, exam.uri, "error", str(exc)[:1000])
                report["problems"].append(f"Образование: {year}: {exc}")
    except Exception as exc:
        report["problems"].append(f"Образование: исторически каталог: {exc}")
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def refresh_legacy(conn, post=http.post):
    """Publish NVO VII's earlier years with an explicit no-code verification status."""
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"years": {}, "problems": []}
    try:
        catalogs = {}
        for dataset in (NVO7_DATASET, SCHOOLS_DATASET):
            uri = "https://data.egov.bg/api/listResources?dataset=" + dataset
            raw = post("listResources", {"criteria": {"dataset_uri": dataset}, "records_per_page": 100, "page_number": 1})
            db.save_raw(conn, uri, raw)
            catalogs[dataset] = raw
            db.state(conn, uri, "ok")
        for year, ((exam_uri, exam_title), (register_uri, register_title)) in LEGACY.items():
            try:
                exam = legacy_resource(catalogs[NVO7_DATASET], NVO7_DATASET, exam_uri, year, exam_title)
                register = legacy_resource(catalogs[SCHOOLS_DATASET], SCHOOLS_DATASET,
                                           register_uri, year, register_title)
                exam_raw = post("getResourceData", {"resource_uri": exam.uri})
                exam_sha = db.save_raw(conn, exam.uri, exam_raw)
                register_raw = post("getResourceData", {"resource_uri": register.uri})
                register_sha = db.save_raw(conn, register.uri, register_raw)
                results = parse_nvo7_year(exam_raw, year)
                register_rows = parse_code_free_register(register_raw, year)
                codes = sorted({r.neispuo for r in results})
                checks = {"schools": len(codes), "matched": 0, "unmatched": codes}
                status = db.publish(conn, year, exam, register, exam_sha, register_sha,
                                    results, {}, checks, verification="no-code")
                db.state(conn, register.uri, "ok", rows=register_rows)
                report["years"][year] = {"status": status, "schools": len(codes),
                                         "register_rows": register_rows, "verification": "no-code"}
                if status == "held":
                    report["problems"].append(f"Образование: {year} чака второ четене")
            except Exception as exc:
                db.state(conn, exam_uri, "error", str(exc)[:1000])
                report["problems"].append(f"Образование: {year}: {exc}")
    except Exception as exc:
        report["problems"].append(f"Образование: стар каталог: {exc}")
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def refresh_dzi(conn, post=http.post):
    """Read all observed DZI resources, preserving session and optional-exam distinctions."""
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"resources": {}, "problems": []}
    try:
        dzi_url = "https://data.egov.bg/api/listResources?dataset=" + DZI_DATASET
        registry_url = "https://data.egov.bg/api/listResources?dataset=" + SCHOOLS_DATASET
        dzi_raw = post("listResources", {"criteria": {"dataset_uri": DZI_DATASET},
                                         "records_per_page": 100, "page_number": 1})
        db.save_raw(conn, dzi_url, dzi_raw)
        resources = dzi_catalog(dzi_raw)
        registry_raw = post("listResources", {"criteria": {"dataset_uri": SCHOOLS_DATASET},
                                              "records_per_page": 100, "page_number": 1})
        db.save_raw(conn, registry_url, registry_raw)
        registers = {}
        for item in catalog(registry_raw, SCHOOLS_DATASET):
            if item.year not in registers or item.updated_at > registers[item.year].updated_at:
                registers[item.year] = item
        cache = {}
        for resource in resources:
            try:
                raw = post("getResourceData", {"resource_uri": resource.uri})
                sha = db.save_raw(conn, resource.uri, raw)
                table = parse_dzi(raw, resource.uri)
                if table.year != resource.year:
                    raise ValueError("DZI catalog and table years differ")
                register, register_sha, schools = None, None, None
                if table.year >= "2021/2022":
                    register = registers.get(table.year)
                    if not register:
                        raise ValueError(f"No same-year school register for {table.year}")
                    if table.year not in cache:
                        school_raw = post("getResourceData", {"resource_uri": register.uri})
                        school_sha = db.save_raw(conn, register.uri, school_raw)
                        school_rows = (parse_schools(school_raw) if table.year == "2025/2026"
                                       else parse_schools_year(school_raw))
                        cache[table.year] = (school_sha, school_rows)
                    register_sha, schools = cache[table.year]
                status = db.publish_dzi(conn, resource, sha, table, register, register_sha, schools)
                report["resources"][resource.uri[:8]] = {"year": table.year, "session": table.session,
                    "kind": table.kind, "status": status, "rows": table.rows,
                    "results": len(table.results)}
                if status == "held":
                    report["problems"].append(f"Образование: ДЗИ {resource.name} чака второ четене")
            except Exception as exc:
                db.state(conn, resource.uri, "error", str(exc)[:1000])
                report["problems"].append(f"Образование: ДЗИ {resource.uri[:8]}: {exc}")
        db.state(conn, dzi_url, "ok", rows=len(resources))
        db.state(conn, registry_url, "ok", rows=len(registers))
    except Exception as exc:
        report["problems"].append(f"Образование: каталог ДЗИ: {exc}")
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def refresh_nvo(conn, post=http.post):
    """Read all observed NVO IV/X years and reconcile code-bearing years."""
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"resources": {}, "problems": []}
    try:
        registry_url = "https://data.egov.bg/api/listResources?dataset=" + SCHOOLS_DATASET
        registry_raw = post("listResources", {"criteria": {"dataset_uri": SCHOOLS_DATASET},
                                              "records_per_page": 100, "page_number": 1})
        db.save_raw(conn, registry_url, registry_raw)
        registers = {}
        for item in catalog(registry_raw, SCHOOLS_DATASET):
            if item.year not in registers or item.updated_at > registers[item.year].updated_at:
                registers[item.year] = item
        cache = {}
        for exam, dataset in NVO_DATASETS.items():
            catalog_url = "https://data.egov.bg/api/listResources?dataset=" + dataset
            raw = post("listResources", {"criteria": {"dataset_uri": dataset},
                                         "records_per_page": 100, "page_number": 1})
            db.save_raw(conn, catalog_url, raw)
            resources = nvo_catalog(raw, exam)
            for resource in resources:
                try:
                    answer = post("getResourceData", {"resource_uri": resource.uri})
                    sha = db.save_raw(conn, resource.uri, answer)
                    table = parse_nvo(answer, resource.uri)
                    if table.rows < NVO_LAYOUTS[resource.uri]["observed_rows"] * 0.8:
                        raise ValueError("NVO response has fewer than 80% of observed schools")
                    register, register_sha, schools = None, None, None
                    if table.year >= "2021/2022":
                        register = registers.get(table.year)
                        if not register:
                            raise ValueError(f"No same-year school register for {table.year}")
                        if table.year not in cache:
                            reg_raw = post("getResourceData", {"resource_uri": register.uri})
                            reg_sha = db.save_raw(conn, register.uri, reg_raw)
                            reg_rows = (parse_schools(reg_raw) if table.year == "2025/2026"
                                        else parse_schools_year(reg_raw))
                            cache[table.year] = (reg_sha, reg_rows)
                        register_sha, schools = cache[table.year]
                    status = db.publish_nvo(conn, resource, sha, table, register, register_sha, schools)
                    report["resources"][resource.uri[:8]] = {"exam": exam, "year": table.year,
                        "status": status, "schools": table.rows, "results": len(table.results)}
                    if status == "held":
                        report["problems"].append(f"Образование: {exam} {table.year} чака второ четене")
                except Exception as exc:
                    db.state(conn, resource.uri, "error", str(exc)[:1000])
                    report["problems"].append(f"Образование: {exam} {resource.uri[:8]}: {exc}")
            db.state(conn, catalog_url, "ok", rows=len(resources))
        db.state(conn, registry_url, "ok", rows=len(registers))
    except Exception as exc:
        report["problems"].append(f"Образование: каталог НВО IV/X: {exc}")
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def refresh_status(conn, post=http.post):
    """Read all reviewed MON protected and central lists with NEISPUO codes."""
    if not conn.execute("SELECT pg_try_advisory_lock(8016001)").fetchone()[0]:
        return {"status": "skipped", "problems": []}
    report = {"resources": {}, "problems": []}
    try:
        for kind, dataset in STATUS_DATASETS.items():
            catalog_url = "https://data.egov.bg/api/listResources?dataset=" + dataset
            try:
                raw = post("listResources", {"criteria": {"dataset_uri": dataset},
                                             "records_per_page": 100, "page_number": 1})
                db.save_raw(conn, catalog_url, raw)
                resources = status_catalog(raw, kind)
                for resource in resources:
                    try:
                        answer = post("getResourceData", {"resource_uri": resource.uri})
                        sha = db.save_raw(conn, resource.uri, answer)
                        rows = parse_status(answer, resource.uri)
                        status = db.publish_status(conn, kind, resource, sha, rows)
                        report["resources"][resource.uri[:8]] = {
                            "kind": kind, "year": resource.year, "rows": len(rows), "status": status}
                        if status == "held":
                            report["problems"].append(f"Образование: списък {resource.uri[:8]} чака второ четене")
                    except Exception as exc:
                        db.state(conn, resource.uri, "error", str(exc)[:1000])
                        report["problems"].append(f"Образование: списък {resource.uri[:8]}: {exc}")
                db.state(conn, catalog_url, "ok", rows=len(resources))
            except Exception as exc:
                db.state(conn, catalog_url, "error", str(exc)[:1000])
                report["problems"].append(f"Образование: каталог {kind}: {exc}")
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["migrate", "mon", "history", "legacy", "dzi", "nvo", "status", "freshness"])
    args = parser.parse_args()
    with db.connect(autocommit=True) as conn:
        if args.step == "migrate":
            report = {"migrations": db.migrate(conn)}
        elif args.step == "mon":
            report = refresh(conn)
        elif args.step == "history":
            report = refresh_history(conn)
        elif args.step == "legacy":
            report = refresh_legacy(conn)
        elif args.step == "dzi":
            report = refresh_dzi(conn)
        elif args.step == "nvo":
            report = refresh_nvo(conn)
        elif args.step == "status":
            report = refresh_status(conn)
        else:
            report = {"problems": freshness(conn)}
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())

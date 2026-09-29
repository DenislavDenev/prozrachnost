"""Refresh the latest NVO VII school year from MON."""

import argparse
import datetime as dt
import json

from . import db, http
from .history import parse_nvo7_year
from .parse import parse_nvo7
from .registry import parse_schools_year
from .sources import NVO7_DATASET, SCHOOLS_DATASET, catalog, current_pair, parse_schools, reconcile


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
        latest = max(exams)
        # Earlier official register tables have no NEISPUO code. Their display policy is separate.
        for year in sorted(set(exams) & registers):
            if year < "2021/2022" or year == latest:
                continue
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["migrate", "mon", "history", "freshness"])
    args = parser.parse_args()
    with db.connect(autocommit=True) as conn:
        if args.step == "migrate":
            report = {"migrations": db.migrate(conn)}
        elif args.step == "mon":
            report = refresh(conn)
        elif args.step == "history":
            report = refresh_history(conn)
        else:
            report = {"problems": freshness(conn)}
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())

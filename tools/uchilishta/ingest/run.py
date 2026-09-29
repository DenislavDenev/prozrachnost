"""Refresh the latest NVO VII school year from MON."""

import argparse
import datetime as dt
import json

from . import db, http
from .parse import parse_nvo7
from .sources import NVO7_DATASET, SCHOOLS_DATASET, current_pair, parse_schools, reconcile


def freshness(conn, today=None):
    today = today or dt.date.today()
    problems = []
    for url, status, error, last_ok in conn.execute("SELECT url,status,error,last_ok FROM ops.source_state"):
        if status != "ok":
            problems.append(f"Училища: {url}: {error or status}")
        elif not last_ok or (dt.datetime.now(dt.timezone.utc) - last_ok).days > 8:
            problems.append(f"Училища: източникът {url} не е проверяван от 8 дни")
    latest = conn.execute("SELECT max(school_year) FROM live.publication").fetchone()[0]
    expected_start = today.year - (1 if today.month >= 9 else 2)
    if not latest or int(latest[:4]) < expected_start:
        problems.append("Училища: няма резултати от НВО VII за очакваната учебна година")
    for year, first_at in conn.execute("SELECT school_year,first_at FROM ops.held"):
        if dt.datetime.now(dt.timezone.utc) - first_at > dt.timedelta(days=1):
            problems.append(f"Училища: {year} е задържана над ден")
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
            report["problems"].append(f"Училища: {exam.year} чака второ четене")
    except Exception as exc:
        report["problems"].append(f"Училища: {exc}")
        for uri in catalog_urls.values():
            db.state(conn, uri, "error", str(exc)[:1000])
    finally:
        conn.execute("SELECT pg_advisory_unlock(8016001)")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, choices=["migrate", "mon", "freshness"])
    args = parser.parse_args()
    with db.connect(autocommit=True) as conn:
        if args.step == "migrate":
            report = {"migrations": db.migrate(conn)}
        elif args.step == "mon":
            report = refresh(conn)
        else:
            report = {"problems": freshness(conn)}
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())

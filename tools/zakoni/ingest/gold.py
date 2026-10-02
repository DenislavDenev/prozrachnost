"""Silver -> gold in one transaction, then the quality checks of db/checks/*.sql. A check that returns a row rolls the
gold back: the previous gold stays and the build reports the violations (STANDARD 3А)."""
import csv
import json

from . import db
from .config import ROOT


def load_ref(conn):
    """db/ref/*.csv -> ref.* (replaced whole: the files are the copy)."""
    with conn.transaction():
        conn.execute("DELETE FROM ref.institution_municipality")
        conn.execute("DELETE FROM ref.municipality")
        for r in csv.DictReader(open(ROOT / "db" / "ref" / "municipality.csv", encoding="utf-8")):
            conn.execute("INSERT INTO ref.municipality (id, name_bg, name_en, oblast, nuts3, nuts2, nuts1) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (r["id"], r["name_bg"], r["name_en"], r["oblast"], r["nuts3"], r["nuts2"], r["nuts1"]))
        for r in csv.DictReader(open(ROOT / "db" / "ref" / "institution_municipality.csv", encoding="utf-8")):
            conn.execute("INSERT INTO ref.institution_municipality VALUES (%s,%s,%s,%s,%s,%s)",
                         (r["institution_id"], r["municipality_id"], r["method"], r["evidence"], r["checked"], r["checked_by"]))


def violations(conn):
    out = []
    for f in sorted((ROOT / "db" / "checks").glob("*.sql")):
        for check, key, detail in conn.execute(f.read_text(encoding="utf-8")).fetchall():
            out.append({"check": check, "key": key, "detail": detail, "file": f.name})
    return out


class CheckFailed(RuntimeError):
    def __init__(self, found):
        self.found = found
        super().__init__(f"{len(found)} нарушения на проверките: " + "; ".join(f"{v['check']} {v['key']}" for v in found[:5]))


def build(conn, st, run_id):
    load_ref(conn)
    try:
        with conn.transaction():
            bid = conn.execute("INSERT INTO gold.build (run_id, code_sha) VALUES (%s,%s) RETURNING build_id", (run_id, db.code_sha())).fetchone()[0]
            for f in sorted((ROOT / "db" / "gold").glob("*.sql")):
                for stmt in f.read_text(encoding="utf-8").split(";" + chr(10)):      # one statement at a time: parameters need it
                    if stmt.strip():
                        conn.execute(stmt, {"build": bid})
            found = violations(conn)
            if found:
                raise CheckFailed(found)
            rows = {t: conn.execute(f"SELECT count(*) FROM gold.{t}").fetchone()[0]
                    for t in ("act", "act_institution", "act_relation", "consultation", "strategy_doc", "impact_contract")}
            rows["unmatched"] = conn.execute("SELECT count(*) FROM gold.unmatched WHERE build_id = %s", (bid,)).fetchone()[0]
            conn.execute("UPDATE gold.build SET rows = %s, checks = %s, ok = true WHERE build_id = %s",
                         (json.dumps(rows), json.dumps({"violations": 0}), bid))
    except CheckFailed as e:
        conn.execute("INSERT INTO gold.build (run_id, code_sha, rows, checks, ok) VALUES (%s,%s,NULL,%s,false)",
                     (run_id, db.code_sha(), json.dumps({"violations": e.found[:50]}, ensure_ascii=False)))
        raise
    st["gold"] = rows
    st["build_id"] = bid
    return bid

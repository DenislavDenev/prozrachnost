"""The clean tables (schema zakoni) from the current loads of the landing zone: db/build/*.sql in one transaction, whole.
The rules of db/checks.toml judge the result; the pages see it only through a publication (pipeline.publish)."""
from .config import ROOT

TABLES = ("act", "act_institution", "act_relation", "consultation", "consultation_file", "strategy_doc", "impact_contract", "unmatched")


def statements():
    for f in sorted((ROOT / "db" / "build").glob("*.sql")):
        for stmt in f.read_text(encoding="utf-8").split(";\n"):
            if stmt.strip() and not all(x.strip().startswith("--") for x in stmt.strip().splitlines()):
                yield stmt


def build(conn, r):
    with conn.transaction():
        for stmt in statements():
            conn.execute(stmt)
        rows = {t: conn.execute(f"SELECT count(*) FROM zakoni.{t}").fetchone()[0] for t in TABLES}
    r.stats["clean"] = rows
    return rows

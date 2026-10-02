"""The build step: archive -> silver (versioned) -> gold (checked) for the 14 resources of the data set.

  load      the files that are loaded into silver (acts of the Council of Ministers, consultations, strategic documents,
            contracts for impact assessments)
  check     reports whose numbers are compared with ours (they say their own totals)
  skip      read and counted, not loaded (publications, polls, advisory councils; the reason is in docs/sources.md)
  same      the 'legislative initiatives' report is, byte for byte, the strategic documents report (a fault of the source)
"""
import json
from dataclasses import dataclass, field

from . import archive, db, parse, store
from .config import ROOT, SET_ID
from .stream import ShapeError, records

SOURCE = store.SOURCE


class ReconError(RuntimeError):
    """A count does not agree with the source: nothing is published."""


@dataclass
class Res:
    id: str
    name: str
    role: str
    fn: object = None
    tables: list = field(default_factory=list)
    origin: str = None
    same_as: str = None


RESOURCES = [
    Res("057c5c06-a11e-4e2d-8862-189c4d06e7b7", "Правна информация на Министерския съвет (ПРИС), действащи", "load",
        lambda it: parse.pris(it, "current"), ["pris_act", "pris_institution", "pris_tag", "pris_related"], "current"),
    Res("e01f99a7-e1e0-4b5e-be8d-d5141a41e0f2", "Правна информация на Министерския съвет (ПРИС), архив", "load",
        lambda it: parse.pris(it, "archive"), ["pris_act", "pris_institution", "pris_tag", "pris_related"], "archive"),
    Res("b600c109-9d30-4ba0-8463-9ed88a89f745", "Обществени консултации, обединена справка", "load",
        parse.consultation, ["consultation", "consultation_file"]),
    Res("0a1d9485-a8fd-4e59-97f5-4063b5b72001", "Стратегически документи, пълна справка", "load", parse.strategy,
        ["strategy_doc", "strategy_author", "strategy_file", "strategy_sub"]),
    Res("043ba0e4-18e1-4880-8a1f-ea739103dca4", "Договори за оценка на въздействието", "load", parse.impact, ["impact_contract"]),
    Res("e935536f-db99-45a2-a024-d55631d873e1", "Обществени консултации, стандартна справка", "check", parse.standard),
    Res("210747d1-9840-4345-939a-2338fda61031", "Обществени консултации по институции", "check", parse.by_institution),
    Res("403043ed-7b82-477d-bb0f-ec7e0db996e9", "Обществени консултации по области на политика", "check", parse.by_area),
    Res("e590f7d0-b76c-4ae8-a4fb-7ac46ec6f527", "Обществени консултации по области на политика и институции", "skip",
        lambda it: parse.count_only(it, "name", "Институция", "Справка по области и институции")),
    Res("357d8f6d-7839-4ab9-9c9d-7510515ce9f5", "Стратегически документи, стандартна справка", "skip",
        lambda it: parse.count_only(it, "name", "Заглавие", "Стратегически документи, стандартна")),
    Res("31a54d26-3d66-425f-a242-e70bcee4b387", "Публикации на портала", "skip",
        lambda it: parse.count_only(it, "title", "Заглавие", "Публикации")),
    Res("52f8c24a-348a-47a1-a591-19903c9bb5aa", "Анкети", "skip", lambda it: parse.count_only(it, "name", "Наименование", "Анкети")),
    Res("94b547f8-8236-45b5-b6ee-60174d92b887", "Консултативни съвети", "skip",
        lambda it: parse.count_only(it, "title", "Заглавие", "Консултативни съвети")),
    Res("8b718708-ab20-4c25-8d6f-133f0d5a5ce3", "Законодателни инициативи", "same", same_as="357d8f6d-7839-4ab9-9c9d-7510515ce9f5"),
]
BY_ID = {r.id: r for r in RESOURCES}
LOADED = [r for r in RESOURCES if r.role == "load"]


def known_gaps():
    return json.loads((ROOT / "db" / "ref" / "known_gaps.json").read_text(encoding="utf-8"))


def read(conn, r, extras, now, force=False, run_id=None):
    """Read one resource from the archive. -> a dict for the report; problems are in 'error'."""
    out = {"ref": r.id, "name": r.name, "role": r.role}
    prev = conn.execute("SELECT sha256, status FROM ops.source_state WHERE source = %s AND ref = %s", (SOURCE, r.id)).fetchone()
    try:
        path, sha, size, version, mtime = archive.verified(r.id)
    except archive.ArchiveError as e:
        status = "липсва при източника" if "липсва" in str(e) else "грешка при четене"
        db.state(conn, SOURCE, r.id, status=status, error=str(e)[:500], role=r.role)
        return {**out, "status": status, "error": str(e)}
    conn.execute("""INSERT INTO ops.raw_file (source, ref, path, sha256, bytes, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source, ref, sha256) DO NOTHING""", (SOURCE, r.id, str(path), sha, size, mtime))
    out.update(sha256=sha, bytes=size, version=version)
    try:
        if r.role == "same":
            other = archive.verified(r.same_as)[1]
            status = "същият файл като " + BY_ID[r.same_as].name if other == sha else "собствени данни, не се зареждат"
            db.state(conn, SOURCE, r.id, status=status, error=None, last_ok="now", sha256=sha, version=version, role=r.role)
            return {**out, "status": status, "same": other == sha}
        if r.role == "load":
            return {**out, **_load(conn, r, path, sha, version, prev, now, force, run_id)}
        parsed = r.fn(records(path))
        n = parsed if isinstance(parsed, int) else len(parsed)
        extras[r.id] = parsed
        db.state(conn, SOURCE, r.id, status="наред", error=None, last_ok="now", rows=n, sha256=sha, version=version, role=r.role)
        return {**out, "status": "наред", "rows": n}
    except ShapeError as e:
        if not prev or prev[0] != sha or prev[1] != "невалиден отговор":
            db.log_change(conn, SOURCE, r.id, "sha256", prev[0] if prev else None, sha, "invalid")
        db.state(conn, SOURCE, r.id, status="невалиден отговор", error=str(e)[:500], sha256=sha, version=version, role=r.role)
        return {**out, "status": "невалиден отговор", "error": str(e)}


def _scope_count(conn, r):
    parent = r.tables[0]
    sc, sa = store._scope(parent, r.origin)
    return conn.execute(f"SELECT count(*) FROM silver.{parent} c WHERE c.valid_to IS NULL AND {sc}", sa).fetchone()[0]


def _reconcile(conn, run_id, name, expected, actual, kind="block", note=None):
    conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (run_id, name, expected, actual, expected == actual, kind, note))


def _load(conn, r, path, sha, version, prev, now, force, run_id):
    if not force and prev and prev[0] == sha and prev[1] == "наред":
        # the same answer as the last good build: nothing to parse; the count of what we hold is still checked
        rows = conn.execute("SELECT rows FROM ops.source_state WHERE source = %s AND ref = %s", (SOURCE, r.id)).fetchone()[0]
        held = _scope_count(conn, r)
        if rows is not None and rows != held:
            raise ReconError(f"{r.name}: четохме {rows} записа, в сребърния слой са {held}")
        _reconcile(conn, run_id, f"file-records-vs-silver:{r.id}", rows, held, note=r.name)
        db.state(conn, SOURCE, r.id, status="наред", error=None, last_ok="now", sha256=sha, version=version, role=r.role)
        return {"status": "наред", "result": "unchanged", "rows": held}
    parsed = r.fn(records(path))
    parent = r.tables[0]
    n_parent = len(parsed.tables.get(parent, []))
    folded = parsed.notes.get("folded", 0)
    if parsed.count != n_parent + folded:
        raise ShapeError(f"{r.name}: във файла са {parsed.count} записа, а след разбора {n_parent} (+{folded} повторени)")
    result = store.apply(conn, r.id, sha, parsed, r.tables, r.origin, now)
    if result == "held":
        return {"status": "задържан до второ четене", "result": result, "rows": parsed.count}
    held = _scope_count(conn, r)
    if held != n_parent:
        raise ReconError(f"{r.name}: след записа са {held} записа вместо {n_parent}")
    _reconcile(conn, run_id, f"file-records-vs-silver:{r.id}", parsed.count, held + folded, note=r.name)
    db.state(conn, SOURCE, r.id, status="наред", error=None, last_ok="now", rows=n_parent, sha256=sha, version=version, role=r.role,
             **({"last_change": "now"} if result == "stored" else {}))
    return {"status": "наред", "result": result, "rows": n_parent, "folded": folded, **parsed.notes}


def crosschecks(conn, run_id, extras):
    """The totals of the source's other reports against ours (STANDARD 1Б: when the source has no total for a file, its own
    other reports are the counters). A difference that is in the source itself and is recorded in db/ref/known_gaps.json
    is `known`; any other difference is shown as `info` and, for the main file, blocks nothing but is reported."""
    gaps = known_gaps()
    total = conn.execute("SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL").fetchone()[0]
    named = conn.execute("SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL AND institution_name IS NOT NULL").fetchone()[0]
    out = []
    inst = extras.get("210747d1-9840-4345-939a-2338fda61031")
    if inst is not None:
        s = sum(x["pc_cnt"] for x in inst)
        g = gaps["consultations_vs_institution_report"]
        ok = (s - named) == g["delta"]
        conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,'known',%s)",
                     (run_id, "consultations-vs-institution-report", s, named, ok,
                      f"справката по институции има {s}, обединената {named} с посочена институция; разликата {s - named}, записана в известните ({g['delta']}): {g['evidence']}"))
        out.append(("consultations-vs-institution-report", s, named, ok))
        less = conn.execute("""SELECT count(*) FROM silver.consultation WHERE valid_to IS NULL AND institution_name IS NOT NULL
                               AND date_close - date_open < 30""").fetchone()[0]
        conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,'info',%s)",
                     (run_id, "short-term-vs-institution-report", sum(x["less_days_cnt"] for x in inst), less,
                      sum(x["less_days_cnt"] for x in inst) == less, "консултации със срок под 30 дни, според справката по институции и по нашето изчисление"))
    area = extras.get("403043ed-7b82-477d-bb0f-ec7e0db996e9")
    if area is not None:
        s = sum(x["pc_cnt"] for x in area)
        conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,'info',%s)",
                     (run_id, "consultations-vs-area-report", s, total, s == total,
                      "справката по области на политика брои и консултации, които обединената справка няма"))
    std = extras.get("e935536f-db99-45a2-a024-d55631d873e1")
    if std is not None:
        conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,'info',%s)",
                     (run_id, "consultations-vs-standard-report", len(std), total, len(std) == total,
                      "стандартната справка съдържа и неактивни консултации; обединената само активните"))
    sd = extras.get("357d8f6d-7839-4ab9-9c9d-7510515ce9f5")
    if sd is not None:
        docs = conn.execute("SELECT count(*) FROM silver.strategy_doc WHERE valid_to IS NULL").fetchone()[0]
        conn.execute("INSERT INTO ops.reconciliation (run_id, name, expected, actual, ok, kind, note) VALUES (%s,%s,%s,%s,%s,'info',%s)",
                     (run_id, "strategy-docs-vs-standard-report", sd, docs, sd == docs,
                      "стандартната справка за стратегическите документи има други записи от пълната"))
    return out


def build(conn, st, run_id, now=None, force=False):
    """Read every resource of the set from the archive into silver, then compare the totals. -> list of problems."""
    now = now or archive.dt.datetime.now(archive.dt.timezone.utc)
    st["archive_last_ok"] = archive.check_fresh(now).isoformat()
    archive.listing()    # the listing must be there and be a list: it is what /sources shows
    extras, problems, report = {}, [], []
    for r in RESOURCES:
        try:
            res = read(conn, r, extras, now, force, run_id)
        except ReconError as e:
            db.state(conn, SOURCE, r.id, status="неуспешно", error=str(e)[:500], role=r.role)
            res = {"ref": r.id, "name": r.name, "role": r.role, "status": "неуспешно", "error": str(e)}
        report.append(res)
        if res.get("error"):
            problems.append(f"Закони: {r.name}: {res['status']}: {res['error'][:300]}")
    st["resources"] = [{k: v for k, v in x.items() if k != "name"} for x in report]
    crosschecks(conn, run_id, extras)
    return problems

"""Reading the 14 resources of the data set from the archive into the landing zone, and the totals compared.

  load      the files that are loaded (acts of the Council of Ministers, consultations, strategic documents, contracts for
            impact assessments)
  check     reports whose numbers are compared with ours (they say their own totals)
  skip      read and counted, not loaded (publications, polls, advisory councils; the reason is in docs/sources.md)
  same      the 'legislative initiatives' report is, byte for byte, the strategic documents report (a fault of the source)

Every count is a result of a rule of db/checks.toml (ops.check_result). The count of a loaded file against the landing
zone stops the publication when it differs; the comparisons with the other reports of the source only inform.
"""
import json
from dataclasses import dataclass, field

from . import archive, parse, pipeline, store
from .config import ROOT
from .stream import ShapeError, records


class ReconError(RuntimeError):
    """A count does not agree with the source: nothing is published."""


@dataclass
class Res:
    id: str
    name: str
    role: str
    fn: object = None
    tables: list = field(default_factory=list)
    check: str = None
    same_as: str = None


RESOURCES = [
    Res("057c5c06-a11e-4e2d-8862-189c4d06e7b7", "Правна информация на Министерския съвет (ПРИС), действащи", "load",
        lambda it: parse.pris(it, "current"), ["pris_act", "pris_institution", "pris_tag", "pris_related"], "records_pris_current"),
    Res("e01f99a7-e1e0-4b5e-be8d-d5141a41e0f2", "Правна информация на Министерския съвет (ПРИС), архив", "load",
        lambda it: parse.pris(it, "archive"), ["pris_act", "pris_institution", "pris_tag", "pris_related"], "records_pris_archive"),
    Res("b600c109-9d30-4ba0-8463-9ed88a89f745", "Обществени консултации, обединена справка", "load",
        parse.consultation, ["consultation", "consultation_file"], "records_consultation"),
    Res("0a1d9485-a8fd-4e59-97f5-4063b5b72001", "Стратегически документи, пълна справка", "load", parse.strategy,
        ["strategy_doc", "strategy_author", "strategy_file", "strategy_sub"], "records_strategy"),
    Res("043ba0e4-18e1-4880-8a1f-ea739103dca4", "Договори за оценка на въздействието", "load", parse.impact, ["impact_contract"],
        "records_impact"),
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


def raw_file_id(conn, sha, r=None, ref=None, path=None):
    """The archive registers its files in ops.raw_file; this tool finds them there. A file the archive has not registered
    yet is registered here, from its place in the archive (nothing is copied)."""
    row = conn.execute("SELECT raw_file_id FROM ops.raw_file WHERE sha256 = %s", (sha,)).fetchone()
    if row or path is None:
        return row[0] if row else None
    return pipeline.register_raw(conn, r, store.SOURCE, ref, path, media_type="application/json", expect_sha=sha)[0].raw_file_id


def read(conn, r, res, extras, now, pick, force=False, hold_when=None):
    """Read one resource from the archive. -> a dict for the report; problems are in 'error'."""
    out = {"ref": res.id, "name": res.name, "role": res.role}
    try:
        path, sha, size, version, _ = pick(res.id)
    except archive.ArchiveError as e:
        status = "липсва при източника" if "липсва" in str(e) else "грешка при четене"
        store.state(conn, res.id, at=now, role=res.role, status=status, error=str(e)[:500])
        return {**out, "status": status, "error": str(e)}
    out.update(sha256=sha, bytes=size, version=version)
    common = dict(at=now, role=res.role, sha256=sha, version=version)
    try:
        if res.role == "same":
            other = pick(res.same_as)[1]
            status = "същият файл като " + BY_ID[res.same_as].name if other == sha else "собствени данни, не се зареждат"
            store.state(conn, res.id, **common, status=status, error=None, last_ok="now", rows=None)
            return {**out, "status": status, "same": other == sha}
        if res.role == "load":
            return {**out, **_load(conn, r, res, path, sha, now, force, hold_when, common)}
        parsed = res.fn(records(path))
        n = parsed if isinstance(parsed, int) else len(parsed)
        extras[res.id] = parsed
        store.state(conn, res.id, **common, status="наред", error=None, last_ok="now", rows=n)
        return {**out, "status": "наред", "rows": n}
    except (ShapeError, pipeline.ShapeError) as e:
        pipeline.reject(conn, r, store.SOURCE, res.id, e, raw_file_id(conn, sha), now=now)
        store.state(conn, res.id, **common, status="невалиден отговор", error=str(e)[:500])
        return {**out, "status": "невалиден отговор", "error": str(e)}


def _load(conn, r, res, path, sha, now, force, hold_when, common):
    parent = res.tables[0]
    fid = raw_file_id(conn, sha, r, res.id, path)
    done = None if force or fid is None else store.same_file(conn, r, res.id, parent, fid, now)
    if done is not None:
        # the very file of the current load: nothing to parse; what we hold is still counted against what we read then
        want = conn.execute("SELECT rows FROM ops.load WHERE load_id = %s", (done.load_id,)).fetchone()[0]
        held = store.held_count(conn, res.id, parent)
        if not pipeline.record_check(conn, r, res.check, want, held, res.name):
            raise ReconError(f"{res.name}: четохме {want} записа, в приемната зона са {held}")
        store.state(conn, res.id, **common, status="наред", error=None, last_ok="now", rows=held)
        return {"status": "наред", "result": "unchanged", "rows": held}
    parsed = res.fn(records(path))
    n_parent = len(parsed.tables.get(parent, []))
    folded = parsed.notes.get("folded", 0)
    if parsed.count != n_parent + folded:
        raise ShapeError(f"{res.name}: във файла са {parsed.count} записа, а след разбора {n_parent} (+{folded} повторени)")
    done = store.apply(conn, r, res.id, parsed, res.tables, fid, now, hold_when)
    result = store.STATUS[done.status]
    if result == "held":
        reason = conn.execute("SELECT reason FROM ops.hold WHERE tool = %s AND source = %s AND ref = %s",
                              (r.tool, store.SOURCE, res.id)).fetchone()
        store.state(conn, res.id, **common, status="задържан до второ четене", error=reason[0] if reason else None)
        return {"status": "задържан до второ четене", "result": result, "rows": parsed.count}
    held = store.held_count(conn, res.id, parent)
    if not pipeline.record_check(conn, r, res.check, parsed.count, held + folded, res.name):
        raise ReconError(f"{res.name}: след записа са {held} записа вместо {n_parent}")
    store.state(conn, res.id, **common, status="наред", error=None, last_ok="now", rows=n_parent,
                **({"last_change": "now"} if result == "stored" else {}))
    return {"status": "наред", "result": result, "rows": n_parent, "folded": folded, **parsed.notes}


def _current(conn, table, where="TRUE"):
    return conn.execute(f"""SELECT count(*) FROM zakoni_src.{table} WHERE {where} AND load_id IN
        (SELECT load_id FROM ops.load WHERE tool = 'zakoni' AND is_current)""").fetchone()[0]


def crosschecks(conn, r, extras):
    """The totals of the source's other reports against ours (STANDARD 1Б: when the source has no total for a file, its own
    other reports are the counters). A difference that is in the source itself and is recorded in db/ref/known_gaps.json
    is expected; any other difference is information. None of them stops the publication."""
    gaps = known_gaps()
    total = _current(conn, "consultation")
    named = _current(conn, "consultation", "institution_name IS NOT NULL")
    inst = extras.get("210747d1-9840-4345-939a-2338fda61031")
    if inst is not None:
        s = sum(x["pc_cnt"] for x in inst)
        g = gaps["consultations_vs_institution_report"]
        pipeline.record_check(conn, r, "consultations_vs_institution_report", s - g["delta"], named,
                              f"справката по институции има {s}, обединената {named} с посочена институция; "
                              f"разликата {s - named}, записана в известните ({g['delta']}): {g['evidence']}")
        less = _current(conn, "consultation", "institution_name IS NOT NULL AND date_close - date_open < 30")
        pipeline.record_check(conn, r, "short_term_vs_institution_report", sum(x["less_days_cnt"] for x in inst), less)
    area = extras.get("403043ed-7b82-477d-bb0f-ec7e0db996e9")
    if area is not None:
        pipeline.record_check(conn, r, "consultations_vs_area_report", sum(x["pc_cnt"] for x in area), total)
    std = extras.get("e935536f-db99-45a2-a024-d55631d873e1")
    if std is not None:
        pipeline.record_check(conn, r, "consultations_vs_standard_report", len(std), total)
    sd = extras.get("357d8f6d-7839-4ab9-9c9d-7510515ce9f5")
    if sd is not None:
        pipeline.record_check(conn, r, "strategy_docs_vs_standard_report", sd, _current(conn, "strategy_doc"))


def read_all(conn, r, now, pick, force=False, hold_when=None):
    """Every resource of the set, then the comparisons with the other reports. -> list of problems."""
    extras, problems, report = {}, [], []
    for res in RESOURCES:
        try:
            out = read(conn, r, res, extras, now, pick, force, hold_when)
        except ReconError as e:
            store.state(conn, res.id, at=now, role=res.role, status="неуспешно", error=str(e)[:500])
            out = {"ref": res.id, "name": res.name, "role": res.role, "status": "неуспешно", "error": str(e)}
        report.append(out)
        if out.get("error"):
            problems.append(f"Закони: {res.name}: {out['status']}: {out['error'][:300]}")
    r.stats["resources"] = [{k: v for k, v in x.items() if k != "name"} for x in report]
    crosschecks(conn, r, extras)
    return problems

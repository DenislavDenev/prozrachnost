"""The reconciliations of the tables and the freshness of the sources.

Reconciliations work on sheet blocks (read from a file or from silver) and return Results. A *gating* result that differs
stops the publication of the table to gold (the previous gold stays). A non-gating result that differs is information: the
difference is in the original, it is named with its resource, and the source's numbers are never corrected.

  structures_sum      the structures add up to the total row (oblasts and general directorates)        gating
  types_vs_structures the total row by crime types is the total row by structures                      gating
  blocks_vs_structures each structure's block total is its row by structures; the blocks and the
                      directorates add up to the country's block                                       gating
  clearance_formula   the printed share of solved is solved / registered x 100 (to 0.01)               information
  solved_le_registered solved crimes of the year's registrations do not exceed the registered          information
  point_sums          the total row against the sum of the main points, and each point against the
                      sum of its sub-points ("в това число" parts are never added to the main points)  information
"""
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from . import archive, config, templates

OK, DIFFERS, SOURCE = "ok", "differs", "source"
TOL = Decimal("0.011")


@dataclass
class Result:
    check_id: str
    scope: str
    expected: Decimal | None
    got: Decimal | None
    status: str
    gating: bool
    detail: str = ""

    @property
    def diff(self):
        return None if self.expected is None or self.got is None else self.got - self.expected


def known_differences():
    """db/ref/source_differences.csv: a difference that is in the original, proven by a resource and the exact numbers of the
    check. Such a result is published and marked `source` (the check is not switched off: any other number is still a failure)."""
    import csv
    with open(config.ROOT / "db/ref/source_differences.csv", encoding="utf-8", newline="") as f:
        return {(r["resource_uri"], r["check_id"], r["scope"], Decimal(r["expected"] or "NaN"), Decimal(r["got"] or "NaN")): r["note"] for r in csv.DictReader(f)}


def isolated():
    """db/ref/isolated.csv: tables kept in silver and left out of gold, each with the reason."""
    import csv
    with open(config.ROOT / "db/ref/isolated.csv", encoding="utf-8", newline="") as f:
        return {r["resource_uri"]: r for r in csv.DictReader(f)}


def apply_known(results, resource_uri, known):
    for r in results:
        if r.status == DIFFERS and r.expected is not None and r.got is not None:
            note = known.get((resource_uri, r.check_id, r.scope, r.expected, r.got))
            if note:
                r.status, r.detail = SOURCE, "разлика в оригинала: " + note
    return results


def _res(check_id, scope, expected, got, gating, detail=""):
    return Result(check_id, scope, expected, got, OK if expected == got else (DIFFERS if gating else SOURCE), gating, detail)


def col_of(block, ind):
    """The column of the block whose label means `ind` (the position differs between the Ministry's templates)."""
    return next(c for c, lab in block.columns if templates.classify(lab) == ind)


def value(row, col):
    for c in row.cells:
        if c[0] == col:
            return c[2]
    return None


def total_row(block):
    return next((r for r in block.rows if r.total), None)


def structures_sum(block, fam="structures"):
    """The structures (oblasts, general directorates) add up to the total row, column by column, for the counts."""
    tot = total_row(block)
    if tot is None:
        return [Result("structures_sum", "total", None, None, DIFFERS, True, "няма ред Общ брой")]
    out = []
    for ind in ("reg", "reg_unknown", "solved", "solved_unknown"):
        c = col_of(block, ind)
        got = sum((value(r, c) or 0 for r in block.rows if not r.total), Decimal(0))
        out.append(_res("structures_sum", ind, value(tot, c), got, True))
    return out


def types_vs_structures(types_block, structures_block):
    """The total row of the table by crime types is the total row of the table by structures."""
    a, b = total_row(types_block), total_row(structures_block)
    out = []
    for ind in ("reg", "reg_unknown", "solved", "solved_unknown"):
        out.append(_res("types_vs_structures", ind, value(b, col_of(structures_block, ind)), value(a, col_of(types_block, ind)), True))
    return out


def blocks_vs_structures(blocks, structures_block, resolve, fam="types_by_structure"):
    """`blocks` are the blocks of a table structure after structure; `resolve(name)` gives the code of a structure or None.
    Each oblast's block total (registered, solved) is that oblast's row by structures. The oblast blocks and the general
    directorates (which have no block) add up to the block of the whole country, and that is the total by structures."""
    out = []
    rows = {resolve(r.text): r for r in structures_block.rows if resolve(r.text)}
    by = {}
    for b in blocks:
        code = resolve(b.structure)
        if code is None:
            out.append(Result("blocks_vs_structures", b.structure, None, None, DIFFERS, True, "непозната структура"))
            continue
        by[code] = b
    for ind, sind in (("reg", "reg"), ("solved", "solved")):
        c, sc = col_of(blocks[0], ind), col_of(structures_block, sind)
        oblast_sum = Decimal(0)
        for code, b in by.items():
            t = total_row(b)
            if code == "BG":
                continue
            oblast_sum += value(t, c) or 0
            row = rows.get(code)
            out.append(_res("blocks_vs_structures", f"{ind}:{code}", value(row, sc) if row else None, value(t, c), True))
        if "BG" in by:
            gd = sum((value(r, sc) or 0 for code, r in rows.items() if code.startswith("GD-") and code not in by), Decimal(0))
            national = value(total_row(by["BG"]), c)
            out.append(_res("blocks_vs_structures", f"{ind}:BG", national, oblast_sum + gd, True, "областите и ГД (с блок или само в таблицата по структури)"))
            tot = total_row(structures_block)
            out.append(_res("blocks_vs_structures", f"{ind}:total", value(tot, sc), national, True))
    return out


def _rows(blocks):
    return [r for b in blocks for r in b.rows]


def clearance_formula(blocks, fam):
    """The printed share is solved / registered x 100 to 0.01; counted over all rows with both numbers."""
    cr, cs, cc = col_of(blocks[0], "reg"), col_of(blocks[0], "solved"), col_of(blocks[0], "clearance")
    bad = n = 0
    for r in _rows(blocks):
        reg, sol, cl = value(r, cr), value(r, cs), value(r, cc)
        if reg is None or sol is None:
            continue
        n += 1
        if reg > 0 and cl is not None and abs(sol * 100 / reg - cl) > TOL:
            bad += 1
        elif reg == 0 and cl not in (None, 0):
            bad += 1
    return Result("clearance_formula", f"{n} реда", Decimal(0), Decimal(bad), OK if not bad else SOURCE, False)


def solved_le_registered(blocks, fam):
    cr, cs = col_of(blocks[0], "reg"), col_of(blocks[0], "solved")
    n = bad = 0
    for r in _rows(blocks):
        reg, sol = value(r, cr), value(r, cs)
        if reg is None or sol is None:
            continue
        n += 1
        bad += sol > reg
    return Result("solved_le_registered", f"{n} реда", Decimal(0), Decimal(bad), OK if not bad else SOURCE, False)


def point_sums(block, fam):
    """Information about the original's arithmetic: the total row against the sum of the main points (rows with a printed
    number of one level, "1." "2." ...), and each main point against the sum of its sub-points ("1.1." ...). The "·" and "-"
    rows are "в това число" parts and are never added."""
    cr = col_of(block, "reg")
    out = []
    tot = total_row(block)
    points = [r for r in block.rows if r.level == 1 and r.code]
    if tot is not None and points:
        out.append(Result("point_sums", "total:points", value(tot, cr), sum((value(r, cr) or 0 for r in points), Decimal(0)),
                          SOURCE, False, "общият ред срещу сбора на точките 1., 2., ..."))
        if out[-1].expected == out[-1].got:
            out[-1].status = OK
    cur, kids = None, []
    groups = []
    for r in block.rows:
        if r.level == 1 and r.code:
            if cur is not None:
                groups.append((cur, kids))
            cur, kids = r, []
        elif r.level == 2 and cur is not None:
            kids.append(r)
        elif r.total:
            continue
    if cur is not None:
        groups.append((cur, kids))
    for p, kids in groups:
        if kids:
            out.append(Result("point_sums", p.code, value(p, cr), sum((value(k, cr) or 0 for k in kids), Decimal(0)), SOURCE, False,
                              "точката срещу сбора на подточките"))
            if out[-1].expected == out[-1].got:
                out[-1].status = OK
    return out


# --- freshness -------------------------------------------------------------------------------------------------------

TEXT = "Сигурност: {}"


def freshness(c, now=None, st=None):
    """(problems, info) of the tool itself; the shared ones (holds past their time, failed rules and steps, a list not read)
    are pipeline.freshness(). Problems are what n8n reports; info is shown on /sources and is not a failure (a yearly set
    that is not yet published, a monthly bulletin that stopped, empty resources, cells with text)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    problems, info = [], []
    try:
        st = st or archive.state()
        age = archive.age_hours(st, now)
        if age > config.MAX_ARCHIVE_AGE_H:
            problems.append(TEXT.format(f"архивът не е четен успешно {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"))
    except archive.ArchiveError as e:
        problems.append(TEXT.format(str(e)))
    # the last read of a table of a police set (or the old one) was refused, and nothing read after it
    for uri, reason in c.execute(
            """SELECT x.ref, x.reason FROM (
                 SELECT DISTINCT ON (l.ref) l.ref, l.status, l.reason FROM ops.load l
                 WHERE l.tool = 'sigurnost' AND l.source = 'egov' AND l.status IN ('rejected', 'loaded', 'confirmed', 'unchanged')
                 ORDER BY l.ref, l.load_id DESC) x
               WHERE x.status = 'rejected'
                 AND EXISTS (SELECT 1 FROM sigurnost.dataset d JOIN sigurnost_src.list_entry e ON e.load_id = d.list_load_id
                             WHERE e.resource_uri = x.ref AND d.kind IN ('police', 'crime-old'))
               ORDER BY x.ref"""):
        problems.append(TEXT.format(f"{uri} е невалиден: {reason}"))
    from . import gold      # the table that stands for each (year, family) is the one the clean tables take
    for year, fam in c.execute("""SELECT DISTINCT d.year, r.family FROM sigurnost_src.current_resource r JOIN sigurnost.dataset d USING (set_uri)
                                  WHERE r.family IS NOT NULL AND r.kind = 'table' AND d.kind = 'police'
                                  ORDER BY 1, 2""").fetchall():
        cand = gold.candidates(c, year, fam)
        if not cand:
            continue          # isolated with a reason (db/ref/isolated.csv)
        pub = c.execute("SELECT load_id FROM sigurnost.source_table WHERE year = %s AND family = %s", (year, fam)).fetchone()
        if pub is None or pub[0] != cand[0][7]:
            problems.append(TEXT.format(f"{year} {fam}: таблицата не е публикувана (сверката не е минала)"))
    n_un = c.execute("SELECT count(*) FROM sigurnost.unmatched").fetchone()[0]
    if n_un:
        problems.append(TEXT.format(f"{n_un} структури не са в списъка (sigurnost.unmatched)"))
    newest = c.execute("""SELECT max(d.year) FROM sigurnost.dataset d WHERE d.kind = 'police'
                          AND EXISTS (SELECT 1 FROM sigurnost_src.current_resource r WHERE r.set_uri = d.set_uri)""").fetchone()[0]
    if newest is None:
        problems.append(TEXT.format("няма прочетена полицейска статистика"))
    else:
        if newest < now.year - config.STALE_YEARS:
            problems.append(TEXT.format(f"Полицейска статистика: последната година е {newest}, а има {now.year - newest} години без нова"))
        elif newest < now.year - 1:
            info.append(f"Полицейска статистика за {newest + 1} още не е публикувана на портала (последната е за {newest}).")
    last_bulletin = c.execute("SELECT max(period) FROM sigurnost.link").fetchone()[0]
    if last_bulletin and (now.date() - last_bulletin).days > 75:
        info.append(f"Последният месечен бюлетин на МВР е за {last_bulletin:%m.%Y}: няма нов над два месеца.")
    n_empty = c.execute("SELECT count(*) FROM sigurnost_src.current_resource WHERE kind = 'empty'").fetchone()[0]
    if n_empty:
        info.append(f"{n_empty} ресурса на портала са без таблица (празен отговор).")
    n_issue = c.execute("SELECT coalesce(sum(issues), 0) FROM sigurnost_src.current_resource WHERE kind = 'table'").fetchone()[0]
    if n_issue:
        info.append(f"{n_issue} клетки в оригиналните таблици съдържат текст на мястото на число: стойността е „няма данни“, текстът е запазен.")
    return problems, info


def weekly(c):
    row = c.execute("""SELECT count(*), coalesce(sum(n_rows), 0) FROM sigurnost_src.current_resource""").fetchone()
    invalid = c.execute("SELECT count(DISTINCT (ref, raw_file_id)) FROM ops.load WHERE tool = 'sigurnost' AND source = 'egov' AND status = 'rejected'").fetchone()[0]
    pub = c.execute("SELECT count(*), min(year), max(year) FROM sigurnost.source_table").fetchone()
    return {"resources": row[0], "invalid": invalid, "rows": row[1], "gold_tables": pub[0], "from": pub[1], "to": pub[2]}

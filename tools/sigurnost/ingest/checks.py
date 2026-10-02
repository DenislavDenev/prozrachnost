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


def _res(check_id, scope, expected, got, gating, detail=""):
    return Result(check_id, scope, expected, got, OK if expected == got else (DIFFERS if gating else SOURCE), gating, detail)


def col_of(fam, ind):
    return next(c for c, i in templates.SPECS[fam][0] if i == ind)


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
        c = col_of(fam, ind)
        got = sum((value(r, c) or 0 for r in block.rows if not r.total), Decimal(0))
        out.append(_res("structures_sum", ind, value(tot, c), got, True))
    return out


def types_vs_structures(types_block, structures_block):
    """The total row of the table by crime types is the total row of the table by structures."""
    a, b = total_row(types_block), total_row(structures_block)
    out = []
    for ind in ("reg", "reg_unknown", "solved", "solved_unknown"):
        out.append(_res("types_vs_structures", ind, value(b, col_of("structures", ind)), value(a, col_of("types", ind)), True))
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
        c, sc = col_of(fam, ind), col_of("structures", sind)
        oblast_sum = Decimal(0)
        for code, b in by.items():
            t = total_row(b)
            if code == "BG":
                continue
            oblast_sum += value(t, c) or 0
            row = rows.get(code)
            out.append(_res("blocks_vs_structures", f"{ind}:{code}", value(row, sc) if row else None, value(t, c), True))
        if "BG" in by:
            gd = sum((value(r, sc) or 0 for code, r in rows.items() if code.startswith("GD-")), Decimal(0))
            national = value(total_row(by["BG"]), c)
            out.append(_res("blocks_vs_structures", f"{ind}:BG", national, oblast_sum + gd, True, "областите и ГД без блок"))
            tot = total_row(structures_block)
            out.append(_res("blocks_vs_structures", f"{ind}:total", value(tot, sc), national, True))
    return out


def _rows(blocks):
    return [r for b in blocks for r in b.rows]


def clearance_formula(blocks, fam):
    """The printed share is solved / registered x 100 to 0.01; counted over all rows with both numbers."""
    cr, cs, cc = col_of(fam, "reg"), col_of(fam, "solved"), col_of(fam, "clearance")
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
    cr, cs = col_of(fam, "reg"), col_of(fam, "solved")
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
    cr = col_of(fam, "reg")
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
    """(problems, info). Problems are what n8n reports; info is shown on /sources and is not a failure (a yearly set that is
    not yet published, a monthly bulletin that stopped in 2020, empty resources, cells with text)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    problems, info = [], []
    try:
        st = st or archive.state()
        age = archive.age_hours(st, now)
        if age > config.MAX_ARCHIVE_AGE_H:
            problems.append(TEXT.format(f"архивът не е четен успешно {age:.0f} ч (над {config.MAX_ARCHIVE_AGE_H} ч)"))
    except archive.ArchiveError as e:
        problems.append(TEXT.format(str(e)))
    for ref, first, reason in c.execute("SELECT ref, first_seen, reason FROM ops.held"):
        if now - first > dt.timedelta(days=1):
            problems.append(TEXT.format(f"{ref} е задържан над ден: {reason}"))
    for uri, note, name in c.execute(
            """SELECT r.resource_uri, r.note, r.name FROM silver.resource r
               WHERE r.status = 'invalid' AND NOT EXISTS (SELECT 1 FROM silver.resource q WHERE q.resource_uri = r.resource_uri
                     AND q.status = 'built' AND q.read_at > r.read_at)"""):
        problems.append(TEXT.format(f"{uri} е невалиден: {note}"))
    from . import gold      # the table that stands for each (year, family) is the one gold picks
    for year, fam in c.execute("""SELECT DISTINCT d.year, r.family FROM silver.resource r JOIN silver.dataset d USING (set_uri)
                                  WHERE r.family IS NOT NULL AND r.is_current AND r.status = 'built' AND d.kind = 'police'
                                  ORDER BY 1, 2""").fetchall():
        top = gold.candidates(c, year, fam)[0]
        pub = c.execute("SELECT resource_uri, sha256 FROM gold.source_table WHERE year = %s AND family = %s", (year, fam)).fetchone()
        if pub != (top[0], top[1]):
            problems.append(TEXT.format(f"{year} {fam}: таблицата не е публикувана (сверката не е минала)"))
    n_un = c.execute("SELECT count(*) FROM gold.unmatched").fetchone()[0]
    if n_un:
        problems.append(TEXT.format(f"{n_un} структури не са в списъка (gold.unmatched)"))
    newest = c.execute("SELECT max(d.year) FROM silver.dataset d WHERE d.kind = 'police' AND EXISTS (SELECT 1 FROM silver.resource r WHERE r.set_uri = d.set_uri AND r.is_current)").fetchone()[0]
    if newest is None:
        problems.append(TEXT.format("няма прочетена полицейска статистика"))
    else:
        if newest < now.year - config.STALE_YEARS:
            problems.append(TEXT.format(f"Полицейска статистика: последната година е {newest}, а има {now.year - newest} години без нова"))
        elif newest < now.year - 1:
            info.append(f"Полицейска статистика за {newest + 1} още не е публикувана на портала (последната е за {newest}).")
    last_bulletin = c.execute("SELECT max(source_updated) FROM silver.dataset WHERE kind = 'bulletin'").fetchone()[0]
    if last_bulletin and (now.date() - last_bulletin).days > 60:
        info.append(f"Месечният бюлетин на МВР не е обновяван от {last_bulletin}: източникът е спрял.")
    n_empty = c.execute("SELECT count(*) FROM silver.resource WHERE kind = 'empty' AND is_current").fetchone()[0]
    if n_empty:
        info.append(f"{n_empty} ресурса на портала са без таблица (празен отговор).")
    n_issue = c.execute("SELECT coalesce(sum(issues), 0) FROM silver.resource WHERE is_current AND kind = 'table'").fetchone()[0]
    if n_issue:
        info.append(f"{n_issue} клетки в оригиналните таблици съдържат текст на мястото на число: стойността е „няма данни“, текстът е запазен.")
    return problems, info


def weekly(c):
    row = c.execute("""SELECT count(*) FILTER (WHERE is_current AND status = 'built'), count(*) FILTER (WHERE status = 'invalid'),
                              coalesce(sum(n_rows) FILTER (WHERE is_current AND status = 'built'), 0) FROM silver.resource""").fetchone()
    pub = c.execute("SELECT count(*), min(year), max(year) FROM gold.source_table").fetchone()
    return {"resources": row[0], "invalid": row[1], "rows": row[2], "gold_tables": pub[0], "from": pub[1], "to": pub[2]}

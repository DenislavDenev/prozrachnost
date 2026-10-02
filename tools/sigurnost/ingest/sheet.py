"""Reads one resource of the Ministry of the Interior: a table of the "Excel sheet" kind, as data.egov.bg serves it.

The answer is {"success": true, "data": [[cell, ...], ...]}: every cell is a text, merged headers are empty strings,
the header takes several rows (words are cut with a hyphen across them), numbers are texts. All 25 resources of one
"Полицейска статистика" set have the same name, so the kind of a table is found from its header rows, never from the
name. The first column holds the number of the row ("4.7.", "31."), the second its label; the rest are the values.

The parser returns blocks. A sheet has one block; the two long sheets "by crime type, structure after structure"
repeat a header and its rows for each structure (the structure's name stands in the second column of the header).
Anything the parser does not recognise is a ShapeError: the import stops and writes nothing.
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

NUM = re.compile(r"^-?\d+(\.\d+)?$")
BANNER = re.compile(r"^\s*Полицейска статистика\b")
MARK = re.compile(r"^[·\-–]\s*")
SPACES = re.compile(r"\s+")
DIMENSION = re.compile(r"^(Видове престъпления|Области|ПРЕСТЪПЛЕНИЯ|НАКАЗУЕМИ ДЕЯНИЯ|\(вкл)", re.I)   # words of the second column's heading


class ShapeError(Exception):
    """The answer is not a table of the known kind: nothing is written."""


@dataclass
class Row:
    no: int                      # position in its block, from 1
    code: str                    # the printed number ("4.7."), "" when the row has none
    label: str                   # the label as printed, markers and all
    text: str                    # the label without the marker
    marker: str                  # "", "·" or "-"
    level: int                   # 1 a point ("1."), 2 a sub-point ("1.1."), 3 "·", 4 "-"; 0 a total row
    cells: list                  # [(col_no, text, Decimal | None, issue)] for the value columns
    total: bool = False


@dataclass
class Block:
    structure: str               # the name in the header of a structure block, else ""
    dimension: str               # the label of the second column in a one-block sheet ("Области", "Видове престъпления")
    columns: list                # [(col_no, label)] of the value columns
    rows: list
    header: list                 # the header rows as printed
    first_row: int               # the position of the block's first data row in the sheet


@dataclass
class Sheet:
    kind: str                    # "table" | "empty"
    title: str                   # the leading lines with at most two texts
    blocks: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # the lines after the last data row
    n_rows: int = 0
    n_cols: int = 0
    note: str = ""
    issues: int = 0              # cells that hold a text where a number belongs


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def clean(s):
    return SPACES.sub(" ", s.replace("\xa0", " ")).strip()


def load(raw):
    """The grid of texts from the bytes of the answer; ShapeError for anything else (HTML, truncated JSON, wrong type)."""
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ShapeError("Отговорът не е JSON: " + str(e)[:80]) from e
    if d == {"success": True}:
        return []           # the portal's answer for a resource that holds no table
    if not isinstance(d, dict) or d.get("success") is not True or not isinstance(d.get("data"), list):
        raise ShapeError("Отговорът няма вида {success, data}")
    grid = []
    for i, r in enumerate(d["data"]):
        if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
            raise ShapeError(f"Ред {i} не е списък от текстове")
        grid.append([clean(x) for x in r])
    if grid and len({len(r) for r in grid}) != 1:
        raise ShapeError("Редовете са с различна дължина: " + str(sorted({len(r) for r in grid})))
    return grid


def _is_data(r):
    return any(NUM.match(x) for x in r[2:])


def _join(parts):
    """Words cut with a hyphen across header rows are joined back: "регист-" + "рирани" is "регистрирани"."""
    out = ""
    for p in parts:
        if not out:
            out = p
        elif out.endswith("-") and not out.endswith(" -"):
            out = out[:-1] + p
        else:
            out += " " + p
    return out


def _label_row(r):
    code, label = r[0], r[1]
    marker = ""
    m = MARK.match(label)
    if m:
        marker = m.group(0).strip()
        text = label[m.end():].strip()
    else:
        text = label
    return code, label, text, marker


def _level(code, marker, total):
    if total:
        return 0
    if marker == "·":
        return 3
    if marker in ("-", "–"):
        return 4
    n = len([x for x in code.replace(",", ".").split(".") if x])
    return 1 if n <= 1 else 2


def _cell(t):
    """(value, issue). An empty cell is no value and no issue; a text that is not a number (a dash, a stray letter) is
    no value and an issue: the text stays in silver and the table's checks decide whether it can be published."""
    if t in ("", "-", "–"):     # a dash stands where the share is undefined (nothing registered): no value, not a zero
        return None, False
    if NUM.match(t):
        return Decimal(t), False
    return None, True


def parse(raw):
    grid = load(raw)
    if not grid or all(not any(r) for r in grid):
        return Sheet("empty", "", n_rows=len(grid), n_cols=len(grid[0]) if grid else 0, note="всички клетки са празни")
    ncols = len(grid[0])
    if ncols < 3:
        if all(not re.sub(r'[\s,"]', "", c) for r in grid for c in r):
            return Sheet("empty", "", n_rows=len(grid), n_cols=ncols, note="само разделители")
        raise ShapeError("Таблица с по-малко от три колони")
    # runs of rows: header rows (no number in the value columns) and data rows
    runs, cur = [], None
    for i, r in enumerate(grid):
        if not any(r):
            continue
        kind = "d" if _is_data(r) else "h"
        if cur is None or cur[0] != kind:
            cur = [kind, []]
            runs.append(cur)
        cur[1].append(i)
    if not runs or runs[0][0] != "h":
        raise ShapeError("Таблицата започва без заглавие")
    sheet = Sheet("table", "", n_rows=len(grid), n_cols=ncols)
    pending, pos = None, 0
    for kind, idx in runs:
        if kind == "h":
            pending = idx
            continue
        if pending is None:
            raise ShapeError("Данни без заглавие пред тях")
        sheet.blocks.append(_block(grid, pending, idx, pos, sheet))
        pos += len(idx)
        pending = None
    if pending:     # after the last data row: footnotes
        sheet.notes = [" ".join(x for x in grid[i] if x) for i in pending]
    if not sheet.blocks:
        raise ShapeError("Таблица без числови редове")
    cells = sum(len(r.cells) for b in sheet.blocks for r in b.rows)
    bad = sum(1 for b in sheet.blocks for r in b.rows for c in r.cells if c[3])
    if bad * 50 > cells:
        raise ShapeError(f"{bad} от {cells} клетки не са числа: това не е таблица с числа")
    sheet.issues = bad
    _same_columns(sheet)
    return sheet


def _block(grid, head, data, pos, sheet):
    head = [i for i in head if not (BANNER.match(grid[i][0]) and sum(1 for x in grid[i] if x) == 1)]
    texts = [x for i in head for x in grid[i] if x]
    start = next((k for k, i in enumerate(head) if sum(1 for x in grid[i] if x) >= 3), None)
    ncols = len(grid[0])
    col1 = [grid[i][1] for i in head if grid[i][1]]
    structure = clean(next((x for x in col1 if not DIMENSION.match(x)), ""))
    dimension = _join([x for x in col1 if DIMENSION.match(x)])
    if start is None:
        # a later block of a sheet whose header is printed once: only the structure's name stands above its rows
        if not sheet.blocks or len(texts) > 2 or not structure:
            raise ShapeError("Заглавие без редове с колони")
        cols = list(sheet.blocks[0].columns)
        hrows = head
    else:
        if not sheet.blocks:
            sheet.title = " ".join(" ".join(x for x in grid[i] if x) for i in head[:start])
        hrows = head[start:]
        cols = [(c, _join([grid[i][c] for i in hrows if grid[i][c]])) for c in range(2, ncols)]
    rows = []
    for n, i in enumerate(data, 1):
        r = grid[i]
        code, label, text, marker = _label_row(r)
        if not label and code and not NUM.match(code.rstrip(".")) and "." not in code:
            code, label, text = "", code, code        # "ОБЩ БРОЙ" stands in the first column of some tables
        total = not code and text.lower() in ("общ брой", "общо", "общо за страната")
        if not label:       # the source lost the label of this row: kept, marked, and the checks decide
            label = text = "(без етикет)"
        rows.append(Row(pos + n, code, label, text, marker, _level(code, marker, total),
                        [(c, r[c], *_cell(r[c])) for c in range(2, ncols)], total))
    return Block(structure, dimension, cols, rows, [grid[i] for i in hrows], pos + 1)


def _same_columns(sheet):
    """Blocks of one sheet have the same value columns (the structure's name is not among them)."""
    first = [lab for _, lab in sheet.blocks[0].columns]
    for b in sheet.blocks[1:]:
        if [lab for _, lab in b.columns] != first:
            raise ShapeError("Блоковете на листа са с различни колони")
    if len(sheet.blocks) == 1:
        sheet.blocks[0].structure = ""
    else:
        for b in sheet.blocks:
            b.dimension = ""

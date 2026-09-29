"""Verified layouts of the school and kindergarten register with NEISPUO codes."""

import re

from .parse import ShapeError
from .sources import School, _payload


HEADER = ("№", "Област", "Община", "Населено място", "Код по НЕИСПУО",
          "Име на училище/детска градина")
NUMBERING = ("", "1", "2", "3", "4", "5")


def _cells(row):
    if not isinstance(row, list) or len(row) != 6 or any(not isinstance(v, str) for v in row):
        raise ShapeError("Changed historical register row shape")
    return tuple(value.replace("\ufeff", "").strip() for value in row)


def parse_schools_year(raw: bytes) -> dict[str, School]:
    """Parse the observed 2021–2025 tabular register; reject code-free layouts."""
    value = _payload(raw, "historical school register")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list):
        raise ShapeError("Unknown historical school register")
    rows = [_cells(row) for row in value["data"]]
    if not rows:
        raise ShapeError("Empty historical school register")
    offset = 0
    if rows[0][0].startswith("Списък на училищата и детските градини") and all(not v for v in rows[0][1:]):
        offset = 1
    if len(rows) <= offset + 1 or rows[offset] != HEADER:
        raise ShapeError("Unknown historical school register columns")
    offset += 1
    if rows[offset] == NUMBERING:
        offset += 1
    result = {}
    for number, row in enumerate(rows[offset:], offset + 1):
        ordinal, oblast, municipality, town, code, name = row
        if not ordinal.isdecimal() or any(not v for v in (oblast, municipality, town, name)):
            raise ShapeError(f"Register row {number}: missing school identity")
        if not re.fullmatch(r"\d{6,7}", code) or code in result:
            raise ShapeError(f"Register row {number}: invalid or duplicate NEISPUO code")
        result[code] = School(code, name, oblast, municipality, town)
    if not result:
        raise ShapeError("Empty historical school register")
    return result


CODE_FREE_HEADERS = {
    "2017/2018": ("№", "Област", "Община", "Населено място", "Име на училище/детска градина", "", "", "", ""),
    "2018/2019": ("№", "Обаст", "Община", "Населено място", "Име на училище/детска градина"),
    "2019/2020": ("№", "Област", "Община", "Населено място", "Име на училище/детска градина"),
    "2020/2021": ("№", "Област", "Община", "Населено място", "Име на училище/детска градина"),
}


def parse_code_free_register(raw: bytes, year: str) -> int:
    """Validate the matching year's official table and confirm it has no code column."""
    if year not in CODE_FREE_HEADERS:
        raise ShapeError("No observed code-free register for this year")
    value = _payload(raw, "code-free school register")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list):
        raise ShapeError("Unknown code-free school register")
    width = len(CODE_FREE_HEADERS[year])
    rows = []
    for row in value["data"]:
        if not isinstance(row, list) or len(row) != width or any(not isinstance(v, str) for v in row):
            raise ShapeError("Changed code-free register row shape")
        cleaned = tuple(v.replace("\ufeff", "").strip() for v in row)
        rows.append((cleaned[0].strip('"'), *cleaned[1:]))
    header_at = 1 if year in {"2017/2018", "2019/2020"} else 0
    if len(rows) <= header_at + 1 or rows[header_at] != CODE_FREE_HEADERS[year]:
        raise ShapeError("Unknown code-free register columns")
    start = header_at + 1
    if year == "2017/2018":
        if rows[start] != ("", "1", "2", "3", "4", "", "", "", ""):
            raise ShapeError("Unknown register numbering row")
        start += 1
    if header_at and not rows[0][0].startswith("Списък на училищата и детските градини"):
        raise ShapeError("Unknown register title")
    for number, row in enumerate(rows[start:], start + 1):
        # The 2018 source has one malformed CSV quotation merging records 1058 and 1059.
        if (year == "2017/2018" and row[0] == "1058" and row[4].strip('"').endswith("\n1059")
                and row[5:8] == ("Добрич", "Балчик", "Сенокос") and row[8]):
            continue
        if not row[0].isdecimal() or any(not item for item in row[1:5]) or any(row[5:]):
            raise ShapeError(f"Register row {number}: invalid code-free school identity")
    if not rows[start:]:
        raise ShapeError("Empty code-free register")
    return len(rows) - start

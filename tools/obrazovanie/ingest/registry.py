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

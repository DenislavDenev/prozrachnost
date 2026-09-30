"""Observed MON protected and central institution lists."""

import re
from dataclasses import dataclass

from .parse import ShapeError
from .sources import Resource, _payload


DATASETS = {
    "protected": "7b3c3cbb-e855-4fe7-93f6-fd68016570f8",
    "central": "ba5d0920-e634-4519-8914-b592a23d6d5c",
}

# Only code-bearing publications can be linked to a school profile. The
# 2021/22 central and 2022/23 protected lists have no NEISPUO column.
LAYOUTS = {
    "57eeeae0-75ed-491b-84a8-2f0dafb6dc8d": ("protected", "2021/2022", 1, 7, 1, 5, 4, 6, 266),
    "b6cb07d9-1c46-43dd-8288-4016e6549913": ("protected", "2023/2024", 2, 7, 5, 4, 3, 6, 264),
    "48768206-039e-4d57-930a-19fff2d6ecba": ("protected", "2024/2025", 4, 15, 5, 4, 3, 6, 270),
    "bfc18bd8-66b1-4be7-abd9-5292969d5dba": ("protected", "2025/2026", 3, 7, 5, 4, 3, 6, 278),
    "4744f5a8-f96c-44de-aed2-fe7018ce4b91": ("central", "2022/2023", 1, 5, 4, 3, 2, None, 957),
    "858bf86c-fa91-4bd3-9c6d-b45405749372": ("central", "2023/2024", 1, 6, 5, 4, 3, None, 956),
    "47a7b2af-1cb2-4a14-ad83-5cd12abde2ac": ("central", "2024/2025", 3, 6, 5, 4, 3, None, 944),
    "3d798ab3-4404-46d3-94f2-a3abbeda6c8f": ("central", "2025/2026", 2, 6, 5, 4, 3, None, 946),
}


@dataclass(frozen=True)
class StatusRow:
    number: int
    code: str
    name: str
    town: str
    scope: str | None
    is_school: bool


def status_catalog(raw: bytes, kind: str):
    payload = _payload(raw, "status catalog")
    rows = payload.get("resources")
    if (set(payload) != {"success", "resources", "total_records"} or not isinstance(rows, list)
            or len(rows) != payload["total_records"]):
        raise ShapeError("Incomplete status catalog")
    known = {uri for uri, layout in LAYOUTS.items() if layout[0] == kind}
    available = {r.get("uri") for r in rows if isinstance(r, dict)}
    if not known <= available:
        raise ShapeError(f"Missing known {kind} publication")
    unexpected = [r for r in rows if r.get("uri") not in known]
    # New releases must be inspected before publication; old no-code lists
    # are explicitly documented and cannot join to a school.
    skipped = {
        "protected": {"fcff5e41-8d28-48a6-a726-877cbb42d08f"},
        "central": {"1b5f0301-7d65-4fc0-a2f5-3d1134342324"},
    }[kind]
    if any(r.get("uri") not in skipped for r in unexpected):
        raise ShapeError(f"Unreviewed {kind} publication")
    result = []
    for row in rows:
        if row["uri"] not in known:
            continue
        year = LAYOUTS[row["uri"]][1]
        if (row.get("dataset_uri") != DATASETS[kind] or not isinstance(row.get("name"), str)
                or not isinstance(row.get("updated_at"), str) or year not in row["name"]):
            raise ShapeError("Changed status publication metadata")
        result.append(Resource(row["uri"], year, row["name"], row["updated_at"]))
    return result


def parse_status(raw: bytes, uri: str) -> list[StatusRow]:
    if uri not in LAYOUTS:
        raise ShapeError("Unreviewed status list layout")
    kind, year, start, width, code_col, name_col, town_col, scope_col, observed = LAYOUTS[uri]
    value = _payload(raw, "status list")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list):
        raise ShapeError("Unknown status list response")
    rows = value["data"]
    if len(rows) - start < observed * .8:
        raise ShapeError("Status list is much shorter than observed")
    header_index = {"2024/2025": 2, "2025/2026": 1}.get(year, 0)
    header = rows[header_index]
    if not isinstance(header, list) or "НЕИСПУО" not in str(header[code_col]):
        raise ShapeError("Status list NEISPUO column changed")
    parsed = []
    for number, row in enumerate(rows[start:], 1):
        if not isinstance(row, list) or len(row) != width or any(not isinstance(cell, str) for cell in row):
            raise ShapeError(f"Status list row {number}: changed columns")
        code, name, town = (row[col].strip() for col in (code_col, name_col, town_col))
        if not re.fullmatch(r"\d{6,7}", code) or not name or not town:
            raise ShapeError(f"Status list row {number}: invalid institution")
        scope = row[scope_col].strip() if scope_col is not None else None
        if scope_col is not None and not (scope.startswith("учениците") or scope.startswith("децата")):
            raise ShapeError(f"Status list row {number}: unknown scope")
        is_school = scope.startswith("учениците") if scope is not None else bool(re.search(r"училищ|гимназ", name, re.I))
        if scope is None and not (is_school or re.search(r"детска\s+градин", name, re.I)):
            raise ShapeError(f"Status list row {number}: unknown institution type")
        parsed.append(StatusRow(number, code, name, town, scope, is_school))
    return parsed

"""Versioned, strict parser for MON's NVO IV and X school results."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .parse import ExamResult, ShapeError
from .sources import Resource, _payload


DATASETS = {"nvo4": "5613e75f-2b1b-4244-9f54-b27580a91dfb",
            "nvo10": "2f801b2f-d4cb-4ddb-a23d-3e372339c80f"}
LAYOUTS = {row["uri"]: row for row in json.loads(
    (Path(__file__).parent / "nvo_layouts.json").read_text(encoding="utf-8"))}


def nvo_catalog(raw: bytes, exam: str) -> list[Resource]:
    if exam not in DATASETS:
        raise ShapeError("Unknown NVO exam")
    value = _payload(raw, f"{exam} catalog")
    rows = value.get("resources")
    expected = {uri for uri, layout in LAYOUTS.items() if layout["exam"] == exam}
    if (set(value) != {"success", "resources", "total_records"} or not isinstance(rows, list)
            or type(value["total_records"]) is not int or len(rows) != value["total_records"]
            or len(rows) != len(expected) or {row.get("uri") for row in rows if isinstance(row, dict)} != expected):
        raise ShapeError("Incomplete or changed NVO resource catalog")
    result = []
    for row in rows:
        layout = LAYOUTS[row["uri"]]
        name, updated = row.get("name"), row.get("updated_at")
        end_year = layout["year"][-4:]
        if (row.get("dataset_uri") != DATASETS[exam] or not isinstance(name, str)
                or not isinstance(updated, str) or not updated or end_year not in name
                or (exam == "nvo4" and not ("IV клас" in name or "4 клас" in name))
                or (exam == "nvo10" and "X клас" not in name)):
            raise ShapeError("Changed NVO resource metadata")
        result.append(Resource(row["uri"], layout["year"], name, updated))
    return sorted(result, key=lambda item: item.year)


@dataclass(frozen=True)
class NvoTable:
    exam: str
    year: str
    rows: int
    subjects: tuple[str, ...]
    results: list[ExamResult]
    codes: frozenset[str]


def parse_nvo(raw: bytes, resource_uri: str) -> NvoTable:
    layout = LAYOUTS.get(resource_uri)
    if layout is None:
        raise ShapeError("Unknown NVO resource layout")
    value = _payload(raw, "NVO resource")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list):
        raise ShapeError("Unknown NVO response")
    rows = value["data"]
    head, width = layout["header_rows"], layout["width"]
    if (len(rows) <= head
            or any(not isinstance(row, list) or len(row) != width or
                   any(not isinstance(cell, str) for cell in row) for row in rows)):
        raise ShapeError("Changed or incomplete NVO table")
    digest = hashlib.sha256(json.dumps(rows[:head], ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()
    if digest != layout["header_sha"]:
        raise ShapeError("Changed NVO columns or scale")
    results, seen = [], set()
    for number, row in enumerate(rows[head:], head + 1):
        oblast, municipality, town = (cell.strip() for cell in row[:3])
        school = row[layout["school_col"]].strip()
        code = re.sub(r"\s+", "", row[layout["code_col"]])
        if not all((oblast, municipality, town, school)):
            raise ShapeError(f"NVO row {number}: missing school identity")
        if not re.fullmatch(r"\d{6,7}", code) or code in seen:
            raise ShapeError(f"NVO row {number}: invalid or duplicate NEISPUO code")
        seen.add(code)
        for subject, count_col, score_col in layout["columns"]:
            count_raw, score_raw = row[count_col].strip(), row[score_col].strip()
            if count_raw and not re.fullmatch(r"\d+", count_raw):
                raise ShapeError(f"NVO row {number}: invalid taker count")
            if score_raw and not re.fullmatch(r"\d+(?:[,.]\d+)?", score_raw):
                raise ShapeError(f"NVO row {number}: invalid score")
            takers = int(count_raw) if count_raw else None
            score = Decimal(score_raw.replace(",", ".")) if score_raw else None
            if score is not None and score > 100:
                raise ShapeError(f"NVO row {number}: score exceeds 100 points")
            results.append(ExamResult(oblast, municipality, town, school, code,
                                      subject, takers, score, "points100"))
    return NvoTable(layout["exam"], layout["year"], len(seen),
                    tuple(item[0] for item in layout["columns"]), results, frozenset(seen))

"""Strict, versioned parser for MON's school-level State Matriculation Exam tables."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .parse import ShapeError
from .sources import Resource, _payload


DZI_DATASET = "066b4b04-d81d-444e-a61c-8ca0516079e4"
LAYOUTS = {row["uri"]: row for row in json.loads(
    (Path(__file__).parent / "dzi_layouts.json").read_text(encoding="utf-8"))}


def dzi_catalog(raw: bytes) -> list[Resource]:
    value = _payload(raw, "DZI catalog")
    rows = value.get("resources")
    if (set(value) != {"success", "resources", "total_records"} or not isinstance(rows, list)
            or type(value["total_records"]) is not int or len(rows) != value["total_records"]
            or any(not isinstance(row, dict) for row in rows)
            or {row.get("uri") for row in rows} != LAYOUTS.keys()
            or len(rows) != len(LAYOUTS)):
        raise ShapeError("Incomplete or changed DZI resource catalog")
    result = []
    for row in rows:
        layout = LAYOUTS[row["uri"]]
        name, updated = row.get("name"), row.get("updated_at")
        if (row.get("dataset_uri") != DZI_DATASET or not isinstance(name, str)
                or not isinstance(updated, str) or not updated
                or ("август-септември" in name) != (layout["session"] == "august")
                or ("по желание на ученика" in name) != (layout["kind"] == "optional")
                or (layout["year"] not in name and layout["year"][-4:] not in name)):
            raise ShapeError("Changed DZI resource metadata")
        result.append(Resource(row["uri"], layout["year"], name, updated))
    return sorted(result, key=lambda item: (item.year, item.uri))


@dataclass(frozen=True)
class DziResult:
    row_number: int
    neispuo: str | None
    school: str
    oblast: str
    municipality: str
    town: str
    subject: str
    takers: int | None
    score: Decimal | None
    is_school: bool


@dataclass(frozen=True)
class DziTable:
    year: str
    session: str
    kind: str
    rows: int
    schools: int
    codes: frozenset[str]
    aggregates: int
    results: list[DziResult]
    anomalies: dict[str, int]


def _clean(value):
    return re.sub(r"\s+", " ", value.replace("\ufeff", "")).strip(' "')


def _header_sha(rows):
    normalized = [[_clean(value) for value in row] for row in rows]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _single_subject(first, second, count_first):
    if count_first:
        if first.startswith("Бр. ") and second.startswith("Ср.усп. "):
            left, right = first.removeprefix("Бр. "), second.removeprefix("Ср.усп. ")
        elif first.endswith(" Брой") and second.endswith("Ср.успех"):
            left, right = first.removesuffix(" Брой"), second.removesuffix("Ср.успех").strip()
        else:
            raise ShapeError("Unknown DZI count and grade columns")
    else:
        if (first, second) == ("ср.успех", "общо"):
            return "Общо"
        if first.endswith(" Ср.усп.") and second.endswith(" Бр."):
            left, right = first.removesuffix(" Ср.усп."), second.removesuffix(" Бр.")
        elif first.endswith(" Ср.Усп") and second.endswith(" Бр."):
            left, right = first.removesuffix(" Ср.Усп"), second.removesuffix(" Бр.")
        else:
            raise ShapeError("Unknown early DZI grade and count columns")
    if not left or left.replace(" ", "") != right.replace(" ", ""):
        raise ShapeError("DZI subject columns do not pair")
    return left


def _subjects(headers, layout):
    subjects = []
    first = headers[0]
    carry = ""
    for col in range(5, layout["width"], 2):
        if layout["header_rows"] == 1:
            subject = _single_subject(_clean(first[col]), _clean(first[col + 1]), layout["count_first"])
        elif layout["header_rows"] == 2:
            if (_clean(first[col]), _clean(first[col + 1])) != ("Брой", "Ср.успех"):
                raise ShapeError("Unknown two-row DZI measures")
            left, right = _clean(headers[1][col]), _clean(headers[1][col + 1])
            if not left or left != right:
                raise ShapeError("Unknown two-row DZI subject")
            subject = left
        else:
            if (_clean(headers[2][col]), _clean(headers[2][col + 1])) != ("Бр.", "Ср.усп."):
                raise ShapeError("Unknown three-row DZI measures")
            carry = _clean(first[col]) or carry
            level = _clean(headers[1][col])
            if not carry or not level:
                raise ShapeError("Unknown three-row DZI subject")
            subject = carry + " " + level
        if subject in subjects:
            raise ShapeError("Duplicate DZI subject column")
        subjects.append(subject)
    return subjects


def _grade(value, number, col):
    if not re.fullmatch(r"\d+(?:[,.]\d+)?", value):
        raise ShapeError(f"DZI row {number}, column {col}: invalid grade")
    score = Decimal(value.replace(",", "."))
    if not Decimal("2") <= score <= Decimal("6"):
        raise ShapeError(f"DZI row {number}, column {col}: grade outside 2–6")
    return score


def parse_dzi(raw: bytes, resource_uri: str) -> DziTable:
    """Accept only observed resource headers and retain source suppression and anomalies."""
    layout = LAYOUTS.get(resource_uri)
    if layout is None:
        raise ShapeError("Unknown DZI resource layout")
    value = _payload(raw, "DZI resource")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list):
        raise ShapeError("Unknown DZI response")
    rows = value["data"]
    width, head = layout["width"], layout["header_rows"]
    if len(rows) <= head or any(not isinstance(row, list) or len(row) != width or
                                any(not isinstance(v, str) for v in row) for row in rows):
        raise ShapeError("Changed or empty DZI table")
    if _header_sha(rows[:head]) != layout["header_sha"]:
        raise ShapeError("Changed DZI columns")
    subjects = _subjects(rows[:head], layout)
    results = []
    seen = set()
    aggregates = 0
    anomalies = {"suppressed_takers": 0, "missing_grade": 0, "source_placeholder": 0}
    for number, row in enumerate(rows[head:], head + 1):
        oblast = row[0].strip()
        municipality = row[1].strip() if layout["code_col"] is not None else ""
        town = row[2].strip() if layout["code_col"] is not None else row[1].strip()
        school = row[3 if layout["code_col"] == 4 else 4].strip()
        code = re.sub(r"\s+", "", row[layout["code_col"]]) if layout["code_col"] is not None else None
        is_school = not school.upper().startswith(("РУО ", "РИО "))
        if not oblast or not school or (is_school and (not town or (layout["code_col"] is not None and not municipality))):
            raise ShapeError(f"DZI row {number}: missing school identity")
        if is_school and layout["code_col"] is not None and not re.fullmatch(r"\d{6,7}", code or ""):
            raise ShapeError(f"DZI row {number}: invalid NEISPUO code")
        if is_school:
            # The 2016 table repeats generic names and has no NEISPUO identifier.
            key = code if code is not None else number
            if key in seen:
                raise ShapeError(f"DZI row {number}: duplicate school")
            seen.add(key)
        else:
            aggregates += 1
            code = None
        for subject, col in zip(subjects, range(5, width, 2)):
            count_raw, score_raw = (row[col:col+2] if layout["count_first"] else
                                    (row[col+1], row[col]))
            count_raw, score_raw = count_raw.strip(), score_raw.strip()
            if count_raw == "-":
                takers = None
                anomalies["suppressed_takers"] += 1
            elif count_raw == "":
                takers = None
            elif count_raw.isdecimal():
                takers = int(count_raw)
            else:
                raise ShapeError(f"DZI row {number}, column {col}: invalid takers")
            if score_raw == "(" and resource_uri.startswith("bd8b058b") and number == 18 and col == 47:
                score_raw = ""
                anomalies["source_placeholder"] += 1
            if takers == 0:
                if score_raw not in {"", "0", "0,00", "0.00"}:
                    raise ShapeError(f"DZI row {number}, column {col}: grade without takers")
                continue
            if score_raw in {"", "0", "0,00", "0.00"}:
                score = None
                if takers is not None or count_raw == "-":
                    anomalies["missing_grade"] += 1
            else:
                score = _grade(score_raw, number, col)
            if takers is None and score is None and count_raw == "":
                continue
            results.append(DziResult(number, code, school, oblast, municipality, town, subject,
                                     takers, score, is_school))
    return DziTable(layout["year"], layout["session"], layout["kind"], len(rows) - head,
                    len(seen), frozenset(code for code in seen if isinstance(code, str)),
                    aggregates, results, anomalies)

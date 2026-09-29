"""MON catalog and institution register: accept only observed source shapes."""

import json
import re
from dataclasses import dataclass

from .parse import ExamResult, ShapeError


NVO7_DATASET = "b56288b6-25aa-4049-9aa6-de2cd4cdabf8"
SCHOOLS_DATASET = "81b81e76-e6aa-4895-b0b0-e0a9af53742f"


@dataclass(frozen=True)
class Resource:
    uri: str
    year: str
    name: str
    updated_at: str


@dataclass(frozen=True)
class School:
    neispuo: str
    name: str
    oblast: str
    municipality: str
    town: str


def _payload(raw: bytes, kind: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ShapeError(f"Invalid {kind} response") from exc
    if not isinstance(value, dict) or value.get("success") is not True:
        raise ShapeError(f"Failed {kind} response")
    return value


def catalog(raw: bytes, dataset: str) -> list[Resource]:
    """Read a complete listResources page; identify academic years from source titles."""
    value = _payload(raw, "catalog")
    if set(value) != {"success", "resources", "total_records"} or not isinstance(value["resources"], list):
        raise ShapeError("Unknown catalog shape")
    rows = value["resources"]
    if not rows or type(value["total_records"]) is not int or len(rows) != value["total_records"]:
        raise ShapeError("Incomplete catalog page")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not {"uri", "dataset_uri", "name", "updated_at"} <= row.keys():
            raise ShapeError("Unknown resource metadata")
        if row["dataset_uri"] != dataset or any(not isinstance(row[k], str) for k in ("uri", "name", "updated_at")):
            raise ShapeError("Resource belongs to another dataset or has invalid metadata")
        if not re.fullmatch(r"[0-9a-f-]{36}", row["uri"]) or row["uri"] in seen:
            raise ShapeError("Invalid or duplicate resource URI")
        seen.add(row["uri"])
        years = re.findall(r"(?:учебна(?:та)?\s+)(20\d{2})\s*/\s*(20\d{2})", row["name"], re.IGNORECASE)
        if len(years) != 1:
            continue  # Older dated tables have no academic year and require a separate parser.
        first, last = map(int, years[0])
        if last != first + 1:
            raise ShapeError("Invalid academic year")
        result.append(Resource(row["uri"], f"{first}/{last}", row["name"], row["updated_at"]))
    if not result:
        raise ShapeError("No resources with academic years")
    return result


def current_pair(nvo_catalog: bytes, school_catalog: bytes) -> tuple[Resource, Resource]:
    nvo = catalog(nvo_catalog, NVO7_DATASET)
    schools = catalog(school_catalog, SCHOOLS_DATASET)
    newest_year = max(r.year for r in nvo)
    exam = [r for r in nvo if r.year == newest_year]
    register = [r for r in schools if r.year == newest_year]
    if len(exam) != 1 or not register:
        raise ShapeError("Missing or ambiguous latest academic year")
    # The updated December list wins over the start-of-year list.
    register.sort(key=lambda r: r.updated_at, reverse=True)
    return exam[0], register[0]


def parse_schools(raw: bytes) -> dict[str, School]:
    value = _payload(raw, "school register")
    if set(value) != {"success", "data"} or not isinstance(value["data"], list) or not value["data"]:
        raise ShapeError("Unknown or empty school register")
    result = {}
    expected = {"InstRegName", "InstMunName", "InstTownName", "InstId", "InstName"}
    for number, row in enumerate(value["data"], 1):
        if not isinstance(row, dict) or set(row) != expected:
            raise ShapeError(f"Register row {number}: changed columns")
        if type(row["InstId"]) is not int or row["InstId"] < 0:
            raise ShapeError(f"Register row {number}: invalid code")
        if any(not isinstance(row[k], str) or not row[k].strip() for k in expected - {"InstId"}):
            raise ShapeError(f"Register row {number}: invalid name or place")
        code = str(row["InstId"])
        if not re.fullmatch(r"\d{6,7}", code) or code in result:
            raise ShapeError(f"Register row {number}: invalid or duplicate code")
        result[code] = School(code, row["InstName"], row["InstRegName"], row["InstMunName"], row["InstTownName"])
    return result


def reconcile(results: list[ExamResult], schools: dict[str, School]) -> dict:
    """Join only on the official code; hold if more than 2% cannot be linked."""
    if not results or not schools:
        raise ShapeError("Empty results or register")
    codes = {r.neispuo for r in results}
    if len(results) != 2 * len(codes):
        raise ShapeError("Each school needs exactly two subject rows")
    unmatched = sorted(codes - schools.keys())
    if len(unmatched) / len(codes) > 0.02:
        raise ShapeError(f"Too many unmatched school codes: {len(unmatched)} of {len(codes)}")
    return {"schools": len(codes), "matched": len(codes) - len(unmatched), "unmatched": unmatched}

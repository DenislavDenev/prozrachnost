"""Strict parser for MON's school-level NVO VII results."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class ShapeError(ValueError):
    """The source response cannot be safely interpreted."""


HEADER = [
    "Област", "Община", "Населено място", "Училище", "Код по НЕИСПУО",
    "БЕЛ Явили се", "БЕЛ Ср. успех в точки",
    "МАТ Явили се", "МАТ Ср. успех в точки",
]


@dataclass(frozen=True)
class ExamResult:
    oblast: str
    municipality: str
    town: str
    school: str
    neispuo: str
    subject: str
    takers: int | None
    score: Decimal | None
    scale: str = "points100"


def _count(value: str, row_number: int) -> int | None:
    if value == "":
        return None
    if not re.fullmatch(r"\d+", value):
        raise ShapeError(f"Row {row_number}: invalid taker count")
    return int(value)


def _score(value: str, row_number: int) -> Decimal | None:
    if value == "":
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ShapeError(f"Row {row_number}: invalid score")
    try:
        score = Decimal(value)
    except InvalidOperation as exc:
        raise ShapeError(f"Row {row_number}: invalid score") from exc
    if score > 100:
        raise ShapeError(f"Row {row_number}: score exceeds 100 points")
    return score


def parse_nvo7(raw: bytes) -> list[ExamResult]:
    """Parse one getResourceData response without guessing changed columns."""
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ShapeError("Invalid JSON resource") from exc
    if not isinstance(payload, dict) or set(payload) != {"success", "data"}:
        raise ShapeError("Unexpected response fields")
    if payload["success"] is not True or not isinstance(payload["data"], list):
        raise ShapeError("Resource request failed")
    table = payload["data"]
    if len(table) < 2 or table[0] != HEADER:
        raise ShapeError("Unknown or empty NVO VII table")

    results = []
    seen = set()
    for row_number, row in enumerate(table[1:], start=2):
        if not isinstance(row, list) or len(row) != len(HEADER) or any(not isinstance(value, str) for value in row):
            raise ShapeError(f"Row {row_number}: unexpected columns or types")
        oblast, municipality, town, school, neispuo = row[:5]
        if any(not value.strip() for value in row[:5]):
            raise ShapeError(f"Row {row_number}: missing school identity")
        if not re.fullmatch(r"\d{6,7}", neispuo):
            raise ShapeError(f"Row {row_number}: invalid NEISPUO code")
        if neispuo in seen:
            raise ShapeError(f"Row {row_number}: duplicate NEISPUO code")
        seen.add(neispuo)
        for subject, takers_column, score_column in (("БЕЛ", 5, 6), ("МАТ", 7, 8)):
            takers = _count(row[takers_column], row_number)
            score = _score(row[score_column], row_number)
            results.append(ExamResult(oblast, municipality, town, school, neispuo, subject, takers, score))
    return results

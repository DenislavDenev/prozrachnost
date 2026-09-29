"""Observed historical formats of MON's school-level NVO VII resources."""

import json
import re
from decimal import Decimal, InvalidOperation

from .parse import ExamResult, ShapeError, _count, parse_nvo7


def labels(row):
    return tuple(re.sub(r"\s+", " ", value.replace("\ufeff", "").replace("MAT", "МАТ")).strip()
                 for value in row)


IDENTITY = ("Област", "Община", "Населено място")
SCORES = ("Явили се БЕЛ", "Ср. успех в точки БЕЛ", "Явили се МАТ", "Ср. успех в точки МАТ")
HEADERS = {
    "2017/2018": ("Регион", "Община", "Населено място", "Код", "Училище", *SCORES),
    "2018/2019": (*IDENTITY, "Код", "Училище", *SCORES),
    "2019/2020": (*IDENTITY, "Код", "Училище", *SCORES),
    "2020/2021": (*IDENTITY, "Училище", "Код по Админ", "Явили се", "Ср. успех в точки",
                  "Явили се", "Ср. успех в точки"),
    "2021/2022": (*IDENTITY, "Училище", "Код по Админ", *SCORES),
    "2022/2023": (*IDENTITY, "Училище", "Код по Админ", "БЕЛ", "", "МАТ", ""),
}
SECOND = ("", "", "", "", "", "БЕЛ", "БЕЛ", "МАТ", "МАТ")
SECOND_2022 = ("", "", "", "", "", "Явили се", "Ср. успех в точки",
               "Явили се", "Ср. успех в точки")
HEAD_AT = {"2017/2018": 3, "2022/2023": 4}
DATA_AT = {"2017/2018": 4, "2020/2021": 2, "2022/2023": 6}


def historical_score(value: str, row_number: int, maximum: int) -> Decimal | None:
    if value == "":
        return None
    if not re.fullmatch(r"\d+(?:[,.]\d+)?", value):
        raise ShapeError(f"Row {row_number}: invalid score")
    try:
        score = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ShapeError(f"Row {row_number}: invalid score") from exc
    if score > maximum:
        raise ShapeError(f"Row {row_number}: score exceeds {maximum} points")
    return score


def parse_nvo7_year(raw: bytes, year: str) -> list[ExamResult]:
    """Use the year to select a verified layout; never mix the 65- and 100-point scales."""
    if not re.fullmatch(r"20\d{2}/20\d{2}", year) or int(year[5:]) != int(year[:4]) + 1:
        raise ShapeError("Invalid academic year")
    if year >= "2023/2024":
        return parse_nvo7(raw)
    if year not in HEADERS:
        raise ShapeError("No verified NVO VII layout for this year")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ShapeError("Invalid JSON resource") from exc
    if not isinstance(payload, dict) or set(payload) != {"success", "data"} or payload["success"] is not True:
        raise ShapeError("Unexpected resource response")
    rows = payload["data"]
    if not isinstance(rows, list) or len(rows) <= DATA_AT.get(year, 1):
        raise ShapeError("Empty historical NVO VII table")
    if any(not isinstance(row, list) or len(row) != 9 or any(not isinstance(v, str) for v in row) for row in rows):
        raise ShapeError("Changed historical NVO VII row shape")
    if labels(rows[HEAD_AT.get(year, 0)]) != HEADERS[year]:
        raise ShapeError("Unknown historical NVO VII columns")
    if year == "2017/2018":
        if rows[0][0] != "Максимален бал" or rows[1][2] != "65" or rows[2][2] != "65":
            raise ShapeError("Unknown 2018 NVO VII point scale")
    elif year == "2022/2023":
        if year not in rows[0][0] or rows[1][0] != "Максимален бал" or rows[2][2] != "100" or rows[3][2] != "100":
            raise ShapeError("Unknown 2023 NVO VII point scale")
        if labels(rows[5]) != SECOND_2022:
            raise ShapeError("Unknown 2023 NVO VII subject columns")
    elif year == "2020/2021" and labels(rows[1]) != SECOND:
        raise ShapeError("Unknown 2021 NVO VII subject columns")
    maximum = 65 if year == "2017/2018" else 100
    code_col, school_col = (3, 4) if year in {"2017/2018", "2018/2019", "2019/2020"} else (4, 3)
    result, seen = [], set()
    for number, row in enumerate(rows[DATA_AT.get(year, 1):], start=DATA_AT.get(year, 1) + 1):
        oblast, municipality, town = row[:3]
        school = row[school_col]
        code = re.sub(r"\s+", "", row[code_col])
        if any(not value.strip() for value in (oblast, municipality, town, school)):
            raise ShapeError(f"Row {number}: missing school identity")
        if not re.fullmatch(r"\d{6,7}", code) or code in seen:
            raise ShapeError(f"Row {number}: invalid or duplicate NEISPUO code")
        seen.add(code)
        for subject, count_col, score_col in (("БЕЛ", 5, 6), ("МАТ", 7, 8)):
            result.append(ExamResult(oblast, municipality, town, school, code, subject,
                                     _count(row[count_col], number),
                                     historical_score(row[score_col], number, maximum),
                                     f"points{maximum}"))
    return result

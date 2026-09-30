"""National MON school-type counts; no individual institution identifiers exist."""

from dataclasses import dataclass
from decimal import Decimal

from .parse import ShapeError
from .sources import _payload, catalog


DATASETS = {
    "pupils": "8c03a6fb-598f-44ea-81e5-9765d6e573df",
    "classes": "17a20272-3a70-4056-9f4e-1b5f038fb11a",
}
GRADES = tuple(range(1, 13)) + (14, 15)
KINDS = {
    "духовно", "по изкуствата", "по културата", "спортно", "начално", "основно",
    "обединено", "средно", "профилирана гимназия", "професионална гимназия",
    "за обучение и подкрепа на ученици с увреден слух",
    "за обучение и подкрепа на ученици с нарушено зрение",
    "възпитателно училище - интернат", "социално-педагогически интернат",
    "към местата за лишаване от свобода",
}
PUPIL_GROUPS = {"br_stds_vtora", "br_stds_treta", "br_stds_purva"}
CLASS_GROUPS = {"br_gr_vtora", "br_gr_raznovuzrastova", "br_gr_treta", "br_gr_chetvurta"}
PUPIL_KEYS = {"DSTName", "br_inst", "br_stds", *PUPIL_GROUPS,
              *(f"br_stds_klas_{g}" for g in GRADES)}
CLASS_KEYS = {"DSTName", "br_gr_klas_all", *CLASS_GROUPS,
              *(f"br_klas_{g}" for g in GRADES)}


@dataclass(frozen=True)
class KindCounts:
    kind: str
    institutions: int
    grades: dict[int, int]
    preschool: int
    reported_pupils: int
    reported_groups: Decimal


def latest_pair(pupils_catalog: bytes, classes_catalog: bytes):
    pupils = catalog(pupils_catalog, DATASETS["pupils"])
    classes = catalog(classes_catalog, DATASETS["classes"])
    shared = {r.year for r in pupils} & {r.year for r in classes}
    if not shared:
        raise ShapeError("No shared pupil/class year")
    year = max(shared)
    return tuple(max((r for r in rows if r.year == year), key=lambda r: r.updated_at)
                 for rows in (pupils, classes))


def _rows(raw: bytes, expected: set[str], label: str) -> dict[str, dict]:
    value = _payload(raw, label)
    data = value.get("data")
    if set(value) != {"success", "data"} or not isinstance(data, list) or len(data) != len(KINDS):
        raise ShapeError(f"Unknown {label} table")
    result = {}
    for row in data:
        if not isinstance(row, dict) or set(row) != expected or row["DSTName"] not in KINDS:
            raise ShapeError(f"Changed {label} columns or school types")
        if row["DSTName"] in result:
            raise ShapeError(f"Duplicate {label} school type")
        result[row["DSTName"]] = row
    if set(result) != KINDS:
        raise ShapeError(f"Incomplete {label} school types")
    return result


def parse_context(pupils_raw: bytes, classes_raw: bytes, year: str) -> list[KindCounts]:
    pupils = _rows(pupils_raw, PUPIL_KEYS, "pupils")
    classes = _rows(classes_raw, CLASS_KEYS, "classes")
    result = []
    for kind in sorted(KINDS):
        p, c = pupils[kind], classes[kind]
        for key in PUPIL_KEYS - {"DSTName"}:
            if type(p[key]) is not int or p[key] < 0:
                raise ShapeError(f"Invalid pupil count for {kind}/{key}")
        for key in CLASS_KEYS - {"DSTName"}:
            if type(c[key]) not in (int, float) or not 0 <= c[key] <= 100000:
                raise ShapeError(f"Invalid group count for {kind}/{key}")
        grades = {g: p[f"br_stds_klas_{g}"] for g in GRADES}
        preschool = sum(p[key] for key in PUPIL_GROUPS)
        pupil_gap = p["br_stds"] - sum(grades.values()) - preschool
        class_parts = sum((Decimal(str(c[key])) for key in CLASS_KEYS - {"DSTName", "br_gr_klas_all"}),
                          Decimal(0))
        groups = Decimal(str(c["br_gr_klas_all"]))
        class_gap = groups - class_parts
        known_gap = kind == "за обучение и подкрепа на ученици с увреден слух" and year == "2025/2026"
        if pupil_gap != (3 if known_gap else 0) or abs(class_gap - (Decimal("0.25") if known_gap else 0)) > Decimal("0.00001"):
            raise ShapeError(f"Pupil/group totals do not reconcile for {kind}")
        result.append(KindCounts(kind, p["br_inst"], grades, preschool, p["br_stds"], groups))
    return result

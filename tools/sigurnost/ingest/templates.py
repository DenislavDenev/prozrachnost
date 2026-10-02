"""Which table is this? Found from the header rows, never from the name: all 25 resources of a "Полицейска статистика" set
carry one name.

A family is a kind of table this tool publishes (gold). The title lines, the label of the second column and the labels of the
value columns, as sheet.py assembles them, must all match; a table whose title says it is of a family but whose columns do
not match is a TemplateError (the resource is marked invalid and the problem is reported). A table that matches no family
stays in silver as it is (the other 20 kinds of table of a set are offered as raw tables).
"""
import hashlib
import re

from . import sheet

# indicator codes are listed in db/migrations/0002_gold.sql (gold.indicator)
FAMILIES = {
    "types": "Регистрирани и разкрити, по видове престъпления (против личността и собствеността), страната",
    "structures": "Регистрирани и разкрити, по структури на МВР (против личността и собствеността)",
    "main": "Основна таблица: всички видове престъпления, страната",
    "types_by_structure": "По видове престъпления, структура след структура (против личността и собствеността)",
    "econ_by_structure": "Икономически престъпления по видове, структура след структура",
}

_AB = [(2, "reg"), (3, "reg_unknown"), (4, "per100k"), (5, "solved"), (6, "clearance"), (7, "solved_unknown"),
       (8, "share_solved_unknown"), (9, "share_unknown_solved")]
_AB_LABELS = [
    "общ брой регистрирани престъпления",
    "от тях с неизвестен извършител",
    "брой на 100 хил. души",
    "общ брой",
    "% на разкриваемост спрямо общия брой регистрирани престъпления",
    "брой на разкритите престъпления с неизвестентен извършител",
    "% спрямо общо разкритите престъпления",
    "% спрямо регистрираните престъпления с неизвестен извършител",
]
_C = [(2, "reg"), (3, "reg_attempts"), (4, "per100k"), (5, "solved"), (6, "clearance"), (7, "persons"), (8, "persons_women"),
      (9, "persons_minors"), (10, "persons_foreigners")]
_C_LABELS = [
    "регистрирани престъпления през текущата година общ брой",
    "от тях опити",
    "брой на 100 хил. души",
    "престъпления от регистрираните през текущата година общ брой",
    "% на разкриваемост",
    "по разкритите престъпления, общ брой",
    "регистрирани през текущата година жени",
    "от тях: непълнолетни (14-17 г.)",
    "чужденци",
]
_D = [(2, "reg"), (3, "per100k"), (4, "solved"), (5, "clearance"), (6, "persons"), (7, "persons_women"), (8, "persons_minors"),
      (9, "persons_foreigners"), (10, "solved_prev"), (11, "persons_prev")]
_D_LABELS = [
    "регистрирани престъпления през текущата година общ брой",
    "брой на 100 хил, души",
    "разкрити престъпления от регистрираните през текущата година общ брой",
    "% на разкриваемост",
    "установени извършители по разкритите престъпления, общ брой",
    "регистрирани през текущата година жени",
    "от тях: непълнолетни (14-17 г.)",
    "чужденци",
    "регистрирани в предишни години и разкрити през текущата. установени извършители разкрити престъпления",
    "установени извършители",
]

SPECS = {
    "types": (_AB, _AB_LABELS),
    "structures": (_AB, _AB_LABELS),
    "main": (_C, _C_LABELS),
    "types_by_structure": (_D, _D_LABELS),
    "econ_by_structure": (_D, _D_LABELS),
}


class TemplateError(Exception):
    pass


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def _title(sh):
    return norm(sh.title)


def family_of(sh):
    """The family by the header, or None when the table is of a kind that is not published as a family. Raises
    TemplateError when the title is of a family but the columns are not."""
    if sh.kind != "table":
        return None
    b = sh.blocks[0]
    title, dim = _title(sh), norm(b.dimension)
    fam = None
    if "основна таблица" in title:
        fam = "main"
    elif ("регистрирани и разкрити престъпления против личността и собствеността на гражданите разкрити престъпления по регистрираните през текущата година" in title
          and len(sh.blocks) == 1):
        fam = "types" if dim.startswith("видове престъпления") else "structures" if dim.startswith("области") else None
    elif len(sh.blocks) > 1:
        # structure after structure: the title lines say nothing, the first rows tell the kinds apart
        first = next((norm(r.text) for r in b.rows if not r.total), "")
        if first.startswith("престъпления против личността"):
            fam = "types_by_structure"
        elif first.startswith("престъпления против собствеността"):
            fam = "econ_by_structure"
    if fam is None:
        return None
    labels = SPECS[fam][1]
    got = [norm(lab) for _, lab in b.columns]
    if got != labels:
        bad = [(i, g, w) for i, (g, w) in enumerate(zip(got, labels)) if g != w]
        raise TemplateError(f"{fam}: {len(got)} колони вместо {len(labels)}" if len(got) != len(labels) else f"{fam}: непознати заглавия на колони: {bad[:2]}")
    return fam


def template_id(sh, fam):
    """A short fingerprint of the family and its column labels: tables with the same id are comparable across years, a
    new id is a break in the series."""
    b = sh.blocks[0]
    key = fam + "|" + "|".join(norm(lab) for _, lab in b.columns)
    return fam + "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def indicators(fam):
    return dict(SPECS[fam][0])

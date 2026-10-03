"""Which table is this, and what does each column mean? Found from the header rows, never from the name: all 25 resources of a
"Полицейска статистика" set carry one name.

A family is a kind of table this tool publishes (gold). The kind comes from the title lines, the label of the second column and
the number of blocks. Each value column gets its indicator from the *meaning* of its label (not from its position, not from
the exact words: the Ministry has printed the same table with long prefixed headings in 2016-2020, with a comma for a dot,
with two columns swapped in 2023). Every column must be understood and the set of indicators must be one the family has;
otherwise TemplateError and the resource is marked invalid. The `template_id` is a fingerprint of the family and the
normalised labels: tables with the same id are comparable across years, a different id is a break in the series even when
the meaning is the same (the dialect of the heading changed).
"""
import hashlib
import re

FAMILIES = {
    "types": "Регистрирани и разкрити, по видове престъпления (против личността и собствеността), страната",
    "structures": "Регистрирани и разкрити, по структури на МВР (против личността и собствеността)",
    "main": "Основна таблица: всички видове престъпления, страната",
    "types_by_structure": "По видове престъпления, структура след структура (против личността и собствеността)",
    "econ_by_structure": "Икономически престъпления по видове, структура след структура",
}

AB = {"reg", "reg_unknown", "per100k", "solved", "clearance", "solved_unknown", "share_solved_unknown", "share_unknown_solved"}
C = {"reg", "reg_attempts", "per100k", "solved", "clearance", "persons", "persons_women", "persons_minors", "persons_foreigners"}
D = {"reg", "per100k", "solved", "clearance", "persons", "persons_women", "persons_minors", "persons_foreigners", "solved_prev", "persons_prev"}
ALLOWED = {
    "types": [AB], "structures": [AB],
    "main": [C, C | {"solved_prev", "persons_prev"}],
    "types_by_structure": [D], "econ_by_structure": [D],
}


class TemplateError(Exception):
    pass


def norm(s):
    """Lower case, dots and commas removed (the Ministry prints "г." and "г," for the same thing), one space."""
    return re.sub(r"\s+", " ", re.sub(r"[.,;*]", "", s.lower().replace("текушата", "текущата"))).strip()


def classify(label):
    """The indicator a column label means, or None. The rules are ordered: the specific ones first."""
    s = norm(label)
    if "разкриваемост" in s:
        return "clearance"
    if "%" in s:
        return "share_unknown_solved" if "спрямо регистр" in s else "share_solved_unknown" if "спрямо общо" in s else None
    if "на 100 хил" in s:
        return "per100k"
    if "опити" in s:
        return "reg_attempts"
    if "непълнолетни" in s:
        return "persons_minors"
    if "чужденци" in s:
        return "persons_foreigners"
    if s.endswith("жени"):
        return "persons_women"
    if "предишни години" in s:
        return "persons_prev" if s.startswith("установени извършители") else "solved_prev"
    if s == "установени извършители":
        return "persons_prev"
    if "неизвестен" in s:
        return "solved_unknown" if "разкрит" in s else "reg_unknown"
    if "установени извършители" in s or "по разкритите престъпления" in s:
        return "persons"
    if "от регистрираните" in s or "разкрит" in s:
        return "solved" if "общ брой" in s else None
    if "общ брой" in s:
        return "solved" if s == "общ брой" else "reg"
    return None


def column_map(fam, columns):
    """{col_no: indicator} for the value columns [(col_no, label)], or TemplateError."""
    got = {c: classify(lab) for c, lab in columns}
    unknown = [(c, lab) for c, lab in columns if got[c] is None]
    if unknown:
        raise TemplateError(f"{fam}: колона без разпознат смисъл: {unknown[0]}")
    inds = list(got.values())
    if len(set(inds)) != len(inds):
        dup = sorted({i for i in inds if inds.count(i) > 1})
        raise TemplateError(f"{fam}: два пъти един показател: {dup}")
    if set(inds) not in ALLOWED[fam]:
        want = min(ALLOWED[fam], key=lambda a: len(a ^ set(inds)))
        raise TemplateError(f"{fam}: показателите не са на таблицата: липсват {sorted(want - set(inds))}, излишни {sorted(set(inds) - want)}")
    return got


def _is_ab(b):
    """The table by types / by structures: eight value columns with the unknown-offender parts and the clearance."""
    labels = " | ".join(norm(lab) for _, lab in b.columns)
    return len(b.columns) == 8 and "неизвестен" in labels


def family_of(sh, set_kind="police"):
    """The family by the header, or None when the table is of a kind that is not published as a family. Raises
    TemplateError when the title is of a family but the columns are not understood. Only the sets "Полицейска статистика"
    have families; the old set of 2014-2015 stays in silver as it is."""
    if sh.kind != "table" or set_kind != "police":
        return None
    b = sh.blocks[0]
    title, dim = norm(sh.title), norm(b.dimension)
    fam = None
    if "основна таблица" in title or dim.startswith("наказуеми деяния"):
        fam = "main"
    elif len(sh.blocks) == 1 and dim.startswith(("видове престъпления", "области")) and _is_ab(b):
        fam = "types" if dim.startswith("видове престъпления") else "structures"
    elif len(sh.blocks) > 1:
        # structure after structure: the title lines say nothing, the first rows tell the kinds apart
        first = next((norm(r.text) for r in b.rows if not r.total), "")
        if first.startswith("престъпления против личността"):
            fam = "types_by_structure"
        elif first.startswith("престъпления против собствеността"):
            fam = "econ_by_structure"
    if fam is None:
        return None
    column_map(fam, b.columns)
    return fam


def template_id(sh, fam):
    b = sh.blocks[0]
    key = fam + "|" + "|".join(norm(lab) for _, lab in b.columns)
    return fam + "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def indicators(fam, columns):
    return column_map(fam, columns)

"""The parliamentary groups (db/ref/groups.csv): the one code of a group under all the names the Assembly's files give
it in an assembly, its full name and its party's colour.

- The same group under several names was found by its MPs: in the 51st assembly "ДПС - НН" and "ДПС - Ново начало"
  are the same 30 MPs; "АПС", "ДПС - ДПС", "Демокрация, права и свободи - ДПС" and, on 06.12.2024, "ДПС" the same 19-20
  (checked 28.09.2026).
- The colours are the parties' colours as Wikipedia gives them (Module:Political party, the key in `color_source`),
  so that people know the group at a glance; the MPs outside a group are grey.
- A group not in the file keeps its code and is grey, until it is added here.
"""
import csv
from functools import lru_cache

from .config import ROOT

GREY = "#9aa1aa"
INDEPENDENT = ("НЕЗ", "НЕЧЛ")   # the MPs outside a group: no group line


@lru_cache(maxsize=1)
def table():
    with open(ROOT / "db" / "ref" / "groups.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def aliases():
    """[(assembly or None for every one, code in the files, group)], an assembly's own rows first."""
    rows = [(None if r["assembly"] == "*" else int(r["assembly"]), r["code"], r["group"]) for r in table()]
    return sorted((r for r in rows if r[1] != r[2]), key=lambda r: r[0] is None)


def info(group):
    """-> (full name, colour) of a group's code; the code itself and grey for one not in the file."""
    for r in table():
        if r["group"] == group:
            return r["name"], r["color"]
    return group, GREY

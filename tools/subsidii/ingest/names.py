"""What a name of the report is: a legal entity, a sole trader or a natural person; the key for searching legal entities;
and the municipality of a (oblast, municipality) pair. Python, not SQL: the result must not depend on the locale of the
database (upper() of Cyrillic does nothing in the C locale).

The rules are deliberately cautious about people (docs/methodology.md):

* a recipient with a last name in its own column is a natural person;
* a name that begins with ЕТ (or ET) and has no company form (ЕООД, ООД, ЕАД, АД, ДЗЗД, СД, КД) is a sole trader: the
  firm name of an ЕТ carries the owner's name, so it is treated as a natural person for display, export and search;
* everything else is a legal entity.

Legal entities with the same normalized name get the same org_id. That is a name match, not a company number, and it is
said so wherever it is shown. People are never grouped.
"""
import csv
import hashlib
import re

from . import config

RULES = 1       # change it when the rules change: the classification is built again

_LOOKALIKE = str.maketrans("ABCEHKMOPTXYabcehkmoptxy", "АВСЕНКМОРТХУАВСЕНКМОРТХУ")
_COMPANY = re.compile(r"(^| )(ЕООД|ООД|ЕАД|АД|ДЗЗД|СД|КД)( |$)")
_SOLE = re.compile(r"^ЕТ")


def norm(s):
    """Upper case, Latin look-alikes as Cyrillic, everything that is not a letter or digit as one space."""
    s = s.translate(_LOOKALIKE).upper()
    return " ".join(re.sub(r"[^0-9A-ZА-Я]+", " ", s).split())


def kind_of(name, surname):
    if surname.strip() not in ("-", ""):
        return "natural"
    n = norm(name)
    if _SOLE.match(n) and not _COMPANY.search(n):
        return "sole_trader"
    return "legal"


def org_id(name_norm):
    return hashlib.sha1(name_norm.encode("utf-8")).hexdigest()[:12]


def place_key(oblast, obshtina):
    """The two names the way they are compared: the report writes 'София (област)', the list 'София област',
    and the capitalisation differs ('Вълчи дол' against 'Вълчи Дол')."""
    def f(s):
        return " ".join(s.replace("(област)", "област").replace("-", " ").replace(".", " ").split()).casefold()
    return f(oblast), f(obshtina)


def load_places():
    """({(oblast, municipality) key: id}, {key: (id, evidence)} of the aliases)."""
    ref = config.ROOT / "db" / "ref"
    by = {}
    for r in csv.DictReader((ref / "municipality.csv").open(encoding="utf-8-sig")):
        by[place_key(r["oblast"], r["name_bg"])] = r["id"]
    alias = {}
    for r in csv.DictReader((ref / "municipality_alias.csv").open(encoding="utf-8")):
        alias[place_key(r["oblast_raw"], r["obshtina_raw"])] = (r["municipality_id"], r["evidence"])
    return by, alias


def match_place(oblast, obshtina, places=None):
    """(municipality id, method, evidence) or None."""
    by, alias = places or load_places()
    k = place_key(oblast, obshtina)
    if k in alias:
        return alias[k][0], "alias", alias[k][1]
    if k in by:
        return by[k], "name", None
    return None

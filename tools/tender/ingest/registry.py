"""Търговски регистър: partida client and role extraction.

Ported from SIGMA (github.com/midt-bg/sigma, MIT), commit 62369ff335275c482452d56c848530650778bd70:
packages/ingest/src/registry.ts, registry-roles.ts and packages/shared/src/person-identity.ts.
See ingest/NOTICE for the licence. Behavioural differences from the TS original are commented.

A role field is a sequence of entries per sub-partida. An Add entry lists the field's holders in
full as they stand after it, so a holder's role ends at the first later entry that leaves them
out. An Erase entry removes the whole field.
"""
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from .config import BGN_PER_EUR, TR_BASE, TR_PORTAL_ENTRIES
from .http import Gone, get

ROLE_FIELDS = {
    "00070": "manager", "00071": "manager", "00090": "chair",
    "00100": "representative", "00101": "representative", "00102": "representative",
    "00103": "representative",
    "00120": "board_of_directors", "00125": "governing_body", "00130": "management_board",
    "00131": "management_board", "00132": "management_board", "00135": "board_of_trustees",
    "00140": "supervisory_board", "00150": "controlling_board", "00151": "controlling_board",
    "00152": "verification_commission",
    "00180": "trader", "00190": "partner", "00200": "partner", "00201": "partner",
    "00210": "partner", "00230": "sole_owner", "00231": "sole_owner",
    "00410": "procurator", "00530": "branch_manager", "05020": "liquidator",
    "05030": "representative", "05500": "beneficial_owner",
    "09120": "trustee", "09122": "trustee", "09123": "trustee",
}
OWNERSHIP_ROLES = {"partner", "sole_owner", "trader"}
HIDDEN_ROLES = {"beneficial_owner"}
VIEW_OF_ROLE = {r: ("ownership" if r in OWNERSHIP_ROLES else "hidden" if r in HIDDEN_ROLES
                    else "management") for r in set(ROLE_FIELDS.values())}

HASH = re.compile(r"^[0-9a-f]{64}$", re.I)
EIK = re.compile(r"^\d{9}(\d{4})?$")
UIC9 = re.compile(r"^\d{9}$")
PERSON_TITLE = re.compile(
    r"^(?:\s*(?:д-?р|доц|проф|акад|инж|арх|адв|ген|полк|подп|кап|м-?р|ст\.?\s*н\.?\s*с)\.?\s+)+",
    re.I)


class RegistryError(Exception):
    pass


# ---------- identity helpers (person-identity.ts) ----------

def person_name_key(value):
    """Comparison key: letters only, upper case, single spaces."""
    import unicodedata
    s = unicodedata.normalize("NFC", str(value or "")).upper()
    return " ".join(re.findall(r"[^\W\d_]+", s))


def without_title(value):
    return PERSON_TITLE.sub("", str(value or ""))


def collective_person_name(value):
    if re.search(r"наследници|наследствена общност|съсобственици", value, re.I):
        return True
    parts = re.split(r"[,;\n]|\s+и\s+", value, flags=re.I)
    return sum(1 for p in parts if len(person_name_key(p).split()) >= 2) > 1


def personal_indent(indent, indent_type):
    return bool(indent and HASH.match(indent) and (indent_type or "").upper() in ("EGN", "LNCH"))


# ---------- XML ----------

def _strip(el):
    for e in el.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return el


def _text(el, name):
    """Attribute or child element text, trimmed; None when empty."""
    if el is None:
        return None
    v = el.get(name)
    if v is None:
        c = el.find(name)
        v = c.text if c is not None else None
    v = (v or "").strip()
    return v or None


@dataclass
class Field:
    ident: str
    element: str
    operation: str
    entry_number: str
    entry_date: str
    node: ET.Element


@dataclass
class SubDeed:
    sub_uic: str
    sub_type: str
    status: str
    fields: list = field(default_factory=list)


@dataclass
class Deed:
    uic: str
    name: str
    status: str
    legal_form: str
    subdeeds: list


def parse_deed(xml, uic):
    if re.search(rb"<!DOCTYPE|<!ENTITY", xml, re.I):
        raise RegistryError("refusing XML with DTD")
    root = _strip(ET.fromstring(xml))
    d = root.find("Deed")
    if d is None or d.get("UIC") != uic:
        raise RegistryError(f"registry returned no partida {uic}")
    subs = []
    for s in d.findall("SubDeed"):
        sd = SubDeed(s.get("SubUIC", ""), s.get("SubUICType", ""), s.get("SubDeedStatus", ""))
        for f in s:
            if f.get("FieldIdent"):
                sd.fields.append(Field(f.get("FieldIdent"), f.tag, f.get("FieldOperation", ""),
                                       f.get("FieldEntryNumber", ""), f.get("FieldEntryDate", ""), f))
        subs.append(sd)
    return Deed(uic, d.get("CompanyName", ""), d.get("DeedStatus", ""), d.get("LegalForm", ""), subs)


# ---------- roles (registry-roles.ts) ----------

def _records(node):
    """The records of a field entry: the node itself when it is a record, else records under it."""
    if node.get("RecordID") is not None:
        return [node]
    out = []
    for c in node:
        out.extend(_records(c))
    return out


def _holders(rec):
    return [c for c in rec if c.tag in ("Person", "Subject")]


@dataclass
class Subject:
    kind: str       # person | entity
    id: str         # hash, local:..., ЕИК, or name:...
    name: str
    indent_type: str | None


def subject_of(eik, holder):
    name = _text(holder, "Name")
    if not name:
        return None
    indent = _text(holder, "Indent")
    itype = _text(holder, "IndentType")
    t = (itype or "").upper()
    if personal_indent(indent, itype):
        return Subject("person", indent.lower(), name, itype)
    if indent and t == "BIRTHDATE" and HASH.match(indent):
        return Subject("person", f"local:{eik}:birthdate:{indent.lower()}:{name}", name, itype)
    if indent and t == "UIC" and EIK.match(indent):
        return Subject("entity", indent, name, itype)
    company = _text(holder, "LegalForm") or _text(holder, "ForeignLegalFormCode") or \
        _text(holder, "RegistrationNumber")
    if company or t == "UIC":
        return Subject("entity", f"name:{name.upper()}", name, itype)
    return Subject("person", f"local:{eik}:{name.upper()}", name, itype)


def share_of(rec):
    share = _text(rec, "share") or _text(rec, "Share")
    if share:
        cur = _text(rec, "currency") or _text(rec, "Currency")
        return f"{share} {cur}" if cur else share
    sizes = [s.text.strip() for s in rec.iter("OwnedRightSize") if s.text and s.text.strip()]
    if sizes:
        return "; ".join(sizes)
    return _text(rec, "OwnedRights")


def share_pct(share, capital=None):
    """Percent from a registered share: '50%' or an amount over the capital. None when unknown."""
    if not share:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", share)
    if m:
        return float(m.group(1).replace(",", "."))
    m = re.match(r"\s*(\d+(?:[.,]\d+)?)", share)
    if m and capital:
        return round(100 * float(m.group(1).replace(",", ".")) / capital, 4)
    return None


def country_of(rec, holder):
    res = rec.find("CountryOfResidence")
    return _text(res, "Country") if res is not None else _text(holder, "CountryName")


def _listed(eik, node):
    out = {}
    for rec in _records(node):
        for h in _holders(rec):
            s = subject_of(eik, h)
            if s and s.id not in out:
                out[s.id] = (s, rec, h)
    return out


def _chrono(f):
    return (f.entry_date, f.entry_number)


def observations(eik, deed):
    """Per-entry holder names; marks one hash listed under several names as collective."""
    out = []
    for sub in deed.subdeeds:
        for f in sub.fields:
            if f.ident not in ROLE_FIELDS or f.operation == "Erase":
                continue
            i = 0
            for rec in _records(f.node):
                for h in _holders(rec):
                    name = _text(h, "Name")
                    if not name:
                        continue
                    raw = _text(h, "Indent") or ""
                    itype = _text(h, "IndentType")
                    personal = personal_indent(raw, itype)
                    out.append({"sub": sub.sub_uic, "field": f.ident, "entry": f.entry_number,
                                "on": f.entry_date, "i": i, "indent": raw.lower() if personal else None,
                                "type": itype, "name": name, "key": person_name_key(name),
                                "kind": ("collective" if collective_person_name(name) else "person")
                                if personal else "other"})
                    i += 1
    names = {}
    for o in out:
        if o["indent"]:
            names.setdefault((o["sub"], o["field"], o["entry"], o["indent"]), set()).add(o["key"])
    for o in out:
        if o["indent"] and len(names[(o["sub"], o["field"], o["entry"], o["indent"])]) > 1:
            o["kind"] = "collective"
    return out


def roles_from_deed(eik, deed):
    """(roles, persons) — every role fact with the entry that added it and the day it ended."""
    roles, persons = [], {}
    obs = observations(eik, deed)
    collective = {}
    for o in obs:
        if o["kind"] == "collective" and o["indent"]:
            collective.setdefault((o["sub"], o["field"], o["entry"]), set()).add(o["indent"])
    for sub in deed.subdeeds:
        by_field = {}
        for f in sub.fields:
            if f.ident in ROLE_FIELDS:
                by_field.setdefault(f.ident, []).append(f)
        for ident, entries in by_field.items():
            role = ROLE_FIELDS[ident]
            standing = {}
            for f in sorted(entries, key=_chrono):
                on = f.entry_date[:10]
                now = {} if f.operation == "Erase" else _listed(eik, f.node)
                ambiguous = collective.get((sub.sub_uic, ident, f.entry_number), set())
                for sid in [k for k, (s, _, _) in now.items()
                            if s.kind == "person" and (collective_person_name(s.name) or k in ambiguous)]:
                    del now[sid]
                for sid in [k for k in standing if k not in now]:
                    r = standing.pop(sid)
                    if sid in ambiguous:
                        r["uncertain_after"] = on
                    else:
                        r["valid_to"] = on
                for sid, (s, rec, h) in now.items():
                    share, country = share_of(rec), country_of(rec, h)
                    if sid in standing:
                        standing[sid].update(holder_name=s.name, share=share, country=country)
                    else:
                        r = {"eik": eik, "sub_uic": sub.sub_uic, "field_ident": ident, "role": role,
                             "holder_kind": s.kind, "holder_id": sid, "holder_name": s.name,
                             "indent_type": s.indent_type, "share": share, "country": country,
                             "entry_no": f.entry_number, "valid_from": on, "valid_to": None,
                             "uncertain_after": None}
                        roles.append(r)
                        standing[sid] = r
                    if s.kind == "person" and not sid.startswith("local:"):
                        prior = persons.get(sid)
                        if (not prior or f.entry_date > prior["observed_on"] or
                                (f.entry_date == prior["observed_on"] and s.name < prior["name"])):
                            persons[sid] = {"indent": sid, "name": s.name,
                                            "indent_type": s.indent_type, "observed_on": f.entry_date}
    return roles, list(persons.values())


def deed_facts(deed):
    """Current name, legal form, seat settlement, capital and registration day."""
    facts = {"name": deed.name, "legal_form": deed.legal_form, "seat": None, "capital": None,
             "registered_on": None}
    for sub in deed.subdeeds:
        last = {}
        for f in sub.fields:
            if f.ident not in last or _chrono(f) > _chrono(last[f.ident]):
                last[f.ident] = f
            if f.ident == "00010" and sub.sub_type == "MainCircumstances":
                d = f.entry_date[:10]
                if d and (facts["registered_on"] is None or d < facts["registered_on"]):
                    facts["registered_on"] = d
        if sub.sub_type != "MainCircumstances":
            continue
        for ident, f in last.items():
            if f.operation == "Erase":
                continue
            if ident == "00020":
                facts["name"] = (f.node.text or "").strip() or facts["name"]
            elif ident == "00050":
                facts["seat"] = _text(f.node.find("Address"), "Settlement")
            elif ident == "00310":  # capital; euro="0" means BGN
                try:
                    amount = float((f.node.text or "").replace(" ", "").replace(",", "."))
                except ValueError:
                    continue
                facts["capital"] = amount if f.node.get("euro") == "1" else round(amount / BGN_PER_EUR, 2)
    return facts


# ---------- client ----------

def fetch_deed(uic):
    """Raw XML bytes, or None when the register has no such partida."""
    if not UIC9.match(uic):
        raise RegistryError(f"not a partida ЕИК: {uic}")
    try:
        return get(f"{TR_BASE}/deeds/{uic}", accept="application/xml", timeout=60)
    except Gone:
        return None


_legacy_search = False


def search_holders(name, page=1, page_size=100):
    """Partidas naming a holder: {'items': [{uic, companyName, fieldIdent, name}], 'total': n}."""
    global _legacy_search
    target = name.strip()
    if not target or len(target) > 200:
        raise RegistryError("invalid search name")
    if _legacy_search:
        q = {"name": target, "page": page, "pageSize": page_size}
        url = f"{TR_BASE}/deeds/fields/summary?{urllib.parse.urlencode(q)}"
    else:
        q = {"target": target, "limit": page_size, "offset": (page - 1) * page_size}
        url = f"{TR_BASE}/deeds/search?{urllib.parse.urlencode(q)}"
    try:
        raw = json.loads(get(url, accept="application/json", timeout=60))
    except Exception:
        if _legacy_search:
            raise
        _legacy_search = True
        return search_holders(name, page, page_size)
    items = [{"uic": r.get("uic"), "companyName": r.get("companyName") or "",
              "fieldIdent": r.get("fieldIdent") or r.get("field") or "", "name": r.get("name") or ""}
             for r in raw.get("items") or [] if UIC9.match(str(r.get("uic") or ""))]
    return {"items": items, "total": int(raw.get("total") or 0)}


def changes(day, page=1, page_size=25):
    """Partidas with an entry on `day` (Europe/Sofia), from the portal's public entry list."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Sofia")
    d = dt.date.fromisoformat(day)
    start = dt.datetime.combine(d, dt.time(0, 0), tz).isoformat()
    end = dt.datetime.combine(d, dt.time(23, 59, 59, 999000), tz).isoformat(timespec="milliseconds")
    q = {"dateFrom": start, "dateTo": end, "page": page, "pageSize": page_size}
    raw = json.loads(get(f"{TR_PORTAL_ENTRIES}?{urllib.parse.urlencode(q)}", accept="application/json"))
    items = [{"uic": r["uic"], "date": r.get("date"), "name": r.get("companyFullName") or ""}
             for r in raw if UIC9.match(str(r.get("uic") or ""))]
    return {"items": items, "has_more": len(raw) == page_size}

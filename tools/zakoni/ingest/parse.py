"""Parsers of the resources of the data set 'Справки от Портала за обществени консултации' (data.egov.bg, АМС).

Each parser takes the records of one answer (the first is the legend with the field labels, not data), checks the
shape of every record (an unknown or missing field or a wrong type is a ShapeError and nothing is written), and returns
the rows of the silver tables. Personal data never gets here: the names of those who commented, the contact persons of
the consultations, the names of natural persons who were paid for an assessment, and a 10-digit 'ЕИК' (which can be
an ЕГН) are dropped at this point.
"""
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .stream import ShapeError
from .text import iso, one_line, plain, scrub, sentinel

S, I, B, L, D, N = str, int, bool, list, dict, type(None)


@dataclass
class Parsed:
    tables: dict = field(default_factory=dict)
    count: int = 0                    # records of the answer without the legend
    notes: dict = field(default_factory=dict)

    def add(self, table, row):
        self.tables.setdefault(table, []).append(row)


def check(rec, spec, where):
    extra, missing = rec.keys() - spec.keys(), spec.keys() - rec.keys()
    if extra or missing:
        raise ShapeError(f"{where}: непознати полета {sorted(extra)}, липсващи {sorted(missing)}")
    for k, types in spec.items():
        if type(rec[k]) not in types:
            raise ShapeError(f"{where}: полето {k} е {type(rec[k]).__name__}, очаква се {'/'.join(t.__name__ for t in types)}")


def legend(it, spec, key, label, where):
    head = next(it, None)
    if head is None:
        raise ShapeError(f"{where}: няма нито легенда, нито данни")
    # the legend gives a label to the fields; it can name a field the records do not have (ПРИС: date_updated_at) or
    # call one by another key (strategic documents: title for name), so only the label of the key field is compared
    if head.get(key) != label or not all(isinstance(v, str) for v in head.values()):
        raise ShapeError(f"{where}: първият запис не е легендата с етикетите ({key} = {head.get(key)!r}, очаква се {label!r})")


def date(s, where):
    try:
        return iso(s, where)
    except ValueError as e:
        raise ShapeError(str(e)) from None


def odate(s, where):
    return None if s is None else date(s, where)


# ---------------------------------------------------------------- acts of the Council of Ministers (ПРИС)
PRIS = {"pris_id": (I,), "doc_num": (S,), "doc_accepted_date": (S,), "doc_about": (S,), "legal_act_type": (S,),
        "legal_reason": (S, N), "importer": (S, N), "institutions": (L, N), "version": (N, S, I), "protocol": (S, N),
        "public_consultation_number": (S, N), "state_gazette_number": (I, N), "state_gazette_year": (S, I, N),
        "active": (B,), "date_published_at": (S,), "date_deleted_at": (S, N), "tags": (L, N), "related": (L, N)}


def gazette_year(raw, accepted):
    """The register writes the year of the State Gazette in full ('2021') or in two digits ('89'). Two digits are the
    year of the act's century, or its neighbour: the one within a year of the act's date; otherwise unknown."""
    if raw is None:
        return None
    s = str(raw).strip()
    if re.fullmatch(r"\d{4}", s):
        return int(s)
    if re.fullmatch(r"\d{2}", s):
        for c in (1900, 2000):
            if abs(c + int(s) - accepted.year) <= 1:
                return c + int(s)
    return None


def is_confidential(about):
    return bool(about) and "поверител" in about.lower() and len(about) < 40


def pris(records, origin):
    it = iter(records)
    legend(it, PRIS, "pris_id", "ID", "ПРИС")
    out = Parsed()
    seen = set()
    for rec in it:
        check(rec, PRIS, "ПРИС")
        pid = rec["pris_id"]
        if pid in seen:
            raise ShapeError(f"ПРИС: pris_id {pid} се повтаря")
        seen.add(pid)
        out.count += 1
        accepted = date(rec["doc_accepted_date"], f"ПРИС {pid} дата на издаване")
        about = plain(rec["doc_about"]) or ""
        year = gazette_year(rec["state_gazette_year"], accepted)
        out.add("pris_act", dict(
            pris_id=pid, origin=origin, doc_num=rec["doc_num"], accepted=accepted, about=about, about_raw=scrub(rec["doc_about"]),
            legal_act_type=rec["legal_act_type"], legal_reason=plain(rec["legal_reason"]), importer=one_line(rec["importer"]),
            protocol=rec["protocol"], public_consultation_number=rec["public_consultation_number"],
            gazette_number=rec["state_gazette_number"],
            gazette_year_raw=None if rec["state_gazette_year"] is None else str(rec["state_gazette_year"]),
            gazette_year=year, version=None if rec["version"] is None else str(rec["version"]), active=rec["active"],
            published=date(rec["date_published_at"], f"ПРИС {pid} дата на публикуване"),
            deleted=odate(rec["date_deleted_at"], f"ПРИС {pid} изтрит на"), confidential=is_confidential(about)))
        for n, i in enumerate(rec["institutions"] or []):
            check(i, {"id": (I,), "name": (S,)}, f"ПРИС {pid} институция")
            out.add("pris_institution", dict(pris_id=pid, ord=n, institution_id=i["id"], name=i["name"]))
        for n, t in enumerate(rec["tags"] or []):
            if type(t) is not S:
                raise ShapeError(f"ПРИС {pid}: термин, който не е текст")
            out.add("pris_tag", dict(pris_id=pid, ord=n, tag=t))
        for n, r in enumerate(rec["related"] or []):
            check(r, {"relation_type": (S,), "pris_id": (I,), "act_type": (S,), "act_name": (S,)}, f"ПРИС {pid} връзка")
            out.add("pris_related", dict(pris_id=pid, ord=n, relation_type=r["relation_type"], related_pris_id=r["pris_id"],
                                         act_type=r["act_type"], act_name=r["act_name"]))
    return out


# ---------------------------------------------------------------- public consultations (the unified report)
CONSULTATION = {
    "reg_num": (S,), "name": (S,), "description": (S,), "consultation_type": (S,), "act_type": (S, N), "date_open": (S,),
    "date_close": (S,), "short_term_reason": (S, N), "active": (B,), "policy_area": (S,),
    "legislative_program_id": (I, N), "operational_program_id": (I, N), "responsible_institution_id": (I,),
    "responsible_institution_name": (S, N), "responsible_institution_address": (S, N), "proposal_ways": (S, N),
    "contacts": (L, N), "law_name": (S, N), "law_id": (I, N), "pris_id": (I, N), "author_name": (S, N),
    "comments": (L, N), "consultation_document": (D,), "files": (L, N)}
# what is compared when a number occurs twice: the rest (documents, comments) is merged
_DOCS = ("consultation_document", "files", "comments", "contacts")
FILE_KINDS = {"consultation_document": "Консултационен документ"}


def consultation(records):
    it = iter(records)
    legend(it, CONSULTATION, "reg_num", "№", "Консултации")
    out = Parsed()
    by_reg = {}
    folded = 0
    for rec in it:
        check(rec, CONSULTATION, "Консултации")
        reg = rec["reg_num"]
        if not re.fullmatch(r"\d+-K", reg):
            raise ShapeError(f"Консултации: номер {reg!r} не е във вида 12345-K")
        out.count += 1
        files = _files(rec, reg)
        row = _consultation_row(rec)
        if reg in by_reg:
            # the portal has numbers that stand twice (12367-K on 02.10.2026: two consultation documents of one
            # consultation). The same consultation twice is one; two different ones under one number are not ours to merge.
            first, first_files = by_reg[reg]
            if first != row:
                raise ShapeError(f"Консултации: номер {reg} се повтаря с различни данни")
            first_files.extend(f for f in files if f not in first_files)
            folded += 1
            continue
        by_reg[reg] = (row, files)
    for reg, (row, files) in by_reg.items():
        out.add("consultation", row)
        for n, f in enumerate(files):
            out.add("consultation_file", dict(reg_num=reg, ord=n, kind=f[0], doc_date=f[1], link=f[2]))
    out.notes["folded"] = folded
    return out


def _files(rec, reg):
    res = []
    doc = rec["consultation_document"]
    if doc.get("file"):
        res.append((FILE_KINDS["consultation_document"], None, doc["file"]))
    for f in rec["files"] or []:
        check(f, {"type": (S,), "versions": (L,)}, f"Консултация {reg} файл")
        for v in f["versions"]:
            check(v, {"date": (S, N), "link": (S,)}, f"Консултация {reg} версия на файл")
            res.append((f["type"], odate(v["date"], f"Консултация {reg} дата на файл"), v["link"]))
    return res


def _consultation_row(rec):
    reg = rec["reg_num"]
    comments = rec["comments"] or []
    dates = []
    for c in comments:
        check(c, {"date": (S, N), "text": (S, N), "author_name": (S, N)}, f"Консултация {reg} коментар")
        if c["date"]:
            dates.append(date(c["date"], f"Консултация {reg} дата на коментар"))
    for c in rec["contacts"] or []:
        check(c, {"name": (S, N), "email": (S, N)}, f"Консултация {reg} контакт")
    opened, closed = date(rec["date_open"], f"Консултация {reg} откриване"), date(rec["date_close"], f"Консултация {reg} приключване")
    if closed < opened:
        raise ShapeError(f"Консултация {reg}: приключва преди да е открита")
    return dict(
        reg_num=reg, consultation_type=rec["consultation_type"], name=one_line(rec["name"]), description=plain(rec["description"]),
        description_raw=scrub(rec["description"]), act_type=rec["act_type"], date_open=opened, date_close=closed,
        short_term_reason=plain(rec["short_term_reason"]), active=rec["active"], policy_area=rec["policy_area"],
        legislative_program_id=rec["legislative_program_id"], operational_program_id=rec["operational_program_id"],
        institution_id=rec["responsible_institution_id"], institution_name=rec["responsible_institution_name"],
        institution_address=one_line(rec["responsible_institution_address"]), proposal_ways=plain(rec["proposal_ways"]),
        law_name=rec["law_name"], law_id=rec["law_id"], pris_id=rec["pris_id"], comment_count=len(comments),
        comment_first=min(dates, default=None), comment_last=max(dates, default=None))


# ---------------------------------------------------------------- strategic documents
STRATEGY = {"name": (S,), "level": (S, N), "policy_area": (S, N), "strategic_document_type": (S, N), "act_link": (S, N),
            "pris_act_id": (I, N), "author_institutions": (L,), "accepting_institution_type": (S, N),
            "document_date": (S, N), "public_consultation_number": (S, N), "active": (B,), "link_to_monitorstat": (S, N),
            "date_accepted": (S,), "date_expiring": (S, N), "files": (L,), "subdocuments": (L,)}
SUBDOC = {"id": (I,), "name": (S,), "level": (S, N), "policy_area": (S, N), "strategic_document_type": (S, N),
          "pris_act_id": (I, N), "author_institutions": (L, N), "accepting_institution_type": (S, N),
          "document_date": (S, N), "public_consultation_number": (S, N), "active": (B,), "link_to_monitorstat": (S, N),
          "date_accepted": (S, N), "date_expiring": (S, N), "files": (L, N), "subdocuments": (L, N)}


def strategy(records):
    it = iter(records)
    legend(it, STRATEGY, "title", "Заглавие", "Стратегически документи")
    out = Parsed()
    n_key = {}
    for rec in it:
        check(rec, STRATEGY, "Стратегически документи")
        out.count += 1
        # the report has no identifier of a document: the key is the title, the level, the area and the date of acceptance;
        # a second document with the same four (the report has such pairs) gets the next number
        base = hashlib.sha1("|".join(str(rec[k]) for k in ("name", "level", "policy_area", "date_accepted")).encode()).hexdigest()[:16]
        n = n_key[base] = n_key.get(base, 0) + 1
        key = base if n == 1 else f"{base}-{n}"
        acc, exp = date(rec["date_accepted"], "Стратегически документ дата на приемане"), odate(rec["date_expiring"], "Стратегически документ валидност")
        out.add("strategy_doc", dict(
            doc_key=key, name=one_line(rec["name"]), level=rec["level"], policy_area=rec["policy_area"],
            doc_type=rec["strategic_document_type"], act_link=rec["act_link"], pris_act_id=rec["pris_act_id"],
            accepting_institution_type=rec["accepting_institution_type"], document_date=odate(rec["document_date"], "дата на акта"),
            public_consultation_number=rec["public_consultation_number"], active=rec["active"],
            date_accepted=sentinel(acc), date_accepted_raw=rec["date_accepted"], date_expiring=sentinel(exp),
            date_expiring_raw=rec["date_expiring"]))
        for i, a in enumerate(rec["author_institutions"]):
            if type(a) is not S:
                raise ShapeError("Стратегически документ: вносител, който не е текст")
            out.add("strategy_author", dict(doc_key=key, ord=i, institution=a))
        for i, f in enumerate(rec["files"]):
            check(f, {"name": (S, N), "path": (S,), "version": (S, N)}, "Стратегически документ файл")
            out.add("strategy_file", dict(doc_key=key, ord=i, name=f["name"], path=f["path"], version=f["version"]))
        _subs(out, key, rec["subdocuments"], None, [0])
    return out


def _subs(out, key, subs, parent, counter):
    for s in subs:
        check(s, SUBDOC, "Стратегически документ, дъщерен")
        a, e = odate(s["date_accepted"], "дъщерен документ"), odate(s["date_expiring"], "дъщерен документ")
        out.add("strategy_sub", dict(doc_key=key, ord=counter[0], sub_id=s["id"], parent_sub_id=parent, name=one_line(s["name"]),
                                     level=s["level"], policy_area=s["policy_area"], doc_type=s["strategic_document_type"],
                                     pris_act_id=s["pris_act_id"], date_accepted=sentinel(a), date_expiring=sentinel(e)))
        counter[0] += 1
        _subs(out, key, s["subdocuments"] or [], s["id"], counter)


# ---------------------------------------------------------------- contracts for impact assessments
IMPACT = {"eik": (S, N), "date_contract": (S, N), "price": (S, N), "active": (B,), "institution_id": (I,),
          "institution_name": (S,), "executor": (S,), "contract_subject": (S,), "services_description": (S,)}
_ORG = re.compile(r"ООД|ЕООД|\bАД\b|ЕАД|ДЗЗД|\bЕТ\b|\bСД\b|\bКД\b|[Дд]ружество|[Фф]ондация|[Сс]дружение|[Уу]ниверситет|"
                  r"[Ии]нститут|[Сс]индикат|[Аа]съциация|[Аа]генция|[Цц]ентър|[Фф]онд\b|[Кк]онфедерация|[Кк]амара|[Аа]кадемия", re.U)


def eik_valid(e):
    """ЕИК/БУЛСТАТ of 9 or 13 digits with its check digit (weights 1..8, else 3..10; for 13 digits 2,7,3,5, else 4,9,5,7)."""
    if not re.fullmatch(r"\d{9}|\d{13}", e or ""):
        return False
    d = [int(c) for c in e]

    def ctrl(digits, w1, w2):
        r = sum(a * b for a, b in zip(digits, w1)) % 11
        if r == 10:
            r = sum(a * b for a, b in zip(digits, w2)) % 11
            if r == 10:
                r = 0
        return r

    if ctrl(d[:8], range(1, 9), range(3, 11)) != d[8]:
        return False
    return len(d) == 9 or ctrl(d[8:12], (2, 7, 3, 5), (4, 9, 5, 7)) == d[12]


def impact(records):
    it = iter(records)
    legend(it, IMPACT, "eik", "ЕИК", "Оценка на въздействието")
    out = Parsed()
    seen = {}
    dropped = 0
    for rec in it:
        check(rec, IMPACT, "Оценка на въздействието")
        out.count += 1
        eik = rec["eik"].strip() if rec["eik"] else None
        if eik and not re.fullmatch(r"\d{9}|\d{13}", eik):
            eik, dropped = None, dropped + 1          # a 10-digit number can be an ЕГН: it is not kept at all
        name = one_line(rec["executor"])
        kind = "юридическо лице" if eik or _ORG.search(name or "") else "физическо лице"
        try:
            price = None if rec["price"] is None else Decimal(rec["price"])
        except InvalidOperation:
            raise ShapeError(f"Оценка на въздействието: цена {rec['price']!r}") from None
        subject, desc = plain(rec["contract_subject"]), plain(rec["services_description"])
        base = hashlib.sha1("|".join(str(x) for x in (rec["institution_id"], rec["date_contract"], rec["price"], eik, name, subject)).encode()).hexdigest()[:16]
        n = seen[base] = seen.get(base, 0) + 1
        out.add("impact_contract", dict(
            ic_key=base if n == 1 else f"{base}-{n}", institution_id=rec["institution_id"], institution_name=rec["institution_name"],
            contract_date=odate(rec["date_contract"], "дата на договор"), price_bgn=price, eik=eik,
            executor=name if kind == "юридическо лице" else None, executor_kind=kind, subject=subject, description=desc,
            active=rec["active"]))
    out.notes["dropped_ids"] = dropped
    return out


# ---------------------------------------------------------------- reports used to cross-check the unified one
STANDARD = {"title": (S,), "field_of_action_name": (S,), "status": (S,), "institution_name": (S,), "act_type_name": (S, N),
            "active_in_days": (I, N), "short_term_reason": (S, N), "comments": (I,), "has_proposal_report": (B,),
            "missing_documents": (S,)}
BY_INSTITUTION = {"name": (S,), "pc_cnt": (I,), "less_days_cnt": (I,), "no_less_days_reason_cnt": (I,), "has_report": (I,),
                  "missing_documents": (S,)}
BY_AREA = {"name": (S,), "pc_cnt": (I,)}


def rows_of(records, spec, key, label, where):
    it = iter(records)
    legend(it, spec, key, label, where)
    out = []
    for rec in it:
        check(rec, spec, where)
        out.append(rec)
    return out


def standard(records):
    return rows_of(records, STANDARD, "title", "Наименование", "Справка, стандартна")


def by_institution(records):
    return rows_of(records, BY_INSTITUTION, "name", "Институция", "Справка по институции")


def by_area(records):
    return rows_of(records, BY_AREA, "name", "Област на политика", "Справка по области")


def count_only(records, key, label, where):
    """Resources that are read and counted but not loaded (the shape of the legend is still checked)."""
    it = iter(records)
    head = next(it, None)
    if head is None or head.get(key) != label:
        raise ShapeError(f"{where}: първият запис не е легендата ({key} = {None if head is None else head.get(key)!r})")
    return sum(1 for _ in it)

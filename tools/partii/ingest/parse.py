"""Parsers of the answers of ЕРИК: bytes in, typed rows out. Nothing here touches the database.

Every table answer is checked against its own shape (the exact set of fields) and against the counters and totals the
source gives in the same answer. An unknown or missing field, another type, or a counter that does not agree raises
ShapeError (Mismatch for the counters): the answer is then not written anywhere.

The names of donors, candidates and checked persons are natural persons' names. They are read only to tell a legal
entity from a person and never leave this module for a person.
"""
import datetime as dt
import hashlib
import html as htmllib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation

from . import config


class ShapeError(Exception):
    """The answer is not what the source should give."""


class Mismatch(ShapeError):
    """The rows do not agree with the counter or the total of the same answer."""


MONTHS = {m: i + 1 for i, m in enumerate("януари февруари март април май юни юли август септември октомври ноември декември".split())}
NO_DATE = {"1753-01-01", "0001-01-01"}      # the source's empty dates
RECIPIENT = {"coalitionMembers", "id", "masterId", "electionId", "participantTypeId", "tradeRegisterId", "abreviation", "registeredName",
             "registryNumber", "contactName", "contactPhone", "contactSettlement", "contactPostCode", "contactAddress", "contactEmail",
             "isArchived", "regIn", "registryNumberNumericPart", "registrationDate", "registrationSettlement", "registrationAddressDetails",
             "phone", "personResponsible", "personEgn", "dateEnd", "bankAccInDate", "bankName", "bankIban", "bankBic", "notes", "archivedDate",
             "contactSettlementEkatteCode", "participant", "election", "contactSettlementEkatte"}
RECIPIENT_LOCAL = RECIPIENT | {"cikId", "subElectionId", "districtId", "municipalityId", "mayorId", "tmpGuid", "cityAreaId", "typeOfSubElection",
                               "district", "municipality", "mayor", "cityArea"}
DONOR = {"cik", "rik", "oik", "fullName", "donateTo", "donationDate", "donationType", "description", "donationPriceTxt", "donationPrice",
         "donationValueTxt", "donationValue", "declarationLink", "hasDeclaration", "hasDefacedDeclaration"}
CANDIDATE = {"name", "participant", "dateDonation", "candidateType", "moneyDonation", "moneyDonationTxt", "noMoneyDonation", "noMoneyDonationTxt",
             "fileDownLoadLink", "donationSum", "hasDeclaration", "hasDefacedDeclaration"}
AGENCY = {"name", "eik", "typeOfAgency", "participant", "descr"}
RESULT = {"fullName", "money", "nonMoney", "sum", "compliance", "reportId"}
PARTICIPANT = {"id", "registeredName", "registryNumber", "participantName", "participantId"}
PARTICIPANT_LOCAL = PARTICIPANT | {"typeOfSubElectionName", "municipalityName", "mayorName", "cityAreaName"}
KINDS = {0: "party", 1: "coalition", 2: "committee", 3: "local_coalition"}
KIND_NAMES = {"Партия": "party", "Коалиция": "coalition", "Инициативен комитет": "committee", "Местна коалиция": "local_coalition"}
DONATION_KINDS = {"Парично": "money", "Непарично": "in_kind"}
# a legal form as a word of its own marks a legal entity; "ЕТ" is a sole trader, whose name is a person's name
LEGAL_ABBR = re.compile(r"(?<![\w-])(ЕООД|ООД|ЕАД|АД|АДСИЦ|СД|КД|КДА|ДЗЗД)(?![\w-])")
LEGAL_WORD = re.compile(r"(?<!\w)(сдружение|фондация|кооперация)(?!\w)", re.I)
SOLE = re.compile(r"^\s*ЕТ(?![\w-])")


def clean(s):
    return re.sub(r"\s+", " ", s).strip() if isinstance(s, str) else s


def text_of(page):
    body = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", page)
    return clean(htmllib.unescape(re.sub(r"<[^>]+>", " ", body)))


def decode(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ShapeError("отговорът не е UTF-8: " + str(e)) from e


def table(data, row_keys, extra=()):
    """The DataTables envelope: draw, recordsTotal, recordsFiltered, data[], and what `extra` names. Returns the dict."""
    text = decode(data).strip()
    if not text:
        raise ShapeError("празен отговор")
    if text[:1] == "<":
        raise ShapeError("HTML страница вместо таблица")
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ShapeError("невалиден JSON: " + str(e)) from e
    need = {"draw", "recordsTotal", "recordsFiltered", "data", *extra}
    if not isinstance(d, dict):
        raise ShapeError("отговорът не е обект")
    if set(d) - need - {"additionalParameters"} or need - set(d):
        raise ShapeError("непознати или липсващи полета на таблицата: " + ", ".join(sorted((set(d) - {"additionalParameters"}) ^ need)))
    if not isinstance(d["data"], list) or not isinstance(d["recordsTotal"], int) or isinstance(d["recordsTotal"], bool):
        raise ShapeError("data не е списък или recordsTotal не е число")
    for i, r in enumerate(d["data"]):
        if not isinstance(r, dict) or not any(set(r) == k for k in row_keys):
            got = set(r) if isinstance(r, dict) else set()
            raise ShapeError(f"ред {i + 1}: непознати или липсващи полета: " + ", ".join(sorted(got ^ row_keys[0])))
    if len(d["data"]) != d["recordsTotal"] or d["recordsFiltered"] != d["recordsTotal"]:
        raise Mismatch(f"редовете са {len(d['data'])}, а източникът брои {d['recordsTotal']} (филтрирани {d['recordsFiltered']})")
    return d


def money(text, number, what):
    """The amount from the source's text (exact) checked against its number (a float)."""
    try:
        v = Decimal(text) if isinstance(text, str) else Decimal(repr(text))
    except (InvalidOperation, ValueError) as e:
        raise ShapeError(f"{what}: не е число: {text!r}") from e
    if not v.is_finite() or v < 0:
        raise ShapeError(f"{what}: отрицателна или безкрайна сума: {text!r}")
    if not isinstance(number, (int, float)) or isinstance(number, bool) or abs(Decimal(repr(float(number))) - v) > config.SUM_TOLERANCE:
        raise ShapeError(f"{what}: текстът {text!r} и числото {number!r} са различни")
    return v


def total(d, name, rows_sum, what):
    v = d.get(name)
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ShapeError(f"{what}: общата сума {name} липсва или не е число")
    if abs(Decimal(repr(float(v))) - rows_sum) > config.SUM_TOLERANCE:
        raise Mismatch(f"{what}: сборът на редовете е {rows_sum}, а източникът дава {v}")
    return rows_sum


def day(value, what):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?", value):
        raise ShapeError(f"{what}: не е дата: {value!r}")
    if value[:10] in NO_DATE:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError as e:
        raise ShapeError(f"{what}: не е дата: {value!r}") from e


def declaration(row, what):
    """published: the declaration of origin is in the register (personal data removed); processing: filed, still
    being prepared for publishing; none: no declaration in the register."""
    a, b = row["hasDeclaration"], row["hasDefacedDeclaration"]
    if not isinstance(a, bool) or not isinstance(b, bool):
        raise ShapeError(f"{what}: признакът за декларация не е да/не")
    return "published" if b else "processing" if a else "none"


def donor_kind(name):
    if not isinstance(name, str) or not name.strip():
        return "person"
    if SOLE.search(name):
        return "sole_trader"
    return "legal" if LEGAL_ABBR.search(name) or LEGAL_WORD.search(name) else "person"


def key(*parts):
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:20]


def numbered(rows):
    """Gives equal rows an ordinal (1, 2, ...) in the order of the file: the source has rows that are the same in every field."""
    seen = {}
    for r in rows:
        seen[r["row_key"]] = r["ordinal"] = seen.get(r["row_key"], 0) + 1
    return rows


# ---------- the tables ----------

def donors(data):
    d = table(data, [DONOR])
    extra = d.get("additionalParameters")
    if not isinstance(extra, dict) or set(extra) != {"TotalDonationPrice", "TotalDonationValue"}:
        raise ShapeError("дарители: липсват общите суми TotalDonationPrice и TotalDonationValue")
    rows = []
    for i, r in enumerate(d["data"]):
        what = f"дарители, ред {i + 1}"
        homes = [(k, r[k]) for k in ("cik", "rik", "oik") if r[k] is not None]
        if len(homes) != 1 or not isinstance(homes[0][1], dict) or set(homes[0][1]) not in (RECIPIENT, RECIPIENT_LOCAL):
            raise ShapeError(f"{what}: получателят не е точно един обект с познатите полета")
        o = homes[0][1]
        if not isinstance(o["id"], int) or o["regIn"] not in (1, 2, 3) or o["participantTypeId"] not in KINDS or not isinstance(o["registeredName"], str):
            raise ShapeError(f"{what}: непознат получател (id, regIn или вид)")
        if r["donationType"] not in DONATION_KINDS or not isinstance(r["donateTo"], str):
            raise ShapeError(f"{what}: непознат вид дарение {r['donationType']!r}")
        stated = DONATION_KINDS[r["donationType"]]
        m = money(r["donationPriceTxt"], r["donationPrice"], what + ", размер")
        v = money(r["donationValueTxt"], r["donationValue"], what + ", стойност")
        # the column the amount stands in decides (that is what the totals of the source add up); the stated kind is kept beside it:
        # the register has rows marked "Парично" whose amount is in the column of the non-monetary value
        kind = "both" if m and v else "money" if m else "in_kind" if v else stated
        who = donor_kind(r["fullName"])
        row = dict(n=i + 1, reg_in=o["regIn"], source_id=o["id"], recipient_kind=KINDS[o["participantTypeId"]], recipient_name=clean(o["registeredName"]),
                   registry_number=clean(o["registryNumber"]), label=clean(r["donateTo"]), day=day(r["donationDate"], what), kind=kind, stated_kind=stated, money=m, in_kind=v,
                   decl=declaration(r, what), donor_kind=who, donor_name=clean(r["fullName"]) if who == "legal" else None)
        # the identity of a person's row has no name in it: two people who gave the same on the same day are told apart only by the ordinal
        row["row_key"] = key(o["regIn"], o["id"], row["day"], stated, r["donationPriceTxt"], r["donationValueTxt"], row["decl"], row["donor_name"])
        rows.append(row)
    return dict(records_total=d["recordsTotal"], rows=numbered(rows),
                total_money=total(extra, "TotalDonationPrice", sum((r["money"] for r in rows), Decimal(0)), "дарители, парични"),
                total_in_kind=total(extra, "TotalDonationValue", sum((r["in_kind"] for r in rows), Decimal(0)), "дарители, непарични"))


def candidates(data):
    d = table(data, [CANDIDATE], extra=("totalDonationsMoney", "totalDonationsNoMoney"))
    rows = []
    for i, r in enumerate(d["data"]):
        what = f"кандидати, ред {i + 1}"
        if not isinstance(r["participant"], str) or not (r["candidateType"] is None or isinstance(r["candidateType"], str)):
            raise ShapeError(f"{what}: участникът или видът не са текст")
        m = money(r["moneyDonationTxt"], r["moneyDonation"], what + ", парични")
        v = money(r["noMoneyDonationTxt"], r["noMoneyDonation"], what + ", непарични")
        row = dict(n=i + 1, label=clean(r["participant"]), role=clean(r["candidateType"]) or None, day=day(r["dateDonation"], what), money=m, in_kind=v,
                   decl=declaration(r, what))
        row["row_key"] = key(row["label"], row["role"], row["day"], r["moneyDonationTxt"], r["noMoneyDonationTxt"], row["decl"])
        rows.append(row)
    return dict(records_total=d["recordsTotal"], rows=numbered(rows),
                total_money=total(d, "totalDonationsMoney", sum((r["money"] for r in rows), Decimal(0)), "кандидати, парични"),
                total_in_kind=total(d, "totalDonationsNoMoney", sum((r["in_kind"] for r in rows), Decimal(0)), "кандидати, непарични"))


def eik_ok(eik):
    """The check digit of a 9-digit ЕИК or a 13-digit БУЛСТАТ of a branch."""
    if not isinstance(eik, str) or not eik.isdigit() or len(eik) not in (9, 13):
        return False
    d = [int(x) for x in eik]
    s = sum(d[i] * (i + 1) for i in range(8)) % 11
    if s == 10:
        s = sum(d[i] * (i + 3) for i in range(8)) % 11 % 10
    if s != d[8]:
        return False
    if len(eik) == 9:
        return True
    s = sum(d[8 + i] * w for i, w in enumerate((2, 7, 3, 5))) % 11
    if s == 10:
        s = sum(d[8 + i] * w for i, w in enumerate((4, 9, 5, 7))) % 11 % 10
    return s == d[12]


def agencies(data):
    d = table(data, [AGENCY])
    rows = []
    for i, r in enumerate(d["data"]):
        what = f"агенции, ред {i + 1}"
        if not isinstance(r["name"], str) or not isinstance(r["participant"], str) or not all(r[k] is None or isinstance(r[k], str) for k in ("eik", "typeOfAgency", "descr")):
            raise ShapeError(f"{what}: поле не е текст")
        eik = clean(r["eik"]) or None
        row = dict(n=i + 1, label=clean(r["participant"]), name=clean(r["name"]), eik=eik, eik_valid=eik_ok(eik) if eik else None,
                   kind=clean(r["typeOfAgency"]) or None, descr=clean(r["descr"]) or None)
        row["row_key"] = key(row["label"], row["name"], eik, row["kind"], row["descr"])
        rows.append(row)
    return dict(records_total=d["recordsTotal"], rows=numbered(rows))


def results(data):
    """The check of donated sums against the income of the person. The rows are persons: only their count and sums are kept."""
    d = table(data, [RESULT], extra=("moneyDonationsTotal", "nonMoneyDonationsTotal", "sumDonationsTotal"))
    m = v = s = Decimal(0)
    ok = 0
    for i, r in enumerate(d["data"]):
        what = f"проверки, ред {i + 1}"
        if not isinstance(r["compliance"], bool):
            raise ShapeError(f"{what}: съответствието не е да/не")
        a, b, c = (money(repr(float(r[k])) if isinstance(r[k], (int, float)) and not isinstance(r[k], bool) else None, r[k], what) for k in ("money", "nonMoney", "sum"))
        if abs(a + b - c) > config.SUM_TOLERANCE:
            raise Mismatch(f"{what}: парични и непарични не дават общото")
        m, v, s, ok = m + a, v + b, s + c, ok + r["compliance"]
    return dict(records_total=d["recordsTotal"], checked=len(d["data"]), compliant=ok,
                total_money=total(d, "moneyDonationsTotal", m, "проверки, парични"), total_in_kind=total(d, "nonMoneyDonationsTotal", v, "проверки, непарични"),
                total_sum=total(d, "sumDonationsTotal", s, "проверки, общо"))


def participants(data, reg_in):
    d = table(data, [PARTICIPANT, PARTICIPANT_LOCAL])
    rows, ids = [], set()
    for i, r in enumerate(d["data"]):
        what = f"участници, ред {i + 1}"
        if not isinstance(r["id"], int) or r["participantId"] not in KINDS or KIND_NAMES.get(r["participantName"]) != KINDS[r["participantId"]]:
            raise ShapeError(f"{what}: непознат вид участник {r['participantName']!r} ({r['participantId']!r})")
        if r["id"] in ids:
            raise ShapeError(f"{what}: повторен номер на участник {r['id']}")
        ids.add(r["id"])
        rows.append(dict(reg_in=reg_in, source_id=r["id"], kind=KINDS[r["participantId"]], kind_name=r["participantName"], name=clean(r["registeredName"]),
                         registry_number=clean(r["registryNumber"]), sub_election=clean(r.get("typeOfSubElectionName")),
                         municipality=clean(r.get("municipalityName")), settlement=clean(r.get("mayorName")), city_area=clean(r.get("cityAreaName"))))
    return dict(records_total=d["recordsTotal"], rows=rows)


# ---------- the pages ----------

def home(data):
    """[(election id, title)] in the order of the page (the newest first)."""
    page = decode(data)
    out, seen = [], set()
    for eid, title in re.findall(r'href="/Reports\?electionId=(\d+)"[^>]*>(.*?)</a>', page, re.S):
        if int(eid) not in seen:
            seen.add(int(eid))
            out.append((int(eid), clean(htmllib.unescape(re.sub(r"<[^>]+>", " ", title)))))
    if not out or any(not t for _, t in out):
        raise ShapeError("началната страница няма списък с избори")
    return out


def title_info(title):
    """(kind, day) from the title of an election; the day is None when the title has none."""
    t = title.lower()
    kind = ("president" if "президент" in t else "european" if "европейския парламент" in t else "parliament" if "народно събрание" in t
            else "local_partial" if "частич" in t else "local_new" if re.search(r"(?<!\w)нови? избор", t) else "local" if "общински съветници" in t or "кмет" in t
            else "other")
    m = re.search(r"(\d{1,2})\s+(%s)\s+(\d{4})" % "|".join(MONTHS), t)
    if m:
        return kind, dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
    return kind, dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def page_participants(data):
    """The numbers of registered participants the page states, by where they are registered."""
    t = text_of(decode(data))
    out = {}
    for k, name in (("cik", "ЦИК"), ("rik", "РИК"), ("oik", "ОИК")):
        m = re.search(r"Общ брой участници регистрирани в %s:\s*(\d+)" % name, t)
        if not m:
            raise ShapeError("страницата с участниците няма брой за " + name)
        out[k] = int(m.group(1))
    return out


def page_donation(data):
    """The threshold above which a declaration of origin is required, in euro, as the page states it in its text and in its script."""
    page = decode(data)
    m = re.search(r"За дарения над\s*([\d\s]+,\d{2})\s*(\S+)\s*се изисква декларация", text_of(page))
    js = re.search(r'declarationSum\s*=\s*parseFloat\("([\d.]+)"\)', page)
    if not js:
        raise ShapeError("страницата с дарителите няма праг за декларация в таблицата си")
    if not m:
        if Decimal(js.group(1)) == 0:      # an earlier form of the page stated no threshold at all
            return dict(threshold=None, threshold_shown=None, currency=None)
        raise ShapeError("страницата с дарителите има праг в таблицата, но не и в текста")
    if m.group(2) != "€":
        raise ShapeError("прагът за декларация не е в евро: " + m.group(2))
    v = Decimal(m.group(1).replace(" ", "").replace(",", "."))
    exact = Decimal(js.group(1))      # for the elections before the euro the script has the sum in leva divided by 1,95583, the text its rounding
    if abs(v - exact) > Decimal("0.005"):
        raise ShapeError(f"прагът в текста ({v}) и в таблицата ({js.group(1)}) са различни")
    return dict(threshold=exact, threshold_shown=v, currency="EUR")


# ---------- the summary of the reports after the election (a spreadsheet per kind of participant) ----------

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REPORT_KINDS = {"0": "party", "1": "coalition", "2": "committee", "3": "local_coalition"}


def col_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def sheet(data):
    """[[cell text or None]] of the first sheet."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            strings = []
            if "xl/sharedStrings.xml" in z.namelist():
                strings = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(NS + "si")]
            root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ShapeError("справката не е валиден файл на Excel: " + str(e)) from e
    rows = []
    for r in root.iter(NS + "row"):
        cells = {}
        for c in r.findall(NS + "c"):
            v, t = c.find(NS + "v"), c.get("t")
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(NS + "t"))
            elif v is None or v.text is None:
                val = None
            else:
                val = strings[int(v.text)] if t == "s" else v.text
            cells[col_index(c.get("r"))] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def columns(headers):
    """The lines of the report from the headers of the spreadsheet: dicts with col, code, label, section, depth, parent.
    The numbers of the income and of the expenses repeat (1., 1.1, ...), so the section is part of the code."""
    out, section, codes = [], "income", set()
    for i, h in enumerate(headers):
        label = clean(h or "")
        low = label.lower().replace("і", "i")      # the source writes the Roman numerals with the Cyrillic letter і
        m = re.match(r"(?:т\.\s*)?(\d+(?:\.\d+)*)\.?\s*\S", label)
        if low.startswith("общ размер на набраните средства"):
            code, depth, parent, nxt = "income.total", 0, None, "expense"
        elif low.startswith("общ размер на разходите"):
            code, depth, parent, nxt = "expense.total", 0, None, "tail"
        elif section == "tail" and low.startswith("в т.ч.: поети задължения"):
            code, depth, parent, nxt = "expense.unpaid", 1, "expense.total", "tail"
        elif section == "tail" and low.startswith("iii."):
            code, depth, parent, nxt = "in_kind.total", 0, None, "tail"
        elif section == "tail" and low.startswith("iv."):
            code, depth, parent, nxt = "total", 0, None, "tail"
        elif section != "tail" and m:
            num = m.group(1)
            code, depth, nxt = f"{section}.{num}", num.count(".") + 1, section
            parent = f"{section}.{num.rsplit('.', 1)[0]}" if "." in num else None
        elif section == "income" and i == 0 and low.startswith("собствени средства"):      # the local coalitions' sheet leaves the "1." out
            code, depth, parent, nxt = "income.1", 1, None, "income"
        else:
            raise ShapeError(f"справка: непознато заглавие на колона {i + 2}: {label!r}")
        if code in codes or (parent and parent not in codes):
            raise ShapeError(f"справка: повторен код или липсващ родител за {label!r}")
        codes.add(code)
        out.append(dict(col=i + 1, code=code, label=label, section=code.split(".")[0], depth=depth, parent=parent))
        section = nxt
    if not {"income.total", "expense.total", "total"} <= codes:
        raise ShapeError("справка: липсва обща колона")
    return out


def report(data, variant, layouts=None):
    """The summary of the reports of one kind of participant: its title, columns and, for each participant, the amounts as filed.
    `-1` in the file is "nothing filed in this cell": it becomes None, never zero."""
    rows = sheet(data)
    if len(rows) < 3 or not rows[0] or not rows[1] or not (rows[0][0] or "").startswith("Отчет за приходите, разходите"):
        raise ShapeError("справката няма заглавие, избор и ред със заглавия")
    headers = [clean(h) for h in rows[2]]
    if layouts is not None and headers not in layouts.values():
        raise ShapeError("справка: заглавията на колоните не са от познатите")
    cols = columns(headers[1:])
    out = []
    for n, r in enumerate(rows[3:], start=4):
        r = r + [None] * (len(headers) - len(r))
        name = clean(r[0] or "")
        if not name:
            if any(x not in (None, "") for x in r[1:]):
                raise ShapeError(f"справка, ред {n}: суми без име на участник")
            continue
        if len(r) != len(headers):
            raise ShapeError(f"справка, ред {n}: {len(r)} колони вместо {len(headers)}")
        cells = []
        for c, x in zip(cols, r[1:]):
            if x in (None, ""):
                cells.append((c["col"], None, None))
                continue
            try:
                v = Decimal(x)
            except InvalidOperation as e:
                raise ShapeError(f"справка, ред {n}, колона {c['col'] + 1}: не е число: {x!r}") from e
            if not v.is_finite() or (v < 0 and v != -1):
                raise ShapeError(f"справка, ред {n}, колона {c['col'] + 1}: отрицателна сума {x!r}")
            cells.append((c["col"], x, None if v == -1 else v))
        out.append(dict(n=n, label=name, cells=cells))
    content = hashlib.sha256(json.dumps([headers, [(r["label"], [c[1] for c in r["cells"]]) for r in out]], ensure_ascii=False).encode()).hexdigest()
    return dict(kind=REPORT_KINDS[variant], title=clean(rows[1][0]), headers=headers, columns=cols, rows=out, content_sha256=content,
                records_total=len(out))

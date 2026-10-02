"""Reads one day of "Колко струва": a ZIP with one CSV per chain.

Pure functions, no database. Every record of a CSV ends in exactly one place: a valid row, an exact duplicate of a
valid row, or a bad row with its reason. Nothing is corrected: a price is kept as submitted (rounded to 1e-4 only
when it carries more decimals), an odd value is flagged, an unreadable row is kept with the reason.
"""
import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

HEADER = ("Населено място", "Търговски обект", "Наименование на продукта", "Код на продукта", "Категория",
          "Цена на дребно", "Цена в промоция")
MAX_RETAIL_E4 = 5000 * 10_000       # above 5 000 a price is an anomaly, not a price
MAX_E4 = 2_000_000_000              # int4 column
CUT_SIZE = 20 * 1024 * 1024         # the portal cuts a file at 20 MiB (the Billa file of 12.12.2025 stops mid-line there)
CATEGORY_MAX = 2_147_483_647

# flags of a price row (silver.price_span.flags)
RETAIL_HIGH = 1         # retail price above 5 000: out of every figure
PROMO_NOT_BELOW = 2     # promotion >= retail price: not a promotion, the retail price still counts
CATEGORY_UNLISTED = 4   # category outside 1..101, missing or not a number: out of every figure
CONFLICT = 8            # the same shop and code with another price the same day: out of every figure
JUMP = 16               # 5 times up or down against the same shop's previous price: out of every figure
PROMO_ZERO = 32         # promotion given as 0: read as "no promotion" (information)
ROUNDED = 64            # more than 4 decimals in the file (information)
DECIMAL_COMMA = 128     # decimal comma (information)
NO_RETAIL = RETAIL_HIGH | CATEGORY_UNLISTED | CONFLICT | JUMP     # the row is out of the retail figures
NO_PROMO = NO_RETAIL | PROMO_NOT_BELOW                            # and out of the promotion figures
FLAG_NAMES = {RETAIL_HIGH: "retail_high", PROMO_NOT_BELOW: "promo_not_below_retail", CATEGORY_UNLISTED: "category_unlisted",
              CONFLICT: "conflicting_prices", JUMP: "price_jump", PROMO_ZERO: "promo_zero", ROUNDED: "rounded",
              DECIMAL_COMMA: "decimal_comma"}


class ShapeError(Exception):
    """The answer is not what the source promised: nothing is written."""


def eik_valid(eik):
    """EIK (9 digits) and EIK with branch (13 digits) check digits, as in the Commerce Register."""
    if not re.fullmatch(r"\d{9}|\d{13}", eik or ""):
        return False
    d = [int(c) for c in eik]
    s = sum(d[i] * (i + 1) for i in range(8)) % 11
    if s == 10:
        s = sum(d[i] * (i + 3) for i in range(8)) % 11
        if s == 10:
            s = 0
    if s != d[8]:
        return False
    if len(d) == 9:
        return True
    s = (d[8] * 2 + d[9] * 7 + d[10] * 3 + d[11] * 5) % 11
    if s == 10:
        s = (d[8] * 4 + d[9] * 9 + d[10] * 5 + d[11] * 7) % 11
        if s == 10:
            s = 0
    return s == d[12]


NAME = re.compile(r"^(?P<name>.*)_(?:BG)?(?P<eik>\d{9}|\d{13})\.csv$", re.S)


def chain_from_name(member):
    """`<Chain (Firm)>_<EIK>.csv`; some files carry a `BG` prefix before the EIK. None when there is no EIK."""
    base = member.rsplit("/", 1)[-1]
    m = NAME.match(base)
    if not m:
        return None
    return m["name"].strip(), m["eik"], eik_valid(m["eik"])


def read_zip(raw):
    """[(member name, bytes)] of a ZIP. An empty file, an HTML page or a broken ZIP is a ShapeError."""
    if not raw:
        raise ShapeError("Празен отговор")
    if raw[:2] != b"PK":
        kind = "HTML страница" if raw.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")) else "не е ZIP"
        raise ShapeError("Очакван ZIP, получено: " + kind)
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        bad = z.testzip()
        if bad:
            raise ShapeError("Повреден ZIP: " + bad)
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        return [(n, z.read(n)) for n in names]
    except zipfile.BadZipFile as e:
        raise ShapeError("Повреден ZIP: " + str(e)) from e


PRICE = re.compile(r"^(\d+)(?:([.,])(\d*))?$")


def price_e4(text):
    """(value in 1e-4 units, flags) or (None, reason). '1.99' -> 19900, '1,99' -> 19900 + DECIMAL_COMMA, '' -> None."""
    t = (text or "").strip()
    if t == "":
        return None, "empty"
    m = PRICE.match(t)
    if not m:
        return None, "not_a_number"
    whole, sep, frac = m.group(1), m.group(2), m.group(3) or ""
    flags = DECIMAL_COMMA if sep == "," else 0
    if len(frac) > 4:
        flags |= ROUNDED
    v = int((Decimal(whole + "." + (frac or "0")) * 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if v > MAX_E4:
        return None, "out_of_range"
    return v, flags


EKATTE = re.compile(r"^(\d{1,6})(?:-(\d{2}))?$")


def place(text):
    """(ekatte 5 digits or None, district '68134-02' or None). The files lose leading zeros ('7702')."""
    m = EKATTE.match((text or "").strip())
    if not m:
        return None, None
    digits = m.group(1)
    if len(digits) == 6:
        if digits[0] != "0":       # six digits are only a five-digit code with one zero too many ("068134")
            return None, None
        digits = digits[1:]
    e = digits.zfill(5)
    return e, (e + "-" + m.group(2) if m.group(2) else None)


def category(text):
    """(int or None, flags)."""
    t = (text or "").strip().strip('"').strip()
    if re.fullmatch(r"\d{1,10}", t) and int(t) <= CATEGORY_MAX:
        c = int(t)
        return c, 0 if 1 <= c <= 101 else CATEGORY_UNLISTED
    return None, CATEGORY_UNLISTED


@dataclass(slots=True)
class Row:
    place_raw: str
    ekatte: str
    district: str
    store: str
    code: str
    name: str
    category: int
    retail: int
    promo: int
    flags: int


@dataclass
class FileResult:
    member: str
    chain_name: str = None
    eik: str = None
    eik_valid: bool = False
    delimiter: str = ","
    bom: bool = False
    encoding: str = "utf-8"
    truncated: bool = False           # cut at 20 MiB or in the middle of a character: the last record is not trusted
    records: int = 0                  # data records, header and blank lines not counted
    blank: int = 0
    rows: list = field(default_factory=list)
    dups: int = 0                     # records identical to an earlier valid record of the same shop and code
    bad: list = field(default_factory=list)   # (line_no, reason, raw)
    error: str = None                 # the whole file is unreadable: its records are in `bad` as reason `file`
    digest: int = 0                   # order-independent hash of the valid rows, to find files that copy each other
    copy_of: str = None               # ЕИК of the file this one is an exact copy of (set by parse_day)


def _norm(h):
    return h.strip().strip('"').strip()


def parse_csv(member, data):
    """One chain's CSV. Raises nothing: a whole-file problem is `error` and every record becomes a bad row."""
    r = FileResult(member)
    named = chain_from_name(member)
    if named:
        r.chain_name, r.eik, r.eik_valid = named
    bom = data[:3] == b"\xef\xbb\xbf"
    r.bom = bom
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        if e.reason == "unexpected end of data" and e.end == len(data):     # a UTF-8 character cut in two at the end
            text = data[: e.start].decode("utf-8-sig")
            r.truncated = True
        else:
            text = data.decode("cp1251", errors="replace")
            r.encoding = "cp1251"
    if len(data) == CUT_SIZE:
        r.truncated = True
    text = text.replace("\x00", "")     # a NUL cannot be stored in text and is never part of a name worth keeping
    head = text.split("\n", 1)[0]
    r.delimiter = ";" if head.count(";") >= 6 and "," not in head else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=r.delimiter, skipinitialspace=True)
    header = next(reader, None)
    if header is None:
        r.error = "empty_file"
        return r
    if tuple(_norm(h) for h in header) != HEADER:
        r.error = "header"
    elif not named:
        r.error = "file_name"
    seen = {}
    records = [(reader.line_num, row) for row in reader]
    last = max((i for i, (_, row) in enumerate(records) if row and any(c.strip() for c in row)), default=-1)
    for i, (line, row) in enumerate(records):
        if not row or all(not c.strip() for c in row):
            r.blank += 1
            continue
        r.records += 1
        raw = r.delimiter.join(row)[:2000]
        if r.truncated and i == last:
            r.bad.append((line, "truncated", raw))        # cut in the middle: a price may be missing its tail
            continue
        if r.error:
            r.bad.append((line, "file:" + r.error, raw))
            continue
        if len(row) != 7:
            r.bad.append((line, "columns:%d" % len(row), raw))
            continue
        pl, store, name, code, cat, ret, promo = [c.strip() for c in row]
        eka, district = place(pl)
        if not pl or not store or not name or not code:
            r.bad.append((line, "missing:" + ",".join(n for n, v in (("place", pl), ("store", store), ("name", name), ("code", code)) if not v), raw))
            continue
        rv, fl = price_e4(ret)
        if rv is None:
            r.bad.append((line, "retail:" + fl, raw))
            continue
        if rv <= 0:
            r.bad.append((line, "retail:not_positive", raw))
            continue
        pv, pf = price_e4(promo)
        if pv is None and pf != "empty":
            r.bad.append((line, "promo:" + pf, raw))
            continue
        flags = fl
        if pv is not None:
            flags |= pf
            if pv == 0:
                flags |= PROMO_ZERO
            elif pv >= rv:
                flags |= PROMO_NOT_BELOW
        if rv > MAX_RETAIL_E4:
            flags |= RETAIL_HIGH
        c, cf = category(cat)
        flags |= cf
        key = (pl, store, code)
        have = seen.setdefault(key, [])
        if any(h.category == c and h.retail == rv and h.promo == pv for h in have):
            r.dups += 1
            continue
        row_ = Row(pl, eka, district, store, code, name, c, rv, pv, flags)
        if have:
            for h in have + [row_]:
                h.flags |= CONFLICT
        have.append(row_)
        r.rows.append(row_)
    r.digest = sum(hash((x.place_raw, x.store, x.code, x.category, x.retail, x.promo)) & 0xFFFFFFFFFFFF for x in r.rows)
    return r


@dataclass
class DayResult:
    files: list
    records: int
    valid: int
    dups: int
    bad: int
    blank: int


def parse_day(raw):
    """Every CSV of the ZIP. Two files of one EIK in a day is a ShapeError (a chain is one file)."""
    files = [parse_csv(n, d) for n, d in read_zip(raw)]
    if not files:
        raise ShapeError("ZIP без файлове")
    eiks = [f.eik for f in files if f.eik]
    dupe = {e for e in eiks if eiks.count(e) > 1}
    if dupe:
        raise ShapeError("Два файла за един ЕИК: " + ", ".join(sorted(dupe)))
    mark_copies(files)
    return DayResult(files, sum(f.records for f in files), sum(len(f.rows) for f in files), sum(f.dups for f in files),
                     sum(len(f.bad) for f in files), sum(f.blank for f in files))


def mark_copies(files):
    """A chain's file that holds exactly the rows of another chain's file (a copy uploaded under the wrong name; it
    happens: the Billa file has held the rows of Hipermarket Zhanet and of Nove Farm) cannot be told from the
    original by its content alone. The file whose chain name appears in the shop names is the owner and the others
    are its copies (`copy_of` is its ЕИК). When none or several qualify, every file of the group is a copy of
    "group": the rows are kept but belong to nobody's figures."""
    groups = {}
    for f in files:
        if f.rows and not f.error:
            groups.setdefault((f.digest, len(f.rows)), []).append(f)
    for g in groups.values():
        if len(g) < 2:
            continue
        owners = [f for f in g if _owns(f)]
        keep = owners[0] if len(owners) == 1 else None
        for f in g:
            if f is not keep:
                f.copy_of = keep.eik if keep else "group"


def _owns(f):
    """The chain's own name (the part before the brackets) is in the names of the shops of its file."""
    tokens = [t for t in re.split(r"[^\w]+", (f.chain_name or "").split("(")[0].upper()) if len(t) >= 3]
    if not tokens:
        return False
    hits = sum(1 for s in {r.store.upper() for r in f.rows} if any(t in s for t in tokens))
    return hits * 2 > len({r.store for r in f.rows})

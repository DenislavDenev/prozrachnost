"""The CSV of the ДФЗ report (Oracle APEX page 8110), as the archive of Наблюдател keeps it.

The shape, as read from the two files of 28.09.2026 (docs/sources.md):

* semicolon, every field in double quotes, a line feed ends each line, no BOM; the answer says UTF-8 but is cp1251;
* a header of 18 columns, then blocks: one `ОБЩО` row of a recipient in a municipality (the totals of the recipient by
  fund), followed by that recipient's payments, one row each, with one amount in the column of the fund;
* `-` is an empty cell; amounts have a decimal point, at most two digits, and may lack the leading zero (`.92`);
* the first and last name are two columns for a natural person; a legal entity has `-` as the last name;
* the same name text can differ a little between the `ОБЩО` row and its payment rows (a dash the encoding lost), so the
  payment belongs to the block it stands in, not to the name it carries.

Anything else raises ShapeError and nothing is written (STANDARD 1А).
"""
import csv
import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass
from decimal import Decimal

HEADER = ("Име или код на бенефициент", "Фамилно име на бенефициент", "Група", "Област", "Община", "Код", "Интервенция",
          "Специфична цел", "Начална дата", "Крайна дата", "ЕФГЗ", "ЕФГЗ - Общо", "ЕЗФРС", "ЕЗФРС - Общо", "НБ", "НБ - Общо",
          "ЕЗФРС и НБ - Общо", "Обща сума")
NAME, SURNAME, GROUP, OBLAST, OBSHTINA, CODE, MEASURE, OBJECTIVE, START, END = range(10)
EFGZ, EFGZ_T, EZFRS, EZFRS_T, NB, NB_T, EZFRS_NB_T, TOTAL_T = range(10, 18)
PAYMENT_AMOUNTS = (EFGZ, EZFRS, NB)
TOTAL_AMOUNTS = (EFGZ_T, EZFRS_T, NB_T, EZFRS_NB_T, TOTAL_T)
AMOUNT = re.compile(r"^-?(\d+(\.\d{1,2})?|\.\d{1,2})$")
DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
TOTAL_WORD = "ОБЩО"


class ShapeError(Exception):
    pass


def decode(raw):
    """(text, encoding). The report is cp1251 although it says UTF-8; a file that really is UTF-8 is read as such."""
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8"), "utf-8-sig"
    head = None
    for enc in ("utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        first = text.split("\n", 1)[0].rstrip("\r")
        try:
            head = tuple(next(csv.reader([first], delimiter=";")))
        except (StopIteration, csv.Error):
            head = ()
        if head == HEADER:
            return text, enc
    if raw[:1] == b"<" or raw.lstrip()[:1] == b"<":
        raise ShapeError("вместо CSV е получена страница (HTML)")
    raise ShapeError("заглавният ред не е този на справката (колоните или кодировката са други): " + repr(head)[:200])


def amount(v):
    return None if v == "-" else Decimal(v)


def date(v):
    return None if v == "-" else dt.date(int(v[6:10]), int(v[3:5]), int(v[0:2]))


@dataclass(slots=True)
class Line:
    n: int              # the number of the data line, 1 is the first line after the header
    total: bool         # the ОБЩО row of a recipient
    f: tuple            # the 18 cells as they are in the file
    key: str            # md5 of the cells: the identity of the line's content
    owner: tuple        # name, last name, oblast, municipality of the ОБЩО row of the block the line stands in
    block: int          # the number of the block


def digest(fields):
    return hashlib.md5("\x1f".join(fields).encode("utf-8")).hexdigest()


def read_lines(raw):
    """The lines of the file, checked one by one. The structure is checked here; the sums by Tally."""
    if not raw:
        raise ShapeError("празен отговор")
    if not raw.endswith(b"\n"):
        raise ShapeError("файлът е отрязан: последният ред няма край на ред")
    text, _ = decode(raw)
    rows = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
    try:
        next(rows)      # the header, checked by decode
        n = block = 0
        owner = None
        for f in rows:
            n += 1
            if len(f) != len(HEADER):
                raise ShapeError(f"ред {n}: {len(f)} полета вместо {len(HEADER)}")
            f = tuple(f)
            for i in (*PAYMENT_AMOUNTS, *TOTAL_AMOUNTS):
                if f[i] != "-" and not AMOUNT.match(f[i]):
                    raise ShapeError(f"ред {n}: {HEADER[i]} не е сума: {f[i]!r}")
            for i in (START, END):
                if f[i] != "-":
                    if not DATE.match(f[i]):
                        raise ShapeError(f"ред {n}: {HEADER[i]} не е дата: {f[i]!r}")
                    try:
                        date(f[i])
                    except ValueError:
                        raise ShapeError(f"ред {n}: {HEADER[i]} не е дата: {f[i]!r}") from None
            total = f[MEASURE] == TOTAL_WORD
            if total:
                if any(f[i] != "-" for i in (*PAYMENT_AMOUNTS, START, END)):
                    raise ShapeError(f"ред {n}: редът ОБЩО има сума на плащане или дата")
                block += 1
                owner = (f[NAME], f[SURNAME], f[OBLAST], f[OBSHTINA])
            else:
                if owner is None:
                    raise ShapeError(f"ред {n}: плащане преди първия ред ОБЩО")
                if (f[OBLAST], f[OBSHTINA]) != owner[2:]:
                    raise ShapeError(f"ред {n}: плащане в друга област или община от своя ред ОБЩО")
                if any(f[i] != "-" for i in TOTAL_AMOUNTS):
                    raise ShapeError(f"ред {n}: плащане с обща сума на получателя")
                if all(f[i] == "-" for i in PAYMENT_AMOUNTS):
                    raise ShapeError(f"ред {n}: плащане без сума")
                if f[MEASURE] == "-" or not f[MEASURE].strip():
                    raise ShapeError(f"ред {n}: плащане без интервенция")
            if not f[OBLAST].strip() or f[OBLAST] == "-" or not f[OBSHTINA].strip() or f[OBSHTINA] == "-":
                raise ShapeError(f"ред {n}: липсва област или община")
            yield Line(n, total, f, digest(f), owner, block)
    except csv.Error as e:
        raise ShapeError("CSV не се чете: " + str(e)) from e
    if n == 0:
        raise ShapeError("файлът няма нито един ред с данни")


class Tally:
    """The sums and the checks of a file, added line by line. `result()` is what is stored in silver.snapshot.

    * rows: lines after the header; they are compared with the count of the archive (a different count of newlines);
    * identities: in every ОБЩО row, ЕЗФРС + НБ = "ЕЗФРС и НБ" and ЕФГЗ + "ЕЗФРС и НБ" = "Обща сума" (a blank is 0);
    * diffs: a recipient whose payments do not add up, fund by fund, to its own ОБЩО row. The source has a few of them
      (the same payment listed twice but counted once); they are kept as they are, never corrected.
    """

    def __init__(self):
        z = Decimal(0)
        self.rows = self.totals = self.payments = self.negatives = self.identity_fail = 0
        self.t = dict(efgz=z, ezfrs=z, nb=z, ezfrs_nb=z, total=z)
        self.p = dict(efgz=z, ezfrs=z, nb=z)
        self.diffs = []
        self.first_start = self.last_end = None
        self._block = None
        self._acc = None

    def _close(self):
        if self._block is None:
            return
        line, acc = self._block, self._acc
        for fund, col in (("efgz", EFGZ_T), ("ezfrs", EZFRS_T), ("nb", NB_T)):
            want = amount(line.f[col]) or Decimal(0)
            if want != acc[fund]:
                self.diffs.append((line.n, fund, want, acc[fund]))

    def add(self, line):
        self.rows += 1
        f = line.f
        for i in (*PAYMENT_AMOUNTS, *TOTAL_AMOUNTS):
            v = amount(f[i])
            if v is not None and v < 0:
                self.negatives += 1
        if line.total:
            self._close()
            self.totals += 1
            self._block = line
            self._acc = dict(efgz=Decimal(0), ezfrs=Decimal(0), nb=Decimal(0))
            z = Decimal(0)
            e, ez, nb, en, tot = (amount(f[i]) or z for i in TOTAL_AMOUNTS)
            if ez + nb != en or e + en != tot:
                self.identity_fail += 1
            for k, v in zip(("efgz", "ezfrs", "nb", "ezfrs_nb", "total"), (e, ez, nb, en, tot)):
                self.t[k] += v
        else:
            self.payments += 1
            for k, i in (("efgz", EFGZ), ("ezfrs", EZFRS), ("nb", NB)):
                v = amount(f[i])
                if v is not None:
                    self.p[k] += v
                    self._acc[k] += v
            s, e = date(f[START]), date(f[END])
            if s and (self.first_start is None or s < self.first_start):
                self.first_start = s
            if e and (self.last_end is None or e > self.last_end):
                self.last_end = e

    def result(self):
        self._close()
        self._block = None
        return dict(rows=self.rows, holders=self.totals, payments=self.payments, negatives=self.negatives, identity_fail=self.identity_fail,
                    total=self.t, paid=self.p, diffs=list(self.diffs), first_start=self.first_start, last_end=self.last_end)


@dataclass
class Parsed:
    lines: list
    stats: dict
    encoding: str


def parse(raw):
    """Everything at once: for tests and small files. The loader streams read_lines instead."""
    lines = []
    tally = Tally()
    for line in read_lines(raw):
        lines.append(line)
        tally.add(line)
    return Parsed(lines, tally.result(), decode(raw)[1])


def newline_rows(raw):
    """The count of lines after the header by the archive's own method (newlines minus one): an independent count."""
    return raw.count(b"\n") - 1

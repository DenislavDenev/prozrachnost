"""Exchange rates of the Bulgarian National Bank (BNB).

Two layouts of the same archive (a query is at most 3 months and may not cross 31.12.2025):
  - until 31.12.2025: leva for `units` units of the currency, and the currency for 1 lev
    ("Период, Код, за,в BGN, за 1 BGN"; before 05.07.1999 these are old leva, 1000 old = 1 new);
  - from 01.01.2026: the ECB reference rates, currency for 1 euro and euro for 1 unit.
Stored as two indicators in live.series: `fx_bgn` (leva for `units` units) and `fx_eur` (currency for
1 euro). The second number of each pair is only used to check the first.
"""
import datetime as dt
import html
import re
from urllib.parse import urlencode

from .config import BNB
from .jsonstat import ShapeError

LAST_BGN_DAY = dt.date(2025, 12, 31)
FIRST_DAY = dt.date(1991, 1, 1)
CHUNK = 6  # the archive answers at most 6 currencies at a time

HEAD_BGN = "Период, Код, за,в BGN, за 1 BGN"
HEAD_EUR = "Период, Код, Валута за 1 евро, Евро за единица валута"
HEAD_TODAY = "Дата,Наименование,Код,Чуждестранна валута за едно евро,Евро за единица чуждестранна валута"


def num(s):
    s = s.strip()
    if s in ("", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        raise ShapeError(f"not a number: {s!r}") from None


def day(s):
    try:
        return dt.datetime.strptime(s.strip(), "%d.%m.%Y").date()
    except ValueError:
        raise ShapeError(f"not a date: {s!r}") from None


def close(a, b, digits=4):
    """a and b are the same number printed with about `digits` significant digits (the inverse check)."""
    return abs(a - b) <= 10 ** -digits * max(abs(a), abs(b)) * 5


def parse(raw):
    """-> list of (indicator, dims, day, value). ShapeError for anything else than a known CSV layout,
    including the HTML page the archive returns for a query it refuses."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ShapeError(f"not UTF-8: {e}") from None
    if text.lstrip().startswith("<"):
        raise ShapeError("an HTML page instead of CSV (a refused query?)")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ShapeError("no header")
    head, out = lines[1].strip(), []
    if head == HEAD_TODAY:
        for ln in lines[2:]:
            f = ln.split(",")
            if len(f) != 5:
                raise ShapeError(f"expected 5 fields: {ln!r}")
            d, code, per_eur, eur_per = day(f[0]), f[2].strip(), num(f[3]), num(f[4])
            if per_eur is None:
                continue
            if eur_per is not None and not close(per_eur * eur_per, 1):
                raise ShapeError(f"{code} {d}: {per_eur} × {eur_per} is not 1")
            out.append(("fx_eur", {"code": code}, d, per_eur))
        return out
    width = {HEAD_BGN: 4, HEAD_EUR: 3}.get(head)
    if not width:
        raise ShapeError(f"unknown header {head!r}")
    for ln in lines[2:]:
        f = [x for x in ln.split(",")]
        if f and not f[-1].strip():
            f.pop()
        if (len(f) - 1) % width:
            raise ShapeError(f"a row that is not date + groups of {width}: {ln!r}")
        d = day(f[0])
        if (head == HEAD_BGN) != (d <= LAST_BGN_DAY):
            raise ShapeError(f"{d} in the {'leva' if head == HEAD_BGN else 'euro'} layout")
        for i in range(1, len(f), width):
            g = f[i:i + width]
            code = g[0].strip()
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ShapeError(f"not a currency code: {code!r}")
            if width == 4:
                units, rate, inv = num(g[1]), num(g[2]), num(g[3])
                if rate is None:
                    continue
                if units is None or units <= 0:
                    raise ShapeError(f"{code} {d}: units {g[1]!r}")
                if inv is not None and not close(rate * inv / units, 1):
                    raise ShapeError(f"{code} {d}: {rate} for {units:g} and {inv} for 1 lev do not agree")
                out.append(("fx_bgn", {"code": code, "units": f"{units:g}"}, d, rate))
            else:
                rate, inv = num(g[1]), num(g[2])
                if rate is None:
                    continue
                if inv is not None and not close(rate * inv, 1):
                    raise ShapeError(f"{code} {d}: {rate} for 1 euro and {inv} euro do not agree")
                out.append(("fx_eur", {"code": code}, d, rate))
    return out


def currencies(page):
    """The codes in the archive's currency list (the search page)."""
    s = page.decode("utf-8", "replace")
    m = re.search(r'name="valutes".*?</select>', s, re.S)
    codes = re.findall(r'<option value="([A-Z]{3})"', m.group(0)) if m else []
    if len(codes) < 20:
        raise ShapeError(f"the currency list has {len(codes)} codes")
    return [html.unescape(c) for c in codes]


def windows(start, end):
    """Calendar quarters from start to end, split at 31.12.2025 (the archive refuses to cross it)."""
    q = dt.date(start.year, 3 * ((start.month - 1) // 3) + 1, 1)
    while q <= end:
        nxt = dt.date(q.year + 1, 1, 1) if q.month == 10 else dt.date(q.year, q.month + 3, 1)
        yield max(q, start), min(nxt - dt.timedelta(days=1), end)
        q = nxt


def url(start, end, codes):
    p = [("downloadOper", "true"), ("group1", "second"),
         ("periodStartDays", f"{start.day:02d}"), ("periodStartMonths", f"{start.month:02d}"), ("periodStartYear", start.year),
         ("periodEndDays", f"{end.day:02d}"), ("periodEndMonths", f"{end.month:02d}"), ("periodEndYear", end.year)]
    p += [("valutes", c) for c in codes]
    p += [("search", "true"), ("showChart", "false"), ("showChartButton", "false"), ("type", "CSV")]
    return f"{BNB}?{urlencode(p)}"


TODAY_URL = f"{BNB}?download=csv&search=&lang=BG"
SEARCH_URL = f"{BNB}?search=true"


SOURCE = "bnb"


def load(conn, stats, backfill=False, get=None, today=None):
    """Read the archive by quarter and 6 currencies at a time: the last 31 days every day, or everything
    from 1991 (`backfill`, which skips the quarters already read). Every row of the day's CSV must be the
    same in the archive (the reconciliation). -> stats with problems."""
    from . import http, store  # noqa: PLC0415 - keeps parse() importable without a database driver
    get = get or http.get
    today = today or dt.date.today()
    problems, counts = [], {}
    codes = currencies(get(SEARCH_URL))
    raw = get(TODAY_URL)
    store.save_raw(conn, SOURCE, "today", "today.csv", raw)
    latest = parse(raw)
    eur_codes = sorted({d["code"] for _, d, _, _ in latest})
    done = {r[0] for r in conn.execute(
        "SELECT ref FROM ops.source_state WHERE source = %s AND status = 'ok'", (SOURCE,))} if backfill else set()
    arch = {}
    for a, b in windows(FIRST_DAY if backfill else today - dt.timedelta(days=31), today):
        use = eur_codes if a > LAST_BGN_DAY else codes
        for i in range(0, len(use), CHUNK):
            chunk = use[i:i + CHUNK]
            ref = f"{a}/{'-'.join(chunk)}"
            if ref in done and b < today - dt.timedelta(days=31):
                continue
            try:
                raw = get(url(a, b, chunk))
                sha = store.save_raw(conn, SOURCE, ref, f"{a}-{chunk[0]}.csv", raw)
                rows = parse(raw)
                ind = "fx_eur" if a > LAST_BGN_DAY else "fx_bgn"
                for r_ind, dims, d, _v in rows:
                    if r_ind != ind or dims["code"] not in chunk or not a <= d <= b:
                        raise ShapeError(f"{r_ind} {dims} {d} is outside the query {ref} to {b}")
                    arch[(dims["code"], d)] = _v
                res = store.apply(conn, SOURCE, ind, ref, sha, [(dims, "BG", str(d), v, None) for _, dims, d, v in rows],
                                  where="AND time BETWEEN %s AND %s AND dims->>'code' = ANY(%s)", args=(str(a), str(b), chunk))
                counts[res] = counts.get(res, 0) + 1
            except (ShapeError, http.Gone) as e:
                cause = "invalid" if isinstance(e, ShapeError) else "gone"
                store.state(conn, SOURCE, ref, status=cause, error=str(e)[:2000])
                problems.append(f"Икономика: БНБ {ref}: {e}")
    # the day's CSV and the archive are the same numbers
    for _, d, dd, v in latest:
        if dd >= today - dt.timedelta(days=31) and arch.get((d["code"], dd)) != v:
            problems.append(f"Икономика: БНБ {d['code']} {dd}: {v} в курсовете за деня, {arch.get((d['code'], dd))} в архива")
    store.state(conn, SOURCE, "fx", status="ok" if not problems else "invalid",
                error="; ".join(problems)[:2000] or None, **({"last_ok": "now"} if not problems else {}))
    stats.update(windows=counts, currencies=len(codes), problems=problems)
    return stats

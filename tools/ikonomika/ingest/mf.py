"""The macroeconomic forecasts of the Ministry of Finance (data.egov.bg, data set 335961f9-…, one resource per
forecast since 11.2020, CSV served as rows by getResourceData).

Each forecast is a table: a header row "Основни макроикономически показатели" with the years, one row per item,
and a note of which years are reported and which forecast. That note has changed form over the years:
`2018*` / `2020**` in the header, "2024 и 2025 г. отчетни данни" / "2026-2029 г. прогноза" or
"2022г. - прогноза" under the table, or a row "ОТЧЕТНИ ДАННИ … ПРОГНОЗА" over the years (the forecast of 24.01.2022
for the budget). A table without any of them is refused, so a new layout is seen, not guessed.

Stored as indicator `mf_forecast`: dims {vintage: the forecast's date, item: a stable code}, geo BG, the year, the
value, flag `f` for a forecast year and none for a reported one. The check: the nominal growth of GDP is its real
growth times its deflator, in every forecast (to 0.5 percentage points).
"""
import json
import re
import time
import urllib.error
import urllib.request

from . import store
from .config import EGOV, USER_AGENT
from .jsonstat import ShapeError

SOURCE = "mf"
DATASET = "335961f9-5027-4e5d-bbda-667464a72b37"
INDICATORS = [
    {"id": "mf_forecast", "dataset": DATASET, "source": SOURCE, "geo": "BG", "stale_days": "250", "read_days": "8",
     "label": "Макроикономическите прогнози на Министерството на финансите", "unit_label": "по показател"},
]
PAUSE = 10   # data.egov.bg drops the connection after a few quick requests (seen 28.09.2026 at 3 s)
HEADER = "Основни макроикономически показатели"
# stable codes for the items, whose names have changed a little between forecasts (first match wins)
ITEMS = [
    ("world", r"^Световна икономика"), ("eu", r"^Европейска икономика"), ("usd", r"^Валутен курс"),
    ("brent", r"^Цена на петрол"), ("commodities", r"^Цена на неенергийни"), ("euribor", r"^EURIBOR"),
    ("gdp_bgn", r"^БВП \[млн\. лв"), ("gdp_eur", r"^БВП \[млн\. евро"), ("gdp_growth", r"^БВП \[реален растеж"),
    ("consumption", r"^Потребление$"), ("gfcf", r"^Брутообразуване на основен капитал"),
    ("exports", r"^Износ на стоки и услуги"), ("imports", r"^Внос на стоки и услуги"),
    ("employment", r"^Заетост \(СНС\)"), ("unemployment", r"^Коефициент на безработица"),
    ("compensation", r"^Компенсации на един нает"), ("deflator", r"^Дефлатор на БВП"),
    ("hicp", r"^Средногодишна инфлация"), ("current_account", r"^Текуща сметка"), ("trade_balance", r"^Търговски баланс"),
    ("fdi", r"^Преки чуждестранни инвестиции"), ("m3", r"^М3|^M3"), ("credit_nfc", r"^Вземания от (фирми|предприятия)"),
    ("credit_hh", r"^Вземания от домакинства"), ("hicp_eoy", r"^Инфлация в края на годината"),
]
NAMES = {"world": "Световна икономика, реален ръст, %", "eu": "Икономиката на ЕС, реален ръст, %",
         "usd": "Долари за 1 евро", "brent": "Петрол Брент, $ за барел", "commodities": "Неенергийни суровини, % промяна",
         "euribor": "EURIBOR 3 месеца, %", "gdp_bgn": "БВП, млн. лв.", "gdp_eur": "БВП, млн. €",
         "gdp_growth": "БВП, реален ръст, %", "consumption": "Потребление, реален ръст, %",
         "gfcf": "Инвестиции (брутообразуване на основен капитал), реален ръст, %",
         "exports": "Износ на стоки и услуги, реален ръст, %", "imports": "Внос на стоки и услуги, реален ръст, %",
         "employment": "Заетост, ръст, %", "unemployment": "Безработица, %", "compensation": "Компенсация на един нает, ръст, %",
         "deflator": "Дефлатор на БВП, %", "hicp": "Средногодишна инфлация (ХИПЦ), %",
         "current_account": "Текуща сметка, % от БВП", "trade_balance": "Търговски баланс, % от БВП",
         "fdi": "Преки чуждестранни инвестиции, % от БВП", "m3": "Парично предлагане (М3), ръст, %",
         "credit_nfc": "Кредити на фирмите, ръст, %", "credit_hh": "Кредити на домакинствата, ръст, %",
         "hicp_eoy": "Инфлация в края на годината (ХИПЦ), %"}
MIN_ITEMS = 15
GROWTH_TOLERANCE = 0.5   # percentage points


def post(method, body, timeout=180):
    req = urllib.request.Request(f"{EGOV}/{method}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(60 * (attempt + 1))
    raise AssertionError("unreachable")


def number(s):
    if s is None or not str(s).strip():
        return None
    try:
        return float(str(s).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        raise ShapeError(f"not a number: {s!r}") from None


def item_code(name):
    name = " ".join(name.split())
    return next((code for code, rx in ITEMS if re.search(rx, name)), None)


def parse(raw):
    """-> ({item: {year: value}}, first forecast year, [unknown item names]). ShapeError for any other layout."""
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise ShapeError(f"not JSON: {e}") from None
    if not isinstance(d, dict) or not d.get("success") or not isinstance(d.get("data"), list):
        raise ShapeError(f"not a data.egov answer: {str(d)[:200]}")
    rows = [[("" if c is None else str(c)) for c in r] for r in d["data"] if isinstance(r, list)]
    head = next((i for i, r in enumerate(rows) if r and r[0].strip().startswith(HEADER)), None)
    if head is None:
        raise ShapeError(f"no header row {HEADER!r}")
    cells = rows[head][1:]
    while cells and not cells[-1].strip():   # empty cells after the last year
        cells.pop()
    years = [int(m.group(1)) if (m := re.match(r"\s*(\d{4})", c)) else None for c in cells]
    if not years or None in years or years != list(range(years[0], years[0] + len(years))):
        raise ShapeError(f"the years are not consecutive: {cells}")
    first_forecast = None
    starred = [y for y, c in zip(years, cells) if c.strip().endswith("**")]
    if starred:
        first_forecast = min(starred)
    for r in rows:   # a note under the table: "2026-2029 г. прогноза", "2022г. - прогноза"
        text = " ".join(r).strip()
        if first_forecast is None and "прогноза" in text.lower() and (m := re.match(r"\s*(\d{4})", text)):
            first_forecast = int(m.group(1))
    if first_forecast is None and head > 0:   # a row over the years: "ОТЧЕТНИ ДАННИ … ПРОГНОЗА"
        over = rows[head - 1][1:]
        at = next((i for i, c in enumerate(over) if c.strip().upper() == "ПРОГНОЗА"), None)
        if at is not None:
            first_forecast = years[at]
    if first_forecast is None or not years[0] <= first_forecast <= years[-1]:
        raise ShapeError("the table does not say which years are forecast")
    items, unknown = {}, []
    for r in rows[head + 1:]:
        if not r or not r[0].strip() or not any(c.strip() for c in r[1:]):
            continue   # a section title or a note
        code = item_code(r[0])
        if code is None:
            unknown.append(" ".join(r[0].split()))
            continue
        items[code] = {y: number(c) for y, c in zip(years, r[1:len(years) + 1])}
    if len(items) < MIN_ITEMS:
        raise ShapeError(f"only {len(items)} known items")
    return items, first_forecast, unknown


def growth_check(vintage, items):
    """[problems]: GDP in current prices grows as real growth times the deflator (1 + g)(1 + d)."""
    level = items.get("gdp_eur") or items.get("gdp_bgn") or {}
    real, defl, bad = items.get("gdp_growth", {}), items.get("deflator", {}), []
    for y in sorted(level):
        a, b, g, d = level.get(y), level.get(y - 1), real.get(y), defl.get(y)
        if None in (a, b, g, d) or not b:
            continue
        nominal, implied = (a / b - 1) * 100, ((1 + g / 100) * (1 + d / 100) - 1) * 100
        if abs(nominal - implied) > GROWTH_TOLERANCE:
            bad.append(f"{vintage} {y}: номиналният ръст {nominal:.2f}% ≠ реален по дефлатора {implied:.2f}%")
    return bad


def load(conn, stats, call=None, pause=PAUSE):
    call = call or post
    lst = json.loads(call("listResources", {"criteria": {"dataset_uri": DATASET}, "records_per_page": 100, "page_number": 1}))
    res = lst.get("resources") or []
    if not lst.get("success") or not res or len(res) != int(lst.get("total_records") or 0):
        raise ShapeError(f"the list of forecasts: {str(lst)[:200]}")
    rows, raws, vintages, problems, unknown = [], [], {}, [], set()
    for r in sorted(res, key=lambda r: r["created_at"]):
        time.sleep(pause)
        raw = call("getResourceData", {"resource_uri": r["uri"]})
        vintage = r["created_at"][:10]
        try:
            items, first_forecast, new_names = parse(raw)
        except ShapeError as e:
            problems.append(f"Икономика: прогноза на МФ {vintage} ({r['name'][:60]}): {e}")
            continue
        raws.append(raw)
        vintages[vintage] = " ".join(r["name"].split())
        unknown.update(new_names)
        problems += [f"Икономика: прогноза на МФ: {b}" for b in growth_check(vintage, items)]
        for code, by_year in items.items():
            for y, v in by_year.items():
                rows.append(({"vintage": vintage, "item": code}, "BG", str(y), v, "f" if y >= first_forecast else None))
    stats["forecasts"], stats["unknown_items"] = len(vintages), sorted(unknown)
    if problems:
        store.state(conn, SOURCE, "mf_forecast", status="invalid", error="; ".join(problems)[:2000])
        stats["indicators"], stats["problems"] = {"mf_forecast": "invalid"}, problems
        return stats
    sha = store.save_raw(conn, SOURCE, "mf_forecast", "forecasts.json", b"\n".join(raws))
    labels = {"vintage": vintages, "item": {c: NAMES[c] for c in {d["item"] for d, *_ in rows}}}
    stats["indicators"] = {"mf_forecast": store.apply(conn, SOURCE, "mf_forecast", "mf_forecast", sha, rows, labels)}
    stats["problems"] = []
    return stats


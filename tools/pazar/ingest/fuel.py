"""Fuel prices: the weekly Oil Bulletin of the European Commission (a published XLSX, history from 2005).

One request per run. The raw file is kept by sha256; a smaller file (fewer weeks, a value that vanished) is held until
a second read at least a day later gives the same file. Prices are in EUR per 1000 litres, with and without taxes.
The NRA files of average prices from fiscal receipts are not read here: see docs/sources.md.
"""
import datetime as dt
import io
import urllib.request

from . import config, pipeline
from .parse import ShapeError

URL = "https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
SHEETS = {"Prices with taxes": "with_tax", "Prices wo taxes": "wo_tax"}
GEOS = ("BG", "EU", "EUR")          # Bulgaria, the EU average, the euro area average
FUELS = ("euro95", "diesel", "LPG")


def fetch(url=URL):
    req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        ctype = r.headers.get("Content-Type", "")
        raw = r.read()
    if "spreadsheetml" not in ctype or raw[:2] != b"PK":
        raise ShapeError(f"Очакван XLSX, получено {ctype or 'нищо'} ({len(raw)} байта)")
    return raw


def parse(raw):
    """[(week, geo, fuel, with_tax, wo_tax)] for the geos and fuels above. Columns are found by header name."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:
        raise ShapeError("XLSX не се чете: " + str(e)) from e
    got = {}
    for sheet, kind in SHEETS.items():
        if sheet not in wb.sheetnames:
            raise ShapeError("Липсва лист " + sheet)
        rows = ws_rows(wb[sheet])
        header = rows[0]
        cols = {}
        for geo in GEOS:
            for fuel in FUELS:
                name = f"{geo}_price_with_tax_{fuel}" if kind == "with_tax" else f"{geo}_price_wo_tax_{fuel}"
                if name not in header:
                    raise ShapeError("Липсва колона " + name)
                cols[(geo, fuel)] = header.index(name)
        if str(rows[2][0]).strip() != "Date":
            raise ShapeError("Променена структура: очаквано `Date` в третия ред")
        for r in rows[3:]:
            week = r[0]
            if not isinstance(week, dt.datetime):
                continue
            for (geo, fuel), i in cols.items():
                v = r[i] if i < len(r) else None
                if v is None or v == "":
                    continue
                if not isinstance(v, (int, float)) or v <= 0:
                    raise ShapeError(f"Невалидна цена {v!r} за {week:%Y-%m-%d} {geo} {fuel}")
                got.setdefault((week.date(), geo, fuel), {})[kind] = round(float(v), 4)
    if not got:
        raise ShapeError("Празен бюлетин")
    return sorted((w, g, f, v.get("with_tax"), v.get("wo_tax")) for (w, g, f), v in got.items())


def ws_rows(ws):
    return [list(r) for r in ws.iter_rows(min_row=1, max_row=None, values_only=True)]


SOURCE = "oil-bulletin"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def rows_of(raw):
    """{week/geo/fuel: prices} as the shared module compares two reads."""
    return {f"{w}/{g}/{f}": {"with_tax": _f(a), "wo_tax": _f(b)} for w, g, f, a, b in parse(raw)}


def write(c, load_id, rows):
    with c.cursor() as cur:
        cur.executemany("INSERT INTO pazar_src.fuel_week (load_id, week, geo, fuel, with_tax, wo_tax) VALUES (%s, %s, %s, %s, %s, %s)",
                        [(load_id, *k.split("/"), v["with_tax"], v["wo_tax"]) for k, v in rows.items()])


def current(c, load_id):
    return {f"{w}/{g}/{f}": {"with_tax": _f(a), "wo_tax": _f(b)} for w, g, f, a, b in
            c.execute("SELECT week, geo, fuel, with_tax, wo_tax FROM pazar_src.fuel_week WHERE load_id = %s", (load_id,))}


def store(c, r, raw, now=None, path=None):
    """The bulletin as one load of the shared module: the raw file once by sha256, a smaller bulletin (fewer weeks, a value
    that vanished) held until the same file is read again a day later, every change in ops.change_log. `path` is a file
    already kept (the replay of the files read before the move); otherwise the answer is stored under its sha256."""
    try:
        rows = rows_of(raw)
    except ShapeError as e:
        f = pipeline.save_raw(c, r, SOURCE, URL, raw, config.DATA / "raw" / SOURCE, name="oil-bulletin.xlsx", media_type=XLSX, fetched_at=now)
        pipeline.reject(c, r, SOURCE, "history", e, f.raw_file_id, now=now)
        return {"state": "invalid", "problems": [f"Бюлетинът за горивата: {e}"]}
    if path:
        f, _ = pipeline.register_raw(c, r, SOURCE, URL, path, media_type=XLSX, fetched_at=now)
    else:
        f = pipeline.save_raw(c, r, SOURCE, URL, raw, config.DATA / "raw" / SOURCE, name="oil-bulletin.xlsx", media_type=XLSX, fetched_at=now)
    done = pipeline.load(c, r, SOURCE, "history", rows, write, raw_file_id=f.raw_file_id, current=current, now=now)
    newest = max(k.split("/")[0] for k in rows)
    return {"state": done.status, "rows": len(rows), "newest": newest, "problems": []}


def _f(v):
    return None if v is None else round(float(v), 4)

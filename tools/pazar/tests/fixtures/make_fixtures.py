"""Cuts the real daily ZIPs of "Колко струва" down to a few chains and a few dozen rows each, byte-exact lines.

Not part of the tests: the cut files in kolkostruva/ are committed. To redo them, point ARCHIVE at the Наблюдател
archive (`/opt/tender/arhiv/kolkostruva` on the server) and run `python tests/fixtures/make_fixtures.py`.
Every kept line is a line of the real file; for each chain the first 40 rows of the base day, one example of every
odd flag, and every bad row of the day are kept, so the fixtures hold the dirt of the source, not hand-written rows.
"""
import codecs
import csv
import io
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from ingest import parse  # noqa: E402

ARCHIVE = Path(os.environ.get("ARCHIVE", "/opt/tender/arhiv/kolkostruva"))
BASE = "2026-09-30"
DAYS = ["2025-10-16", "2026-01-15", "2026-09-26", "2026-09-29", "2026-09-30", "2026-10-01"]
# EIK -> why it is here
CHAINS = {
    "131071587": "Лидл: plain comma file",
    "131129282": "Кауфланд: a bad row",
    "131324923": "T Market",
    "823077024": "ГРИЗЛИ: semicolon, no BOM",
    "128614343": "Маркет Диана: semicolon with BOM",
    "106609436": "ОГАФАРМ: header with spaces and quotes, pharmacy categories",
    "130007884": "Билла: its file holds the rows of other chains",
    "203105528": "Нове Фарм: the owner of the rows the Billa file copied",
    "204096886": "Хипермаркет Жанет: the owner of the rows the Billa file copied on 26.09",
    "204786976": "eBag: one online shop",
    "124634359": "ABC MARKET: copy of ЙОМИ on 16.10.2025",
    "127621783": "ЙОМИ",
    "103060217": "Макао: file name with a BG prefix",
    "102827804": "Жанет: a stray category 1972047017 on 26.09.2026",
}
ODD = [parse.PROMO_NOT_BELOW, parse.CONFLICT, parse.RETAIL_HIGH, parse.CATEGORY_UNLISTED, parse.PROMO_ZERO, parse.ROUNDED, parse.DECIMAL_COMMA]


def find(day):
    return sorted((ARCHIVE / day[:4]).glob(day + ".*.zip"))[-1]


def key_of(delim, row):
    p = [c.strip() for c in row]
    return (p[0], p[1], p[3]) if len(p) == 7 else None


def main():
    wanted = {}
    base = zipfile.ZipFile(find(BASE))
    for name in base.namelist():
        c = parse.chain_from_name(name)
        if not c or c[1] not in CHAINS:
            continue
        r = parse.parse_csv(name, base.read(name))
        keys = {(x.place_raw, x.store, x.code) for x in r.rows[:40]}
        for bit in ODD:
            keys |= {(x.place_raw, x.store, x.code) for x in [x for x in r.rows if x.flags & bit][:5]}
        wanted[c[1]] = keys
    out = HERE / "kolkostruva"
    out.mkdir(exist_ok=True)
    for day in DAYS:
        raw = find(day).read_bytes()
        src = zipfile.ZipFile(io.BytesIO(raw))
        full = {f.eik: f for f in parse.parse_day(raw).files if f.eik in CHAINS}
        keys = {e: wanted.get(e, set()) | {(x.place_raw, x.store, x.code) for x in f.rows[:15]}
                | {(x.place_raw, x.store, x.code) for bit in ODD for x in [x for x in f.rows if x.flags & bit][:5]}
                for e, f in full.items()}
        for e, f in full.items():     # a copy keeps the lines of the file it copies, so the cut files are still copies
            if f.copy_of:
                group = sorted(g for g, h in full.items() if h.digest == f.digest and len(h.rows) == len(f.rows))
                keys[e] = keys[group[0] if f.copy_of == "group" else f.copy_of] if (f.copy_of in keys or f.copy_of == "group") else keys[e]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for name in sorted(src.namelist()):
                c = parse.chain_from_name(name)
                if not c or c[1] not in CHAINS:
                    continue
                data = src.read(name)
                text = data.decode("utf-8-sig", errors="replace")
                lines = text.splitlines(keepends=True)
                bom = data[:3] == codecs.BOM_UTF8
                delim = ";" if (lines[0].count(";") >= 6 and "," not in lines[0]) else ","
                keep = [lines[0]]
                bad = {b[0] for b in full[c[1]].bad}
                w = keys[c[1]]
                reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim, skipinitialspace=True)
                next(reader)
                for row in reader:
                    n = reader.line_num
                    if n in bad or key_of(delim, row) in w:
                        keep.append(lines[n - 1])
                body = "".join(keep).encode("utf-8")
                dst.writestr(name, (codecs.BOM_UTF8 if bom else b"") + body)
        (out / (day + ".zip")).write_bytes(buf.getvalue())
        print(day, len(buf.getvalue()))


if __name__ == "__main__":
    main()

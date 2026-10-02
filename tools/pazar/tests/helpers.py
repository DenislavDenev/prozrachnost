import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path

from ingest import build, parse

FIX = Path(__file__).parent / "fixtures" / "kolkostruva"
HEAD = "Населено място,Търговски обект,Наименование на продукта,Код на продукта,Категория,Цена на дребно,Цена в промоция\n"
EUR = 1.95583


def fixture(name):
    return (FIX / (name + ".zip")).read_bytes()


def csv_of(rows):
    """CSV text from parse.Row objects (prices written back as submitted: four decimals)."""
    def q(s):
        return '"' + s.replace('"', '""') + '"'
    out = [HEAD]
    for r in rows:
        promo = "" if r.promo is None else f"{r.promo / 10000:.4f}"
        out.append(",".join([q(r.place_raw), q(r.store), q(r.name), q(r.code), str(r.category), f"{r.retail / 10000:.4f}", promo]) + "\n")
    return "".join(out)


def make_zip(files):
    """{member: csv text} -> ZIP bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


def derive(name, drop=(), scale=None, only=None, edit=None):
    """A new day from a real fixture: without some chains (by ЕИК), with prices scaled, or edited by a function."""
    d = parse.parse_day(fixture(name))
    files = {}
    for f in d.files:
        if f.eik in drop or (only is not None and f.eik not in only):
            continue
        rows = []
        for r in f.rows:
            r = parse.Row(r.place_raw, r.ekatte, r.district, r.store, r.code, r.name, r.category, r.retail, r.promo, r.flags)
            if scale:
                r.retail = round(r.retail / scale)
                r.promo = None if r.promo is None else round(r.promo / scale)
            if edit:
                edit(f.eik, r)
            rows.append(r)
        files[f.member] = csv_of(rows)
    return make_zip(files)


ARCH = {}     # what the archive would hold: {day: (raw, sha, path)}; a rebuild of a month reads from here


def put(c, day, raw, now=None):
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    sha = hashlib.sha256(raw).hexdigest()
    ARCH[day] = (raw, sha, f"/archive/{day}.{sha[:12]}.zip")
    return build.build_one(c, day, raw, sha, ARCH[day][2], now=now, source=ARCH)


def archive_dir(tmp_path, days):
    """A tiny archive as Наблюдател keeps it: {day: raw bytes} -> state/kolkostruva.json and the files."""
    st = {"seen": {}, "run_at": "2026-10-02T05:40:00Z", "gaps": [], "last_ok": "2026-10-02T05:40:00Z"}
    for day, raw in days.items():
        sha = hashlib.sha256(raw).hexdigest()
        rel = f"kolkostruva/{day[:4]}/{day}.{sha[:12]}.zip"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(raw)
        st["seen"][day] = {"sha": sha, "file": rel, "at": day + "T23:40:00Z"}
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "kolkostruva.json").write_text(json.dumps(st), encoding="utf-8")
    return tmp_path


def spans(c):
    """All spans with natural keys, sorted: the public face of the silver prices."""
    return c.execute("""SELECT ch.eik, st.place_raw, st.name, p.code, s.from_day, s.to_day, s.retail, s.promo, s.category, s.flags
                        FROM silver.price_span s JOIN silver.store st USING (store_id) JOIN silver.product p USING (product_id)
                        JOIN silver.chain ch ON ch.chain_id = st.chain_id
                        ORDER BY 1, 2, 3, 4, 5, 7, 8""").fetchall()


def day_rows(c, day):
    """The rows the spans give back for one day, for the chains that filed it."""
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    return set(c.execute("""SELECT ch.eik, st.place_raw, st.name, p.code, s.retail, s.promo, s.category
                            FROM silver.price_span s JOIN silver.store st USING (store_id) JOIN silver.product p USING (product_id)
                            JOIN silver.chain ch ON ch.chain_id = st.chain_id
                            JOIN silver.chain_day cd ON cd.chain_id = ch.chain_id AND cd.day = %s AND cd.filed
                            WHERE s.from_day >= %s AND s.from_day <= %s AND coalesce(s.to_day, 'infinity') >= %s""",
                         (day, day.replace(day=1), day, day)).fetchall())


def csv_rows(raw):
    """The valid rows of a ZIP, as day_rows returns them."""
    out = set()
    for f in parse.parse_day(raw).files:
        for r in f.rows:
            out.add((f.eik, r.place_raw, r.store, r.code, r.retail, r.promo, r.category))
    return out


def csv_idents(raw):
    """The rows of a ZIP as spans see them: the values and the flags that make a span a different span."""
    from ingest import load
    return {(f.eik, r.place_raw, r.store, r.code, r.retail, r.promo, r.category, r.flags & load.IDENT_MASK)
            for f in parse.parse_day(raw).files for r in f.rows}

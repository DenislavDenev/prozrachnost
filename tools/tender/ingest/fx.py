"""ECB euro reference rates (official daily history), for contracts in currencies other than BGN/EUR."""
import csv
import datetime as dt
import io
import zipfile

from . import db
from .config import DATA
from .http import get

ECB_HIST = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


def refresh(conn):
    """Bring tender_src.fx_rate up to the ECB history, as one load of the shared module: the file goes to the register
    of raw files, the same rates write nothing, a history with fewer rates than we hold is held. Returns the number
    of rows of the answer."""
    data = get(ECB_HIST)
    file = db.keep_raw(conn, "ecb", ECB_HIST, data, DATA / "raw" / "ecb", "eurofxref-hist.zip", media_type="application/zip", compress=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rows = list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")))
    out = []
    for r in rows:
        d = dt.date.fromisoformat(r["Date"].strip())
        if d < dt.date(2019, 12, 1):
            continue
        for cur, v in r.items():
            cur = (cur or "").strip()
            v = (v or "").strip()
            if cur and cur != "Date" and v and v != "N/A":
                out.append((d, cur, v))
    def write(conn, load_id, _):
        conn.execute("DELETE FROM tender_src.fx_rate")
        with conn.cursor().copy("COPY tender_src.fx_rate(day, currency, per_eur, load_id) FROM STDIN") as cp:
            for row in out:
                cp.write_row((*row, load_id))

    db.land(conn, "ecb", ECB_HIST, {f"{d}/{cur}": v for d, cur, v in out}, write, file)
    return len(out)


def load(conn):
    """{currency: [(date, units_per_eur)] ascending}"""
    rates = {}
    for d, c, v in conn.execute("SELECT day, currency, per_eur FROM tender_src.fx_rate ORDER BY currency, day"):
        rates.setdefault(c, []).append((d, float(v)))
    return rates

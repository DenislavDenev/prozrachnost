"""ECB euro reference rates (official daily history), for contracts in currencies other than BGN/EUR."""
import csv
import datetime as dt
import io
import zipfile

from .http import get

ECB_HIST = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


def refresh(conn):
    """Replace ops.fx_rate with the ECB history. Returns the number of rows."""
    data = get(ECB_HIST)
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
                out.append((d, cur, float(v)))
    with conn.transaction():
        conn.execute("DELETE FROM ops.fx_rate")
        with conn.cursor().copy("COPY ops.fx_rate(day, currency, per_eur) FROM STDIN") as cp:
            for row in out:
                cp.write_row(row)
    return len(out)


def load(conn):
    """{currency: [(date, units_per_eur)] ascending}"""
    rates = {}
    for d, c, v in conn.execute("SELECT day, currency, per_eur FROM ops.fx_rate ORDER BY currency, day"):
        rates.setdefault(c, []).append((d, float(v)))
    return rates

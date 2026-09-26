"""Budget payments from СЕБРА: the quarterly lists of individual payments of 5 000 лв. and more that
МЕУ publishes on data.egov.bg (dataset below, from 01.07.2022). CSV files are read through the portal
API (getResourceData); periods published only as ZIP through the resource's download link. Each file
is kept gzipped with its sha256 and loaded once; a file the portal marks as updated is loaded again.

The lists carry the receiver's name and account, not its ЕИК: companies are matched to our companies
by exact name in the build (db/derive/70_payments.sql). Natural persons arrive anonymised
('ФИЗИЧЕСКО ЛИЦЕ'); for them the account, the reasons and the document number are dropped.
"""
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import urllib.request
import zipfile

from .config import DATA, USER_AGENT

API = "https://data.egov.bg/api/"
DATASET = "57f1e2e7-b235-45e8-94c4-4d69f0b1a690"
RAW = DATA / "raw" / "sebra"
PERSON = "ФИЗИЧЕСКО ЛИЦЕ"
COLS = ["SETTLEMENT_DATE", "CLIENT_RECEIVER_NAME", "CLIENT_RECEIVER_ACC", "CLIENT_RECEIVER_BIC", "FIN_CODE", "FIN_NAME",
        "AMOUNT", "CURRENCY", "REASON1", "REASON2", "REG_DATE", "REG_NO", "SEBRA_PAY_CODE", "ORGANIZATION",
        "PRIMARY_ORGANIZATION", "PRIMARY_ORG_CODE"]


def _post(method, body):
    req = urllib.request.Request(API + method, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=900) as r:
        return r.read()


def resources():
    """The dataset's files, one per period; a period published both as CSV and ZIP is read from the CSV."""
    rs = json.loads(_post("listResources", {"criteria": {"dataset_uri": DATASET}}))["resources"]
    out, periods = [], {}
    for r in rs:
        fmt = (r.get("file_format") or "").lower()
        period = re.sub(r"\s*\(ZIP\)\s*$", "", r.get("name") or "")
        if fmt not in ("csv", "zip"):
            continue
        if period in periods and (periods[period]["format"] == "csv" or fmt == "zip"):
            continue
        periods[period] = {"uri": r["uri"], "name": period, "format": fmt, "updated_at": r.get("updated_at")}
    out = list(periods.values())
    return sorted(out, key=lambda r: r["name"])


def read_rows(res):
    """(rows as lists with the header first, raw bytes) of one file."""
    if res["format"] == "csv":
        raw = _post("getResourceData", {"resource_uri": res["uri"]})
        d = json.loads(raw)
        if not d.get("success") or not d.get("data"):
            raise ValueError(f"no data for {res['uri']}")
        return d["data"], raw
    req = urllib.request.Request(f"https://data.egov.bg/resource/download/zip/{res['uri']}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=1800) as r:
        raw = r.read()
    rows = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if not name.lower().endswith(".csv"):
                continue
            text = z.read(name)
            for enc in ("utf-8-sig", "cp1251"):
                try:
                    s = text.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            part = list(csv.reader(io.StringIO(s), delimiter=";" if s[:500].count(";") > s[:500].count(",") else ","))
            rows += part if not rows else part[1:]
    return rows, raw


def day(v):
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", (v or "").strip())
    return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def amount(v):
    s = str(v or "").strip().replace(" ", "")
    s = s.replace(",", ".") if s.count(",") == 1 and "." not in s else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse(rows):
    """Header + rows of one file -> sebra.payment rows (without resource_uri, row_no)."""
    head = [h.strip().upper() for h in rows[0]]
    missing = set(COLS) - set(head)
    if missing:
        raise ValueError(f"missing columns {sorted(missing)}")
    ix = {c: head.index(c) for c in COLS}
    out = []
    for r in rows[1:]:
        g = lambda c: (r[ix[c]].strip() if ix[c] < len(r) and r[ix[c]] is not None else "") or None
        person = (g("CLIENT_RECEIVER_NAME") or "").upper() == PERSON
        reason = " · ".join(x for x in (g("REASON1"), g("REASON2")) if x) or None
        out.append({"settlement_date": day(g("SETTLEMENT_DATE")), "receiver_name": g("CLIENT_RECEIVER_NAME"), "is_person": person,
                    "receiver_iban": None if person else g("CLIENT_RECEIVER_ACC"), "fin_code": g("FIN_CODE"), "fin_name": g("FIN_NAME"),
                    "amount": amount(g("AMOUNT")), "currency": g("CURRENCY"), "reason": None if person else reason,
                    "reg_date": day(g("REG_DATE")), "reg_no": None if person else g("REG_NO"), "pay_code": g("SEBRA_PAY_CODE"),
                    "organization": g("ORGANIZATION"), "primary_organization": g("PRIMARY_ORGANIZATION"),
                    "primary_org_code": g("PRIMARY_ORG_CODE")})
    return out


def load(conn, stats):
    """Load every file that is new or changed since it was loaded."""
    stats.update(files=0, rows=0)
    for res in resources():
        had = conn.execute("SELECT updated_at::text, loaded_at FROM sebra.resource WHERE uri = %s", (res["uri"],)).fetchone()
        if had and had[1] and str(had[0])[:19] == str(res["updated_at"])[:19]:
            continue
        rows, raw = read_rows(res)
        recs = parse(rows)
        sha = hashlib.sha256(raw).hexdigest()
        RAW.mkdir(parents=True, exist_ok=True)
        (RAW / f"{res['uri']}.{res['format']}.gz").write_bytes(gzip.compress(raw))
        with conn.transaction():
            conn.execute("""INSERT INTO sebra.resource (uri, name, format, updated_at) VALUES (%s, %s, %s, %s)
                ON CONFLICT (uri) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at""",
                         (res["uri"], res["name"], res["format"], res["updated_at"]))
            conn.execute("DELETE FROM sebra.payment WHERE resource_uri = %s", (res["uri"],))
            cols = ["settlement_date", "receiver_name", "is_person", "receiver_iban", "fin_code", "fin_name", "amount", "currency",
                    "reason", "reg_date", "reg_no", "pay_code", "organization", "primary_organization", "primary_org_code"]
            with conn.cursor().copy(f"COPY sebra.payment (resource_uri, row_no, {', '.join(cols)}) FROM STDIN") as cp:
                for i, r in enumerate(recs):
                    cp.write_row([res["uri"], i] + [r[c] for c in cols])
            conn.execute("UPDATE sebra.resource SET sha256 = %s, rows = %s, loaded_at = now() WHERE uri = %s", (sha, len(recs), res["uri"]))
        stats["files"] += 1
        stats["rows"] += len(recs)
        stats.setdefault("loaded", []).append(res["name"])

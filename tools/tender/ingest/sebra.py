"""Budget payments from СЕБРА: the quarterly lists of individual payments of 5 000 лв. and more that
МЕУ publishes on data.egov.bg (dataset below, from 01.07.2022). CSV files are read through the portal
API (getResourceData); periods published only as ZIP through the resource's download link. Each file
is kept gzipped in the register of raw files and loaded once, as one load of the shared module
(pipeline.load); a file the portal marks as updated is loaded again.

The lists carry the receiver's name and account, not its ЕИК: companies are matched to our companies
by exact name in the build (db/derive/70_payments.sql). Natural persons arrive anonymised
('ФИЗИЧЕСКО ЛИЦЕ'); for them the account, the reasons and the document number are dropped.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import re
import urllib.request
import zipfile

from .config import DATA, USER_AGENT
from . import db
from .db import log_change

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
    else:
        req = urllib.request.Request(f"https://data.egov.bg/resource/download/zip/{res['uri']}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=1800) as r:
            raw = r.read()
    return rows_of(res, raw), raw


def rows_of(res, raw):
    """The rows of one answer (the header first), from its bytes."""
    if res["format"] == "csv":
        d = json.loads(raw)
        if not d.get("success") or not d.get("data"):
            raise ValueError(f"no data for {res['uri']}")
        return d["data"]
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
    return rows


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
    """Header + rows of one file -> tender_src.sebra_payment rows (without resource_uri, row_no)."""
    head = [str(h).replace("﻿", "").strip().strip('"').upper() for h in rows[0]]
    missing = set(COLS) - set(head)
    if missing:
        raise ValueError(f"missing columns {sorted(missing)} in {head[:6]}")
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


COLUMNS = ["settlement_date", "receiver_name", "is_person", "receiver_iban", "fin_code", "fin_name", "amount", "currency",
           "reason", "reg_date", "reg_no", "pay_code", "organization", "primary_organization", "primary_org_code"]


def land(conn, res, recs, raw, stats, same_date=False, at=None):
    """One file through the shared module: the raw answer into the register of files, its rows as one load.
    Returns the status of the load. An answer with fewer rows than we hold is held (pipeline.load)."""
    sha = hashlib.sha256(raw).hexdigest()
    file = db.keep_raw(conn, "sebra", res["uri"], raw, RAW, f"{res['uri']}.{res['format']}")
    had = conn.execute("SELECT rows FROM tender_src.sebra_resource WHERE uri = %s AND sha256 IS NOT NULL", (res["uri"],)).fetchone()

    def write(conn, load_id, rows):
        conn.execute("""INSERT INTO tender_src.sebra_resource (uri, name, format, updated_at) VALUES (%s, %s, %s, %s)
            ON CONFLICT (uri) DO UPDATE SET name = EXCLUDED.name, updated_at = EXCLUDED.updated_at""",
                     (res["uri"], res["name"], res["format"], res["updated_at"]))
        conn.execute("DELETE FROM tender_src.sebra_payment WHERE resource_uri = %s", (res["uri"],))
        with conn.cursor().copy(f"COPY tender_src.sebra_payment (resource_uri, row_no, load_id, {', '.join(COLUMNS)}) FROM STDIN") as cp:
            for i, r in enumerate(recs):
                cp.write_row([res["uri"], i, load_id] + [r[c] for c in COLUMNS])
        conn.execute("UPDATE tender_src.sebra_resource SET sha256 = %s, rows = %s, loaded_at = COALESCE(%s, now()), held_sha = NULL, load_id = %s WHERE uri = %s",
                     (sha, len(recs), at, load_id, res["uri"]))

    done = db.land(conn, "sebra", res["uri"], {str(i): r for i, r in enumerate(recs)}, write, file, now=at)
    if done.status == "held":
        stats["held"] += 1
    elif done.status == "unchanged":   # other bytes, the same rows
        conn.execute("UPDATE tender_src.sebra_resource SET sha256 = %s, updated_at = %s WHERE uri = %s", (sha, res["updated_at"], res["uri"]))
        stats["verified"] += 1
    else:
        if had and done.status == "loaded" and at is None:   # at: an older answer taken by the step adopt, not a change
            log_change(conn, "sebra", res["uri"], "rows", had[0], len(recs), "rewritten" if same_date else "new-record")
        stats["files"] += 1
        stats["rows"] += len(recs)
        stats.setdefault("loaded", []).append(res["name"])
    return done.status


def load(conn, stats, verify=False):
    """Load every file that is new or changed since it was loaded. data.egov.bg dates a changed file;
    verify (weekly) downloads every file anyway and compares its sha256, for a file replaced without a new
    date. A file with fewer rows than we hold is kept back until a second download a day or more later agrees;
    every answer is kept in the register of raw files; every change goes to ops.change_log."""
    stats.update(files=0, rows=0, verified=0, held=0)
    for res in resources():
        # the date compared by Postgres, which parsed and stored the portal's text the same way
        had = conn.execute("""SELECT updated_at IS NOT DISTINCT FROM %s::timestamptz, loaded_at, sha256
                              FROM tender_src.sebra_resource WHERE uri = %s""", (res["updated_at"], res["uri"])).fetchone()
        same_date = bool(had and had[1] and had[0])
        if same_date and not verify:
            continue
        rows, raw = read_rows(res)
        try:
            recs = parse(rows)
        except ValueError as e:   # another shape: recorded, nothing replaced
            db.reject(conn, "sebra", res["uri"], e, db.keep_raw(conn, "sebra", res["uri"], raw, RAW, f"{res['uri']}.{res['format']}"))
            raise
        land(conn, res, recs, raw, stats, same_date)

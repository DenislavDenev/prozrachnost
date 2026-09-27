"""Mirror of the ЦАИС ЕОП open-data feed (storage.eop.bg): one MinIO bucket per day,
four JSON files (tenders, contracts, annexes, OCDS). Raw files land on disk untouched;
loading into Postgres is load_day()."""
import datetime as dt
import hashlib
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

from .config import EOP_BASE, EOP_FIRST_DAY, RAW_EOP
from .http import Gone, get

S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
KINDS = ("tenders", "contracts", "annexes", "ocds")


def classify(key):
    """Bucket keys are Bulgarian sentences; the kind is a word in them (SIGMA classifyBucketKey)."""
    if "OCDS" in key:
        return "ocds"
    for word, kind in (("анекси", "annexes"), ("договори", "contracts"), ("поръчки", "tenders")):
        if re.search(rf"\b{word}\b", key):
            return kind
    return None


def list_day(day):
    """{kind: (key, size, last_modified)} for a published day; None when the bucket is absent."""
    try:
        xml = get(f"{EOP_BASE}/open-data-{day}/", accept="application/xml")
    except Gone:
        return None
    out = {}
    for c in ET.fromstring(xml).iter(f"{S3}Contents"):
        key = c.find(f"{S3}Key").text
        kind = classify(key)
        if kind:
            modified = c.find(f"{S3}LastModified")
            out[kind] = (key, int(c.find(f"{S3}Size").text), modified.text if modified is not None else None)
    return out


def fetch_day(day, force=False, listing=None):
    """Download one day to RAW_EOP/<day>/<kind>.json plus manifest.json. Returns the manifest.

    Nothing we hold is lost: a file that is not valid JSON never replaces the copy we have (the kind is
    listed under "invalid"), a file whose content changed moves the old copy to <day>/history/, and every
    write goes through a temporary file. "changed" lists the kinds whose content differs from before."""
    folder = RAW_EOP / day
    manifest_path = folder / "manifest.json"
    cached = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if cached and not force:
        # an absent bucket may still appear; keep asking for two weeks
        settled = dt.date.fromisoformat(day) < dt.date.today() - dt.timedelta(days=14)
        if cached["published"] or settled:
            return cached
    listing = list_day(day) if listing is None else listing
    before = (cached or {}).get("files", {})
    if listing is None and cached and cached["published"]:
        return {**cached, "gone": True}  # a published day that stops answering keeps our copy
    manifest = {"day": day, "published": listing is not None, "files": {}, "changed": [], "invalid": [],
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    folder.mkdir(parents=True, exist_ok=True)
    for kind, (key, size, modified) in (listing or {}).items():
        body = get(f"{EOP_BASE}/open-data-{day}/{urllib.parse.quote(key)}")
        path, prev = folder / f"{kind}.json", before.get(kind)
        try:
            json.loads(body)
        except ValueError:
            manifest["invalid"].append(kind)
            if prev:
                manifest["files"][kind] = prev
            continue
        sha = hashlib.sha256(body).hexdigest()
        if prev and prev["sha256"] != sha and path.exists():
            (folder / "history").mkdir(exist_ok=True)
            path.replace(folder / "history" / f"{kind}.{prev['sha256'][:12]}.json")
            manifest["changed"].append(kind)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        manifest["files"][kind] = {"key": key, "size": len(body), "sha256": sha, "modified": modified}
    for kind in set(before) - set(manifest["files"]):  # the source dropped a file: keep ours, say so
        manifest["files"][kind] = {**before[kind], "dropped": True}
        manifest["changed"].append(kind)
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(manifest_path)
    return manifest


def differs(files, listing):
    """Kinds whose listing no longer matches the manifest: a file added or gone, another key or size, or a
    LastModified other than the one recorded (a rewrite of the same size). Manifests written before the
    audit have no LastModified; for them key and size decide, and the audit records it."""
    out = []
    for kind in sorted(set(files) | set(listing)):
        f, l = files.get(kind), listing.get(kind)
        if not f or not l:
            if not (f and f.get("dropped")):
                out.append(kind)
            continue
        key, size, modified = l
        if key != f["key"] or size != f["size"] or (f.get("modified") and modified != f["modified"]):
            out.append(kind)
    return out


def audit(day_list, pause=0.5):
    """Ask the source for the listing of every day we hold and refetch the days that differ. One
    connection, `pause` seconds between requests. Returns {checked, changed: [{day, kinds, content}],
    gone: [days], baseline: n (LastModified recorded for the first time)}."""
    rep = {"checked": 0, "changed": [], "gone": [], "baseline": 0}
    for day in day_list:
        path = RAW_EOP / day / "manifest.json"
        if not path.exists():
            continue
        m = json.loads(path.read_text(encoding="utf-8"))
        if not m["published"]:
            continue
        listing = list_day(day)
        time.sleep(pause)
        rep["checked"] += 1
        if listing is None:
            rep["gone"].append(day)
            continue
        kinds = differs(m["files"], listing)
        if kinds:
            new = fetch_day(day, force=True, listing=listing)
            time.sleep(pause)
            rep["changed"].append({"day": day, "kinds": kinds, "content": new["changed"], "invalid": new["invalid"],
                                   "old": {k: m["files"].get(k) for k in kinds}, "new": {k: new["files"].get(k) for k in kinds}})
        elif any(not f.get("modified") for f in m["files"].values()):
            for kind, f in m["files"].items():
                if kind in listing:
                    f["modified"] = listing[kind][2]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
            rep["baseline"] += 1
    return rep


def days(start=EOP_FIRST_DAY, end=None):
    end = end or str(dt.date.today() - dt.timedelta(days=1))
    d, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while d <= e:
        yield str(d)
        d += dt.timedelta(days=1)


def sync(start=EOP_FIRST_DAY, end=None, refetch_last=3, workers=4):
    """Fetch every missing day, and always refetch the last `refetch_last` days (late files)."""
    all_days = list(days(start, end))
    recent = set(all_days[-refetch_last:]) if refetch_last else set()
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda d: fetch_day(d, force=d in recent), all_days))


def read_rows(day, kind):
    """Rows of a raw file: flat files are a JSON list (or a dict holding one); OCDS -> releases."""
    path = RAW_EOP / day / f"{kind}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_bytes())
    if kind == "ocds":
        return doc.get("releases", [])
    if isinstance(doc, list):
        return doc
    return next((v for v in doc.values() if isinstance(v, list)), [])


if __name__ == "__main__":
    import sys
    res = sync(*sys.argv[1:3])
    print(f"days={len(res)} published={sum(m['published'] for m in res)}")

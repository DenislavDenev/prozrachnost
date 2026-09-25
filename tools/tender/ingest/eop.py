"""Mirror of the ЦАИС ЕОП open-data feed (storage.eop.bg): one MinIO bucket per day,
four JSON files (tenders, contracts, annexes, OCDS). Raw files land on disk untouched;
loading into Postgres is load_day()."""
import datetime as dt
import hashlib
import json
import re
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
    """{kind: (key, size)} for a published day; None when the bucket is absent."""
    try:
        xml = get(f"{EOP_BASE}/open-data-{day}/", accept="application/xml")
    except Gone:
        return None
    out = {}
    for c in ET.fromstring(xml).iter(f"{S3}Contents"):
        key = c.find(f"{S3}Key").text
        kind = classify(key)
        if kind:
            out[kind] = (key, int(c.find(f"{S3}Size").text))
    return out


def fetch_day(day, force=False):
    """Download one day to RAW_EOP/<day>/<kind>.json plus manifest.json. Returns the manifest."""
    folder = RAW_EOP / day
    manifest_path = folder / "manifest.json"
    if manifest_path.exists() and not force:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        # an absent bucket may still appear; keep asking for two weeks
        settled = dt.date.fromisoformat(day) < dt.date.today() - dt.timedelta(days=14)
        if cached["published"] or settled:
            return cached
    listing = list_day(day)
    manifest = {"day": day, "published": listing is not None, "files": {},
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if listing:
        folder.mkdir(parents=True, exist_ok=True)
        for kind, (key, size) in listing.items():
            body = get(f"{EOP_BASE}/open-data-{day}/{urllib.parse.quote(key)}")
            (folder / f"{kind}.json").write_bytes(body)
            manifest["files"][kind] = {"key": key, "size": len(body),
                                       "sha256": hashlib.sha256(body).hexdigest()}
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


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

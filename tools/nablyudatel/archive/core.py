"""The archive's store and HTTP client (stdlib only).

The store keeps every distinct answer of a source once: a file `<source>/<name>.<sha12>.<ext>` and a line in
`index.jsonl`. `state/<source>.json` remembers the last sha of every key, so an answer that has not changed costs nothing,
and when each source last ran well or failed (what the freshness check reads).
"""
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path

UA = "Prozrachnost/nablyudatel (+https://github.com/DenislavDenev/prozrachnost)"


class Bad(Exception):
    """The answer is not what the source should give (an error page, a broken file): nothing is stored."""


class Blocked(Exception):
    """The source refuses us (403, 429 without Retry-After, the connection is cut): stop and try tomorrow."""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(data, fmt):
    if not data or not data.strip():
        raise Bad("empty answer")
    head = data[:600].lstrip().lower()
    if fmt != "html" and (head.startswith(b"<!doctype html") or head.startswith(b"<html")):
        raise Bad(f"an HTML page instead of {fmt}")
    try:
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if z.testzip() is not None or not z.namelist():
                    raise Bad("a broken or empty zip")
        elif fmt == "json":
            json.loads(data)
        elif fmt == "xml":
            ET.fromstring(data)
        elif fmt == "egov":   # getResourceData can be hundreds of MB: only its head is read
            if not re.match(rb'\s*\{\s*"success"\s*:\s*true', data[:200]):
                raise Bad("data.egov did not answer success: " + data[:200].decode("utf-8", "replace"))
    except (zipfile.BadZipFile, json.JSONDecodeError, ET.ParseError) as e:
        raise Bad(f"not a valid {fmt}: {e}") from e


def safe(key):
    s = re.sub(r"[^\w.=-]+", "_", key).strip("_")
    return s if len(s) <= 120 else s[:100] + "_" + hashlib.sha256(key.encode()).hexdigest()[:12]


class Store:
    """`state/<source>.json` per source, so runs of different sources (a long crawl and a daily file) never overwrite
    each other's state; two runs of the same source are kept apart by a lock in the step script."""

    def __init__(self, root):
        self.root = Path(root)
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        self.state = {}
        self.puts = 0

    def src(self, source):
        if source not in self.state:
            p = self.root / "state" / f"{source}.json"
            self.state[source] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            self.state[source].setdefault("seen", {})
        return self.state[source]

    def all_states(self):
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((self.root / "state").glob("*.json"))}

    def put(self, source, key, data, ext, name=None, sha_of=None):
        """Stores the answer unless the last one of this key was the same; True when it was new. `sha_of` is what
        decides "the same" when the answer carries parts that change on every read (tokens in a page)."""
        sha = hashlib.sha256(data if sha_of is None else sha_of).hexdigest()
        seen = self.src(source)["seen"]
        if seen.get(key, {}).get("sha") == sha:
            return False
        rel = f"{source}/{name or safe(key)}.{sha[:12]}.{ext}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        with open(self.root / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), "source": source, "key": key, "file": rel, "sha256": sha, "bytes": len(data)},
                               ensure_ascii=False) + "\n")
        seen[key] = {**seen.get(key, {}), "sha": sha, "file": rel, "at": now()}
        self.puts += 1
        if self.puts % 50 == 0:   # a long crawl that dies keeps what it has done
            self.save()
        return True

    def save(self):
        for source, st in self.state.items():
            p = self.root / "state" / f"{source}.json"
            tmp = p.with_name(p.name + ".part")
            tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)


class Http:
    """One connection's manners: at most one request per `pause` seconds, Retry-After on 429 and 5xx, a few
    retries on a dropped connection; 404 and 410 (and what `absent` adds) are an answer with an empty body, not an
    error."""

    def __init__(self, pause=1.0, ua=UA, cookies=False, tries=3, absent=(404, 410)):
        self.pause, self.ua, self.tries, self.last, self.absent = pause, ua, tries, 0.0, absent
        handlers = [urllib.request.HTTPCookieProcessor(CookieJar())] if cookies else []
        self.opener = urllib.request.build_opener(*handlers)

    def _wait(self):
        w = self.pause - (time.monotonic() - self.last)
        if w > 0:
            time.sleep(w)

    def req(self, url, data=None, headers=None, timeout=120):
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode()
        for attempt in range(self.tries):
            self._wait()
            try:
                with self.opener.open(urllib.request.Request(url, data, {"User-Agent": self.ua, **(headers or {})}),
                                      timeout=timeout) as r:
                    body = r.read()
                self.last = time.monotonic()
                return r.status, body
            except urllib.error.HTTPError as e:
                self.last = time.monotonic()
                if e.code in self.absent:
                    return e.code, b""
                retry = e.headers.get("Retry-After") if e.headers else None
                if e.code == 403 or (e.code == 429 and not retry):
                    raise Blocked(f"{e.code} from {url}") from e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.tries - 1:
                    time.sleep(min(int(retry) if retry and retry.isdigit() else 30 * (attempt + 1), 300))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                self.last = time.monotonic()
                if attempt < self.tries - 1:
                    time.sleep(30 * (attempt + 1))
                    continue
                raise Blocked(f"{url}: {e}") from e
        raise AssertionError("unreachable")

    def get(self, url, **kw):
        return self.req(url, **kw)

    def post(self, url, data, **kw):
        return self.req(url, data=data, **kw)

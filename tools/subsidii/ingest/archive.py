"""Reads the CSV files of the ДФЗ report from the archive of Наблюдател (the bronze layer). Nothing is downloaded here.

The archive keeps `dfz/<year>/<day>.<sha12>.csv` (a file only when its bytes differ from the newest one of that year),
`index.jsonl` with the full sha256 and size of each, and `state/dfz.json` with the newest file of each year, `last_ok`
(the time of the last successful read of the source) and `last`, the years the form offered at that read.
"""
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass

from . import config

NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.([0-9a-f]{12})\.csv$")


class ArchiveError(Exception):
    pass


@dataclass(frozen=True)
class Snap:
    fy: int
    day: dt.date
    sha12: str
    rel: str


def state(root=None):
    root = root or config.ARCHIVE
    try:
        return json.loads((root / "state" / "dfz.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError("Състоянието на архива не се чете: " + str(e)) from e


def age_hours(st, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        last = dt.datetime.fromisoformat(st["last_ok"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as e:
        raise ArchiveError("Архивът няма час на последното успешно четене") from e
    return (now - last).total_seconds() / 3600


def years_in_form(st):
    """The years the source offered at the last read. A year that was read before and is not here has left the form."""
    return {int(y) for y in st.get("last", {})}


def index_shas(root=None):
    """{relative file: (sha256, bytes, at)} from index.jsonl, the archive's own record of what it wrote."""
    root = root or config.ARCHIVE
    out = {}
    try:
        with (root / "index.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if '"source": "dfz"' in line:
                    e = json.loads(line)
                    out[e["file"]] = (e["sha256"], e["bytes"], e.get("at", ""))
    except (OSError, ValueError, KeyError) as e:
        raise ArchiveError("index.jsonl не се чете: " + str(e)) from e
    return out


def snapshots(root=None, idx=None):
    """{fy: [Snap, ...]} oldest first: every file of the archive, found by its name. Two files of one day (the source
    was read twice and changed in between) count as one snapshot: the later one by the archive's own time."""
    root = root or config.ARCHIVE
    idx = idx if idx is not None else index_shas(root)
    out = {}
    base = root / "dfz"
    if not base.is_dir():
        raise ArchiveError("В архива няма папка dfz")
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and d.name.isdigit()):
            continue
        days = {}
        for f in sorted(d.iterdir()):
            m = NAME.match(f.name)
            if not m:
                continue
            snap = Snap(int(d.name), dt.date.fromisoformat(m.group(1)), m.group(2), f"dfz/{d.name}/{f.name}")
            old = days.get(snap.day)
            if old is None or idx.get(snap.rel, ("", 0, ""))[2] >= idx.get(old.rel, ("", 0, ""))[2]:
                days[snap.day] = snap
        if days:
            out[int(d.name)] = [days[k] for k in sorted(days)]
    return out


def read(snap, idx=None, root=None):
    """(bytes, sha256, path). The file is checked against the name and against the archive's record: a changed archive
    is an error, never a quiet new answer."""
    root = root or config.ARCHIVE
    path = root / snap.rel
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ArchiveError("Файлът не се чете: " + str(e)) from e
    sha = hashlib.sha256(raw).hexdigest()
    if not sha.startswith(snap.sha12):
        raise ArchiveError("Файлът в архива не съвпада с името си: " + snap.rel)
    if idx is not None and snap.rel in idx and (sha, len(raw)) != tuple(idx[snap.rel][:2]):
        raise ArchiveError("Файлът в архива не съвпада със записа в index.jsonl: " + snap.rel)
    return raw, sha, str(path)


def fiscal_year_bounds(fy):
    """16.10 of the year before to 15.10 of the year named: FY2025 is 16.10.2024 to 15.10.2025."""
    return dt.date(fy - 1, 10, 16), dt.date(fy, 10, 15)

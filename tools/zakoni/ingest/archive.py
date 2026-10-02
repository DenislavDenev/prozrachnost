"""The bronze layer: the files of the Наблюдател archive (/opt/tender/arhiv/egov/<set>/<resource>/<version>.<sha12>.json).

This tool only reads them, as a member of the archive's group; it refuses an archive older than STALE_HOURS (the archive
reads data.egov.bg once a day) and a file whose content does not match the sha256 prefix in its name.
"""
import datetime as dt
import json

from .config import SET_DIR, STALE_HOURS, STATE_FILE
from .stream import ShapeError, sha256_file


class ArchiveError(RuntimeError):
    """The archive is missing, stale or damaged. Nothing is built from it."""


def state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError(f"състоянието на архива не се чете: {e}") from None


def last_ok(st=None):
    st = st or state()
    try:
        return dt.datetime.fromisoformat(st["last_ok"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        raise ArchiveError("архивът няма час на последното успешно четене") from None


def check_fresh(now=None, st=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    t = last_ok(st)
    if now - t > dt.timedelta(hours=STALE_HOURS):
        raise ArchiveError(f"архивът е чел data.egov.bg за последно на {t:%d.%m.%Y %H:%M} UTC, преди повече от {STALE_HOURS} часа")
    return t


def latest(resource):
    """The newest file of a resource: (path, sha12, version). The archive keeps one file per different answer."""
    d = SET_DIR / resource
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    if not files:
        raise ArchiveError(f"ресурсът {resource} липсва в архива")
    p = files[-1]
    parts = p.name.split(".")
    if len(parts) != 3 or len(parts[1]) != 12:
        raise ArchiveError(f"името на файла {p.name} не е <версия>.<sha12>.json")
    return p, parts[1], parts[0]


def verified(resource):
    """-> (path, sha256, bytes, version, mtime). The sha256 of the content must start with the one in the name."""
    p, sha12, version = latest(resource)
    sha, size = sha256_file(p)
    if not sha.startswith(sha12):
        raise ArchiveError(f"файлът {p.name} е повреден: sha256 на съдържанието е {sha[:12]}")
    return p, sha, size, version, dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)


def listing():
    """The newest listing of the set's resources (what data.egov.bg said about them): {uri: entry}."""
    d = SET_DIR / "_list"
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    if not files:
        raise ShapeError("в архива няма списък на ресурсите на набора")
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ShapeError("списъкът на ресурсите не е списък")
    return {e["uri"]: e for e in data}

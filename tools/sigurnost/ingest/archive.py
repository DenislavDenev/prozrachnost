"""Reads data.egov.bg answers from the archive of Наблюдател (the bronze layer). Nothing is downloaded here.

The archive keeps `egov/<set>/<resource>/<version>.<sha12>.json`, the list of each set in `egov/<set>/_list/<day>.<sha12>.json`
and `state/egov.json`: for each resource (and for `<set>/list`) the sha256 and file of the newest answer, and `last_ok`, the
time of the last good read of the portal.
"""
import datetime as dt
import hashlib
import json

from . import config


class ArchiveError(Exception):
    pass


def state(root=None):
    root = root or config.ARCHIVE
    try:
        return json.loads((root / "state" / "egov.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError("Състоянието на архива не се чете: " + str(e)) from e


def age_hours(st, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    last = dt.datetime.fromisoformat(st["last_ok"].replace("Z", "+00:00"))
    return (now - last).total_seconds() / 3600


def _file(key, st, root):
    ent = st["seen"].get(key)
    if ent is None:
        return None
    root = root or config.ARCHIVE
    path = root / ent["file"]
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ArchiveError("Файлът не се чете: " + str(e)) from e
    if hashlib.sha256(raw).hexdigest() != ent["sha"]:
        raise ArchiveError("Файлът в архива не съвпада с отчетения хеш: " + ent["file"])
    return raw, ent["sha"], ent["file"], ent["at"]


def resources(set_uri, st, root=None):
    """The list of the set's resources as the portal gave it, or None when the archive has none yet."""
    got = _file(f"{set_uri}/list", st, root)
    if got is None:
        return None
    try:
        data = json.loads(got[0])
    except ValueError as e:
        raise ArchiveError("Списъкът на набора не е JSON: " + got[2]) from e
    return data, got[1], got[2]


def read(uri, st, root=None):
    """(bytes, sha256, relative file, read_at) of the newest answer for the resource; None when the archive has none."""
    return _file(uri, st, root)

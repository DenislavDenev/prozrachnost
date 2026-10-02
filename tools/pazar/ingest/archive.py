"""Reads the daily ZIPs from the archive of Наблюдател (the bronze layer). Nothing is downloaded here.

The archive keeps `kolkostruva/<year>/<day>.<sha12>.zip` and `state/kolkostruva.json` with, for each day, the sha256
and file of the newest answer, and `last_ok`, the time of the last successful read of the source.
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
        return json.loads((root / "state" / "kolkostruva.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError("Състоянието на архива не се чете: " + str(e)) from e


def days(st):
    """{day: (sha256, relative file)} of the newest answer of each day."""
    return {d: (v["sha"], v["file"]) for d, v in st["seen"].items()}


def age_hours(st, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    last = dt.datetime.fromisoformat(st["last_ok"].replace("Z", "+00:00"))
    return (now - last).total_seconds() / 3600


def read(day, st, root=None):
    """(bytes, sha256, path). The file is checked against the sha256 in the state: a changed archive is an error."""
    root = root or config.ARCHIVE
    if day not in st["seen"]:
        raise ArchiveError("Няма файл за " + day)
    sha, rel = days(st)[day]
    path = root / rel
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ArchiveError("Файлът не се чете: " + str(e)) from e
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ArchiveError("Файлът в архива не съвпада с отчетения хеш: " + rel)
    return raw, sha, str(path)

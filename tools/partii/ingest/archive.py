"""Reads the answers of ЕРИК from the archive of Наблюдател (the bronze layer). Nothing is downloaded here.

The archive keeps every distinct answer once: a file `erik/<electionId>/<name>.<sha12>.<ext>` and a line in
`index.jsonl` (when, key, file, sha256, bytes). `state/erik.json` has, for each key, the newest answer, and `last_ok`,
the time of the last successful read of the source.
"""
import datetime as dt
import hashlib
import json
import re

from . import config


class ArchiveError(Exception):
    pass


def when(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def state(root=None):
    root = root or config.ARCHIVE
    try:
        return json.loads((root / "state" / "erik.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError("Състоянието на архива не се чете: " + str(e)) from e


def age_hours(st, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if not st.get("last_ok"):
        raise ArchiveError("Архивът няма успешно четене на ЕРИК")
    return (now - when(st["last_ok"])).total_seconds() / 3600


# which answers the tool uses: (table, variant) by the address. The pages of single participants, the documents and
# the declarations are not read: they hold personal data or nothing the tables do not have.
KEYS = [
    (r"^GET \S+/Reports\?electionId=(\d+)$", "page_participants", None),
    (r"^GET \S+/Reports/Donation\?electionId=(\d+)$", "page_donation", None),
    (r"^POST \S+/Reports/GetDonorsByElectionId\?.*\belectionId=(\d+)", "donors", None),
    (r"^POST \S+/Reports/GetCandidateDeclarations\?.*\belectionId=(\d+)", "candidates", None),
    (r"^POST \S+/Reports/GetAgencies\?.*\belectionId=(\d+)", "agencies", None),
    (r"^POST \S+/Reports/GetResultsByElectionId\?.*\belectionId=(\d+)", "results", None),
    (r"^POST \S+/Reports/GetParticipantsByElectionId\?.*\belectionId=(\d+)&electionCommissionType=(\d)", "participants", 2),
    (r"^GET \S+/Reports/AfterElectionExport/\?electionId=(\d+)&participantTypeId=(\d)$", "report", 2),
]


def classify(key):
    """(table, election id, variant) of an archive key, or None for an answer the tool does not use."""
    if key == "home":
        return "home", None, ""
    for pattern, table, variant in KEYS:
        m = re.match(pattern, key)
        if m:
            return table, int(m.group(1)), m.group(variant) if variant else ""
    return None


def entries(root=None):
    """The archived answers of ЕРИК the tool uses, oldest first: dicts with at, key, file, sha256, bytes, table, erik_id, variant."""
    root = root or config.ARCHIVE
    out = []
    try:
        with (root / "index.jsonl").open(encoding="utf-8") as f:
            for n, line in enumerate(f):
                if '"erik"' not in line:
                    continue
                e = json.loads(line)
                what = classify(e["key"]) if e.get("source") == "erik" else None
                if what:
                    out.append(dict(e, at=when(e["at"]), table=what[0], erik_id=what[1], variant=what[2], line=n))
    except (OSError, ValueError, KeyError) as e:
        raise ArchiveError("Дневникът на архива не се чете: " + str(e)) from e
    out.sort(key=lambda e: (e["at"], e["line"]))
    return out


def page_sha(data):
    """The archive hashes a page without its anti-forgery tokens, which change on every read."""
    return hashlib.sha256(re.sub(rb'value="CfDJ8[^"]*"|nonce="[^"]*"', b"", data)).hexdigest()


def read(entry, root=None):
    """The bytes of an archived answer, checked against the size and sha256 of the index: a changed archive is an error."""
    root = root or config.ARCHIVE
    try:
        raw = (root / entry["file"]).read_bytes()
    except OSError as e:
        raise ArchiveError("Файлът не се чете: " + str(e)) from e
    sha = page_sha(raw) if entry["file"].endswith(".html") else hashlib.sha256(raw).hexdigest()
    if sha != entry["sha256"] or len(raw) != entry["bytes"]:
        raise ArchiveError("Файлът в архива не съвпада с отчетения хеш или размер: " + entry["file"])
    return raw

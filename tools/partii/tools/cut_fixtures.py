"""Cuts the test fixtures from the archive of Наблюдател (run on the server, as a user that reads the archive):

    python3 tools/cut_fixtures.py /srv/prozrachnost/nablyudatel /tmp/partii-fixtures

The fixtures are real answers of ЕРИК, whole (so the source's own counters and totals still hold), with one change:
every value that names or locates a natural person is replaced. The name of a donor becomes "Физическо лице N" (the
same N for the same name inside a file, so repeated donors stay repeated), the free text of the purpose is emptied,
the contact, bank and representative fields of the participant are emptied, and the ids of the personal documents are
set to 0. Amounts, dates, kinds, flags, participants and counters are untouched. The spreadsheets and pages are copied
as they are: they hold no donors.
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

PERSONAL = ("contactName", "contactPhone", "contactEmail", "contactAddress", "contactSettlement", "contactPostCode", "personResponsible",
            "personEgn", "phone", "bankIban", "bankBic", "bankName", "bankAccInDate", "registrationAddressDetails", "registrationSettlement", "notes")
# (election, part of the key, which answer of the key in time order: 0 the first, -1 the last, None all)
WANT = [
    ("79", "GetDonorsByElectionId", -1), ("81", "GetDonorsByElectionId", -1), ("96", "GetDonorsByElectionId", None),
    ("95", "GetDonorsByElectionId", (0, 1, 2)), ("92", "GetDonorsByElectionId", -1),
    ("79", "GetCandidateDeclarations", -1), ("68", "GetCandidateDeclarations", -1), ("96", "GetCandidateDeclarations", -1),
    ("96", "GetAgencies", None), ("81", "GetAgencies", -1), ("81", "GetCandidateDeclarations", -1), ("81", "GetResultsByElectionId", -1),
    ("79", "GetResultsByElectionId", -1), ("96", "GetResultsByElectionId", -1),
    ("96", "GetParticipantsByElectionId", None), ("79", "GetParticipantsByElectionId", None), ("81", "GetParticipantsByElectionId", None),
    ("81", "GetOikRegisteredDataFromOldSystem", None),
    ("79", "/Reports?electionId", -1), ("96", "/Reports?electionId", -1), ("81", "/Reports?electionId", -1), ("95", "/Reports?electionId", -1), ("92", "/Reports?electionId", -1),
    ("79", "/Reports/Donation?", -1), ("96", "/Reports/Donation?", -1), ("81", "/Reports/Donation?", -1), ("95", "/Reports/Donation?", -1), ("92", "/Reports/Donation?", -1),
    ("79", "AfterElectionExport/?", "distinct"), ("93", "AfterElectionExport/?", "distinct"), ("81", "AfterElectionExport/?", "distinct"),
    ("96", "AfterElectionExport/?", "distinct"),
]


def person(names, value, word):
    if not value:
        return value
    return f"{word} {names.setdefault(value, len(names) + 1)}"


def clean(data):
    d = json.loads(data)
    names = {}
    for r in d.get("data") or []:
        for k in ("cik", "rik", "oik"):
            if isinstance(r.get(k), dict):
                for f in PERSONAL:
                    if f in r[k] and r[k][f] is not None:
                        r[k][f] = "" if isinstance(r[k][f], str) else None
        if "donateTo" in r:                                  # a donor
            r["fullName"] = person(names, r["fullName"], "Физическо лице")
            r["description"] = "" if r.get("description") is not None else None
        elif "candidateType" in r:                           # a candidate or a member of an initiative committee
            r["name"] = person(names, r["name"], "Кандидат")
        elif "compliance" in r:                              # a checked person
            r["fullName"] = person(names, r["fullName"], "Лице")
            r["reportId"] = 0
        for f in ("declarationLink", "fileDownLoadLink"):
            if r.get(f):
                r[f] = re.sub(r"id=\d+", "id=0", r[f])
    return json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()


def main(root, out):
    root, out = Path(root), Path(out)
    lines = [json.loads(x) for x in (root / "index.jsonl").open(encoding="utf-8") if '"erik"' in x]
    lines = [x for x in lines if x["source"] == "erik"]
    index, shas = [], {}
    home = [x for x in lines if x["key"] == "home"][-1]
    picked = [home]
    for eid, part, which in WANT:
        by_key = {}
        for x in lines:
            if part in x["key"] and re.search(r"[?&]electionId=%s(&|$)" % eid, x["key"], re.I):
                by_key.setdefault(x["key"], []).append(x)
        for key, xs in sorted(by_key.items()):
            if which is None:
                picked += xs
            elif which == "distinct":                         # the export is a new zip on every read: one is enough
                picked.append(xs[-1])
            elif isinstance(which, tuple):
                picked += [xs[i] for i in which if i < len(xs)]
            else:
                picked.append(xs[which])
    for x in picked:
        src = root / x["file"]
        data = src.read_bytes()
        name = Path(x["file"]).name
        (out / "erik" / Path(x["file"]).parent.name).mkdir(parents=True, exist_ok=True) if x["key"] != "home" else (out / "erik").mkdir(parents=True, exist_ok=True)
        dst = out / x["file"]
        if name.endswith(".json"):
            data = clean(data)
        dst.write_bytes(data)
        cleaned = name.endswith(".json")      # a cleaned file has its own sha256, so the reader's check of the archive still works
        sha = hashlib.sha256(data).hexdigest() if cleaned else x["sha256"]
        shas[x["key"]] = sha
        index.append({"at": x["at"], "source": "erik", "key": x["key"], "file": x["file"], "sha256": sha, "bytes": len(data),
                      **({"original_sha256": x["sha256"]} if cleaned else {})})
    (out / "index.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in index), encoding="utf-8")
    st = json.loads((root / "state" / "erik.json").read_text(encoding="utf-8"))
    keep = {i["key"] for i in index}
    (out / "state").mkdir(exist_ok=True)
    (out / "state" / "erik.json").write_text(json.dumps({"seen": {k: {**v, "sha": shas[k]} for k, v in st["seen"].items() if k in keep}, "last_ok": st["last_ok"], "last": {"elections": len(set(e for e, _, _ in WANT)) and 21}},
                                                        ensure_ascii=False), encoding="utf-8")
    print(len(index), "files")


if __name__ == "__main__":
    main(*sys.argv[1:3])

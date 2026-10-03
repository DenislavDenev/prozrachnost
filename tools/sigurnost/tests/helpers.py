"""Real answers from the archive as fixtures, and a small archive built from them in a temporary folder."""
import hashlib
import json
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures" / "egov"
POLICE = "386ae85b-0c5c-4a5e-bd88-a8c7c123b765"      # Полицейска статистика 2024 г.
OLD = "dc074958-c8d5-4484-808a-800335ea4a23"         # Статистически данни на престъпността (2014, 2015)
BULLETIN = "32cdf912-38b5-4e58-bfc0-70df0b02a36b"      # Ежемесечен бюлетин: 104 hyperlinks to PDF files on mvr.bg
Y2016, Y2017, Y2019, Y2020, Y2023 = ("28dd128d-667c-44e6-bb5a-c9be1334941d", "88669f04-0bb3-4d6a-be84-4da97d77b084", "9c037c22-aeaa-4e4a-8be2-4eda7fa4b4f6",
                                     "230a6a9c-f7b7-456f-b8db-ba67d66a6b91", "b03e8542-0576-4770-8b44-977c0015589a")
POLICE_2019 = "9c037c22-aeaa-4e4a-8be2-4eda7fa4b4f6"
POLICE_2019_B = "fcb6aff7-f948-402d-8005-90091f80f02f"

A = "ef7a8f29-4994-41dd-a42d-e5017079b7b2"   # by crime types (the country)
B = "0e35ccef-7853-4881-b324-18bff541b024"   # by structures
C = "7d075b6c-21ca-46c0-be4a-3be4586384a4"   # the main table
D = "9d2cc270-faf4-4427-a18b-ce950a51da7b"   # personal and property crimes, structure after structure
E = "21a6fa0a-bfa4-4550-935c-55aa95a537a9"   # economic crimes, structure after structure (the fixture holds 3 blocks)
OTHER = "2e387772-e9b6-465f-9e16-fff0ed628cc6"   # by place of the crime: not a published family
NOLABELS = "181dd1cb-6d47-4a28-8914-d78d9407f351"


def fixture(set_uri, res):
    hits = sorted((FIX / set_uri).glob(res + "*.json"))
    return hits[0].read_bytes()


def uri(set_uri, prefix):
    """The full uri of a resource from the name of its fixture file."""
    return sorted((FIX / set_uri).glob(prefix + "*.json"))[0].name.split(".")[0]


def listing(set_uri):
    return json.loads((FIX / set_uri / "_list.json").read_bytes())


def build_archive(root, sets, last_ok="2026-10-02T02:56:05Z", now_read="2026-10-02T02:50:00Z"):
    """sets: {set_uri: {resource_uri: bytes}}. Writes egov/<set>/<resource>/1.<sha12>.json, the list of the set and
    state/egov.json as Наблюдател does; returns the state."""
    seen, vers = {}, {}
    for set_uri, res in sets.items():
        meta = {r["uri"]: r for r in listing(set_uri)} if (FIX / set_uri / "_list.json").exists() else {}
        lst = []
        for uri, raw in (res or {}).items():
            sha = hashlib.sha256(raw).hexdigest()
            rel = f"egov/{set_uri}/{uri}/1.{sha[:12]}.json"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(raw)
            seen[uri] = {"sha": sha, "file": rel, "at": now_read}
            m = dict(meta.get(uri) or {"uri": uri, "name": "Полицейска статистика 2024 г.", "version": "1", "updated_at": "2025-04-10 15:00:00"})
            m["dataset_uri"] = set_uri
            lst.append(m)
        if res is None:       # a set of links: the list as the portal gave it, no files
            lst = list(meta.values())
        body = json.dumps(sorted(lst, key=lambda r: r["uri"]), ensure_ascii=False).encode("utf-8")
        sha = hashlib.sha256(body).hexdigest()
        rel = f"egov/{set_uri}/_list/2026-10-02.{sha[:12]}.json"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(body)
        seen[f"{set_uri}/list"] = {"sha": sha, "file": rel, "at": now_read}
    st = {"run_at": now_read, "last_ok": last_ok, "seen": seen, "versions": vers}
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "egov.json").write_text(json.dumps(st), encoding="utf-8")
    return st


def police_2024():
    return {A: fixture(POLICE, A), B: fixture(POLICE, B), C: fixture(POLICE, C), D: fixture(POLICE, D), E: fixture(POLICE, E),
            OTHER: fixture(POLICE, OTHER)}

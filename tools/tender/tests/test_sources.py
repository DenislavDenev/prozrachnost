"""How each source's changes are caught without losing what we hold (docs/methodology.md, 9). No database:
the ЕОП mirror runs against a temporary folder with the network replaced."""
import json

import pytest

from ingest import eop, eop_offers


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """ЕОП as a dict {day: {kind: (key, body, modified)}}; eop reads and writes tmp_path."""
    src = {}
    monkeypatch.setattr(eop, "RAW_EOP", tmp_path)
    monkeypatch.setattr(eop.time, "sleep", lambda s: None)

    def list_day(day):
        return {k: (key, len(body), mod) for k, (key, body, mod) in src[day].items()} if day in src else None

    def get(url, accept="*/*"):
        day, key = url.split("/open-data-")[1].split("/", 1)
        return next(body for k, body, m in src[day].values() if eop.urllib.parse.quote(k) == key)
    monkeypatch.setattr(eop, "list_day", list_day)
    monkeypatch.setattr(eop, "get", get)
    return src, tmp_path


def put(src, day, kind, rows, modified="2026-09-26T06:00:00Z"):
    src.setdefault(day, {})[kind] = (f"{kind} key", json.dumps(rows).encode(), modified)


def test_listing_differs_on_size_key_date_and_files():
    held = {"contracts": {"key": "k", "size": 10, "sha256": "x", "modified": "A"}}
    assert eop.differs(held, {"contracts": ("k", 10, "A")}) == []
    assert eop.differs(held, {"contracts": ("k", 11, "A")}) == ["contracts"]              # size
    assert eop.differs(held, {"contracts": ("k2", 10, "A")}) == ["contracts"]             # key
    assert eop.differs(held, {"contracts": ("k", 10, "B")}) == ["contracts"]              # rewritten, same size
    assert eop.differs(held, {"contracts": ("k", 10, "A"), "ocds": ("o", 1, "A")}) == ["ocds"]   # file added
    assert eop.differs(held, {}) == ["contracts"]                                         # file gone
    old = {"contracts": {"key": "k", "size": 10, "sha256": "x"}}                          # before the audit: no date
    assert eop.differs(old, {"contracts": ("k", 10, "B")}) == []
    dropped = {"contracts": {"key": "k", "size": 10, "sha256": "x", "dropped": True}}
    assert eop.differs(dropped, {}) == []                                                 # already known as dropped


def test_refetch_keeps_the_old_copy_and_never_takes_a_broken_file(mirror):
    src, raw = mirror
    put(src, "2024-05-01", "contracts", [{"contractNumber": "1", "contractValue": "100"}])
    m = eop.fetch_day("2024-05-01")
    assert m["published"] and m["changed"] == [] and m["files"]["contracts"]["modified"] == "2026-09-26T06:00:00Z"
    first = (raw / "2024-05-01" / "contracts.json").read_bytes()
    # the source rewrites the day: the new file is in place, the old one in history
    put(src, "2024-05-01", "contracts", [{"contractNumber": "1", "contractValue": "900"}], "2026-10-01T06:00:00Z")
    m = eop.fetch_day("2024-05-01", force=True)
    assert m["changed"] == ["contracts"]
    assert json.loads((raw / "2024-05-01" / "contracts.json").read_bytes())[0]["contractValue"] == "900"
    hist = list((raw / "2024-05-01" / "history").iterdir())
    assert len(hist) == 1 and hist[0].read_bytes() == first
    # a file that is not JSON does not replace ours
    src["2024-05-01"]["contracts"] = ("contracts key", b'[{"contractNumber": "1", "contr', "2026-10-02T06:00:00Z")
    m = eop.fetch_day("2024-05-01", force=True)
    assert m["invalid"] == ["contracts"] and m["changed"] == []
    assert json.loads((raw / "2024-05-01" / "contracts.json").read_bytes())[0]["contractValue"] == "900"
    assert m["files"]["contracts"]["modified"] == "2026-10-01T06:00:00Z"   # still the copy we hold
    # the day stops answering: our copy stays, the manifest is not overwritten
    del src["2024-05-01"]
    m = eop.fetch_day("2024-05-01", force=True)
    assert m.get("gone") and m["published"]
    assert json.loads((raw / "2024-05-01" / "manifest.json").read_text())["published"]
    assert (raw / "2024-05-01" / "contracts.json").exists()
    assert not list((raw / "2024-05-01").glob("*.tmp"))


def test_a_file_the_source_drops_stays_and_is_marked(mirror):
    src, raw = mirror
    put(src, "2024-05-02", "contracts", [{"a": 1}])
    put(src, "2024-05-02", "annexes", [{"b": 2}])
    eop.fetch_day("2024-05-02")
    del src["2024-05-02"]["annexes"]
    m = eop.fetch_day("2024-05-02", force=True)
    assert m["files"]["annexes"].get("dropped") and "annexes" in m["changed"]
    assert (raw / "2024-05-02" / "annexes.json").exists()
    assert eop.read_rows("2024-05-02", "annexes") == [{"b": 2}]


def test_audit_refetches_only_what_changed_and_records_dates(mirror):
    src, raw = mirror
    for day in ("2024-05-03", "2024-05-04", "2024-05-05"):
        put(src, day, "contracts", [{"day": day}])
        eop.fetch_day(day)
    # a manifest from before the audit has no LastModified: the audit records it, nothing is refetched
    m = json.loads((raw / "2024-05-03" / "manifest.json").read_text())
    del m["files"]["contracts"]["modified"]
    (raw / "2024-05-03" / "manifest.json").write_text(json.dumps(m))
    put(src, "2024-05-04", "contracts", [{"day": "2024-05-04", "changed": True}], "2026-10-05T06:00:00Z")
    del src["2024-05-05"]
    rep = eop.audit(["2024-05-03", "2024-05-04", "2024-05-05", "2024-05-06"])
    assert rep["checked"] == 3 and rep["baseline"] == 1 and rep["gone"] == ["2024-05-05"]
    assert [c["day"] for c in rep["changed"]] == ["2024-05-04"] and rep["changed"][0]["content"] == ["contracts"]
    assert json.loads((raw / "2024-05-03" / "manifest.json").read_text())["files"]["contracts"]["modified"]
    assert eop.read_rows("2024-05-04", "contracts")[0]["changed"] is True
    assert eop.read_rows("2024-05-05", "contracts") == [{"day": "2024-05-05"}]   # gone at the source, kept here
    # nothing changed since: a second audit refetches nothing
    rep = eop.audit(["2024-05-03", "2024-05-04"])
    assert rep["changed"] == [] and rep["baseline"] == 0


def offer(lot, oid, price, name="А", opened=True):
    return {"lot_no": lot, "round": 1, "offer_id": oid, "bidder_name": name, "bidder_eik": None, "price": price, "price_opened": opened}


def test_offer_diff_names_every_change_and_ignores_rounding():
    old = [offer(1, 10, 100.0), offer(1, 11, 200.0), offer(2, 12, None, opened=False)]
    new = [offer(1, 10, 100.004), offer(2, 12, 300.0), offer(2, 13, 50.0, "Б")]
    d = eop_offers.offer_diff(old, new)
    assert ("1/1/11", None, "А 200.0", None) in d                       # gone
    assert ("2/1/13", None, None, "Б 50.0") in d                        # added
    assert ("2/1/12", "price", None, 300.0) in d and ("2/1/12", "price_opened", False, True) in d
    assert not [x for x in d if x[0] == "1/1/10"]                       # 0.004 is rounding, not a change
    assert eop_offers.offer_diff(old, old) == []
    assert eop_offers.offer_set(new) == [(1, 1, 10), (2, 1, 12), (2, 1, 13)]

"""The archive without the network: a fake HTTP client answers from a dict."""
import io
import json
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from archive import run as runmod
from archive import sources
from archive.core import Bad, Store, check
from archive.sources import ajax_calls, link, row_links


def zbytes(text="a;b\n1;2\n"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("chain.csv", text)
    return b.getvalue()


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        a = self.answers.get(url, (404, b""))
        return a(url) if callable(a) else a

    def post(self, url, data, **kw):
        body = data if isinstance(data, (bytes, str)) else "&".join(f"{k}={v}" for k, v in data.items())
        self.calls.append(("POST", url, body))
        a = self.answers.get(("POST", url), (404, b""))
        return a(data) if callable(a) else a


FAR = time.monotonic() + 3600


# --- checks and the store ---------------------------------------------------------------------------------------------

def test_an_error_page_or_a_broken_file_is_never_stored():
    check(zbytes(), "zip")
    for data, fmt in [(b"<!DOCTYPE html><html>404</html>", "zip"), (b"PK\x03\x04broken", "zip"), (b"", "json"),
                      (b"{not json", "json"), (b"<a><b></a>", "xml"), (b'{"success":false,"error":"x"}', "egov"),
                      (b"<html><body>Service Unavailable</body></html>", "csv")]:
        with pytest.raises(Bad):
            check(data, fmt)
    check(b'{"success":true,"data":[]}', "egov")


def test_the_same_answer_is_kept_once_and_a_new_one_beside_it(tmp_path):
    st = Store(tmp_path)
    assert st.put("x", "k", b"one", "txt", name="2026-09-28") is True
    assert st.put("x", "k", b"one", "txt", name="2026-09-29") is False
    assert st.put("x", "k", b"two", "txt", name="2026-09-29") is True
    st.save()
    files = sorted(p.name for p in (tmp_path / "x").iterdir())
    assert len(files) == 2 and files[0].startswith("2026-09-28.")
    index = [json.loads(line) for line in (tmp_path / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [i["bytes"] for i in index] == [3, 3]
    again = Store(tmp_path)   # the state survives the process
    assert again.put("x", "k", b"two", "txt") is False


def test_a_page_whose_only_change_is_a_token_is_not_stored_again(tmp_path):
    st = Store(tmp_path)
    a = b'<html><input name="__RequestVerificationToken" value="CfDJ8aaa"><td>100</td></html>'
    b = a.replace(b"CfDJ8aaa", b"CfDJ8bbb")
    assert st.put("erik", "p", a, "html", sha_of=sources.erik_sha(a))
    assert not st.put("erik", "p", b, "html", sha_of=sources.erik_sha(b))
    assert st.put("erik", "p", b.replace(b"100", b"200"), "html", sha_of=sources.erik_sha(b.replace(b"100", b"200")))


# --- Колко струва ------------------------------------------------------------------------------------------------------

def test_kolkostruva_reads_every_day_from_the_first_and_remembers_the_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "KOLKOSTRUVA_FROM", date(2026, 9, 1))
    monkeypatch.setattr(sources, "today", lambda: date(2026, 9, 30))
    url = sources.KOLKOSTRUVA.format
    answers = {url(f"2026-09-{d:02d}"): (200, zbytes(str(d))) for d in range(1, 30) if d not in (10, 20)}
    answers[url("2026-09-05")] = (200, b"<!DOCTYPE html><html>not found</html>")
    http, st = FakeHttp(answers), Store(tmp_path)
    with pytest.raises(Bad, match="an HTML page instead of zip"):   # a day that answers with a page stops the run
        sources.kolkostruva(st, http, False, FAR)
    answers[url("2026-09-05")] = (200, zbytes("5"))
    out = sources.kolkostruva(st, http, False, FAR)
    assert out["days"] == 27 and out["newest"] == "2026-09-29" and out["gaps"] == 3   # the 10th, the 20th, today
    assert (tmp_path / "kolkostruva" / "2026").is_dir()
    http.calls.clear()
    sources.kolkostruva(st, http, False, FAR)   # again: only the gaps of the last 14 days, nothing already kept
    assert sorted(u for _, u in http.calls) == [url("2026-09-20"), url("2026-09-30")]
    http.calls.clear()
    sources.kolkostruva(st, http, True, FAR)    # --full asks the old gap too
    assert sorted(u for _, u in http.calls) == [url("2026-09-10"), url("2026-09-20"), url("2026-09-30")]


def test_kolkostruva_is_late_when_no_new_day_comes_for_three_days(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "KOLKOSTRUVA_FROM", date(2026, 9, 1))
    monkeypatch.setattr(sources, "today", lambda: date(2026, 9, 10))
    answers = {sources.KOLKOSTRUVA.format(f"2026-09-0{d}"): (200, zbytes(str(d))) for d in range(1, 7)}
    with pytest.raises(Bad, match="no new day since 2026-09-06"):
        sources.kolkostruva(Store(tmp_path), FakeHttp(answers), False, FAR)


# --- ЕРИК --------------------------------------------------------------------------------------------------------------

REPORT = """<input id="ElectionId" name="ElectionId" type="hidden" value="90" />
<a href="/Reports/AfterElectionExportList?ElectionId=90">Експорт</a>
<script>
var oikUrl = "/Reports/GetParticipantsByElectionId";
if (true == false) {
    oikUrl = "/Reports/GetOikRegisteredDataFromOldSystem"
}
$('#t').DataTable({ ajax: { url: oikUrl, type: "POST", datatype: "json",
    data: function (d) { d.electionId = 90; d.electionCommissionType = 3; } },
  columns: [{ render: function (data, type, row) { var url = "";
    if (true == true) { url = "/Reports/ItemOikReport?id=" + row.id } else { url = "/Reports/ItemOik?id=" + row.id }
    return '<a href="' + url + '">' + data + '</a>'; } }] });
$('#a').DataTable({ ajax: { url: "/Reports/GetAgencies", type: "POST",
    data: function (d) { d.electionId = $('#ElectionId').val(); } } });
</script>"""


def test_the_tables_of_an_erik_page_are_found_with_their_parameters():
    calls = ajax_calls(REPORT, "90")
    assert calls == [
        ("POST", "/Reports/GetParticipantsByElectionId",
         {"draw": "1", "start": "0", "length": "-1", "electionId": "90", "electionCommissionType": "3"}),
        ("POST", "/Reports/GetAgencies", {"draw": "1", "start": "0", "length": "-1", "electionId": "90"}),
    ]
    links = row_links(REPORT)
    assert links == [["/Reports/ItemOikReport?id=", ("row", "id")]]   # the dead branch (ItemOik) is gone
    assert link(links[0], {"id": 118007}) == "/Reports/ItemOikReport?id=118007"
    assert link(links[0], {"id": None}) is None


def test_erik_follows_pages_tables_rows_and_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ERIK_PAGES", ["/Reports/AfterElection?electionId={e}"])
    B = sources.ERIK
    home = b'<a href="/Reports?electionId=90">x</a><a href="/Reports?electionId=91">y</a>'
    rows = json.dumps({"draw": 1, "data": [{"id": 118007}, {"id": 5}]}).encode()
    answers = {
        B + "/": (200, home),
        B + "/Reports/AfterElection?electionId=90": (200, REPORT.encode()),
        B + "/Reports/AfterElection?electionId=91": (200, b"<html>91</html>"),
        ("POST", B + "/Reports/GetParticipantsByElectionId"): (200, rows),
        ("POST", B + "/Reports/GetAgencies"): (200, b'{"data":[]}'),
        B + "/Reports/ItemOikReport?id=118007": (200, b'<html><a href="/Home/Download/?documentId=7&amp;idE=90">f</a></html>'),
        B + "/Reports/ItemOikReport?id=5": (200, home),   # an unknown report: the site gives its home page
        B + "/Home/Download/?documentId=7&idE=90": (200, b"%PDF-1.4 report"),
        B + "/Reports/AfterElectionExportList?ElectionId=90": (200, b"<html>export</html>"),
    }
    st = Store(tmp_path)
    out = sources.erik(st, FakeHttp(answers), True, FAR)
    assert out["errors"] == 0 and out["absent"] == 1 and out["elections"] == 2
    names = sorted(p.name.split(".")[0] for p in (tmp_path / "erik" / "90").iterdir())
    assert "Reports_ItemOikReport_id=118007" in names and any(n.startswith("Home_Download") for n in names)
    assert any(p.suffix == ".pdf" for p in (tmp_path / "erik" / "90").iterdir())
    again = sources.erik(st, FakeHttp(answers), True, FAR)   # nothing changed: nothing new
    assert again["new"] == 0


def test_erik_fails_when_many_reports_break(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "ERIK_PAGES", ["/Reports/AfterElection?electionId={e}"])
    B = sources.ERIK
    rows = json.dumps({"data": [{"id": i} for i in range(10)]}).encode()
    answers = {B + "/": (200, b'<a href="/Reports?electionId=90">x</a>'),
               B + "/Reports/AfterElection?electionId=90": (200, REPORT.encode()),
               ("POST", B + "/Reports/GetParticipantsByElectionId"): (200, rows),
               ("POST", B + "/Reports/GetAgencies"): (200, b"<html>error</html>")}
    for i in range(10):
        answers[B + f"/Reports/ItemOikReport?id={i}"] = (200, b"")
    with pytest.raises(Bad, match="requests failed"):
        sources.erik(Store(tmp_path), FakeHttp(answers), True, FAR)


# --- freshness and the command --------------------------------------------------------------------------------------------

def test_freshness_names_failed_late_and_never_read_sources():
    at = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    ok = (at - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (at - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    states = {name: {"last_ok": ok} for name in sources.SOURCES}
    assert runmod.freshness(states, at) == []
    states["erik"] = {"last_ok": old, "last_error": "Blocked: refused", "last_error_at": ok}
    states["kolkostruva"] = {"last_ok": old}
    del states["egov"]
    problems = runmod.freshness(states, at)
    assert len(problems) == 3
    assert any(p.startswith("erik") and "refused" in p for p in problems)
    assert any(p.startswith("kolkostruva") and "72 h ago" in p for p in problems)
    assert any(p.startswith("egov") and "never read" in p for p in problems)
    states["dfz"] = {"last_ok": old}   # the weekly one may be 3 days old
    assert not any(p.startswith("dfz") for p in runmod.freshness(states, at))


def test_a_failed_source_is_recorded_and_the_command_exits_1(tmp_path, monkeypatch, capsys):
    def broken(st, http, full, deadline):
        raise Bad("the source is down")
    monkeypatch.setitem(sources.SOURCES, "kolkostruva", {**sources.SOURCES["kolkostruva"], "fn": broken})
    monkeypatch.setenv("ARHIV_DIR", str(tmp_path))
    assert runmod.main(["kolkostruva"]) == 1
    line = json.loads(capsys.readouterr().out.strip())
    assert line["ok"] is False and "down" in line["error"]
    state = json.loads((tmp_path / "state" / "kolkostruva.json").read_text(encoding="utf-8"))
    assert "down" in state["last_error"]
    assert runmod.main(["freshness"]) == 1
    assert runmod.main(["nope"]) == 2

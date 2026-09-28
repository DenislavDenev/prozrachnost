"""What the archive keeps, source by source. First the sources that delete their own history (the reason the archive
exists), then the rest that can be read without a browser, a key or a CAPTCHA. Each source is a function
`fn(store, http, full, deadline) -> summary`; `SOURCES` gives its HTTP manners and how old its last good run may be.
The sources that are left out, and why, are in docs/sources.md.
"""
import html as htmllib
import json
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .core import Bad, Blocked, check, safe


def today():
    return datetime.now(timezone.utc).date()


def kind(data):
    head = data[:8].lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        return "zip"
    if head[:1] in (b"{", b"["):
        return "json"
    if head[:1] == b"<":
        return "html"
    return "bin"


# --- Колко струва (КЗП): the page shows 14 days, the files stay on the server from 16.10.2025 (for now) -------------

KOLKOSTRUVA = "https://kolkostruva.bg/opendata_files/{}.zip"
KOLKOSTRUVA_FROM = date(2025, 10, 16)


def kolkostruva(st, http, full, deadline):
    """One zip a day (a CSV per chain). Days not read yet are read, from the first day; a missing day is asked
    again for two weeks (it may come late), older gaps only with --full. No new day for 3 days is a problem."""
    s = st.src("kolkostruva")
    gaps = set(s.setdefault("gaps", []))
    new = 0
    d, last = KOLKOSTRUVA_FROM, today()
    while d <= last and time.monotonic() < deadline:
        k = d.isoformat()
        if k not in s["seen"] and (full or k not in gaps or d >= last - timedelta(days=14)):
            code, body = http.get(KOLKOSTRUVA.format(k), timeout=300)
            if code == 404:
                gaps.add(k)
            else:
                check(body, "zip")
                new += st.put("kolkostruva", k, body, "zip", name=f"{k[:4]}/{k}")
                gaps.discard(k)
        d += timedelta(days=1)
    s["gaps"] = sorted(g for g in gaps if g not in s["seen"])
    newest = max(s["seen"], default=None)
    if newest is None or date.fromisoformat(newest) < last - timedelta(days=3):
        raise Bad(f"no new day since {newest}")
    return {"new": new, "days": len(s["seen"]), "newest": newest, "gaps": len(s["gaps"])}


# --- Fixed addresses: one file each, kept when it changes ---------------------------------------------------------------

URLS = {
    # ЕСО: the load of the last ~10 days, then gone
    "eso_load": ("https://www.eso.bg/api/load_plus_forecast.json.php", "json"),
    # НИГГГ: the earthquakes of the last 30 days, then gone
    "niggg": ("https://ndc.niggg.bas.bg/data.xml", "xml"),
    # НСИ: the register of places (ЕКАТТЕ), replaced on every change
    "ekatte": ("https://www.nsi.bg/nrnm/ekatte/zip/download?files_type=json", "zip"),
    # Столична община: the timetable of Sofia's public transport (GTFS), replaced on every change
    "gtfs_sofia": ("https://gtfs.sofiatraffic.bg/api/v1/static", "zip"),
    # Народно събрание: the members and groups of the current parliament
    "parliament_ns": ("https://www.parliament.bg/api/v1/coll-list-ns/bg", "json"),
}


def urls(st, http, full, deadline):
    out, bad = {}, []
    for name, (url, fmt) in URLS.items():
        try:
            code, body = http.get(url, timeout=300)
            if code != 200:
                raise Bad(f"answer {code}")
            check(body, fmt)
            out[name] = "new" if st.put(name, "latest", body, fmt, name=today().isoformat()) else "same"
        except (Bad, Blocked) as e:   # one address down does not stop the others; it is reported at the end
            bad.append(f"{name}: {e}")
    if bad:
        raise Bad("; ".join(bad))
    return out


# --- ЕРИК (Сметна палата): the register of an election is deleted at the next election of the same kind ------------

ERIK = "https://erik.bulnao.government.bg"
ERIK_PAGES = ["/Reports?electionId={e}", "/Reports/Donation?electionId={e}", "/Reports/InElection?electionId={e}",
              "/Reports/AfterElection?electionId={e}", "/Reports/Agencies?electionId={e}",
              "/Reports/Results?electionId={e}", "/Home/Documents?electionId={e}"]
ERIK_RECENT = 6   # elections read every day (the top of the list); all of them with --full
# ЕРИК's firewall cuts the connection after about 20 requests at 1 per second (28.09.2026): one per 6 s (SOURCES)


def live_js(page):
    """The pages choose between two addresses with `if (true == false) {…} else {…}`, written by the server: keep
    only the branch that runs."""
    def pick(m):
        return m.group(3) if m.group(1) == m.group(2) else (m.group(4) or "")
    return re.sub(r"if\s*\(\s*(true|false)\s*==\s*(true|false)\s*\)\s*\{([^{}]*)\}(?:\s*else\s*\{([^{}]*)\})?", pick, page)


def page_value(page, el_id):
    tag = re.search(r'<input[^>]*\bid="%s"[^>]*>' % re.escape(el_id), page)
    v = re.search(r'\bvalue="([^"]*)"', tag.group(0)) if tag else None
    return htmllib.unescape(v.group(1)) if v else None


def ajax_calls(page, eid):
    """The tables of a page load their rows with `$.ajax` / DataTables: `url`, `type` and `d.<name> = <value>`."""
    js = live_js(page)
    names = dict(re.findall(r"""\b(\w+)\s*=\s*["'](/[\w/]+)["']""", js))
    calls = []
    for m in re.finditer(r"""url:\s*(?:["']([^"']+)["']|(\w+))\s*,\s*type:\s*["'](\w+)["']""", js):
        url = m.group(1) or names.get(m.group(2))
        body = re.match(r"""[\s\S]{0,300}?data:\s*function\s*\(\s*d\s*\)\s*\{([^{}]*)\}""", js[m.end():])
        params = {"draw": "1", "start": "0", "length": "-1"}
        for k, v in re.findall(r"d\.(\w+)\s*=\s*([^;\n]+);", body.group(1) if body else ""):
            v = v.strip()
            el = re.fullmatch(r"""\$\(\s*['"]#(\w+)['"]\s*\)\.val\(\)""", v)
            if el:
                v = page_value(page, el.group(1)) or (str(eid) if k.lower() == "electionid" else "")
            params[k] = v.strip("\"'")
        if url:
            calls.append((m.group(3).upper(), url, params, m.start()))
    return calls


def row_links(page):
    """Links built from a row, as the list of their parts: `"/Participant/Index/?ikRegisteredId=" + row.id +
    "&electionId=" + 95` gives ["/Participant/Index/?ikRegisteredId=", ("row", "id"), "&electionId=", "95"]. A bare
    name (`'./GetDonorsData?financialReportId=' + reportId`) is read from the row's field of that name."""
    part = r"""(?:"[^"
]*"|'[^'
]*'|row\.\w+|\w+)"""
    out = []
    for m in re.finditer(r"""["'](?:\./|/)[\w/]+/?\?\w+=["'](?:\s*\+\s*%s)+""" % part, live_js(page)):
        parts = []
        for t in re.findall(part, m.group(0)):
            if t[0] in "\"'":
                parts.append(t[1:-1])
            elif t.isdigit():
                parts.append(t)
            else:
                parts.append(("row", t.split(".")[-1]))
        out.append((parts, m.start()))
    return out


def link(parts, row):
    vals = [p if isinstance(p, str) else row.get(p[1]) for p in parts]
    return None if any(v in (None, "") for v in vals) else "".join(str(v) for v in vals)


def downloads(page):
    return sorted({htmllib.unescape(h) for h in re.findall(r'href="([^"]*(?:Download|Export)[^"]*)"', page)})


def erik_sha(data):   # the anti-forgery tokens change on every read
    return re.sub(rb'value="CfDJ8[^"]*"|nonce="[^"]*"', b"", data)


def erik(st, http, full, deadline):
    code, home = http.get(ERIK + "/")
    if code != 200:
        raise Bad(f"home page {code}")
    elections = list(dict.fromkeys(re.findall(r'href="/Reports\?electionId=(\d+)"', home.decode("utf-8", "replace"))))
    if not elections:
        raise Bad("no elections on the home page")
    st.put("erik", "home", home, "html", name="home", sha_of=erik_sha(home))
    todo = elections if full else elections[:ERIK_RECENT]
    stats = {"elections": len(todo), "requests": 0, "new": 0, "errors": 0, "absent": 0, "first_errors": []}
    seen = set()

    def fetch(eid, method, url, params=None, depth=0):
        full_url = urllib.parse.urljoin(ERIK + "/", url)
        body_key = urllib.parse.urlencode(params) if params else ""
        key = method + " " + full_url + (("?" if "?" not in full_url else "&") + body_key if body_key else "")
        if key in seen or depth > 3 or time.monotonic() > deadline:
            return
        seen.add(key)
        stats["requests"] += 1
        try:
            code, data = http.post(full_url, params) if method == "POST" else http.get(full_url)
            if code in (403, 404, 410):
                stats["absent"] += 1
                return
            if method == "POST":
                check(data, "json")
            elif not data:
                raise Bad("empty answer")
        except Blocked:
            raise
        except Exception as e:  # one broken report does not stop the others; too many do (below)
            stats["errors"] += 1
            if len(stats["first_errors"]) < 5:
                stats["first_errors"].append(f"{key[:120]}: {str(e)[:120]}")
            return
        if data == home:   # the site answers an unknown report with its home page
            stats["absent"] += 1
            return
        k = kind(data)
        rel = urllib.parse.urlsplit(key.split(" ", 1)[1])
        stats["new"] += st.put("erik", key, data, k, name=f"{eid}/{safe(rel.path.strip('/') + '_' + rel.query)}",
                               sha_of=erik_sha(data) if k == "html" else None)
        if k == "html":
            page = data.decode("utf-8", "replace")
            tables = []   # (position of the table's ajax in the script, its rows)
            for m, u, p, at in ajax_calls(page, eid):
                before = len(stats_rows)
                fetch(eid, m, u, p, depth + 1)
                tables.append((at, stats_rows[before:]))
            for u in downloads(page):
                fetch(eid, "GET", u, depth=depth + 1)
            for parts, at in row_links(page):   # a link is built from the rows of the table defined just before it
                rows = next((r for pos, r in reversed(tables) if pos < at), [])
                for r in rows:
                    u = link(parts, r) if isinstance(r, dict) else None
                    if u:
                        fetch(eid, "GET", urllib.parse.urljoin(full_url, u), depth=depth + 1)
        elif k == "json":
            d = json.loads(data)
            if isinstance(d, dict) and isinstance(d.get("data"), list):
                stats_rows.extend(d["data"])

    stats_rows = []
    for eid in todo:
        for p in ERIK_PAGES:
            fetch(eid, "GET", p.format(e=eid))
    if stats["errors"] > max(3, stats["requests"] // 100):
        raise Bad(f"{stats['errors']} of {stats['requests']} requests failed: " + "; ".join(stats["first_errors"]))
    if time.monotonic() > deadline:
        stats["cut"] = "time budget reached; the rest on the next run"
    return stats


# --- ДФЗ: paid subsidies, kept for 2 financial years only --------------------------------------------------------------

DFZ = "https://seu.dfz.bg/seu/"


def dfz(st, http, full, deadline):
    """The public report (Oracle APEX, page 8110) for every financial year it offers, as the CSV the page's own
    "Download" gives: submit the year, then ask for the CSV of the same session."""
    code, first = http.get(DFZ + "f?p=727:8110:::NO")
    if code != 200:
        raise Bad(f"the report page answered {code}")
    page = first.decode("utf-8", "replace")
    years = re.findall(r'<option value="(\d{4})"', re.search(r'<select[^>]*id="P8110_FORYEAR".*?</select>', page, re.S).group(0))
    if not years:
        raise Bad("no financial years in the form")
    out = {}
    for y in years:
        code, first = http.get(DFZ + "f?p=727:8110:::NO")
        page = first.decode("utf-8", "replace")
        inst = re.search(r'name="p_instance" value="(\d+)"', page).group(1)
        items = []
        for m in re.finditer(r'(?:<select|<input)[^>]*name="(P8110_\w+)"', page):
            n = m.group(1)
            it = {"n": n, "v": {"P8110_FORYEAR": y, "P8110_AMOUNT_FUND": "TOTAL", "P8110_AMOUNT_FTYPE": "GT"}.get(n, "")}
            ck = re.search(r'data-for="%s" value="([^"]+)"' % n, page)
            if ck:
                it["ck"] = ck.group(1)
            items.append(it)
        pj = {"salt": re.search(r'value="([^"]+)" id="pSalt"', page).group(1),
              "pageItems": {"itemsToSubmit": items, "protected": re.search(r'id="pPageItemsProtected" value="([^"]*)"', page).group(1),
                            "rowVersion": ""}}
        code, r = http.post(DFZ + "wwv_flow.accept", {
            "p_flow_id": "727", "p_flow_step_id": "8110", "p_instance": inst,
            "p_page_submission_id": re.search(r'name="p_page_submission_id" value="([^"]+)"', page).group(1),
            "p_request": "GO", "p_reload_on_submit": "S", "p_json": json.dumps(pj)})
        if b"redirectURL" not in r:
            raise Bad(f"{y}: the form was not accepted: {r[:200]!r}")
        http.get(DFZ + json.loads(r)["redirectURL"])
        code, csv = http.get(DFZ + f"f?p=727:8110:{inst}:CSV::::", timeout=1800)
        check(csv, "csv")
        head = csv[:400].decode("utf-8", "replace") + csv[:400].decode("cp1251", "replace")   # cp1251, though the header says UTF-8
        if "бенефициент" not in head or head.lstrip().startswith("<"):
            raise Bad(f"{y}: not the CSV of the report: {head[:120]}")
        out[y] = {"rows": csv.count(b"\n") - 1, "new": st.put("dfz", f"fy{y}", csv, "csv", name=f"{y}/{today()}")}
    return out


# --- data.egov.bg: every resource of the data sets our plans use, each new version --------------------------------------

EGOV = "https://data.egov.bg/api/"
EGOV_SETS = {
    "066b4b04-d81d-444e-a61c-8ca0516079e4": "МОН: ДЗИ по училища",
    "17a20272-3a70-4056-9f4e-1b5f038fb11a": "МОН: групи и паралелки",
    "18da0fff-79b2-45c6-a9af-1509df96261b": "АМС: обществени консултации",
    "2df0c2af-e769-4397-be33-fcbe269806f3": "АВ: Търговски регистър",
    "3178b3ee-cfe8-4adc-b716-659d3228407f": "МФ: фискален резерв",
    "335961f9-5027-4e5d-bbda-667464a72b37": "МФ: макроикономическа прогноза",
    "386ae85b-0c5c-4a5e-bd88-a8c7c123b765": "МВР: полицейска статистика 2024",
    "4b948dd7-c9ef-4239-b2de-9b8e1c312467": "МВР: ПТП от 01.01.2024",
    "79ce7de2-0150-4ba7-a96c-dbacb76c95b6": "МФ: изпълнение на държавния бюджет",
    "980fa747-e0d0-4371-9457-f41d730040cd": "МФ: финансови показатели на общините",
    "9391b0c9-cbf5-449b-a936-56dd55f0591e": "АВ: Имотен регистър, месечни извлечения",
    "b56288b6-25aa-4049-9aa6-de2cd4cdabf8": "МОН: НВО VII клас по училища",
    "bfcdd4cb-4737-4272-92ea-9b2395f7cb14": "МФ: КФП месечно",
    "c4985243-e3ee-4d01-9404-e1cf98828fca": "МОН: профили и професии",
    "dc074958-c8d5-4484-808a-800335ea4a23": "МВР: статистика на престъпността",
    "ee08391e-be09-44c1-b278-b4af8b62a147": "МФ: общински дълг",
    "8c03a6fb-598f-44ea-81e5-9765d6e573df": "МОН: деца и ученици по класове",
    "81b81e76-e6aa-4895-b0b0-e0a9af53742f": "МОН: училища и детски градини",
}


def egov(st, http, full, deadline):
    """data.egov.bg answers 429 and then drops the connection when asked fast, so one request per 8 s and a stop at
    the first refusal. The list of a set's resources is kept too (a resource that vanishes from it is seen)."""
    def call(method, body):
        code, data = http.post(EGOV + method, json.dumps(body).encode(), headers={"Content-Type": "application/json"},
                               timeout=900)
        if code != 200:
            raise Bad(f"{method} {body}: {code}")
        return data

    s = st.src("egov")
    vers = s.setdefault("versions", {})
    out = {"sets": 0, "resources": 0, "new": 0, "missing_sets": []}
    for ds in EGOV_SETS:
        if time.monotonic() > deadline:
            out["cut"] = "time budget reached; the rest on the next run"
            break
        res, page = [], 1
        try:
            while True:
                d = json.loads(call("listResources", {"criteria": {"dataset_uri": ds}, "records_per_page": 100, "page_number": page}))
                res += d.get("resources") or []
                if not d.get("resources") or len(res) >= int(d.get("total_records") or 0):
                    break
                page += 1
        except Bad:   # the portal answers 500 for a set it does not know
            res = []
        if not res:
            out["missing_sets"].append(ds)
            continue
        out["sets"] += 1
        out["resources"] += len(res)
        st.put("egov", f"{ds}/list", json.dumps(sorted(res, key=lambda r: r["uri"]), ensure_ascii=False).encode(), "json",
               name=f"{ds}/_list/{today()}")
        for r in res:
            v = f"{r.get('version')}|{r.get('updated_at')}"
            if vers.get(r["uri"]) == v:
                continue
            if time.monotonic() > deadline:
                out["cut"] = "time budget reached; the rest on the next run"
                break
            data = call("getResourceData", {"resource_uri": r["uri"]})
            check(data, "egov")
            out["new"] += st.put("egov", r["uri"], data, "json", name=f"{ds}/{r['uri']}/{safe(str(r.get('version')))}")
            vers[r["uri"]] = v
    if out["missing_sets"]:
        raise Bad("data sets with no resources (gone?): " + ", ".join(f"{d} ({EGOV_SETS[d]})" for d in out["missing_sets"]))
    return out


# --- the register: HTTP manners and how old the last good run may be (hours) ---------------------------------------------

SOURCES = {
    "kolkostruva": {"fn": kolkostruva, "pause": 5, "max_age": 30, "label": "Колко струва (КЗП), дневните цени"},
    "urls": {"fn": urls, "pause": 2, "max_age": 30, "label": "ЕСО товар, НИГГГ земетресения, ЕКАТТЕ, GTFS София, НС"},
    # ЕРИК answers 403 for a report that does not exist (a participant asked with the wrong type); its firewall refuses
    # the connection instead, which stops the run
    "erik": {"fn": erik, "pause": 6, "max_age": 30, "label": "ЕРИК (Сметна палата), отчетите по изборите",
             "absent": (403, 404, 410)},
    "dfz": {"fn": dfz, "pause": 5, "max_age": 8 * 24, "label": "ДФЗ, изплатените субсидии по финансови години", "cookies": True},
    "egov": {"fn": egov, "pause": 8, "max_age": 30, "label": "data.egov.bg, наборите от плановете"},
}

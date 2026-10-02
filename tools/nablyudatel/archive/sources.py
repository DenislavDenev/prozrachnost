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
    # 02.10.2026, for Престъпност и пожари (plan 17): the police statistics 2016-2023, the monthly bulletin, the road accidents
    # aggregates of the Ministry of the Interior (the uris are read from listDatasets of organisation 113)
    "32cdf912-38b5-4e58-bfc0-70df0b02a36b": "МВР: месечен бюлетин за престъпността",
    "fb9e6cdf-0cf1-4f7b-ad6b-c6b073363186": "МВР: ПТП по области за страната за 2019",
    "b9af33c5-c357-4bc1-a8c9-a74e2182c60d": "МВР: ПТП по области за страната от 01.01.2020 г. до 30.06.2020 г.",
    "ebdc8fd7-5606-40a9-8092-451506542e53": "МВР: ПТП по области за страната от 01.01.2021 г. до 30.06.2021 г.",
    "a24fc292-a47b-49c3-9981-4737aa8a3306": "МВР: ПТП по области за страната през 2020 г.",
    "28dd128d-667c-44e6-bb5a-c9be1334941d": "МВР: полицейска статистика 2016",
    "88669f04-0bb3-4d6a-be84-4da97d77b084": "МВР: полицейска статистика 2017",
    "5a852fa8-3652-4a3c-9124-416cd40438af": "МВР: полицейска статистика 2018",
    "9c037c22-aeaa-4e4a-8be2-4eda7fa4b4f6": "МВР: полицейска статистика 2019",
    "fcb6aff7-f948-402d-8005-90091f80f02f": "МВР: полицейска статистика 2019 (втори набор)",
    "230a6a9c-f7b7-456f-b8db-ba67d66a6b91": "МВР: полицейска статистика 2020",
    "5966f613-f79b-4e34-a558-9511ddd1fb7e": "МВР: полицейска статистика 2021",
    "300807c7-946f-4f4b-8623-a06fc8f1a8b2": "МВР: полицейска статистика 2022",
    "b03e8542-0576-4770-8b44-977c0015589a": "МВР: полицейска статистика 2023",
    "a20de948-11c5-475a-8b77-232defe69e44": "МВР: по възрастови групи и вид на участник в ПТП - 2021 г.",
    "ab220464-dc61-48fe-a7d3-7179eadafbb7": "МВР: по възрастови групи и вид на участник в ПТП - 2022",
    "e9660b84-76a6-4158-9bc9-7a6e3190c99a": "МВР: по възрастови групи и вид на участник в ПТП за 2019 г.",
    "0c5fbe93-0aa8-4d03-8eb8-cde767c6daa6": "МВР: по възрастови групи и вид на участник в ПТП за периода от 01.01.2019 г. до 30.06",
    "6bc82030-6f7d-47df-9e3c-e1332bd58f97": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2020 г. до 30.06.2020 г.",
    "e2df330d-4ccc-4d36-b69b-86e1318fe017": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2021 г. до 30.06.2021 г.",
    "4b065549-812c-4507-84a3-66325bcbe688": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2022 г. до 30.06.2022 г.",
    "3d78a9c6-91c1-4fa3-9bcf-77df6f55a0c6": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2023 г. до 30.06.2023 г.",
    "97127329-b1de-4334-a5da-991888814aa0": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2024 г. до 30.06.2024 г.",
    "0a7246db-cb6f-4928-ae58-552e4ff48d24": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2025 г. до 30.06.2025 г.",
    "3298db5b-66d4-4780-b7f6-3e2042d3adb8": "МВР: по възрастови групи и вид на участник в ПТП от 01.01.2026 г. до 30.06.2026 г.",
    "c6d9d977-46f4-42a4-9c10-0c2ed24685d4": "МВР: по възрастови групи и вид на участник в ПТП през 2023 г.",
    "361f7167-9a37-4d90-a7e6-6f343aafa323": "МВР: по възрастови групи и вид на участник в ПТП през 2024 г.",
    "b8ac2f82-2a2d-4e88-85f9-bfd9263c92eb": "МВР: по възрастови групи и вид на участник в ПТП през 2025 г.",
    "c0cb3302-7c7b-400f-8673-34803bf00a67": "МВР: по възрастови групи и вид на участник в тежки ПТП през 2020 г.",
    "252b13fd-ca99-4e34-84c0-ab78b234d14f": "МВР: тежки ПТП (с пострадали) по области и месеци - 2021 г.",
    "a3ac85b8-2a64-44d6-acb5-2ae0492f1ac0": "МВР: тежки ПТП (с пострадали) по области и месеци - 2022 г.",
    "e8b7a643-8073-4ff9-8dbe-8ceec5173816": "МВР: тежки ПТП (с пострадали) по области и месеци за 2019 г.",
    "df0128e5-2418-4eff-af0b-358723ee540a": "МВР: тежки ПТП (с пострадали) по области и месеци за периода от 01.01.2019 г. до 30.0",
    "6ca4342b-2dae-40d8-b700-1a81667e42f9": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2020 г. до 30.06.2020 г.",
    "cdbd0465-0733-4bed-a11e-0a19e26fb17f": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2021 г. до 30.06.2021 г.",
    "b79093f6-6cd0-45c4-9da5-529343c355de": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2022 г. до 30.06.2022 г.",
    "0a1fa6f1-272e-422d-9868-14a2c68a6f91": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2023 г. до 30.06.2023 г.",
    "dab764b3-a6e8-4d73-bc75-e7d10229597b": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2024 г. до 30.06.2024 г.",
    "259d6bc2-2ffb-4a02-ab3e-d7594f190b08": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2025 г. до 30.06.2025 г.",
    "8c38afe4-af4e-4845-b702-0b0c41f02e8a": "МВР: тежки ПТП (с пострадали) по области и месеци от 01.01.2026 г. до 30.06.2026 г.",
    "133bb4ec-6c80-46ac-abe3-b0db6c985927": "МВР: тежки ПТП (с пострадали) по области и месеци през 2020 г.",
    "0eb6c65c-920c-4989-87a8-a291da948e4b": "МВР: тежки ПТП (с пострадали) по области и месеци през 2023 г.",
    "128b4967-0c0e-42cb-a145-304d014040fb": "МВР: тежки ПТП (с пострадали) по области и месеци през 2024 г.",
    "20b3fcb2-542b-4770-9266-9626145fb690": "МВР: тежки ПТП (с пострадали) по области и месеци през 2025 г.",
    "9839a450-6b5f-44ac-afa7-201463a22e26": "МВР: тежки ПТП, по вид на пътя - 2021 г.",
    "f80f8d2e-cfe6-403c-bf47-da7f6251c0a8": "МВР: Статистически данни за пътно-транспортни произшествия",
    "6fe0e163-d6d6-4d5f-80b5-6bbe2ddf700c": "МВР: Статистически данни за пътнотранспортни произшествия за 2018 г.",
    "164dacfc-a587-4adb-8ceb-6ed2d3c38054": "МВР: Тежки ПТП по области за периода от 01.01.2019 г. до 30.06.2019 г. в сравнение с ",
    "33d2d4cf-0968-4334-814c-0ba688be0745": "МВР: Тежки ПТП по области за страната - 2021 г.",
    "247c0344-cc54-4b48-8f0d-fe842375b1a5": "МВР: Тежки ПТП по области за страната - 2022",
    "c13a85a9-2650-406b-b0e2-da059484e2cb": "МВР: Тежки ПТП по области за страната от 01.01.2022 г. до 30.06.2022 г.",
    "813a5a99-aee2-4461-bea4-cd555309e8e8": "МВР: Тежки ПТП по области за страната от 01.01.2024 г. до 30.06.2024 г.",
    "9d14f234-3a17-459c-a178-7b6e16f64baf": "МВР: Тежки ПТП по области за страната от 01.01.2025 г. до 30.06.2025 г.",
    "da9e770a-c06e-4152-b7e0-ebb73aa95acb": "МВР: Тежки ПТП по области за страната от 01.01.2026 г. до 30.06.2026 г.",
    "89de20e2-22d3-411b-a504-54c42b0907db": "МВР: Тежки ПТП по области за страната през 2023 г.",
    "c6e5b051-bd0f-4272-9183-9d86327a4772": "МВР: Тежки ПТП по области за страната през 2024 г.",
    "28f0355f-b409-4642-9a18-20790ff4338e": "МВР: Тежки ПТП по области за страната през 2025 г.",
    "26193d53-c34e-44a1-a83d-28b007f1f028": "МВР: Тежки ПТП по области, часови интервали и дни от седмицата за страната от 01.01.2",
    "8a01fcee-2878-4987-bb15-f11eb43ecbc0": "МВР: Тежки ПТП по часови интервали и дни от седмицата - 2021 г.",
    "1d06c869-a940-4f4f-88be-2eea2876562c": "МВР: Тежки ПТП по часови интервали и дни от седмицата 2022 г.",
    "f7859b08-13e3-45cf-83a8-d86c42403b86": "МВР: Тежки ПТП по часови интервали и дни от седмицата за 2019 г.",
    "4c18ac86-fb54-4bc5-a602-cc024c0f4198": "МВР: Тежки ПТП по часови интервали и дни от седмицата за периода от 01.01.2019 г. до ",
    "988234db-219b-4c2f-a975-f10b2cf81b7f": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2020 г. до 30.06.2020 ",
    "5e865323-fb80-49a7-aa90-1afa2f49c127": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2021 г. до 30.06.2021 ",
    "e4b1f3b9-5a96-4d91-8933-8268ad5e96be": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2022 г. до 30.06.2022 ",
    "c94c5179-d64b-4afc-89f1-35c56975631f": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2024 г. до 30.06.2024 ",
    "c1c7bc7e-0829-468b-a628-eed07735ab74": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2025 г. до 30.06.2025 ",
    "41fcbe26-7149-4820-8d3d-664b46b73585": "МВР: Тежки ПТП по часови интервали и дни от седмицата от 01.01.2026 г. до 30.06.2026 ",
    "823017b7-08df-4a36-8aff-0d19efd6433f": "МВР: Тежки ПТП по часови интервали и дни от седмицата през 2020 г.",
    "8a4ea11e-a453-440b-a023-54c43007e750": "МВР: Тежки ПТП по часови интервали и дни от седмицата през 2023 г.",
    "2747695a-bacd-4996-918f-cae824f01664": "МВР: Тежки ПТП по часови интервали и дни от седмицата през 2024 г.",
    "9620bc7c-8728-46e6-b6f0-714c7b2f76ae": "МВР: Тежки ПТП по часови интервали и дни от седмицата през 2025 г.",
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

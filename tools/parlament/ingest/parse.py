"""The answers of parliament.bg, parsed and checked. Nothing here touches the database.

- archive-period/bg/Pl_StenV/<y>/<m>/0/0: the sittings of a month, [{t_id, t_date}] (from 1879)
- pl-sten/<id>: one sitting: its date, its heading (which names the National Assembly from 2009), its files, the
  text of the stenogram (from 1992; before, a scanned PDF) and the video (from 2010)
- the files of a sitting: `..._gv<ddmmyy>`, the result of every registration and vote by parliamentary group, and
  `..._iv<ddmmyy>`, every MP's registration and vote (roll call): CSV from 12.2021, XLS from 07.2009, XLSX in
  2020-2021; a sheet is read as the ";" CSV it was exported from (sheet())
- the stenogram's speeches (speeches()), the assemblies and their MPs (fn-assembly, archive, fn-mps, mp-profile), the
  official absences and penalties (mp-absense, mp-penalty)

A wrong API path answers 200 with the site's HTML page: everything that is not the JSON or CSV expected is a
ShapeError, which stops the sitting without writing.
"""
import csv
import datetime as dt
import html
import io
import json
import re


class ShapeError(ValueError):
    """The answer is not what the parser knows: nothing of it is written."""


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ShapeError(f"not JSON: {raw[:80]!r}") from None


def _date(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        raise ShapeError(f"not a date: {s!r}") from None


def sittings(raw):
    """-> [(sitting id, date)] of one month."""
    got = _json(raw)
    if not isinstance(got, list):
        raise ShapeError(f"not a list of sittings: {str(got)[:80]}")
    out = []
    for s in got:
        if not isinstance(s, dict) or not isinstance(s.get("t_id"), int):
            raise ShapeError(f"not a sitting: {str(s)[:80]}")
        out.append((s["t_id"], _date(s.get("t_date"))))
    return out


# the heading names the assembly in words: "ПЕТДЕСЕТ И ВТОРО НАРОДНО СЪБРАНИЕ, ..."
TENS = {"ТРИДЕСЕТ": 30, "ЧЕТИРИДЕСЕТ": 40, "ПЕТДЕСЕТ": 50, "ШЕСТДЕСЕТ": 60}
TENTH = {"ТРИДЕСЕТО": 30, "ЧЕТИРИДЕСЕТО": 40, "ПЕТДЕСЕТО": 50, "ШЕСТДЕСЕТО": 60}
UNITS = {"ПЪРВО": 1, "ВТОРО": 2, "ТРЕТО": 3, "ЧЕТВЪРТО": 4, "ПЕТО": 5, "ШЕСТО": 6, "СЕДМО": 7, "ОСМО": 8, "ДЕВЕТО": 9}
ASSEMBLY = re.compile(r"([А-ЯЪ]+)(?:\s+И\s+([А-ЯЪ]+))?\s+НАРОДНО\s+СЪБРАНИЕ")


# the site writes some Cyrillic letters with their Latin twins ("Чeтиридесет и шесто", with a Latin e)
LATIN = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")


def assembly_no(heading):
    """"ПЕТДЕСЕТ И ВТОРО НАРОДНО СЪБРАНИЕ, ..." -> 52; None when the heading does not name it."""
    m = ASSEMBLY.search((heading or "").upper().translate(LATIN))
    if not m:
        return None
    a, b = m.groups()
    if b:
        return TENS[a] + UNITS[b] if a in TENS and b in UNITS else None
    return TENTH.get(a)


def sitting(raw):
    """-> {id, date, heading, assembly, files, pdf, body, video}: files are the paths of the sitting's vote files of
    the two kinds, CSV, XLS or XLSX, by their name ("..._gv<ddmmyy>..." or "..._iv<ddmmyy>..."). Which is which is
    decided by the content (kind()): one of 04.2023 has the same file twice, one of 12.2022 a roll call under the
    name of the file by group. pdf: the scanned stenogram (before 1992); body: the stenogram's text as published
    (HTML lines); video: the URLs of the recording's parts, in order."""
    s = _json(raw)
    if not isinstance(s, dict) or not {"Pl_Sten_id", "Pl_Sten_date", "Pl_Sten_sub"} <= set(s):
        raise ShapeError(f"not a sitting: {str(s)[:120]}")
    files = s.get("files") or []
    if not isinstance(files, list):
        raise ShapeError(f"files is not a list: {str(files)[:80]}")
    paths = [((f or {}).get("Pl_StenDfile") or "", (f or {}).get("Pl_StenDname") or "") for f in files]
    votes = [p for p, _ in paths if re.search(r"[gi]v\d{6}[^/]*\.csv$", p, re.I)]         or [p for p, _ in paths if re.search(r"[gi]v\d{6}[^/]*\.xlsx?$", p, re.I)]   # the sheets only where no CSV
    pdf = next((p for p, n in paths if p.lower().endswith(".pdf") and n.startswith("Текст на стенограма")), None)
    v = s.get("video")
    video = [x["file"] for x in sorted(v.get("playlist") or [], key=lambda x: x.get("item") or 0) if x.get("file")] \
        if isinstance(v, dict) else []
    return {"id": int(s["Pl_Sten_id"]), "date": _date(s["Pl_Sten_date"]), "heading": s["Pl_Sten_sub"] or "",
            "assembly": assembly_no(s["Pl_Sten_sub"]), "files": votes, "pdf": pdf,
            "body": s.get("Pl_Sten_body") or "", "video": video}


def sheet(raw):
    """An XLS or XLSX vote file -> the same file as the ";" CSV the Assembly's system exports (numbers as integers),
    so groups() and rollcall() read it; CSV and anything else is returned as it is."""
    try:
        if raw[:4] == b"\xd0\xcf\x11\xe0":
            import xlrd
            sh = xlrd.open_workbook(file_contents=raw, logfile=io.StringIO()).sheet_by_index(0)
            rows = [sh.row_values(r) for r in range(sh.nrows)]
        elif raw[:2] == b"PK":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        else:
            return raw
    except Exception as e:  # noqa: BLE001 - a damaged sheet is a file we cannot read, said as such
        raise ShapeError(f"повреден файл: {e}"[:200]) from None
    cell = lambda v: "" if v is None else str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()  # noqa: E731
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    for r in rows:
        w.writerow([cell(v) for v in r])
    return buf.getvalue().encode("utf-8")


def file_date(path):
    """"..._iv011223.csv" -> date(2023, 12, 1): the day the name says, or None."""
    m = re.search(r"[gi]v(\d\d)(\d\d)(\d\d)", path.rsplit("/", 1)[-1], re.I)
    try:
        return dt.date(2000 + int(m[3]), int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None


def kind(raw):
    """"gv" for a file by group, "iv" for a roll call, None for anything else (read from the content, not the name)."""
    text = _text(raw)          # a damaged file says so (ShapeError)
    if text.startswith(("NAME,", "Регистрации и гласувания от:")):
        return "iv"
    if text.startswith("textbox3") or re.search(r"^Номер \(\d+", text, re.M) and re.search(r"^ПГ;", text, re.M):
        return "gv"
    return None


MARKERS = ("Номер (", "NAME,", "Регистрации и гласувания")


def _text(raw):
    """UTF-8; some files of early 2022 are in windows-1251, two in Mac Cyrillic: the code page is the one in which
    the file says what it is (a wrong guess could not pass the check against the other file anyway)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if raw.startswith(b"\xef\xbb\xbf"):     # marked as UTF-8 but not: damaged at the source (18.03.2026)
            raise ShapeError("повреден файл: започва като UTF-8, но не е")
        for enc in ("cp1251", "mac_cyrillic"):
            text = raw.decode(enc, errors="replace")
            if any(m in text[:3000] for m in MARKERS):
                break
        else:
            raise ShapeError("a file in an unknown code page")
    if text.lstrip()[:1] == "<":
        raise ShapeError("an HTML page instead of CSV")
    return text.replace("\r\n", "\n").replace("\r", "\n")   # the Mac Cyrillic files end their lines with CR


def _int(s, what):
    s = (s or "").strip()
    if not s.isdigit():
        raise ShapeError(f"{what}: not a count: {s!r}")
    return int(s)


# "Номер (2) ГЛАСУВАНЕ проведено на 24-09-2026 10:12 по тема ЗИД на Наказателния кодекс – първо гласуване "
# (spaces as typed: "Номер (5 )ГЛАСУВАНЕ" happens)
ITEM = re.compile(r"^Номер \((\d+)\s*\)\s*(РЕГИСТРАЦИЯ|ГЛАСУВАНЕ)\s+проведен[ао]\s+на\s+(\d\d)-(\d\d)-(\d{4})\s+(\d\d):(\d\d)(?:\s+по тема\s*(.*))?$", re.S)
GV_HEAD = {"textbox3", "textbox11", "textbox12", "textbox13", "textbox18", "NAME", "START_DATE", "ALL_DEPUTIES", "REGISTERED", "textbox22"}


def groups(raw):
    """The file by group -> {item: {kind, at, topic, total, groups}}. A registration counts (present, listed), a vote
    (yes, no, abstain, voted); `total` is the row of the whole assembly, `groups` {group: counts}, None for a group
    whose row has empty cells (a new group, 03.2025). Two layouts: a table with a row per item and group (textbox3 ...),
    or a block per item (the online sittings of early 2022, some of 12.2024-04.2026), its columns named in the row "ПГ"."""
    text = _text(raw)
    items = {}
    if text.startswith("textbox3"):
        head, *cells = list(csv.reader(io.StringIO(text)))
        if not GV_HEAD <= set(head):
            raise ShapeError(f"unexpected header of the file by group: {head}")
        rows = []
        for c in cells:
            if not any(x.strip() for x in c):
                continue
            extra = len(c) - len(head)   # a topic with commas and without quotes spreads over more cells
            if extra < 0:
                raise ShapeError(f"a short row: {c[:3]}")
            rows.append(dict(zip(head, [",".join(c[:extra + 1])] + c[extra + 1:] if extra > 0 else c)))
        if not rows:
            raise ShapeError("the file by group is empty")
        for r in rows:
            m = ITEM.match(r["textbox3"].strip())
            vote = not m or m.group(2) == "ГЛАСУВАНЕ"
            keys = ("textbox11", "textbox12", "textbox13", "textbox18") if vote else ("textbox11", "textbox12")
            own = ("START_DATE", "ALL_DEPUTIES", "REGISTERED", "textbox22") if vote else ("START_DATE", "ALL_DEPUTIES")
            _add(items, r["textbox3"], [r[k] for k in keys], r["NAME"], [r[k] for k in own])
    elif re.search(r"^Номер \(\d+", text, re.M) and re.search(r"^ПГ;", text, re.M):
        title = cols = None
        for r in csv.reader(io.StringIO(text), delimiter=";"):
            c0 = (r[0] if r else "").strip()
            if ITEM.match(c0):
                title, total, cols = ";".join(r).rstrip("; "), None, None   # a ";" in the topic splits the cell
                names = BLOCK_VOTE if ITEM.match(c0).group(2) == "ГЛАСУВАНЕ" else BLOCK_REG
            elif not c0 or c0.startswith("Народни представители"):
                title = None                      # the block ends; the MPs who took part online follow the last one
            elif title and c0 == "ПГ":
                head = [x.strip() for x in r]
                if not set(names) <= set(head):
                    raise ShapeError(f"unexpected columns of a block: {head}")
                cols = [head.index(x) for x in names]
            elif title and c0 == "Общо:":
                if cols is None:
                    raise ShapeError(f"a total before the columns: {r}")
                total = [r[i] if i < len(r) else "" for i in cols]
            elif title:
                if total is None:
                    raise ShapeError(f"a group before the total: {r}")
                _add(items, title, total, c0, [r[i] if i < len(r) else "" for i in cols])
    else:
        raise ShapeError(f"unexpected start of the file by group: {text[:80]!r}")
    if not items:
        raise ShapeError("the file by group has no item")
    return items


BLOCK_REG = ("Присъстват", "По списък")
BLOCK_VOTE = ("За", "Против", "Въздържали се", "Гласували")


def _add(items, title, total, group, own):
    m = ITEM.match(title.strip())
    if not m:
        raise ShapeError(f"not an item: {title[:120]!r}")
    no, kind, d, mo, y, h, mi, topic = m.groups()
    kind = "registration" if kind == "РЕГИСТРАЦИЯ" else "vote"
    n = 2 if kind == "registration" else 4
    total = tuple(_int(x, "total") for x in total[:n])
    own = None if any(not (x or "").strip() for x in own[:n]) else tuple(_int(x, group) for x in own[:n])
    if len(total) != n or own is not None and len(own) != n:
        raise ShapeError(f"item {no}: short row")
    it = items.setdefault(int(no), {"kind": kind, "at": dt.datetime(int(y), int(mo), int(d), int(h), int(mi)),
                                    "topic": " ".join((topic or "").split()) or None, "total": total, "groups": {}})
    if it["total"] != total or it["kind"] != kind:
        raise ShapeError(f"item {no}: two different totals")
    g = group_code(group)
    if g in it["groups"]:
        raise ShapeError(f"item {no}: group {g} twice")
    it["groups"][g] = own


def group_code(s):
    """The group as one code in both files: "ГЕРБ - СДС" and "ГЕРБ-СДС", "ДЕМОКРАЦИЯ, ПРАВА" and "ДЕМОКРАЦИЯ,ПРАВА" are
    one group (the two files of 10.12.2024 write it differently)."""
    return re.sub(r"\s*([,-])\s*", r"\1", " ".join((s or "").split()))


VOTES = {"+": "yes", "-": "no", "=": "abstain", "0": "none"}
UNALIGNED = "?"   # a code of a row with a column per item whose codes cannot be matched to the items (split_shifted)
MARKS = {"П", "О", "Р", "Д"}   # a registration: П is counted as present (the file by group proves it); О and Р are not
# "онлайн" in the roll calls of the online sittings of 01-03.2022: registered from afar, kept as Д. Some files by group
# count them as present (27.01.2022: 188 П + 7 онлайн = 195), some do not (21.01.2022: 181 П = 181): check() takes the
# one of the two that the whole assembly's count says
REMOTE = {"онлайн": "Д"}


def rollcall(raw):
    """The roll call -> [(mp_no, name, group, item, code)]. Two layouts: one row per MP and item (NAME, textbox7 ...),
    or one row per MP with a column per item (from "Регистрации и гласувания от:")."""
    text = _text(raw)
    if text.startswith("Регистрации и гласувания от:"):
        return _wide(text)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not {"NAME", "textbox7", "textbox8", "ITEM", "textbox2"} <= set(rows[0]):
        raise ShapeError(f"unexpected header of the roll call: {list(rows[0]) if rows else 'empty'}")
    # an empty code: the MP was not on the hall's list for that item (sworn in or gone that day); the file by group
    # does not count them either, which the check proves
    return [_one(r["textbox7"], r["NAME"], r["textbox8"], r["ITEM"], r["textbox2"]) for r in rows if (r["textbox2"] or "").strip()]


def _wide(text):
    """One row per MP: name, empty, number, group, [assembly], then a column per item. The item numbers are the
    header's non-empty cells, the codes the row's non-empty cells from the header's first number on (an empty
    separator column is not always in the same place in the header and in the rows)."""
    lines = list(csv.reader(io.StringIO(text), delimiter=";"))
    head = lines[1]
    nums = [c.strip() for c in head if c.strip()]
    if not nums or any(not c.isdigit() for c in nums):
        raise ShapeError(f"unexpected header of the roll call: {head[:12]}")
    start = next(i for i, c in enumerate(head) if c.strip())
    out = []
    for r in lines[2:]:
        if not any(x.strip() for x in r):
            continue
        codes = [x.strip() for x in r[start:] if x.strip()]
        if not codes:
            continue   # an MP listed without a single code: not in the hall's list that day (the file by group agrees)
        if len(codes) == len(nums):
            out += [_one(r[2], r[0], r[3], c, v) for c, v in zip(nums, codes)]
        else:   # some items without a code: which code is for which item is not known, the MP is set aside
            first = _one(r[2], r[0], r[3], nums[0], codes[0])
            out += [(*first[:3], int(c), UNALIGNED) for c in nums]
    return out


def _one(no, name, group, item, code):
    code = (code or "").strip()
    code = REMOTE.get(code.lower(), code)
    if code not in VOTES and code not in MARKS:
        raise ShapeError(f"unknown code {code!r} for {name}, item {item}")
    return (_int(no, "MP"), " ".join(name.split()), group_code(group), _int(item, "item"), code)


def roster(raw):
    """The current assembly's list (coll-list-ns/bg) -> {assembly, mps: [{profile, name, group, district, since}]}."""
    d = _json(raw)
    if not isinstance(d, dict) or not isinstance(d.get("colListMP"), list) or not d["colListMP"]:
        raise ShapeError(f"not the list of MPs: {str(d)[:120]}")
    m = re.match(r"\s*(\d+)", d.get("A_ns_CL_value") or "")
    if not m:
        raise ShapeError(f"no assembly number: {d.get('A_ns_CL_value')!r}")
    mps = []
    for x in d["colListMP"]:
        if not isinstance(x.get("A_ns_MP_id"), int) or not x.get("A_ns_MPL_Name1"):
            raise ShapeError(f"not an MP: {str(x)[:120]}")
        name = " ".join(" ".join(x.get(f"A_ns_MPL_Name{i}") or "" for i in (1, 2, 3)).split()).upper()
        mps.append({"profile": x["A_ns_MP_id"], "name": name, "group": (x.get("A_ns_CL_value") or "").strip() or None,
                    "district": (x.get("A_ns_Va_name") or "").strip() or None,
                    "since": _date(x["A_ns_MSP_date_F"]) if x.get("A_ns_MSP_date_F") else None})
    if len({m["profile"] for m in mps}) != len(mps):
        raise ShapeError("an MP twice in the list")
    return {"assembly": int(m.group(1)), "mps": mps}


def split_shifted(items, votes):
    """-> (votes, set aside {mp: (name, group)}). Some roll calls have one MP's codes out of place (04.2024-02.2026:
    a vote in the column of a registration, codes missing after it): no code of that MP can be trusted that day, so
    all of them are set aside and named on the sitting's page; the check allows for them."""
    reg = {n for n, it in items.items() if it["kind"] == "registration"}
    bad = {no: (name, g) for no, name, g, item, code in votes
           if code == UNALIGNED or item in reg and code in VOTES or item in items and item not in reg and code in MARKS}
    return [v for v in votes if v[0] not in bad], bad


# The two files are made by the Assembly from the same voting system, but now and then (a vote corrected in the hall,
# checked 03.2022) they differ by a vote or two in one group. Such an item is shown with the difference said on its
# page; anything larger, or in too many items, means the files or our parser changed, and the sitting is not written.
# When the groups differ but the whole assembly adds up in every item (a group formed that week counted apart in one
# file only, 03.2025), the item is kept with that said.
SMALL = 2
SMALL_SHARE = 1 / 3


def check(items, votes, aside=None):
    """-> (problems, notes). problems: the sitting must not be written; notes {item: text}, said on the item's page.

    Per item and group, the roll call counted by group against the file by group: equal, or at most SMALL apart (a
    note). At a registration only the present count is compared. A group whose row in the file by group is empty is
    a note. Where the groups do not agree but the roll call adds up to the total of the whole assembly, the item is
    kept with a note. `aside` {mp: group}: MPs set aside (split_shifted): the file by group still counts them, so each
    count may be lower in the roll call by up to their number. Every row of the roll call belongs to one item of the
    file by group and every item has its roll call."""
    from collections import Counter
    aside = aside or {}
    allow, allow_all = Counter(aside.values()), len(aside)
    bad, notes, small = [], {}, 0
    counted = {}
    for no, _, g, item, code in votes:
        counted.setdefault(item, {}).setdefault(g, []).append(code)
    if set(counted) != set(items):
        bad.append(f"точките се различават: само в поименното {sorted(set(counted) - set(items))[:5]}, "
                   f"само в гласуването по групи {sorted(set(items) - set(counted))[:5]}")
    for item in sorted(set(items) & set(counted)):
        it, by = items[item], counted[item]
        reg = it["kind"] == "registration"
        if reg:
            remote = sum(c.count("П") + c.count("Д") for c in by.values()) == it["total"][0] and any("Д" in c for c in by.values())
            got = {g: (c.count("П") + (c.count("Д") if remote else 0), len(c)) for g, c in by.items()}
            wrong = [c for cs in by.values() for c in cs if c not in MARKS]
        else:
            got = {g: (c.count("+"), c.count("-"), c.count("="), sum(x in "+-=" for x in c)) for g, c in by.items()}
            wrong = [c for cs in by.values() for c in cs if c not in VOTES]
        if wrong:
            bad.append(f"точка {item}: кодове {sorted(set(wrong))} не са за {it['kind']}")
            continue
        keys = 1 if reg else 4                     # what must agree: the present, or the four counts of a vote

        def off(mine, theirs, slack):
            """How far the roll call is from the file by group beyond what the MPs set aside explain."""
            return max([0] + [max(0, (b - a) - slack) if b >= a else a - b for a, b in zip(mine[:keys], theirs[:keys])])

        roll = tuple(map(sum, zip(*got.values())))
        adds_up = off(roll, it["total"], allow_all) == 0
        broken = sorted(g for g, v in it["groups"].items() if v is None)
        theirs = {g: v for g, v in it["groups"].items() if v is not None and (any(v) or g in got)}
        mine = {g: v for g, v in got.items() if g not in broken}
        said = [f"по групи редът на {', '.join(broken)} е празен"] if broken else []
        lone = sorted(set(mine) ^ set(theirs))
        size = max([0] + [off(mine[g], theirs[g], allow[g]) for g in set(mine) & set(theirs)])
        if not broken:
            size = max(size, off(tuple(map(sum, zip(*theirs.values()))), it["total"], 0))
        diff = sorted(g for g in set(mine) & set(theirs) if off(mine[g], theirs[g], allow[g]))
        if lone or size > SMALL:
            if not adds_up:
                bad.append(f"точка {item}: " + (f"групи само в единия файл: {lone}" if lone else
                           f"поименно {', '.join(f'{g} {mine[g]}' for g in diff)}; по групи {', '.join(f'{g} {theirs[g]}' for g in diff)}"))
                continue
            said.append("групите в двата файла на Народното събрание се различават; общият резултат съвпада")
        elif diff:
            small += 1
            said.insert(0, f"поименно {', '.join(f'{g} {mine[g]}' for g in diff)}; по групи {', '.join(f'{g} {theirs[g]}' for g in diff)}")
        elif broken and not adds_up:
            text = f"поименното {roll[:keys]}, общото {it['total'][:keys]}"
            if off(roll, it["total"], allow_all) > SMALL:
                bad.append(f"точка {item}: празен ред по групи за {', '.join(broken)} и {text}")
                continue
            small += 1
            said.append(text)
        if said:
            notes[item] = "; ".join(said)
    if small > max(1, SMALL_SHARE * len(items)):
        bad.append(f"разминавания в {small} от {len(items)} точки")
    seen = set()
    for no, _, _, item, _ in votes:
        if (no, item) in seen:
            bad.append(f"депутат {no} два пъти в точка {item}")
            break
        seen.add((no, item))
    return bad, notes


# ---------- the stenogram ----------

# "ПРЕДСЕДАТЕЛ МИХАЕЛА ДОЦОВА: ...", "АТАНАС СЛАВОВ (ДБ): ...", "ХРИСТО БИСЕРОВ (от място): ...", "РЕПЛИКА ОТ ДБ: ...":
# the same from 1992 to now. A role (the longest first) comes before the name; the name is two or three words.
SPEAKER = re.compile(r"^(?P<who>[А-ЯЁЪЬЮЯІ][А-ЯЁЪЬЮЯІ\-\.' ]{2,120}?)\s*(?:\((?P<note>[^()]{0,200})\))?\s*:\s*(?P<rest>.*)$", re.S)
ROLES = sorted(("ПРЕДСЕДАТЕЛ", "ПРЕДСЕДАТЕЛЯТ", "ЗАМЕСТНИК-ПРЕДСЕДАТЕЛ", "ЗАМЕСТНИК ПРЕДСЕДАТЕЛ", "ДОКЛАДЧИК", "СЕКРЕТАР",
                "МИНИСТЪР-ПРЕДСЕДАТЕЛ", "МИНИСТЪР", "ЗАМЕСТНИК МИНИСТЪР-ПРЕДСЕДАТЕЛ", "ЗАМЕСТНИК-МИНИСТЪР-ПРЕДСЕДАТЕЛ",
                "ЗАМЕСТНИК-МИНИСТЪР", "ЗАМЕСТНИК МИНИСТЪР", "ПРЕЗИДЕНТ", "ВИЦЕПРЕЗИДЕНТ", "ГЛАВЕН ПРОКУРОР",
                "ОМБУДСМАН", "УПРАВИТЕЛ", "ПОДУПРАВИТЕЛ", "ГЛАВЕН СЕКРЕТАР", "ПОСЛАНИК"), key=len, reverse=True)
# the hall's voices that are no one's speech: "РЕПЛИКА ОТ ДБ", "ГЛАСОВЕ ОТ ГЕРБ", "ВИКОВЕ"
CHORUS = re.compile(r"^(РЕПЛИК[АИ]|ГЛАС(?:ОВЕ)?|ВИКОВЕ|ВЪЗГЛАСИ?|ОБАЖДАНИ[ЕЯ]|ШУМ)(?:\s+ОТ\s+(?P<from>.+))?$")
WORD = re.compile(r"^[А-ЯЁЪЬЮЯІ][А-ЯЁЪЬЮЯІ\-']*[А-ЯЁЪЬЮЯІ]$")
STAGE = {"звъни"}                            # "(Звъни)": what the chair does, not a group
UNPUBLISHED = re.compile(r"^\s*Чл\.\s*67")   # the notice shown until the stenogram is published (in 7 days)


def speaker(line):
    """A line that opens a speech -> {role, name, note, grp, rest}, else None."""
    m = SPEAKER.match(line)
    if not m:
        return None
    who = " ".join(m["who"].split())
    note = " ".join((m["note"] or "").split()) or None
    c = CHORUS.match(who)
    if c:
        return {"role": "реплика", "name": None, "note": note, "grp": c["from"], "rest": m["rest"]}
    role = next((r for r in ROLES if who == r or who.startswith(r + " ")), None)
    name = who[len(role):].strip() if role else who
    words = name.split()
    if not 2 <= len(words) <= 3 or not all(WORD.match(w) for w in words):
        return None
    grp = None
    if note:
        first = note.split(",")[0].strip()
        if first and not first[0].islower() and first.lower() not in STAGE:   # "(ДБ, от място)" names the group
            grp = first
    return {"role": role.lower() if role else None, "name": name, "note": note, "grp": grp, "rest": m["rest"]}


def speeches(body):
    """The stenogram's text -> [{no, role, name, note, grp, text}] in order; no 0 is what comes before the first
    speech (who presided, the secretaries). [] while the stenogram is not published (the Assembly shows a notice)."""
    if not body or UNPUBLISHED.match(re.sub(r"<[^>]+>", "", body)):
        return []
    lines = [html.unescape(re.sub(r"<[^>]+>", "", x)).replace("\xa0", " ").strip() for x in re.split(r"<br\s*/?>|\n", body)]
    out, cur = [], {"no": 0, "role": None, "name": None, "note": None, "grp": None, "lines": []}
    for line in lines:
        sp = speaker(line) if line else None
        if sp:
            out.append(cur)
            rest = sp.pop("rest").strip()
            cur = {"no": len(out), **sp, "lines": [rest] if rest else []}
        else:
            cur["lines"].append(line)
    out.append(cur)
    res = []
    for sp in out:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(sp.pop("lines")).strip())
        if sp["no"] == 0 and not text:
            continue
        res.append({**sp, "text": text})
    if not any(sp["no"] for sp in res):
        raise ShapeError("стенограма без нито едно изказване")
    return res


# ---------- the assemblies and their people ----------

def assemblies(raw):
    """fn-assembly/bg -> [(API id, assembly number)]: the assemblies the API knows (from the 39th, 2001)."""
    got = _json(raw)
    if not isinstance(got, list) or not got:
        raise ShapeError(f"not a list of assemblies: {str(got)[:120]}")
    out = []
    for a in got:
        n = assembly_no(a.get("A_nsL_value") if isinstance(a, dict) else None)
        if not isinstance(a.get("A_ns_id"), int) or n is None:
            raise ShapeError(f"not an assembly: {str(a)[:120]}")
        out.append((a["A_ns_id"], n))
    return out


BODY_KINDS = {"pg": "група", "cm": "комисия", "cm_v": "временна комисия", "cm_u": "подкомисия", "dl": "делегация",
              "fg": "група за приятелство", "pc": "друго"}


def archive(raw):
    """archive/bg/<API id> -> {start, end, bodies: [{id, kind, name, since, until, n_start, n_end, n_total}]}."""
    a = _json(raw)
    if not isinstance(a, dict) or "A_ns_start" not in a:
        raise ShapeError(f"not an assembly: {str(a)[:120]}")
    bodies = []
    for key, kind_ in BODY_KINDS.items():
        for b in a.get(key) or []:
            if not isinstance(b.get("A_ns_C_id"), int):
                raise ShapeError(f"not a body: {str(b)[:120]}")
            bodies.append({"id": b["A_ns_C_id"], "kind": kind_, "name": " ".join((b.get("A_ns_CL_value") or b.get("A_ns_C_name") or "").split()),
                           "since": _day(b.get("A_ns_C_date_F")), "until": _day(b.get("A_ns_C_date_T")),
                           "n_start": b.get("A_ns_C_start_count"), "n_end": b.get("A_ns_C_end_count"), "n_total": b.get("A_ns_C_total_count")})
    return {"start": _date(a["A_ns_start"]), "end": _day(a.get("A_ns_end")), "bodies": bodies}


def _day(s):
    """A date of the API, None for none (empty, "9999-12-31": until now, "0000-00-00")."""
    if not s or s.startswith(("9999", "0000", "0001")):
        return None
    return _date(s[:10])


def mps(raw):
    """fn-mps/bg/<API id> -> [profile id]: every MP of the assembly, the ones who left too."""
    got = _json(raw)
    if not isinstance(got, list) or not got or not all(isinstance(x.get("A_ns_MP_id"), int) for x in got):
        raise ShapeError(f"not a list of MPs: {str(got)[:120]}")
    return [x["A_ns_MP_id"] for x in got]


def full_name(x):
    return " ".join(" ".join(x.get(f"A_ns_MPL_Name{i}") or "" for i in (1, 2, 3)).split()).upper()


def profile(raw):
    """mp-profile/bg/<id> -> the MP in their public role: {id, api_assembly, name, district, list, profession,
    languages, past: [API ids of earlier assemblies], memberships: [...]}. The date and place of birth, e-mail,
    phones, links and photo are left out."""
    p = _json(raw)
    if not isinstance(p, dict) or not isinstance(p.get("A_ns_MP_id"), int) or not p.get("A_ns_MPL_Name1"):
        raise ShapeError(f"not a profile: {str(p)[:120]}")
    ms = []
    for m in p.get("mshipList") or []:
        if not isinstance(m.get("A_ns_MSP_id"), int) or not isinstance(m.get("A_ns_C_id"), int):
            raise ShapeError(f"not a membership: {str(m)[:120]}")
        ms.append({"id": m["A_ns_MSP_id"], "body": m["A_ns_C_id"], "body_name": " ".join((m.get("A_ns_CL_value") or "").split()),
                   "body_kind": m.get("A_ns_CT_id"), "role": (m.get("A_ns_MP_PosL_value") or "").strip() or None,
                   "since": _day(m.get("A_ns_MSP_date_F")), "until": _day(m.get("A_ns_MSP_date_T"))})
    words = lambda key, field: ", ".join(x[field] for x in p.get(key) or [] if x.get(field)) or None  # noqa: E731
    lang = next((k for k in (p.get("lngList") or [{}])[0] if k.endswith("L_value")), None) if p.get("lngList") else None
    return {"id": p["A_ns_MP_id"], "api_assembly": p.get("A_ns_id"), "name": full_name(p),
            "district": (p.get("A_ns_Va_name") or "").strip() or None, "list": (p.get("A_ns_CoalL_value") or "").strip() or None,
            "profession": words("prsList", "A_ns_MP_Pr_TL_value"), "languages": words("lngList", lang) if lang else None,
            "past": [x["A_ns_id"] for x in p.get("oldnsList") or [] if isinstance(x.get("A_ns_id"), int)], "memberships": ms}


def absences(raw):
    """POST mp-absense/bg -> [{id, date, profile, name, body, body_name, kind}]: kind 1 the plenary, 2 a committee.
    The Assembly shows only the last months: what we read is kept."""
    got = _json(raw)
    if not isinstance(got, list):
        raise ShapeError(f"not a list of absences: {str(got)[:120]}")
    out = []
    for a in got:
        if not isinstance(a.get("MP_Ab_id"), int) or not isinstance(a.get("A_ns_MP_id"), int):
            raise ShapeError(f"not an absence: {str(a)[:120]}")
        out.append({"id": a["MP_Ab_id"], "date": _date(a["MP_Ab_date"]), "profile": a["A_ns_MP_id"], "name": full_name(a),
                    "body": a.get("A_ns_C_id"), "body_name": (a.get("A_ns_CL_value") or "").strip() or None, "kind": a.get("MP_Ab_T_id")})
    return out


def penalties(raw):
    """POST mp-penalty -> [{id, date, profile, name, kind, note, by, what}]: the chair's penalties (a remark, a
    reprimand, removal from the sitting), who imposed it and for what."""
    got = _json(raw)
    if not isinstance(got, list):
        raise ShapeError(f"not a list of penalties: {str(got)[:120]}")
    out = []
    for a in got:
        if not isinstance(a.get("A_ns_MP_Pen_id"), int) or not isinstance(a.get("A_ns_MP_id"), int):
            raise ShapeError(f"not a penalty: {str(a)[:120]}")
        by = " ".join(" ".join(a.get(f"A_ns_MP_Chr_Name{i}") or "" for i in (1, 2, 3)).split()).upper() or None
        out.append({"id": a["A_ns_MP_Pen_id"], "date": _date(a["A_ns_MP_Pen_date"]), "profile": a["A_ns_MP_id"], "name": full_name(a),
                    "kind": (a.get("A_ns_MP_PenT_name") or "").strip(), "note": (a.get("A_ns_MP_Pen_note") or "").strip() or None,
                    "by": by, "what": ", ".join(x.get("A_ns_MP_PenS_name") or "" for x in a.get("activity") or []) or None})
    return out

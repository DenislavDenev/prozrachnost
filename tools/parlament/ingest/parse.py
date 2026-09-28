"""The answers of parliament.bg, parsed and checked. Nothing here touches the database.

- archive-period/bg/Pl_StenV/<y>/<m>/0/0: the sittings of a month, [{t_id, t_date}]
- pl-sten/<id>: one sitting: its date, its heading (which names the National Assembly) and its files
- the files of a sitting: `..._gv<ddmmyy>.csv`, the result of every registration and vote by parliamentary group, and
  `..._iv<ddmmyy>.csv`, every MP's registration and vote (roll call)

A wrong API path answers 200 with the site's HTML page: everything that is not the JSON or CSV expected is a
ShapeError, which stops the sitting without writing.
"""
import csv
import datetime as dt
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
TENS = {"ЧЕТИРИДЕСЕТ": 40, "ПЕТДЕСЕТ": 50, "ШЕСТДЕСЕТ": 60}
TENTH = {"ЧЕТИРИДЕСЕТО": 40, "ПЕТДЕСЕТО": 50, "ШЕСТДЕСЕТО": 60}
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
    """-> {id, date, heading, assembly, gv, iv}: gv and iv are the paths of the CSV files, or None."""
    s = _json(raw)
    if not isinstance(s, dict) or not {"Pl_Sten_id", "Pl_Sten_date", "Pl_Sten_sub"} <= set(s):
        raise ShapeError(f"not a sitting: {str(s)[:120]}")
    files = s.get("files") or []
    if not isinstance(files, list):
        raise ShapeError(f"files is not a list: {str(files)[:80]}")
    csvs = {}
    for f in files:
        path = (f or {}).get("Pl_StenDfile") or ""
        m = re.search(r"(gv|iv)\d{6}[^/]*\.csv$", path, re.I)
        if m:
            kind = m.group(1).lower()
            if kind in csvs:
                raise ShapeError(f"two {kind} files: {csvs[kind]}, {path}")
            csvs[kind] = path
    return {"id": int(s["Pl_Sten_id"]), "date": _date(s["Pl_Sten_date"]), "heading": s["Pl_Sten_sub"] or "",
            "assembly": assembly_no(s["Pl_Sten_sub"]), "gv": csvs.get("gv"), "iv": csvs.get("iv")}


MARKERS = ("Номер (", "NAME,", "Регистрации и гласувания")


def _text(raw):
    """UTF-8; some files of early 2022 are in windows-1251, two in Mac Cyrillic: the code page is the one in which
    the file says what it is (a wrong guess could not pass the check against the other file anyway)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
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
    (yes, no, abstain, voted); `total` is the row of the whole assembly, `groups` {group: counts}. Two layouts: a table
    with a row per item and group (textbox3 ...), or, in the online sittings of early 2022, a block per item."""
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
        title = None
        for r in csv.reader(io.StringIO(text), delimiter=";"):
            c0 = (r[0] if r else "").strip()
            if ITEM.match(c0):
                title, total = ";".join(r).rstrip("; "), None   # a ";" in the topic splits the cell
                n = 4 if ITEM.match(c0).group(2) == "ГЛАСУВАНЕ" else 2
            elif not c0 or c0.startswith("Народни представители"):
                title = None                      # the block ends; the MPs who took part online follow the last one
            elif title and c0 == "ПГ":
                continue
            elif title and c0 == "Общо:":
                total = r[1:1 + n]
            elif title:
                if total is None:
                    raise ShapeError(f"a group before the total: {r}")
                _add(items, title, total, c0, r[1:1 + n])
    else:
        raise ShapeError(f"unexpected start of the file by group: {text[:80]!r}")
    if not items:
        raise ShapeError("the file by group has no item")
    return items


def _add(items, title, total, group, own):
    m = ITEM.match(title.strip())
    if not m:
        raise ShapeError(f"not an item: {title[:120]!r}")
    no, kind, d, mo, y, h, mi, topic = m.groups()
    kind = "registration" if kind == "РЕГИСТРАЦИЯ" else "vote"
    n = 2 if kind == "registration" else 4
    total, own = tuple(_int(x, "total") for x in total[:n]), tuple(_int(x, group) for x in own[:n])
    if len(total) != n or len(own) != n:
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
    return " ".join((s or "").split())


VOTES = {"+": "yes", "-": "no", "=": "abstain", "0": "none"}
MARKS = {"П", "О", "Р"}   # a registration: П is counted as present (the file by group proves it); О and Р are not


def rollcall(raw):
    """The roll call -> [(mp_no, name, group, item, code)]. Two layouts: one row per MP and item (NAME, textbox7 ...),
    or one row per MP with a column per item (from "Регистрации и гласувания от:")."""
    text = _text(raw)
    if text.startswith("Регистрации и гласувания от:"):
        return _wide(text)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or not {"NAME", "textbox7", "textbox8", "ITEM", "textbox2"} <= set(rows[0]):
        raise ShapeError(f"unexpected header of the roll call: {list(rows[0]) if rows else 'empty'}")
    return [_one(r["textbox7"], r["NAME"], r["textbox8"], r["ITEM"], r["textbox2"]) for r in rows]


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
        if len(codes) != len(nums):
            raise ShapeError(f"{len(codes)} codes for {len(nums)} items: {r[:6]}")
        out += [_one(r[2], r[0], r[3], c, v) for c, v in zip(nums, codes)]
    return out


def _one(no, name, group, item, code):
    code = (code or "").strip()
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


# The two files are made by the Assembly from the same voting system, but now and then (a vote corrected in the hall,
# checked 03.2022) they differ by a vote or two in one group. Such an item is shown with the difference said on its
# page; anything larger, or in too many items, means the files or our parser changed, and the sitting is not written.
SMALL = 2
SMALL_SHARE = 1 / 3


def check(items, votes):
    """-> (problems, notes). problems: the sitting must not be written; notes {item: text}: the roll call, counted by
    group, differs from the file by group by at most SMALL votes (the file by group is the result, the roll call
    the MPs). Every row of the roll call belongs to one item of the file by group and every item has its roll call."""
    bad, notes = [], {}
    counted = {}
    for no, _, g, item, code in votes:
        counted.setdefault(item, {}).setdefault(g, []).append(code)
    if set(counted) != set(items):
        bad.append(f"пунктовете се различават: поименно {sorted(set(counted) - set(items))[:5]}, "
                   f"по групи {sorted(set(items) - set(counted))[:5]}")
    for item in sorted(set(items) & set(counted)):
        it, by = items[item], counted[item]
        if it["kind"] == "registration":
            got = {g: (c.count("П"), len(c)) for g, c in by.items()}
            wrong = [c for cs in by.values() for c in cs if c not in MARKS]
        else:
            got = {g: (c.count("+"), c.count("-"), c.count("="), sum(x in "+-=" for x in c)) for g, c in by.items()}
            wrong = [c for cs in by.values() for c in cs if c not in VOTES]
        if wrong:
            bad.append(f"точка {item}: кодове {sorted(set(wrong))} не са за {it['kind']}")
            continue
        diff = sorted(g for g in set(got) | set(it["groups"]) if got.get(g) != it["groups"].get(g))
        whole = tuple(map(sum, zip(*it["groups"].values())))
        whole_ok = whole == it["total"] if it["kind"] == "vote" else whole[0] == it["total"][0]
        if not diff and whole_ok:
            continue
        size = max([0] + [abs(a - b) for g in diff for a, b in zip(got.get(g) or (0,) * 4, it["groups"].get(g) or (0,) * 4)]
                   + ([abs(a - b) for a, b in zip(whole, it["total"])] if not whole_ok else []))
        mine, theirs = ", ".join(f"{g} {got.get(g)}" for g in diff), ", ".join(f"{g} {it['groups'].get(g)}" for g in diff)
        text = f"поименно {mine}; по групи {theirs}" if diff else f"сборът на групите {whole} не е общото {it['total']}"
        if diff and set(diff) - set(got) or diff and set(diff) - set(it["groups"]) or size > SMALL:
            bad.append(f"точка {item}: {text}")
        else:
            notes[item] = text
    if len(notes) > max(1, SMALL_SHARE * len(items)):
        bad.append(f"разминавания в {len(notes)} от {len(items)} точки")
    seen = set()
    for no, _, _, item, _ in votes:
        if (no, item) in seen:
            bad.append(f"депутат {no} два пъти в точка {item}")
            break
        seen.add((no, item))
    return bad, notes

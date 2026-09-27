"""The 'offers' lane: who offered what in each procedure.

The open data of ЦАИС ЕОП has only the number of offers. The public procedure page
app.eop.bg/today/{tenderId} shows every participant with ЕИК, time and price; it reads them from
service.eop.bg (GetPublicTenderParticipation, and GetPublishedLots to number the lots). This lane
reads that answer once per procedure after its deadline, again when the procedure gets a new record
(a contract, a change), weekly while prices are still closed and monthly for the last 90 days. At most
two requests per second on one kept-alive connection; every raw answer is kept gzipped with its sha256.

The participant of an offer is what the page shows (OfferName), not the account that submitted it
(OrganizationName): for a consortium (ДЗЗД) or a foreign company's branch these differ, and the one who
submitted can be a person. participant() holds the rule; a person's name is never stored.

With the offers the reader takes what else the page shows (GetPublishedTenderDetails): every notice and
decision, appeals to the КЗК, the attached documents (name and size), the opening dates and linked
procedures (audit 27.09.2026). None of these rows is deleted on one answer: what an answer no longer lists
gets gone_at and a line in ops.change_log.

Nothing we hold is dropped on one answer: an answer with fewer offers than we hold is kept back and read
again a day later, and replaces ours only when the second read has the same offers. Every difference
between two reads goes to ops.change_log.

check() is the daily validity check that runs before the reads (step eop-check): fixed canary
procedures must give the same offers, the answer must have the expected shape, and the offers read
must not come back empty where the contracts report offers. A failed canary or shape means the service changed: the
reads stop and an alert goes to Discord (n8n); nothing is lost, the queue waits.
"""
import datetime as dt
import gzip
import html
import hashlib
import http.client
import json
import re
import time

from .config import DATA, USER_AGENT
from .db import log_change
from .normalize import clean_name, eik_valid, name_norm

HOST = "service.eop.bg"
SVC = f"https://{HOST}/NX1Service.svc/"
RAW = DATA / "raw" / "eop_svc"
PAUSE = 0.5          # seconds between requests (two per second at most, one connection)
RECONNECT = 2        # seconds before a new connection when the service dropped ours (about 1 in 10 new ones)
MAX_ATTEMPTS = 6
CULTURE_BG = 3       # RetrieveCultures: 1 en-GB, 3 bg-BG
# procedures closed long ago, whose offers must not change: (offers, sum of prices)
CANARY = {463627: (2, 390000.0), 495012: (3, 1648930.56), 56580: (2, 478373.23), 563386: (6, 0.0)}
# participants the page shows, as (name, ЕИК): a consortium, a branch submitted by a person (audit 27.09.2026)
CANARY_PARTICIPANTS = {563386: {('"ЗА ЧИСТА ВАРНА" ДЗЗД', None),
                                ("АТЛАС ТЕМИЗЛИК ТУРИЗМ ИНШААТ ПЕЙЗАЖ ФИДАНДЖЪЛЪК САНАЙИ ВЕ ТИДЖАРЕТ ЛИМИТЕД ШИРКЕТИ - КЛОН БЪЛГАРИЯ КЧТ", "208392391")}}
# the order of the queue: what someone waits for first, the long backfill last
ORDER = ("CASE q.reason WHEN 'visit' THEN 0 WHEN 'update' THEN 1 WHEN 'new' THEN 1 WHEN 'confirm' THEN 2 WHEN 'recheck' THEN 2 "
         "WHEN 'prices' THEN 3 WHEN 'refresh' THEN 4 ELSE 5 END")
FIRST = ("visit", "update", "new")   # the reasons the D-1 chain waits for
OFFER_KEYS = {"OfferId", "OfferName", "OrganizationName", "RegistryNumber", "Price", "IsPriceOpened", "SubmissionDate", "ConsortiumMembers"}


class ShapeError(ValueError):
    """The answer does not look like the one this reader was written for."""


class HTTPStatus(RuntimeError):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


_conn = None  # one kept-alive connection for the whole run: a new TLS connection is refused about 1 in 10 times


def _drop():
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = None


def call(method, **body):
    """POST one service method. A dropped connection is opened again after RECONNECT seconds (the service
    resets about one in ten new connections, most often the first request on it); 429/5xx wait longer."""
    global _conn
    body.setdefault("ianaTimeZone", "Europe/Sofia")
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT, "Connection": "keep-alive",
               "Origin": "https://app.eop.bg", "Referer": "https://app.eop.bg/"}
    for k in range(5):
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(HOST, timeout=60)
            _conn.request("POST", "/NX1Service.svc/" + method, body=data, headers=headers)
            r = _conn.getresponse()
            raw, status, retry = r.read(), r.status, r.getheader("Retry-After")
            if r.will_close:
                _drop()
        except (http.client.HTTPException, OSError):  # reset, timeout, a closed kept-alive connection
            _drop()
            if k == 4:
                raise
            time.sleep(RECONNECT if k < 2 else 10 * k)
            continue
        if status == 200:
            time.sleep(PAUSE)
            return raw
        if status not in (429, 500, 502, 503, 504) or k == 4:
            raise HTTPStatus(status)
        time.sleep(int(retry or 0) or 10 * (k + 1))


def when(v):
    """'/Date(1749455167797)/' -> aware datetime (UTC)."""
    m = re.fullmatch(r"/Date\((-?\d+)([+-]\d{4})?\)/", v or "")
    return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc) if m else None


def eik(v):
    s = re.sub(r"\D", "", str(v or ""))
    return s if eik_valid(s) else None


def lot_number(name, numbered):
    """'3. Доставка ...' -> 3; the service's own numbering (GetPublishedLots) wins when present."""
    if name in numbered:
        return numbered[name]
    m = re.match(r"\s*(\d+)\s*\.", name or "")
    return int(m.group(1)) if m else 0


PERSON = "Физическо лице"
_PERSON = re.compile(r"^\s*физическо\s+лице\b", re.I)
# legal forms left out when two spellings of one company are compared („ВЕРИГА ДОМИНО ЕООД“ = „… ООД“)
_FORM = re.compile(r"(?<![А-ЯA-Z0-9])(ЕООД|ООД|ЕАД|АД|ЕТ|ДЗЗД|ЗД|КДА|КД|СД|АДСИЦ|ЛТД|LTD|GMBH|SRL|SA|DOO|D\.O\.O\.?|КЧТ)(?![А-ЯA-Z0-9])")


def org_key(name):
    """Two spellings of one organisation compare equal: no quotes, legal form, spaces or punctuation."""
    return re.sub(r"[^0-9A-ZА-Я]", "", _FORM.sub("", name_norm(name))) or None


def member_name(v):
    """A consortium member or appellant as shown: a person is only „Физическо лице“, without the name."""
    s = clean_name((v or "").strip())
    return PERSON if s and _PERSON.match(s) else s


def participant(o):
    """(name, ЕИК, submitter name, submitter ЕИК) of one offer.

    The participant is OfferName, what ЦАИС ЕОП shows. Its ЕИК: the only consortium member when that member
    is the participant itself (a ДЗЗД with its own БУЛСТАТ, a branch), else the member named like it, else the
    submitting account's number when that account is the participant (the same name up to spelling and legal
    form). A consortium without its own number keeps no ЕИК; its members are listed. The submitter is kept
    only when it is another company with a valid ЕИК; a person who submitted is never stored, and a
    participant written as „Физическо лице – <име>“ is only „Физическо лице“."""
    offer = (o.get("OfferName") or "").strip()
    org = (o.get("OrganizationName") or "").strip()
    name = offer or org or None
    own = eik(o.get("RegistryNumber"))
    members = [(m.get("OrganizationName") or m.get("Name") or "", eik(m.get("RegistryNumber"))) for m in o.get("ConsortiumMembers") or []]
    if name and _PERSON.match(name):
        return PERSON, None, None, None
    key = org_key(name)
    same_as_account = bool(key) and org_key(org) == key
    pe = next((e for n, e in members if e and org_key(n) == key), None) or (own if same_as_account else None)
    submitter = (clean_name(org), own) if org and own and not same_as_account else (None, None)
    return clean_name(name), pe, *submitter


def parse(tender_id, part, lots=()):
    """Offers of one answer of GetPublicTenderParticipation as eopsvc.offer rows (without fetched_at)."""
    if not isinstance(part, dict) or not ("Rounds" in part or "Lots" in part):
        raise ShapeError("no Rounds/Lots")
    numbered = {l.get("TenderName"): l.get("OrderNumber") for l in lots or ()}
    units = [(0, part)] if not part.get("Lots") else [(lot_number(L.get("TenderName"), numbered), L) for L in part["Lots"]]
    out = []
    for lot, u in units:
        for rnd in u.get("Rounds") or []:
            for o in rnd.get("Offers") or []:
                missing = OFFER_KEYS - set(o)
                if missing:
                    raise ShapeError(f"offer without {sorted(missing)}")
                opened = bool(o["IsPriceOpened"])
                name, pe, sub_name, sub_eik = participant(o)
                out.append({
                    "tender_id": tender_id, "lot_no": lot, "round": rnd.get("OrderNumber") or 1, "offer_id": o["OfferId"],
                    "bidder_name": name, "bidder_eik": pe, "submitter_name": sub_name, "submitter_eik": sub_eik,
                    "consortium": [{"name": member_name(m.get("OrganizationName") or m.get("Name")),
                                    "eik": eik(m.get("RegistryNumber"))} for m in o.get("ConsortiumMembers") or []] or None,
                    "submitted_at": when(o.get("SubmissionDate")),
                    "price": o["Price"] if opened and o["Price"] is not None else None, "price_opened": opened})
    return out


def parse_contracts(cl):
    """GetPublishedContractListItems -> [(lot_no, contract id)]; the id is the open data's contract number."""
    if not isinstance(cl, dict):
        return []
    out = [(0, str(c["Id"])) for c in cl.get("ContractListItems") or []]
    for L in cl.get("Lots") or []:
        out += [(L.get("LotNumber") or 0, str(c["Id"])) for c in L.get("ContractListItems") or []]
    return out


def fetch(tender_id, contracts=True):
    """One procedure: offers, the lot of each contract and the lot titles, as ЦАИС ЕОП numbers them.
    The lot list is asked for only when the lot names do not carry their number ("3. ..."), the contract
    list only when the procedure has contracts. Returns ({offers, contract_lots, lots}, raw bytes)."""
    raw = call("GetPublicTenderParticipation", tenderId=tender_id, cultureId=CULTURE_BG)
    part = json.loads(raw) if raw else None
    lots = []
    if part and part.get("Lots"):
        named = [re.match(r"\s*(\d+)\s*\.\s*(.*)", L.get("TenderName") or "", re.S) for L in part["Lots"]]
        if all(named):
            lots = [{"OrderNumber": int(m.group(1)), "TenderName": L.get("TenderName")} for m, L in zip(named, part["Lots"])]
        else:
            lots = json.loads(call("GetPublishedLots", tenderId=tender_id) or b"[]") or []
    cl = json.loads(call("GetPublishedContractListItems", tenderId=tender_id) or b"null") if contracts else None
    ann = json.loads(call("GetPublicTenderAnnouncementsByTenderId", tenderId=tender_id) or b"[]") or []
    det = sanitize_details(json.loads(call("GetPublishedTenderDetails", tenderId=tender_id, cultureId=CULTURE_BG) or b"null"))
    blob = json.dumps({"participation": part, "lots": lots, "contracts": cl, "announcements": ann, "details": det},
                      ensure_ascii=False).encode()
    titles = [(l.get("OrderNumber") or 0, re.sub(r"^\s*\d+\s*\.\s*", "", l.get("TenderName") or "").strip() or None) for l in lots]
    return {"offers": parse(tender_id, part, lots) if part else [], "contract_lots": parse_contracts(cl), "lots": titles,
            "announcements": parse_announcements(ann), "details": parse_details(det)}, blob


# the buyer's contact person, the authors and storage names of documents: never kept, not even in the raw copy
_DROP = {"ContactPersonDisplayName", "ContactPersonEmail", "ContactPersonPhone", "OrganizationImageUrl"}
_DOC_KEEP = {"Id", "Name", "Size", "CreatedDate", "Extension", "MimeType"}


def sanitize_details(d):
    if not isinstance(d, dict):
        return d
    d = {k: v for k, v in d.items() if k not in _DROP}
    d["TenderDescriptionDocuments"] = [{k: v for k, v in x.items() if k in _DOC_KEEP} for x in d.get("TenderDescriptionDocuments") or []]
    return d


def _day(v):
    t = when(v)
    return t.date() if t else None


def _linked(d):
    out = []
    for x in (d.get("LinkedTenders") or []) + (d.get("ChildTenders") or []):
        if isinstance(x, dict):
            out.append({"tender_id": x.get("TenderId") or x.get("Id"), "unp": x.get("SpecialNumber") or x.get("UniqueNumber"),
                        "name": (x.get("TenderName") or x.get("Name") or "").strip() or None})
        else:
            out.append({"tender_id": x, "unp": None, "name": None})
    return out


def parse_details(d):
    """GetPublishedTenderDetails -> {detail, publications, appeals, documents}; None when the service has no page."""
    if d is None:
        return None
    if not isinstance(d, dict) or "TenderId" not in d or not isinstance(d.get("TenderPublicationDetails") or [], list):
        raise ShapeError("details: no TenderId / TenderPublicationDetails")
    parent = d.get("ParentTender")
    detail = {"offers_from": when(d.get("OfferPhaseStartDate")), "offers_until": when(d.get("OfferPhaseEndDate")),
              "opening_at": when(d.get("OpeningOfOffersDate")), "prices_opening_at": when(d.get("OpeningOfPricesDate")),
              "parent_tender_id": (parent.get("TenderId") or parent.get("Id")) if isinstance(parent, dict) else parent,
              "linked": _linked(d) or None}
    pubs = []
    for p in d.get("TenderPublicationDetails") or []:
        res = p.get("TenderPublicationAuthorityResultCollection") or []
        sent = [when((r.get("Timestamp") or {}).get("TimestampDate")) for r in res]
        done = [when(r.get("StatusChangedDate")) for r in res if r.get("Status") == 6]   # 6: published (as the page shows it)
        pid = p.get("TenderPublicationId") or next((r.get("TenderPublicationId") for r in res), None)
        if pid:
            pubs.append({"id": int(pid), "form_type": p.get("PublicationFormType"),
                         "ted_number": p.get("TenderPublicationAuthorityNumber") or next((r.get("TenderPublicationAuthorityNumber") for r in res
                                                                                          if r.get("TenderPublicationAuthorityNumber")), None),
                         "sent_at": min((s for s in sent if s), default=None), "published_at": min((s for s in done if s), default=None)})
    val = lambda xs: [x.get("Value") for x in xs or [] if x.get("Value")]
    appeals = []
    for a in d.get("CpcProcurementDossier") or []:
        if not a.get("RegisterId"):
            continue
        appeals.append({"register_id": a["RegisterId"], "proceedings_number": a.get("ProceedingsNumber"), "kind": a.get("ProceedingsType"),
                        "subjects": val(a.get("ProceedingsSubsections")), "initiators": [member_name(v) for v in val(a.get("Initiators"))],
                        "defendants": [member_name(v) for v in val(a.get("Defendants"))],
                        "interim_measures": a.get("InterimMeasures") if a.get("InterimMeasuresSpecified", True) else None,
                        "imposed_measures": len(a.get("ImposedInterimMeasures") or []), "imposed_penalties": len(a.get("ImposedPenalties") or []),
                        "status": a.get("CurrentStatus"), "filed_on": _day(a.get("DossierPublishDate")),
                        "started_on": _day(a.get("ProceedingsStartDate")), "last_decision_on": _day(a.get("LastDecisionPublishDate")),
                        "closed_on": _day(a.get("ProceedingsCloseDate")), "link": a.get("DossierLink")})
    docs = [{"id": int(x["Id"]), "name": x.get("Name"), "size": x.get("Size"), "created_at": when(x.get("CreatedDate"))}
            for x in d.get("TenderDescriptionDocuments") or [] if x.get("Id")]
    return {"detail": detail, "publications": pubs, "appeals": appeals, "documents": docs}


def parse_announcements(ann):
    """GetPublicTenderAnnouncementsByTenderId -> [(id, time, title)]; the title only, without markup."""
    if not isinstance(ann, list):
        raise ShapeError(f"announcements: {type(ann).__name__}")
    out = []
    for a in ann:
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", a.get("Title") or a.get("Text") or ""))).strip()
        if a.get("Id") is not None and title:
            out.append((int(a["Id"]), when(a.get("CreatedDate")), title[:300]))
    return out


def offer_set(rows):
    """What identifies the offers of one answer: sorted (lot, round, offer id)."""
    return sorted((r["lot_no"], r["round"], r["offer_id"]) for r in rows)


def offer_diff(old, new):
    """[(ref, field, old, new)] between two reads, per offer (lot/round/offer id): gone, added, and a
    changed price, opening or bidder."""
    key = lambda r: (r["lot_no"], r["round"], r["offer_id"])
    a, b = {key(r): r for r in old}, {key(r): r for r in new}
    out = []
    for k in sorted(a.keys() | b.keys()):
        ref = "/".join(map(str, k))
        if k not in b:
            out.append((ref, None, f"{a[k]['bidder_name']} {a[k]['price']}", None))
        elif k not in a:
            out.append((ref, None, None, f"{b[k]['bidder_name']} {b[k]['price']}"))
        else:
            for f in ("price", "price_opened", "bidder_name", "bidder_eik"):
                o, n = a[k][f], b[k][f]
                if f == "price" and o is not None and n is not None and abs(float(o) - float(n)) < 0.005:
                    continue
                if o != n:
                    out.append((ref, f, o, n))
    return out


def store(conn, tender_id, got, blob):
    """Keep one answer. Returns 'stored', or 'held' when it has fewer offers than we hold and no earlier
    read has confirmed it yet (it is read again in a day; ours stays until then)."""
    rows = got["offers"]
    now = dt.datetime.now(dt.timezone.utc)
    sha = hashlib.sha256(blob).hexdigest()
    d = RAW / str(tender_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{now:%Y%m%d%H%M}.json.gz").write_bytes(gzip.compress(blob))
    old = [dict(zip(("lot_no", "round", "offer_id", "bidder_name", "bidder_eik", "price", "price_opened"), r)) for r in conn.execute(
        "SELECT lot_no, round, offer_id, bidder_name, bidder_eik, price, price_opened FROM eopsvc.offer WHERE tender_id = %s",
        (tender_id,)).fetchall()]
    held = conn.execute("SELECT held_sha FROM eopsvc.queue WHERE tender_id = %s", (tender_id,)).fetchone()
    mark = hashlib.sha256(json.dumps(offer_set(rows)).encode()).hexdigest()
    with conn.transaction():
        if old and len(rows) < len(old):
            if not held or held[0] != mark:
                log_change(conn, "offers", tender_id, "offers", len(old), len(rows), "held")
                conn.execute("""UPDATE eopsvc.queue SET status = 'pending', reason = 'confirm', next_at = now() + interval '1 day',
                    held_sha = %s, attempts = 0, last_error = NULL WHERE tender_id = %s""", (mark, tender_id))
                return "held"
            log_change(conn, "offers", tender_id, "offers", len(old), len(rows), "confirmed")
        if old:
            for ref, field, o, n in offer_diff(old, rows):
                log_change(conn, "offers", f"{tender_id}/{ref}", field, o, n, "rewritten")
        conn.execute("DELETE FROM eopsvc.offer WHERE tender_id = %s", (tender_id,))
        write_offers(conn, rows, now)
        conn.execute("DELETE FROM eopsvc.contract_lot WHERE tender_id = %s", (tender_id,))
        conn.execute("DELETE FROM eopsvc.lot WHERE tender_id = %s", (tender_id,))
        for cid, lot in {c: l for l, c in got["contract_lots"]}.items():  # one lot per contract
            conn.execute("INSERT INTO eopsvc.contract_lot VALUES (%s, %s, %s)", (tender_id, lot, cid))
        for lot, title in dict(got["lots"]).items():
            conn.execute("INSERT INTO eopsvc.lot VALUES (%s, %s, %s)", (tender_id, lot, title))
        keep(conn, "announcement", tender_id, [{"id": a, "created_at": at, "title": t} for a, at, t in got.get("announcements", [])],
             ("created_at", "title"), now)
        det = got.get("details")
        if det:
            conn.execute("""INSERT INTO eopsvc.detail VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tender_id) DO UPDATE SET
                offers_from = EXCLUDED.offers_from, offers_until = EXCLUDED.offers_until, opening_at = EXCLUDED.opening_at,
                prices_opening_at = EXCLUDED.prices_opening_at, parent_tender_id = EXCLUDED.parent_tender_id, linked = EXCLUDED.linked,
                fetched_at = EXCLUDED.fetched_at""", (tender_id, *[det["detail"][k] for k in DETAIL_COLS],
                                                      json.dumps(det["detail"]["linked"], ensure_ascii=False) if det["detail"]["linked"] else None, now))
            keep(conn, "publication", tender_id, det["publications"], ("form_type", "ted_number", "sent_at", "published_at"), now)
            keep(conn, "appeal", tender_id, det["appeals"], APPEAL_COLS, now, key="register_id")
            keep(conn, "document", tender_id, det["documents"], ("name", "size", "created_at"), now)
        conn.execute("UPDATE eopsvc.queue SET status = 'done', fetched_at = %s, sha256 = %s, last_error = NULL, attempts = 0, "
                     "held_sha = NULL WHERE tender_id = %s", (now, sha, tender_id))
    return "stored"


DETAIL_COLS = ("offers_from", "offers_until", "opening_at", "prices_opening_at", "parent_tender_id")
APPEAL_COLS = ("proceedings_number", "kind", "subjects", "initiators", "defendants", "interim_measures", "imposed_measures",
               "imposed_penalties", "status", "filed_on", "started_on", "last_decision_on", "closed_on", "link")


def _same(a, b):
    if isinstance(a, dt.datetime) and isinstance(b, dt.datetime):
        return abs((a - b).total_seconds()) < 1
    return a == b or (a in (None, [], "") and b in (None, [], ""))


def keep(conn, table, tender_id, rows, cols, now, key="id"):
    """Rows of eopsvc.<table> for one procedure, never deleted on one answer: new ones are added, changed fields
    are updated and logged, rows the answer no longer lists get gone_at (logged once) and stay; a row that comes
    back loses its gone_at. The first read of a procedure logs nothing."""
    held = {r[0]: r[1:] for r in conn.execute(f"SELECT {key}, {', '.join(cols)}, gone_at FROM eopsvc.{table} WHERE tender_id = %s",
                                                 (tender_id,)).fetchall()}
    first = not held
    seen = set()
    for r in rows:
        k = r[key]
        seen.add(k)
        vals = [r[c] for c in cols]
        if k not in held:
            conn.execute(f"INSERT INTO eopsvc.{table} (tender_id, {key}, {', '.join(cols)}) VALUES (%s, %s{', %s' * len(cols)})",
                         (tender_id, k, *vals))
            if not first:
                log_change(conn, f"eop-{table}", f"{tender_id}/{k}", None, None, r.get("title") or r.get("name") or k, "new-record")
            continue
        old, gone = held[k][:-1], held[k][-1]
        diff = [(c, o, n) for c, o, n in zip(cols, old, vals) if not _same(o, n)]
        if diff or gone:
            conn.execute(f"UPDATE eopsvc.{table} SET {', '.join(f'{c} = %s' for c in cols)}, gone_at = NULL WHERE tender_id = %s AND {key} = %s",
                         (*vals, tender_id, k))
            for c, o, n in diff:
                log_change(conn, f"eop-{table}", f"{tender_id}/{k}", c, o, n, "rewritten")
            if gone:
                log_change(conn, f"eop-{table}", f"{tender_id}/{k}", "gone_at", gone, None, "back")
    for k, v in held.items():
        if k not in seen and v[-1] is None:
            conn.execute(f"UPDATE eopsvc.{table} SET gone_at = %s WHERE tender_id = %s AND {key} = %s", (now, tender_id, k))
            log_change(conn, f"eop-{table}", f"{tender_id}/{k}", None, k, None, "gone")


def write_offers(conn, rows, now):
    with conn.cursor().copy("COPY eopsvc.offer (tender_id, lot_no, round, offer_id, bidder_name, bidder_eik, submitter_name, submitter_eik, "
                            "consortium, submitted_at, price, price_opened, fetched_at) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r["tender_id"], r["lot_no"], r["round"], r["offer_id"], r["bidder_name"], r["bidder_eik"], r.get("submitter_name"),
                          r.get("submitter_eik"), json.dumps(r["consortium"], ensure_ascii=False) if r["consortium"] else None,
                          r["submitted_at"], r["price"], r["price_opened"], now])


def reparse(conn, stats):
    """Rebuild eopsvc.offer from the raw answers we hold, without asking ЦАИС ЕОП again, after a change of the
    parsing rules (27.09.2026: participant instead of the submitting account). Only the answer whose sha256 is the
    one the queue stored is used; every changed name or ЕИК goes to ops.change_log as 'reparsed'."""
    stats.update(procedures=0, changed=0, missing_raw=0)
    for tid, sha in conn.execute("SELECT tender_id, sha256 FROM eopsvc.queue WHERE status = 'done' AND sha256 IS NOT NULL").fetchall():
        blob = None
        for f in sorted((RAW / str(tid)).glob("*.json.gz"), reverse=True):
            b = gzip.decompress(f.read_bytes())
            if hashlib.sha256(b).hexdigest() == sha:
                blob = b
                break
        if blob is None:
            stats["missing_raw"] += 1
            continue
        j = json.loads(blob)
        rows = parse(tid, j["participation"], j.get("lots") or []) if j.get("participation") else []
        old = {(r[0], r[1], r[2]): r[3:] for r in conn.execute(
            "SELECT lot_no, round, offer_id, bidder_name, bidder_eik FROM eopsvc.offer WHERE tender_id = %s", (tid,)).fetchall()}
        if set(old) != {(r["lot_no"], r["round"], r["offer_id"]) for r in rows}:
            raise RuntimeError(f"{tid}: the raw answer with the stored sha256 has other offers than the table")
        with conn.transaction():
            for r in rows:
                o = old[(r["lot_no"], r["round"], r["offer_id"])]
                for f, a, b in (("bidder_name", o[0], r["bidder_name"]), ("bidder_eik", o[1], r["bidder_eik"])):
                    if a != b:
                        log_change(conn, "offers", f"{tid}/{r['lot_no']}/{r['round']}/{r['offer_id']}", f, a, b, "reparsed")
                        stats["changed"] += 1
            fetched = conn.execute("SELECT min(fetched_at) FROM eopsvc.offer WHERE tender_id = %s", (tid,)).fetchone()[0]
            conn.execute("DELETE FROM eopsvc.offer WHERE tender_id = %s", (tid,))
            write_offers(conn, rows, fetched)
        stats["procedures"] += 1


def enqueue(conn, periodic=False):
    """Procedures past their deadline that were never read ('new' when the deadline passed in the last
    14 days, else 'backfill'); procedures with a newer record than the read ('update'). periodic (weekly):
    procedures whose prices were still closed at the last read ('prices', deadline within a year) and
    procedures of the last 90 days not read for 30 days ('refresh'). A procedure known only from its contracts has
    no deadline and no notice date: it counts from the day of its contract record (before 27.09.2026 never queued)."""
    new = conn.execute("""INSERT INTO eopsvc.queue (tender_id, reason)
        SELECT t.tender_id::bigint, CASE WHEN coalesce(t.submission_deadline, t.published_at + interval '30 days', t.source_day + interval '30 days') > now() - interval '14 days'
                                         THEN 'new' ELSE 'backfill' END
        FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND coalesce(t.submission_deadline, t.published_at + interval '30 days', t.source_day + interval '30 days') < now()
        ON CONFLICT DO NOTHING""").rowcount
    upd = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'update', next_at = now(), attempts = 0
        FROM live.tender t
        WHERE t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint AND q.status = 'done'
          AND greatest(t.source_day, (SELECT max(c.source_day) FROM live.contract c WHERE c.unp = t.unp)) >= q.fetched_at::date""").rowcount
    out = {"new": new, "update": upd}
    if periodic:
        out["prices"] = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'prices', next_at = now(), attempts = 0
            FROM live.tender t
            WHERE q.status = 'done' AND q.fetched_at < now() - interval '7 days' AND t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint
              AND t.submission_deadline > now() - interval '1 year'
              AND EXISTS (SELECT 1 FROM eopsvc.offer o WHERE o.tender_id = q.tender_id AND NOT o.price_opened)""").rowcount
        out["refresh"] = conn.execute("""UPDATE eopsvc.queue q SET status = 'pending', reason = 'refresh', next_at = now(), attempts = 0
            FROM live.tender t
            WHERE q.status = 'done' AND q.fetched_at < now() - interval '30 days' AND t.tender_id ~ '^[0-9]+$' AND q.tender_id = t.tender_id::bigint
              AND t.submission_deadline > now() - interval '90 days'""").rowcount
    return out


def waiting(conn):
    """Procedures the D-1 chain waits for: opened by a visitor, changed, or just closed."""
    return conn.execute("SELECT count(*) FROM eopsvc.queue WHERE status = 'pending' AND next_at <= now() AND reason = ANY(%s)",
                        (list(FIRST),)).fetchone()[0]


def work(conn, budget_s, stats, first=False):
    """Read the queue until the budget runs out: updates first, then the newest procedures."""
    deadline = time.monotonic() + budget_s
    stats.update(done=0, held=0, errors=0, offers=0)
    while time.monotonic() < deadline - 5:
        row = conn.execute("""SELECT q.tender_id, EXISTS (SELECT 1 FROM live.tender t JOIN live.contract c ON c.unp = t.unp
                                   WHERE t.tender_id = q.tender_id::text)
            FROM eopsvc.queue q WHERE q.status = 'pending' AND q.next_at <= now() """ + ("AND q.reason = ANY(%(first)s) " if first else "") + """
            ORDER BY """ + ORDER + """, q.tender_id DESC LIMIT 1""", {"first": list(FIRST)} if first else None).fetchone()
        if not row:
            break
        tid = row[0]
        try:
            got, blob = fetch(tid, contracts=row[1])
            if store(conn, tid, got, blob) == "held":
                stats["held"] += 1
            else:
                stats["done"] += 1
                stats["offers"] += len(got["offers"])
        except ShapeError:
            raise  # the service changed: stop, the check and the repair agent take over
        except Exception as e:  # network trouble: back off this one procedure
            conn.execute("""UPDATE eopsvc.queue SET attempts = attempts + 1, last_error = %s,
                status = CASE WHEN attempts + 1 >= %s THEN 'error' ELSE 'pending' END,
                next_at = now() + make_interval(mins => 10 * power(2, attempts)::int) WHERE tender_id = %s""",
                         (f"{type(e).__name__}: {e}"[:500], MAX_ATTEMPTS, tid))
            stats["errors"] += 1
    stats["pending"] = conn.execute("SELECT count(*) FROM eopsvc.queue WHERE status = 'pending'").fetchone()[0]


def check(conn):
    """The daily validity check. Returns the report; report['ok'] is False when the reader must be repaired."""
    rep = {"canary": {}, "shape": "ok", "completeness": None}
    ok = True
    for tid, (n, total) in CANARY.items():
        try:
            rows = fetch(tid)[0]["offers"]
            got = (len(rows), round(sum(r["price"] or 0 for r in rows), 2))
            rep["canary"][tid] = {"expected": [n, total], "got": list(got)}
            ok &= got == (n, total)
            want = CANARY_PARTICIPANTS.get(tid, set())
            lost = want - {(r["bidder_name"], r["bidder_eik"]) for r in rows}
            if lost:
                rep["canary"][tid]["participants_missing"] = sorted(map(str, lost))
                ok = False
        except ShapeError as e:
            rep["shape"], ok = str(e), False
        except Exception as e:
            rep["canary"][tid] = {"error": f"{type(e).__name__}: {e}"[:300]}
            ok = False
    # completeness, per procedure read in the last week: offersCount of its contracts vs the offers read.
    # The two sources differ often by one offer (withdrawn, paper, counted by the buyer), so that is only
    # reported; the symptom of a broken reader is no offers at all where the contracts say there were some.
    # the offers counts come from the contracts and, for lots closed without award, from the award notices
    award = "UNION ALL SELECT unp, offers_count FROM live.award" if _has(conn, "live.award") else ""
    rows = conn.execute(f"""WITH c AS (
          SELECT t.tender_id::bigint tid, sum(x.offers_count) n FROM (SELECT unp, offers_count FROM live.contract {award}) x
          JOIN live.tender t ON t.unp = x.unp
          WHERE x.offers_count > 0 AND t.tender_id ~ '^[0-9]+$' GROUP BY 1),
        o AS (SELECT tender_id tid, count(*) n FROM eopsvc.offer GROUP BY 1)
        SELECT c.tid, c.n, coalesce(o.n, 0) FROM c JOIN eopsvc.queue q ON q.tender_id = c.tid LEFT JOIN o ON o.tid = c.tid
        WHERE q.status = 'done' AND q.fetched_at > now() - interval '7 days'""").fetchall()
    empty = [r for r in rows if r[2] == 0]
    rep["completeness"] = {"compared": len(rows), "equal": sum(r[1] == r[2] for r in rows), "none_read": len(empty),
                           "examples": [list(r) for r in empty[:10]]}
    if empty:
        conn.execute("""UPDATE eopsvc.queue SET status = 'pending', reason = 'recheck', next_at = now() + interval '1 day'
            WHERE tender_id = ANY(%s) AND reason <> 'recheck'""", ([r[0] for r in empty],))
    if len(rows) >= 20 and len(empty) > len(rows) / 2:
        ok = False
    rep["mismatches"] = mismatches(conn)
    rep["ok"] = ok
    conn.execute("INSERT INTO eopsvc.check_run (ok, report, mismatches) VALUES (%s, %s, %s)",
                 (ok, json.dumps(rep, default=str), json.dumps(rep["mismatches"], default=str)))
    return rep


def _has(conn, rel):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (rel,)).fetchone()[0]


def mismatches(conn, days=2):
    """ЦАИС ЕОП against the open data, for the procedures read in the last `days`: a notice of the open data
    that the procedure page does not list (one of the two is incomplete), and more contracts on the page than
    in the open data (contracts we do not show). Each new case goes to ops.change_log once (source
    'completeness'); the report carries the counts and examples."""
    if not (_has(conn, "live.notice") and _has(conn, "eopsvc.publication")):
        return {"skipped": "no live.notice yet"}
    notices = conn.execute("""SELECT q.tender_id, n.notice_id FROM eopsvc.queue q
        JOIN live.tender t ON t.tender_id = q.tender_id::text JOIN live.notice n ON n.unp = t.unp
        WHERE q.status = 'done' AND q.fetched_at > now() - make_interval(days => %s) AND n.notice_id ~ '^[0-9]+$'
          AND EXISTS (SELECT 1 FROM eopsvc.detail d WHERE d.tender_id = q.tender_id)
          AND NOT EXISTS (SELECT 1 FROM eopsvc.publication p WHERE p.tender_id = q.tender_id AND p.id = n.notice_id::bigint)""",
                           (days,)).fetchall()
    contracts = conn.execute("""SELECT q.tender_id, t.unp, (SELECT count(*) FROM eopsvc.contract_lot k WHERE k.tender_id = q.tender_id) eop,
          (SELECT count(*) FROM live.contract c WHERE c.unp = t.unp) ours
        FROM eopsvc.queue q JOIN live.tender t ON t.tender_id = q.tender_id::text
        WHERE q.status = 'done' AND q.fetched_at > now() - make_interval(days => %s)""", (days,)).fetchall()
    more = [r for r in contracts if r[2] > r[3]]
    for tid, nid in notices:
        _once(conn, f"{tid}/notice/{nid}", "notice in the open data, not on the ЦАИС ЕОП page")
    for tid, unp, eop, ours in more:
        _once(conn, f"{tid}/contracts", f"{eop} contracts on the ЦАИС ЕОП page, {ours} in the open data ({unp})")
    return {"notice_not_on_page": len(notices), "notice_examples": [list(r) for r in notices[:10]],
            "contracts_only_on_page": len(more), "contract_examples": [list(r) for r in more[:10]], "compared": len(contracts)}


def _once(conn, ref, what):
    if not conn.execute("SELECT 1 FROM ops.change_log WHERE source = 'completeness' AND ref = %s AND new = %s", (ref, what)).fetchone():
        log_change(conn, "completeness", ref, None, None, what, "mismatch")

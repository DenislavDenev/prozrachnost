"""Raw ЕОП files -> normalized rows (buyers, tenders, contracts, suppliers, amendments, subcontracts).

Rules follow docs/methodology.md; value/identity rules are ported from SIGMA's
scripts/normalize-raw.sql (commit 62369ff, MIT) and noted where they differ.
Pure functions over rows; ingest.build writes the result into schema `stage`.
"""
import datetime as dt
import hashlib
import re
import unicodedata
from collections import defaultdict

from .config import BGN_PER_EUR

RULES_VERSION = "normalize_v5"  # v2: implausible dates, estimate ratio, EUR estimates, raw pointers; v3: lot and tender status;
# v4: an annex's value in the annex's own currency (after 01.01.2026 annexes restate лев contracts in euro);
# v5 (audit 27.09.2026): award notices without award kept (award), every notice listed (notice), an annex goes to
# exactly one contract (number, spelling of the number, starting value) or is kept as an orphan, a corrected
# republication of a contract replaces it instead of adding a second one; every input row is accounted for

YES = {"Да": True, "Не": False}


# ---------- parsing ----------

def num(v):
    """'155975,57' / '1 234,5' / 12.3 -> float; '' / None -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def day_of(v):
    """'05.06.2026' or ISO datetime -> date; else None."""
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", s)
    try:
        if m:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def ts_of(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def flag(v):
    return YES.get(str(v).strip()) if v not in (None, "") else None


def text(v):
    s = str(v).strip() if v is not None else ""
    return s if s and s != "None" else None


# ---------- identity ----------

def _eik9_control(d):
    s = sum((i + 1) * d[i] for i in range(8)) % 11
    if s == 10:
        s = sum((i + 3) * d[i] for i in range(8)) % 11
        if s == 10:
            s = 0
    return s


def eik_valid(e):
    """ЕИК/БУЛСТАТ checksum (9 or 13 digits)."""
    if not e or not e.isdigit() or len(e) not in (9, 13) or set(e) == {"0"}:
        return False
    d = [int(c) for c in e]
    if _eik9_control(d) != d[8]:
        return False
    if len(e) == 9:
        return True
    s = (2 * d[8] + 7 * d[9] + 3 * d[10] + 5 * d[11]) % 11
    if s == 10:
        s = (4 * d[8] + 9 * d[9] + 5 * d[10] + 7 * d[11]) % 11
        if s == 10:
            s = 0
    return s == d[12]


def clean_eik(v):
    s = text(v)
    if not s:
        return None
    s = re.sub(r"^ЕИК\s*", "", s, flags=re.I)
    s = re.sub(r"[\s ]", "", s)
    return s or None


QUOTES = "\"'`„“”‚‘’«»′″"


def name_norm(v):
    s = unicodedata.normalize("NFC", str(v or ""))
    s = s.translate({ord(c): None for c in QUOTES})
    s = re.sub(r"[‐‑‒–—―−]", "-", s).replace("­", "").replace("​", "")
    return re.sub(r"\s+", " ", s).strip().upper()


def party_key(eik, name):
    """Company key: valid ЕИК, else normalized name, else unknown (SIGMA three-rung key)."""
    if eik and eik_valid(eik):
        return f"eik:{eik}"
    if name and name_norm(name):
        return f"name:{name_norm(name)}"
    return "unknown"


_REP = re.compile(r"[,;]?\s*([Пп]редставлява[нщ][а-я]*|[Чч]рез|[Сс]\s+представляващ)\s.*$")
_PID = re.compile(r"([Ее][Гг][Нн]|[Лл][Нн][Чч]|[Ее][Ии][Кк]|[Бб][Уу][Лл][Сс][Тт][Аа][Тт])[\s:№.]*\d+")
_LONG = re.compile(r"\d{6,}")


def clean_name(v):
    """A name as shown: no identity number (ЕГН, ЛНЧ, ЕИК, БУЛСТАТ or any run of 6+ digits) and,
    for a company holder, not its representative. Mirrors clean_name() in db/derive/schema.sql."""
    if v is None:
        return None
    s = _LONG.sub("", _PID.sub("", _REP.sub("", str(v))))
    s = re.sub(r"[,;]?\s*([Ее][Гг][Нн]|[Лл][Нн][Чч])\s*[:№.]*\s*$", "", s)  # a bare „ЕГН:“ left at the end
    return re.sub(r"\s{2,}", " ", s).strip(" ,;-–—") or None


def split_members(names, eiks):
    """A group award lists members as 'A; B; C' with ЕИК '1; 2; 3'. Returns [(eik, name)]."""
    ns = [n.strip() for n in str(names or "").split(";")]
    es = [e.strip() for e in str(eiks or "").split(";")]
    if len(ns) == 1 and len(es) == 1:
        return [(clean_eik(es[0]), clean_name(text(ns[0])))]
    if len(es) != len(ns):  # cannot pair them reliably; keep names only
        return [(None, clean_name(text(n))) for n in ns if text(n)]
    return [(clean_eik(e), clean_name(text(n))) for e, n in zip(es, ns) if text(n) or clean_eik(e)]


def contract_id(unp, number, lot, supplier_keys):
    h = hashlib.sha1("|".join([unp or "", number or "", lot or "", ",".join(supplier_keys)])
                     .encode()).hexdigest()
    return "c" + h[:12]


def lot_no(v):
    """'LOT-0003' / '3' -> 3; None when absent."""
    s = text(v)
    if not s:
        return None
    m = re.search(r"(\d+)$", s)
    return int(m.group(1)) if m else None


# Latin letters that look Cyrillic in contract numbers („TO-105“ typed in Latin, „ТО-105“ in Cyrillic)
_HOMO = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def number_key(v):
    """A contract number as spelled in different records: „Договор № 49-209“ = „49-209/23.11.20“ = „49209“."""
    s = (text(v) or "").upper().translate(_HOMO)
    s = re.sub(r"\s*(/|ОТ\s)\s*\d{1,2}\.\d{1,2}\.\d{2,4}\s*(Г\.?)?\s*$", "", s)   # a date at the end
    s = re.sub(r"^(ДОГОВОР|ДОГ\.?)\s*", "", s)
    s = re.sub(r"[^0-9А-Я]", "", s.replace("№", ""))
    return s.lstrip("0") or None


# ---------- money ----------

class Fx:
    """EUR conversion: BGN at the fixed rate, EUR as is, others at the latest ECB rate
    within 10 days before the date (SIGMA rule). Returns None when no rate applies."""

    def __init__(self, rates):  # {currency: sorted [(date, units_per_eur)]}
        self.rates = rates

    def to_eur(self, amount, currency, on):
        if amount is None:
            return None, None
        c = (currency or "BGN").strip().upper() or "BGN"
        if c == "EUR":
            return amount, 1.0
        if c in ("BGN", "ЛВ", "ЛВ."):
            return amount / BGN_PER_EUR, 1 / BGN_PER_EUR
        series = self.rates.get(c)
        if not series or on is None:
            return None, None
        best = None
        for d, per_eur in series:
            if d > on:
                break
            best = (d, per_eur)
        if not best or (on - best[0]).days > 10:
            return None, None
        return amount / best[1], 1 / best[1]


def value_flag(eff_eur, proc_est_eur, own_est_eur, initial, current, annex_steps):
    """SIGMA ADR-0003 verdict. Returns (flag, which value to sum: 'effective'|'estimate'|'initial')."""
    if eff_eur is not None and (
            eff_eur > 2e9 or
            (proc_est_eur and proc_est_eur >= 1000 and
             (eff_eur > 200 * proc_est_eur or 95 * proc_est_eur <= eff_eur <= 105 * proc_est_eur)) or
            (own_est_eur and own_est_eur >= 1000 and proc_est_eur and proc_est_eur >= 1000 and
             eff_eur >= 10 * proc_est_eur and 95 * own_est_eur <= eff_eur <= 105 * own_est_eur)):
        return "value_suspect", "estimate"
    if initial and current is not None and (
            current < 0 or current >= 100 * initial or
            (max(annex_steps, default=0) >= 10 and current >= 5 * initial)):
        return "annex_suspect", "initial"
    eff = current if current is not None else initial
    if eff is not None and eff <= 0:
        return "value_low", "effective"
    if eff_eur is not None and own_est_eur and eff_eur < 1000 and eff_eur < 0.05 * own_est_eur:
        return "value_low", "effective"
    if eff_eur is not None and proc_est_eur and eff_eur >= 10 * proc_est_eur:
        return "review", "effective"
    return "ok", "effective"


# ---------- main ----------

def normalize(days_rows, fx):
    """days_rows: iterable of (day, kind, rows). Returns dict of table -> list of dicts."""
    buyers, tenders = {}, {}
    contracts = {}
    annexes = defaultdict(list)
    ocds_contracts = []
    # where each tender / contract / OCDS release sits in the raw files, so the pages can show
    # every published field of the original record (source_record)
    src = []
    places = {}  # buyer ЕИК -> address from OCDS parties (the flat files carry none)
    unawarded, unawarded_tid = set(), set()  # (unp | OCDS tender id, lot) closed without a contract
    awards, notices = {}, {}   # award notices without award, every notice of the flat files by id
    idents = {}                # (unp, number, lot, signing date) -> contract id: a corrected republication replaces it
    replaced = {}              # contract id -> the id of the correction that replaced it
    st = defaultdict(int)      # every input row lands in exactly one of these counts (checked at the end)
    last_day = None
    for day, kind, rows in days_rows:
        last_day = max(last_day or day, day)
        for i, r in enumerate(rows):
            st[f"in_{kind}"] += 1
            if kind in ("tenders", "contracts"):
                _notice(r, day, kind, notices)
            if kind == "tenders":
                unp = _tender_row(r, day, tenders, buyers)
                if unp:
                    src.append({"entity": "tender", "ref": unp, "day": day, "kind": kind, "idx": i})
                    st["tenders_used"] += 1
                else:
                    st["tenders_without_unp"] += 1
            elif kind == "contracts":
                if flag(r.get("noAwarding")):
                    unp = text(r.get("uniqueProcurementNumber"))
                    unawarded.add((unp, lot_no(r.get("lotIdentifier"))))
                    _award_row(r, day, awards)
                    if unp:
                        src.append({"entity": "award", "ref": unp, "day": day, "kind": kind, "idx": i})
                    st["contracts_no_award"] += 1
                    continue
                cid, how = _contract_row(r, day, contracts, buyers, idents, replaced)
                st[f"contracts_{how}"] += 1
                src.append({"entity": "contract", "ref": cid, "day": day, "kind": kind, "idx": i})
            elif kind == "annexes":
                key = (text(r.get("uniqueProcurementNumber")), text(r.get("contractNumber")))
                annexes[key].append((ts_of(r.get("publicationDate")), day, r))
            elif kind == "ocds":
                ocds_contracts.extend(_ocds_contracts(r, day))
                tid = text((r.get("tender") or {}).get("id"))
                for a in r.get("awards") or []:
                    if a.get("status") == "unsuccessful" and tid:
                        unawarded_tid.add((tid, lot_no((a.get("relatedLots") or [None])[0])))
                if tid:
                    src.append({"entity": "ocds", "ref": tid, "day": day, "kind": kind, "idx": i})
                for party in r.get("parties") or []:
                    eik = clean_eik((party.get("identifier") or {}).get("id"))
                    a = party.get("address") or {}
                    if eik and "buyer" in (party.get("roles") or []) and a.get("locality"):
                        places[eik] = {"locality": text(a.get("locality")), "postal_code": text(a.get("postalCode")),
                                       "nuts": text(a.get("region"))}

    # OCDS adds only contracts whose number the flat feed does not have (SIGMA ADR-0006)
    known = {(c["unp_raw_tender_id"], c["contract_number"]) for c in contracts.values()}
    known_numbers = {c["contract_number"] for c in contracts.values() if c["contract_number"]}
    tid2unp = {t["tender_id"]: u for u, t in tenders.items() if t["tender_id"]}
    tid2unp.update({c["unp_raw_tender_id"]: c["unp"] for c in contracts.values()
                    if c["unp_raw_tender_id"] and c["unp"]})
    ocds_added = 0
    for c in ocds_contracts:
        if c["contract_number"] in known_numbers or (c["unp_raw_tender_id"], c["contract_number"]) in known:
            continue
        c["unp"] = tid2unp.get(c["unp_raw_tender_id"])
        contracts.setdefault(c["id"], c)
        ocds_added += 1

    assigned, orphans = assign_annexes(annexes, contracts.values(), st)
    out_contracts, suppliers, amendments, subcontracts = [], [], [], []
    for c in contracts.values():
        chain = sorted(assigned.get(c["id"], []), key=lambda a: (a[0] or dt.datetime.min, a[1]))
        steps, current, current_ccy = [], None, None
        for ts, aday, a in chain:
            last, cur = num(a.get("lastContractValue")), num(a.get("currentContractValue"))
            if last and cur is not None:
                steps.append(cur / last)
            if cur is not None:  # the value is in the annex's currency, not always the contract's
                current, current_ccy = cur, text(a.get("contractCurrency")) or c["currency"]
            amendments.append({
                "contract_id": c["id"], "published_at": ts, "source_day": aday,
                "last_value": last, "current_value": cur,
                "difference": num(a.get("contractValueDifference")),
                "currency": text(a.get("contractCurrency")),
                "reason": text(a.get("changeReason")),
                "description": text(a.get("changeDescription")) or text(a.get("changeReasonDescription")),
            })
        # a signing date more than 30 days after the day the record was published is a typo
        # (e.g. 2029 in a record published in 2024); use the publication date instead
        src_day = dt.date.fromisoformat(str(c["source_day"]))
        bad_date = bool(c["contract_date"]) and c["contract_date"] > src_day + dt.timedelta(days=30)
        pub_day = c["published_at"].date() if c["published_at"] else src_day
        on = pub_day if bad_date else (c["contract_date"] or (c["published_at"].date() if c["published_at"] else None))
        cur_code = c["currency"]
        initial = c["value_initial"]
        init_eur, rate = fx.to_eur(initial, cur_code, on)
        cur_eur, cur_rate = fx.to_eur(current, current_ccy or cur_code, on)
        eff_eur, rate = (cur_eur, cur_rate) if current is not None else (init_eur, rate)
        own_est_eur, _ = fx.to_eur(c["estimated_value"], c["estimate_currency"] or cur_code, on)
        t = tenders.get(c["unp"])
        proc_est_eur = lot_est_eur = None
        if t and t["estimated_value"] is not None:
            proc_est_eur, _ = fx.to_eur(t["estimated_value"], t["currency"], on)
        lot = t["lots"].get(c["lot_no"]) if t else None
        if lot and lot["estimated_value"] is not None:
            lot_est_eur, _ = fx.to_eur(lot["estimated_value"], lot["currency"] or t["currency"], on)
        # value against the closest estimate: the contract's own, else its lot's, else the procedure's
        ref_est = own_est_eur or lot_est_eur or proc_est_eur
        # initial and current compared in euro: they can be in different currencies
        vflag, basis = value_flag(eff_eur, proc_est_eur, own_est_eur, init_eur if init_eur is not None else initial,
                                  cur_eur if current is not None and cur_eur is not None else current, steps)
        amount_eur = {"effective": eff_eur, "estimate": proc_est_eur,
                      "initial": init_eur if init_eur is not None else cur_eur}[basis]
        dflag = "contract_date_implausible" if bad_date else "ok"
        if not bad_date and c["contract_date"] and c["published_at"] and c["contract_date"] > c["published_at"].date():
            dflag = "signed_after_publication"
        c.update({
            "value_current": current, "value_current_currency": current_ccy, "value_initial_eur": init_eur, "value_current_eur": cur_eur,
            "amount_eur": amount_eur, "value_flag": vflag, "date_flag": dflag,
            "fx_rate": rate, "estimated_eur": ref_est, "annex_count": len(chain),
            "estimate_ratio": round(eff_eur / ref_est, 3) if eff_eur and ref_est and ref_est > 0 else None,
            "date_basis": "contract" if c["contract_date"] and not bad_date else ("publication" if on else None),
            "effective_date": on,
        })
        for pos, (eik, name) in enumerate(c.pop("_members")):
            suppliers.append({"contract_id": c["id"], "position": pos, "party_key": party_key(eik, name),
                              "eik": eik if eik and eik_valid(eik) else None, "name": name,
                              "joint": c["awarded_to_group"] or len(c["supplier_keys"]) > 1})
        for sub in c.pop("_subs"):
            subcontracts.append({"contract_id": c["id"], **sub})
        c.pop("supplier_keys")
        c.pop("unp_raw_tender_id")
        out_contracts.append(c)

    # a procedure seen only through contracts gets a synthetic parent (SIGMA step 2b)
    for c in out_contracts:
        if c["unp"] and c["unp"] not in tenders:
            tenders[c["unp"]] = {
                "unp": c["unp"], "tender_id": None, "buyer_eik": c["buyer_eik"],
                "subject": c["subject"], "procedure_type": c["procedure_type"] or "неизвестна",
                "cpv": c["cpv"], "cpv_description": c["cpv_description"],
                "contract_type": c["contract_type"], "estimated_value": None, "estimated_eur": None, "currency": None,
                "is_eu_funded": c["is_eu_funded"], "european_program": c["european_program"],
                "lots_count": None, "submission_deadline": None, "published_at": None,
                "notice_type": None, "is_cancelled": None, "execution_nuts": None,
                "source_day": c["source_day"], "synthetic": True, "lots": {}}
    # status of every lot and procedure: a contract wins over everything; then cancelled; then closed
    # without award (flat noAwarding, OCDS award "unsuccessful"); then still open (deadline ahead or
    # published within a year of the last data day); else no contract published
    unawarded |= {(tid2unp[t], n) for t, n in unawarded_tid if t in tid2unp}
    contracted = {(c["unp"], c["lot_no"]) for c in out_contracts if c["unp"]}
    with_contract = {u for u, _ in contracted}
    ref = dt.date.fromisoformat(str(last_day)) if last_day else dt.date.today()
    for u, n in unawarded:
        if u in tenders and n is not None and n not in tenders[u]["lots"]:
            tenders[u]["lots"][n] = {"title": None, "estimated_value": None, "currency": None, "cpv": None}
    for t in tenders.values():
        u = t["unp"]
        recent = (t["submission_deadline"] and t["submission_deadline"].date() >= ref) or \
                 (t["published_at"] and (ref - t["published_at"].date()).days <= 365)
        t["state"] = ("contracted" if u in with_contract else "cancelled" if t["is_cancelled"]
                      else "unawarded" if any(x == u for x, _ in unawarded) else "open" if recent else "no_contract")
        for n, l in t["lots"].items():
            l["status"] = ("contracted" if (u, n) in contracted else "unawarded" if (u, n) in unawarded
                           else "cancelled" if t["is_cancelled"] else "open" if recent else "no_contract")
    lots = []
    for t in tenders.values():
        on = t["published_at"].date() if t["published_at"] else dt.date.fromisoformat(str(t["source_day"]))
        t["estimated_eur"] = fx.to_eur(t["estimated_value"], t["currency"], on)[0]
        for n, l in t.pop("lots").items():
            l_eur = fx.to_eur(l["estimated_value"], l["currency"] or t["currency"], on)[0]
            lots.append({"unp": t["unp"], "lot_no": n, **l, "estimated_eur": l_eur})
    for eik, b in buyers.items():
        b.pop("_names", None)
        b.update(places.get(eik) or {"locality": None, "postal_code": None, "nuts": None})
    for t in tenders.values():
        t.pop("synthetic_header", None)
    # the pointers of a replaced contract lead to the correction that replaced it
    for s in src:
        if s["entity"] == "contract":
            while s["ref"] in replaced:
                s["ref"] = replaced[s["ref"]]
    for a in awards.values():
        a["tender_id"] = a["tender_id"] or (tenders.get(a["unp"]) or {}).get("tender_id")
    st["ocds_added"] = ocds_added
    check_accounting(st)
    return {"buyer": list(buyers.values()), "tender": list(tenders.values()), "lot": lots,
            "contract": out_contracts, "contract_supplier": suppliers, "amendment": amendments,
            "subcontract": subcontracts, "source_record": src, "award": list(awards.values()),
            "notice": list(notices.values()), "annex_orphan": orphans, "_stats": dict(st)}


def check_accounting(st):
    """Every input row of the flat files is counted exactly once in one of the outcomes; a row that went
    nowhere (a code path that forgets a record) stops the build instead of disappearing (audit 27.09.2026)."""
    parts = {"tenders": ("tenders_used", "tenders_without_unp"),
             "contracts": ("contracts_no_award", "contracts_new", "contracts_republished", "contracts_older",
                           "contracts_corrected", "contracts_corrected_older"),
             "annexes": ("annexes_number", "annexes_number_lot", "annexes_spelling", "annexes_start_value", "annexes_orphan")}
    bad = {k: (st.get(f"in_{k}", 0), sum(st.get(p, 0) for p in ps)) for k, ps in parts.items()
           if st.get(f"in_{k}", 0) != sum(st.get(p, 0) for p in ps)}
    if bad:
        raise AssertionError(f"input rows not accounted for (in, out): {bad}")


def _buyer(r, buyers):
    eik = clean_eik(r.get("buyerRegistryNumber"))
    if not eik:
        return None
    b = buyers.get(eik)
    name = text(r.get("buyerName"))
    if not b:
        b = buyers[eik] = {"eik": eik, "eik_valid": eik_valid(eik), "name": name,
                           "type": text(r.get("buyerType")),
                           "main_activity": text(r.get("buyerMainActivity")), "_names": defaultdict(int)}
    if name:
        b["_names"][name] += 1
        b["name"] = max(b["_names"].items(), key=lambda kv: (kv[1], kv[0]))[0]  # modal name
    return eik


def _tender_row(r, day, tenders, buyers):
    unp = text(r.get("uniqueProcurementNumber"))
    if not unp:
        return None
    buyer = _buyer(r, buyers)
    pub = ts_of(r.get("publicationDate"))
    t = tenders.get(unp)
    if r.get("isLot") == "Да" or text(r.get("lotIdentifier")):
        if not t:
            t = tenders[unp] = _tender_header(r, day, buyer, pub)
            t["synthetic_header"] = True
        n = lot_no(r.get("lotIdentifier"))
        if n is not None:
            t["lots"][n] = {"title": text(r.get("lotTenderName")) or text(r.get("subject")),
                            "estimated_value": num(r.get("estimatedValue")),
                            "currency": text(r.get("currency")), "cpv": text(r.get("mainCpvCode"))}
        return unp
    if t and not t.get("synthetic_header") and t["published_at"] and pub and pub < t["published_at"]:
        return unp  # keep the latest header
    lots = t["lots"] if t else {}
    tenders[unp] = _tender_header(r, day, buyer, pub)
    tenders[unp]["lots"] = lots
    return unp


def _tender_header(r, day, buyer, pub):
    return {
        "unp": text(r.get("uniqueProcurementNumber")), "tender_id": text(r.get("tenderId")),
        "buyer_eik": buyer, "subject": text(r.get("subject")),
        "procedure_type": text(r.get("procedureType")), "cpv": text(r.get("mainCpvCode")),
        "cpv_description": text(r.get("mainCpvDescription")),
        "contract_type": text(r.get("typeOfContract")),
        "estimated_value": num(r.get("estimatedValue")), "currency": text(r.get("currency")),
        "is_eu_funded": flag(r.get("isEuFunded")), "european_program": text(r.get("europeanProgram")),
        "lots_count": r.get("lotsCount"), "submission_deadline": ts_of(r.get("submissionDeadline")),
        "published_at": pub, "notice_type": text(r.get("noticeType")),
        "is_cancelled": flag(r.get("isCancelled")), "execution_nuts": text(r.get("executionPlaceNuts")),
        "source_day": day, "synthetic": False, "lots": {},
    }


def assign_annexes(annexes, contracts, st):
    """Each annex record to exactly one contract. The annexes of one (УНП, number, lot of the annex) go together;
    their contract is, in order:
      1. the only contract with that УНП and number (the annex's lot is not needed and is often numbered
         differently: 8 873 annexes were lost to that before v5), or among several the one of the annex's lot;
      2. the only contract whose number is spelled the same way (number_key: „Договор № 49-209“, „49-209/23.11.20“);
      3. the only contract of the procedure whose value is the starting value of the first annex (ЦАИС ЕОП
         writes its own contract id into many 2020-2022 annexes, the contract keeps the buyer's number).
    Anything else is an orphan: kept (annex_orphan) and shown with the procedure, never silently dropped.
    Returns ({contract id: [(ts, day, row)]}, [orphan rows])."""
    by_num, by_key, by_unp = defaultdict(list), defaultdict(list), defaultdict(list)
    for c in contracts:
        by_num[(c["unp"], c["contract_number"])].append(c)
        if number_key(c["contract_number"]):
            by_key[(c["unp"], number_key(c["contract_number"]))].append(c)
        by_unp[c["unp"]].append(c)
    groups = defaultdict(list)
    for (unp, number), rows in annexes.items():
        for a in rows:
            groups[(unp, number, lot_no(a[2].get("lotIdentifier")))].append(a)
    assigned, orphans = defaultdict(list), []
    for (unp, number, lot), rows in groups.items():
        rows.sort(key=lambda a: (a[0] or dt.datetime.min, a[1]))
        target, how = None, None
        same = by_num.get((unp, number), [])
        if len(same) == 1:
            target, how = same[0], "number"
        elif same:
            of_lot = [c for c in same if c["lot_no"] == lot]
            target, how = (of_lot[0], "number_lot") if len(of_lot) == 1 else (None, None)
        if not target and number_key(number):
            spelled = by_key.get((unp, number_key(number)), [])
            target, how = (spelled[0], "spelling") if len(spelled) == 1 else (None, None)
        if not target and unp:
            start = num(rows[0][2].get("lastContractValue"))
            if start:
                hits = [c for c in by_unp.get(unp, []) if c["value_initial"] is not None and
                        (abs(c["value_initial"] - start) < 0.01 or abs(c["value_initial"] / BGN_PER_EUR - start) < 0.01)]
                target, how = (hits[0], "start_value") if len(hits) == 1 else (None, None)
        if target:
            assigned[target["id"]] += rows
            st[f"annexes_{how}"] += len(rows)
        else:
            st["annexes_orphan"] += len(rows)
            for ts, day, a in rows:
                orphans.append({"unp": unp, "contract_number": number, "lot_no": lot, "published_at": ts, "source_day": day,
                                "last_value": num(a.get("lastContractValue")), "current_value": num(a.get("currentContractValue")),
                                "currency": text(a.get("contractCurrency")), "reason": text(a.get("changeReason"))})
    return assigned, orphans


def _notice(r, day, kind, notices):
    """Every notice of the flat files once (the latest publication of its id): the procedure page lists them all
    and the completeness check compares the two (eop_offers.mismatches)."""
    nid = text(r.get("noticeId"))
    if not nid:
        return
    prev = notices.get(nid)
    if prev and prev["source_day"] > day:
        return
    notices[nid] = {"notice_id": nid, "unp": text(r.get("uniqueProcurementNumber")), "kind": kind,
                    "notice_type": text(r.get("noticeType")), "published_at": ts_of(r.get("publicationDate")), "source_day": day}


def _award_row(r, day, awards):
    """A lot closed without award (noAwarding): the award notice with its number, date and offer counts."""
    lot = lot_no(r.get("lotIdentifier"))
    key = (text(r.get("noticeId")) or f"{day}/{text(r.get('uniqueProcurementNumber'))}", lot)
    prev = awards.get(key)
    if prev and prev["source_day"] > day:
        return
    awards[key] = {"unp": text(r.get("uniqueProcurementNumber")), "tender_id": text(r.get("tenderId")), "lot_no": lot,
                   "notice_id": text(r.get("noticeId")), "notice_type": text(r.get("noticeType")),
                   "published_at": ts_of(r.get("publicationDate")), "source_day": day,
                   "offers_count": r.get("offersCount"), "sme_offers_count": r.get("smeOffersCount"),
                   "disqualified_offers_count": r.get("disqualifiedOffersCount"), "link_oj": text(r.get("linkToOjEu"))}


def _same_contract(prev, value, keys):
    """A later record of the same number, lot and signing date is a correction of `prev` when the value or a
    supplier is the same (a typo in the ЕИК, a value, a name fixed in a new notice)."""
    if value is not None and prev["value_initial"] is not None and abs(value - prev["value_initial"]) < 0.01:
        return True
    return bool((set(keys) & set(prev["supplier_keys"])) - {"unknown"})


def _contract_row(r, day, contracts, buyers, idents=None, replaced=None):
    """One contract record. Returns (contract id, how): new | republished (same id, a later notice) | older (an
    earlier notice of what we hold) | corrected (a later notice of the same contract with other suppliers or
    value: it replaces the earlier one) | corrected_older (an earlier version of a corrected contract)."""
    idents = {} if idents is None else idents
    replaced = {} if replaced is None else replaced
    unp = text(r.get("uniqueProcurementNumber"))
    number = text(r.get("contractNumber"))
    members = split_members(r.get("supplierName"), r.get("supplierRegisterNumber"))
    keys = [party_key(e, n) for e, n in members]
    lot = lot_no(r.get("lotIdentifier"))
    cid = contract_id(unp, number, str(lot or ""), keys)
    pub = ts_of(r.get("publicationDate"))
    prev = contracts.get(cid)
    if prev and prev["published_at"] and pub and pub < prev["published_at"]:
        return cid, "older"  # republished: keep the latest notice
    how = "republished" if prev else "new"
    signed, value, notice = day_of(r.get("contractDate")), num(r.get("contractValue")), text(r.get("noticeId"))
    ident = (unp, number, lot, signed) if unp and number and signed and not flag(r.get("isFrameworkAgreement")) else None
    other = idents.get(ident) if ident else None
    # a different notice (records of one notice are separate contracts), the same contract by value or supplier
    if other and other != cid and other in contracts and contracts[other]["notice_id"] != notice \
            and _same_contract(contracts[other], value, keys):
        if contracts[other]["published_at"] and pub and pub < contracts[other]["published_at"]:
            replaced[cid] = other
            return other, "corrected_older"
        del contracts[other]
        replaced[other] = cid
        how = "corrected"
    if ident:
        idents[ident] = cid
    subs = []
    if flag(r.get("hasSubcontractors")):
        for e, n in split_members(r.get("subcontractorName"), r.get("subcontractorRegistryNumber")):
            subs.append({"party_key": party_key(e, n), "eik": e if e and eik_valid(e) else None,
                         "name": n, "percent": num(r.get("subcontractingPercent")),
                         "amount": num(r.get("subcontractingAmount"))})
    contracts[cid] = {
        "id": cid, "source": "eop", "source_day": day, "notice_id": text(r.get("noticeId")),
        "unp": unp, "unp_raw_tender_id": text(r.get("tenderId")), "contract_number": number,
        "lot_no": lot, "buyer_eik": _buyer(r, buyers),
        "supplier_display": clean_name(text(r.get("supplierName"))),
        "awarded_to_group": bool(flag(r.get("awardedToGroup"))), "supplier_keys": keys,
        "subject": text(r.get("contractSubject")) or text(r.get("tenderName")),
        "tender_name": text(r.get("tenderName")),
        "procedure_type": text(r.get("procedureType")), "cpv": text(r.get("tenderMainCpv")),
        "cpv_description": text(r.get("tenderMainCpvDescription")),
        "contract_type": text(r.get("typeOfContract")),
        "contract_date": day_of(r.get("contractDate")), "published_at": pub,
        "value_initial": num(r.get("contractValue")),
        "currency": text(r.get("contractCurrency")) or text(r.get("currency")),
        "estimated_value": num(r.get("estimatedValue")), "estimate_currency": text(r.get("currency")),
        "offers_count": r.get("offersCount"), "sme_offers_count": r.get("smeOffersCount"),
        "disqualified_offers_count": r.get("disqualifiedOffersCount"),
        "is_eu_funded": flag(r.get("isEuFunded")), "european_program": text(r.get("europeanProgram")),
        "is_framework": flag(r.get("isFrameworkAgreement")) is True,
        "framework_contract": text(r.get("frameworkAgreementContract")),
        "linked_tenders": text(r.get("linkedTenders")),
        "direct_award_justification": text(r.get("directAwardJustification")),
        "award_method": text(r.get("awardMethod")), "legal_basis": text(r.get("legalBasis")),
        "has_subcontractors": flag(r.get("hasSubcontractors")),
        "supplier_nuts": text(r.get("supplierNutsCode")),
        "supplier_size": text(r.get("supplierCompanySizeCode")),
        "contract_period_days": r.get("contractPeriod"),
        "_members": members, "_subs": subs,
    }
    return cid, how


def _ocds_contracts(rel, day):
    """Contracts of an OCDS release, in the flat contract shape (only used when the flat feed lacks them)."""
    parties = {p.get("id"): p for p in rel.get("parties") or []}
    buyer = parties.get((rel.get("buyer") or {}).get("id")) or rel.get("buyer") or {}
    buyer_eik = clean_eik((buyer.get("identifier") or {}).get("id"))
    awards = {a.get("id"): a for a in rel.get("awards") or []}
    tender = rel.get("tender") or {}
    stats = defaultdict(dict)
    for s in (rel.get("bids") or {}).get("statistics") or []:
        stats[s.get("relatedLot")][s.get("measure")] = s.get("value")
    out = []
    for c in rel.get("contracts") or []:
        a = awards.get(c.get("awardID")) or {}
        members = []
        for s in a.get("suppliers") or []:
            p = parties.get(s.get("id")) or {}
            members.append((clean_eik((p.get("identifier") or {}).get("id")), text(s.get("name"))))
        keys = [party_key(e, n) for e, n in members]
        lot_ref = (a.get("relatedLots") or [None])[0]
        n = lot_no(lot_ref)
        value = c.get("value") or {}
        st = stats.get(lot_ref, {})
        out.append({
            "id": contract_id(None, text(c.get("id")), str(n or ""), keys), "source": "ocds",
            "source_day": day, "notice_id": None, "unp": None,
            "unp_raw_tender_id": text(tender.get("id")), "contract_number": text(c.get("id")),
            "lot_no": n, "buyer_eik": buyer_eik,
            "supplier_display": "; ".join(n for _, n in members if n) or None,
            "awarded_to_group": len(members) > 1, "supplier_keys": keys,
            "subject": text(c.get("title")) or text(tender.get("title")), "tender_name": text(tender.get("title")),
            "procedure_type": text(tender.get("procurementMethod")),
            "cpv": next((i["classification"]["id"] for i in tender.get("items") or []
                         if (i.get("classification") or {}).get("id")), None),
            "cpv_description": None, "contract_type": text(tender.get("mainProcurementCategory")),
            "contract_date": day_of(c.get("dateSigned")), "published_at": ts_of(rel.get("date")),
            "value_initial": num(value.get("amount")), "currency": text(value.get("currency")),
            "estimated_value": num((tender.get("value") or {}).get("amount")),
            "estimate_currency": text((tender.get("value") or {}).get("currency")),
            "offers_count": st.get("bids"), "sme_offers_count": st.get("smeBids"),
            "disqualified_offers_count": None, "is_eu_funded": None, "european_program": None,
            "is_framework": False, "framework_contract": None, "linked_tenders": None,
            "direct_award_justification": None, "award_method": None,
            "legal_basis": text((tender.get("legalBasis") or {}).get("id")),
            "has_subcontractors": None, "supplier_nuts": None, "supplier_size": None,
            "contract_period_days": None, "_members": members, "_subs": [],
        })
    return out

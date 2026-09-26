"""Read-only queries against schema `live`. Money sums follow docs/methodology.md:
amount_eur, frameworks excluded from sums, one contract counted once."""
import datetime as dt
import time
from functools import lru_cache

from ingest.db import connect

SUM = "sum(c.amount_eur) FILTER (WHERE NOT c.is_framework)"
# year charts stop at next year: a later date is a data-entry error in the source
YEARS = "c.effective_date >= '2020-01-01' AND c.effective_date < make_date(extract(year FROM current_date)::int + 2, 1, 1)"


def rows(sql, *args):
    with connect() as conn:
        cur = conn.execute(sql, args)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(sql, *args):
    r = rows(sql, *args)
    return r[0] if r else None


_cache = {}


def cached(key, fn, ttl=600):
    """Per-process cache, invalidated when a new build is published or after ttl seconds."""
    stamp = one("SELECT at FROM ops.published WHERE id = 1")
    stamp = stamp["at"] if stamp else None
    hit = _cache.get(key)
    if hit and hit[0] == stamp and time.monotonic() - hit[1] < ttl:
        return hit[2]
    val = fn()
    _cache[key] = (stamp, time.monotonic(), val)
    return val


def freshness():
    return one("SELECT at, eop_last_day, tr_last_read FROM ops.published WHERE id = 1") or {}


# ---------- dashboard ----------

def dashboard():
    return {
        "totals": one(f"""SELECT count(*) contracts, {SUM.replace('c.', '')} eur, count(DISTINCT buyer_eik) buyers,
                (SELECT count(DISTINCT party_key) FROM live.contract_supplier) companies FROM live.contract c"""),
        "by_year": rows(f"""SELECT extract(year FROM effective_date)::int y, count(*) n, round({SUM}) eur,
                round(100.0 * count(*) FILTER (WHERE offers_count = 1)
                      / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct
                FROM live.contract c WHERE {YEARS} GROUP BY 1 ORDER BY 1"""),
        "procedures": rows(f"""SELECT coalesce(procedure_type, 'неизвестна') p, count(*) n, round({SUM}) eur,
                round(100.0 * count(*) FILTER (WHERE offers_count = 1)
                      / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct
                FROM live.contract c GROUP BY 1 ORDER BY n DESC LIMIT 6"""),
        "sectors": rows(f"""WITH s AS (SELECT left(cpv, 2) d, count(*) n, {SUM} eur,
                  round(100.0 * count(*) FILTER (WHERE offers_count = 1)
                        / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct
                  FROM live.contract c WHERE cpv ~ '^[0-9]{{2}}' GROUP BY 1)
                SELECT s.d, round(s.eur) eur, s.n, s.single_pct,
                  coalesce((SELECT c.cpv_description FROM live.contract c WHERE c.cpv LIKE s.d || '000000%%' LIMIT 1),
                           'CPV ' || s.d) name
                FROM s ORDER BY eur DESC NULLS LAST LIMIT 6"""),
        "top_buyers": rows("""SELECT b.name, s.eik, s.contracts n, round(s.amount_eur) eur FROM live.buyer_stats s
                JOIN live.buyer b USING (eik) ORDER BY s.amount_eur DESC NULLS LAST LIMIT 6"""),
        "top_companies": rows("""SELECT c.name, c.key, s.contracts n, round(s.amount_eur) eur FROM live.company_stats s
                JOIN live.company c USING (key) ORDER BY s.amount_eur DESC NULLS LAST LIMIT 6"""),
        "recent_big": rows("""SELECT c.id, c.effective_date, c.subject, b.name buyer, c.buyer_eik, c.supplier_display supplier,
                round(c.amount_eur) eur, c.offers_count FROM live.contract c LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
                WHERE c.effective_date >= current_date - 60 AND NOT c.is_framework AND c.value_flag = 'ok'
                ORDER BY c.amount_eur DESC NULLS LAST LIMIT 8"""),
    }


# ---------- company ----------

def company(key):
    c = one("""SELECT c.*, s.contracts, s.amount_eur, s.buyers, s.joint_contracts, s.frameworks,
                      s.contracts_without_value, s.first_contract, s.last_contract
               FROM live.company c LEFT JOIN live.company_stats s USING (key) WHERE c.key = %s""", key)
    if not c:
        return None
    k = key
    c["by_year"] = rows(f"""SELECT extract(year FROM c.effective_date)::int y, count(DISTINCT c.id) n, round({SUM}) eur
        FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id
        WHERE s.party_key = %s AND {YEARS} GROUP BY 1 ORDER BY 1""", k)
    c["offers"] = rows("""SELECT CASE WHEN c.offers_count IS NULL THEN 'unknown' WHEN c.offers_count >= 4 THEN '4+'
        ELSE c.offers_count::text END k, count(DISTINCT c.id) n
        FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id WHERE s.party_key = %s GROUP BY 1""", k)
    c["cpv"] = rows(f"""SELECT c.cpv_description d, count(DISTINCT c.id) n, round({SUM}) eur
        FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id WHERE s.party_key = %s
        GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 5""", k)
    c["buyers_top"] = rows(f"""SELECT b.name, c.buyer_eik eik, count(DISTINCT c.id) n, round({SUM}) eur
        FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
        WHERE s.party_key = %s GROUP BY 1, 2 ORDER BY eur DESC NULLS LAST LIMIT 6""", k)
    c["tags"] = rows("""SELECT t.code, d.label, d.kind, t.reason FROM live.tag t JOIN live.tag_def d USING (code)
        WHERE t.entity_type = 'company' AND t.entity_id = %s
        UNION ALL SELECT e.code, e.code, 'editorial', e.note FROM ed.tag e
        WHERE e.entity_type = 'company' AND e.entity_id = %s AND e.removed_at IS NULL""", k, k)
    c["indicators"] = indicators(k)
    c["articles"] = articles("company", k)
    node = "c:" + c["eik"][:9] if c.get("eik") else None
    c["roles"] = rows("""SELECT e.holder, e.holder_kind, e.holder_name, e.role, e.view, e.share, e.share_pct,
        e.valid_from, e.valid_to, e.uncertain_after, e.entry_no, n.ref person_id,
        (SELECT count(DISTINCT e2.company) FROM live.edge e2 WHERE e2.holder = e.holder AND e2.company <> e.company) others
        FROM live.edge e LEFT JOIN live.node n ON n.id = e.holder WHERE e.company = %s
        ORDER BY e.holder, e.valid_from""", node) if node else []
    return c


def articles(kind, ref):
    return rows("""SELECT url, title, source, published_at FROM ed.article WHERE entity_type = %s AND entity_id = %s
                   AND status = 'confirmed' ORDER BY published_at DESC NULLS LAST LIMIT 20""", kind, ref)


def indicators(key):
    """The two company indicators of methodology 4, over the 3-year window."""
    r = one("""SELECT count(DISTINCT c.id) FILTER (WHERE c.offers_count = 1) single,
                      count(DISTINCT c.id) FILTER (WHERE c.offers_count IS NOT NULL) known
               FROM live.contract_supplier s JOIN live.contract c ON c.id = s.contract_id
               WHERE s.party_key = %s AND c.effective_date >= current_date - interval '3 years'""", key)
    b = rows("""SELECT c.buyer_eik, bu.name, sum(c.amount_eur) v FROM live.contract_supplier s
               JOIN live.contract c ON c.id = s.contract_id LEFT JOIN live.buyer bu ON bu.eik = c.buyer_eik
               WHERE s.party_key = %s AND c.effective_date >= current_date - interval '3 years'
                 AND c.amount_eur IS NOT NULL AND NOT c.is_framework GROUP BY 1, 2 ORDER BY v DESC""", key)
    total = sum(float(x["v"]) for x in b)
    return {"single": r["single"], "known": r["known"],
            "top_buyer": b[0]["name"] if b else None,
            "top_share": (float(b[0]["v"]) / total) if b and total else None, "buyers_n": len(b)}


def contracts_of(key=None, buyer=None, person_companies=None, limit=50, offset=0):
    where, args = [], []
    if key:
        where.append("c.id IN (SELECT contract_id FROM live.contract_supplier WHERE party_key = %s)")
        args.append(key)
    if buyer:
        where.append("c.buyer_eik = %s")
        args.append(buyer)
    sql = f"""SELECT c.id, c.unp, c.effective_date, c.subject, c.buyer_eik, b.name buyer, c.supplier_display supplier,
                round(c.amount_eur) eur, c.offers_count, c.value_flag, c.is_framework, c.annex_count, c.awarded_to_group,
                c.estimate_ratio
              FROM live.contract c LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
              WHERE {' AND '.join(where) or 'true'} ORDER BY c.effective_date DESC NULLS LAST, c.id LIMIT %s OFFSET %s"""
    return rows(sql, *args, limit, offset)


# ---------- person ----------

def person(pid):
    p = one("SELECT * FROM live.person WHERE id = %s", pid)
    if not p:
        return None
    holder = "p:" + pid
    p["roles"] = rows("""SELECT e.company, substr(e.company, 3) eik, co.name company_name, co.legal_form, e.role, e.view,
        e.share, e.share_pct, e.valid_from, e.valid_to, e.uncertain_after, e.entry_no, co.key company_key,
        s.contracts, s.amount_eur
        FROM live.edge e LEFT JOIN live.company co ON co.key = 'eik:' || substr(e.company, 3)
        LEFT JOIN live.company_stats s ON s.key = co.key
        WHERE e.holder = %s ORDER BY e.valid_from""", holder)
    p["modes"] = person_modes(holder)
    p["figure"] = one("""SELECT * FROM ed.public_figure WHERE person_id = %s AND status = 'confirmed'
                         ORDER BY reviewed_at DESC LIMIT 1""", pid)
    p["articles"] = articles("person", pid)
    p["contracts"] = rows("""SELECT DISTINCT ON (c.effective_date, c.id) c.id, c.unp, c.effective_date, c.subject,
        b.name buyer, c.buyer_eik, round(c.amount_eur) eur, c.offers_count, co.name company_name, co.key company_key
        FROM live.edge e JOIN live.node_contract k ON k.node = e.company JOIN live.contract c ON c.id = k.contract_id
        LEFT JOIN live.buyer b ON b.eik = c.buyer_eik LEFT JOIN live.company co ON co.key = 'eik:' || substr(e.company, 3)
        WHERE e.holder = %s AND c.effective_date >= e.valid_from AND (e.valid_to IS NULL OR c.effective_date < e.valid_to)
        ORDER BY c.effective_date DESC, c.id LIMIT 12""", holder)
    return p


def person_modes(holder):
    """Contracts of the person's companies under the three time modes (methodology 2); each counted once."""
    base = """FROM live.edge e JOIN live.node_contract k ON k.node = e.company JOIN live.contract c ON c.id = k.contract_id
              WHERE e.holder = %s"""
    q = lambda extra: one(f"""SELECT count(DISTINCT c.id) n,
        (SELECT sum(x.amount_eur) FROM live.contract x WHERE NOT x.is_framework AND x.id IN (SELECT DISTINCT c.id {base} {extra})) eur
        {base} {extra}""", holder, holder)
    return {
        "at_contract_date": q("AND c.effective_date >= e.valid_from AND (e.valid_to IS NULL OR c.effective_date < e.valid_to)"),
        "all_history": q(""),
        "current": q("AND e.valid_to IS NULL AND e.uncertain_after IS NULL"),
    }


# ---------- network ----------

def network(focus, view="all", at=None, depth=2, max_nodes=500):
    """Bounded traversal around a node (methodology 6). With `at`, every edge on a path must be valid
    on that date. Returns edges plus whether the node/depth cap was reached."""
    views = {"all": ("ownership", "management"), "ownership": ("ownership",), "management": ("management",)}[view]
    valid = "AND e.valid_from <= %(at)s AND (e.valid_to IS NULL OR e.valid_to > %(at)s)" if at else ""
    sql = f"""
      WITH RECURSIVE walk(node, d) AS (
        SELECT %(focus)s::text, 0
        UNION
        SELECT CASE WHEN e.holder = w.node THEN e.company ELSE e.holder END, w.d + 1
        FROM walk w JOIN live.edge e ON (e.holder = w.node OR e.company = w.node)
        WHERE w.d < %(depth)s AND e.view = ANY(%(views)s) {valid}
      ), nodes AS (SELECT node, min(d) d FROM walk GROUP BY node LIMIT %(cap)s)
      SELECT e.holder, e.company, e.holder_kind, e.holder_name, e.role, e.share, e.valid_from, e.valid_to,
             e.uncertain_after, hn.ref holder_ref, cn.label company_name, cn.ref company_ref,
             (SELECT count(*) FROM live.node_contract k WHERE k.node = e.company) company_contracts,
             (SELECT count(*) FROM live.node_contract k WHERE k.node = e.holder) holder_contracts
      FROM live.edge e JOIN nodes a ON a.node = e.holder JOIN nodes b ON b.node = e.company
      LEFT JOIN live.node hn ON hn.id = e.holder LEFT JOIN live.node cn ON cn.id = e.company
      WHERE e.view = ANY(%(views)s) {valid}"""
    args = {"focus": focus, "depth": depth, "views": list(views), "at": at, "cap": max_nodes + 1}
    with connect() as conn:
        cur = conn.execute(sql, args)
        cols = [d.name for d in cur.description]
        edges = [dict(zip(cols, r)) for r in cur.fetchall()]
        n = conn.execute(f"""WITH RECURSIVE walk(node, d) AS (SELECT %(focus)s::text, 0 UNION
            SELECT CASE WHEN e.holder = w.node THEN e.company ELSE e.holder END, w.d + 1
            FROM walk w JOIN live.edge e ON (e.holder = w.node OR e.company = w.node)
            WHERE w.d < %(d1)s AND e.view = ANY(%(views)s) {valid})
            SELECT count(DISTINCT node) FILTER (WHERE d <= %(depth)s), count(DISTINCT node) FILTER (WHERE d = %(d1)s)
            FROM walk""", {**args, "d1": depth + 1}).fetchone()
    return {"edges": edges, "capped": n[0] > max_nodes, "beyond": n[1] > 0, "nodes": min(n[0], max_nodes)}


def lineage(node, view="all", at=None, exclude=()):
    """Children of one node in the lineage tree: every node linked to it by a registry edge in the
    chosen view (valid on `at` when given), except the nodes already on the path from the root.
    One row per neighbour, with all the roles that link them and what lies behind it."""
    views = {"all": ["ownership", "management"], "ownership": ["ownership"], "management": ["management"]}[view]
    valid = "AND e.valid_from <= %(at)s AND (e.valid_to IS NULL OR e.valid_to > %(at)s)" if at else ""
    sql = f"""
      WITH l AS (
        SELECT CASE WHEN e.holder = %(n)s THEN e.company ELSE e.holder END AS id,
               e.holder = %(n)s AS down, e.role, e.share, e.valid_from, e.valid_to, e.uncertain_after, e.holder_kind
        FROM live.edge e WHERE (e.holder = %(n)s OR e.company = %(n)s) AND e.view = ANY(%(views)s) {valid}
      )
      SELECT l.id, max(nd.kind) kind, coalesce(max(nd.label), max(e2.holder_name)) name, max(nd.ref) ref,
             bool_or(l.down) down,
             json_agg(json_build_object('role', l.role, 'share', l.share, 'from', l.valid_from, 'to', l.valid_to,
                      'open', l.uncertain_after IS NULL, 'down', l.down) ORDER BY l.valid_from) roles,
             max(cs.contracts) contracts, max(cs.amount_eur) eur,
             (SELECT count(DISTINCT CASE WHEN x.holder = l.id THEN x.company ELSE x.holder END)
                FROM live.edge x WHERE (x.holder = l.id OR x.company = l.id) AND x.view = ANY(%(views)s)) - 1 AS more,
             bool_or(l.valid_to IS NULL AND l.uncertain_after IS NULL) active
      FROM l LEFT JOIN live.node nd ON nd.id = l.id
      LEFT JOIN live.company_stats cs ON cs.key = nd.ref AND nd.kind = 'company'
      LEFT JOIN LATERAL (SELECT holder_name FROM live.edge WHERE holder = l.id LIMIT 1) e2 ON true
      WHERE l.id <> ALL(%(ex)s)
      GROUP BY l.id ORDER BY active DESC, max(cs.amount_eur) DESC NULLS LAST, name"""
    return rows_named(sql, {"n": node, "views": views, "at": at, "ex": list(exclude)})


def rows_named(sql, params):
    with connect() as conn:
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def node_info(node):
    return one("""SELECT n.id, n.kind, n.label name, n.ref, cs.contracts, cs.amount_eur eur FROM live.node n
                  LEFT JOIN live.company_stats cs ON cs.key = n.ref AND n.kind = 'company' WHERE n.id = %s""", node)


# ---------- buyer / contract / tender ----------

def buyer(eik):
    b = one("SELECT b.*, s.* FROM live.buyer b LEFT JOIN live.buyer_stats s USING (eik) WHERE b.eik = %s", eik)
    if not b:
        return None
    b["by_year"] = rows(f"""SELECT extract(year FROM effective_date)::int y, count(*) n, round({SUM}) eur,
        round(100.0 * count(*) FILTER (WHERE offers_count = 1) / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct
        FROM live.contract c WHERE buyer_eik = %s AND {YEARS} GROUP BY 1 ORDER BY 1""", eik)
    b["suppliers_top"] = rows(f"""SELECT s.party_key key, max(s.name) name, count(DISTINCT c.id) n, round({SUM}) eur
        FROM live.contract c JOIN live.contract_supplier s ON s.contract_id = c.id WHERE c.buyer_eik = %s
        GROUP BY 1 ORDER BY eur DESC NULLS LAST LIMIT 8""", eik)
    b["procedures"] = rows(f"""SELECT coalesce(procedure_type, 'неизвестна') p, count(*) n, round({SUM}) eur,
        round(100.0 * count(*) FILTER (WHERE offers_count = 1) / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct
        FROM live.contract c WHERE buyer_eik = %s GROUP BY 1 ORDER BY n DESC LIMIT 6""", eik)
    b["offers"] = rows("""SELECT CASE WHEN offers_count IS NULL THEN 'unknown' WHEN offers_count >= 4 THEN '4+'
        ELSE offers_count::text END k, count(*) n FROM live.contract WHERE buyer_eik = %s GROUP BY 1""", eik)
    return b


def contract(cid):
    c = one("""SELECT c.*, b.name buyer_name, t.subject tender_subject, t.estimated_eur tender_estimate_eur, t.tender_id unp_tender_id
               FROM live.contract c LEFT JOIN live.buyer b ON b.eik = c.buyer_eik LEFT JOIN live.tender t ON t.unp = c.unp
               WHERE c.id = %s""", cid)
    if not c:
        return None
    c["suppliers"] = rows("""SELECT s.*, co.name co_name FROM live.contract_supplier s LEFT JOIN live.company co ON co.key = s.party_key
                             WHERE s.contract_id = %s ORDER BY position""", cid)
    c["amendments"] = rows("SELECT * FROM live.amendment WHERE contract_id = %s ORDER BY published_at", cid)
    c["subcontracts"] = rows("SELECT * FROM live.subcontract WHERE contract_id = %s", cid)
    c["source"] = one("SELECT day, kind, key, sha256 FROM ops.eop_file WHERE day = %s AND kind = %s",
                      c["source_day"], "contracts" if c["source"] == "eop" else "ocds")
    c["records"] = raw_records("contract", cid)
    c["ocds"] = raw_records("ocds", c["unp_tender_id"]) if c.get("unp_tender_id") else []
    return c


def tender(unp):
    t = one("SELECT t.*, b.name buyer_name FROM live.tender t LEFT JOIN live.buyer b ON b.eik = t.buyer_eik WHERE t.unp = %s", unp)
    if not t:
        return None
    t["lots"] = rows("SELECT * FROM live.lot WHERE unp = %s ORDER BY lot_no", unp)
    t["contracts"] = rows("""SELECT c.id, c.lot_no, c.effective_date, c.supplier_display supplier, s0.party_key supplier_key,
        round(c.amount_eur) eur, c.offers_count, c.value_flag, c.estimate_ratio, c.is_framework, c.annex_count
        FROM live.contract c LEFT JOIN live.contract_supplier s0 ON s0.contract_id = c.id AND s0.position = 0
        WHERE c.unp = %s ORDER BY c.lot_no NULLS FIRST, c.effective_date""", unp)
    t["stats"] = one(f"""SELECT count(*) n, round({SUM}) eur FROM live.contract c WHERE c.unp = %s""", unp)
    t["records"] = raw_records("tender", unp)
    t["ocds"] = raw_records("ocds", t["tender_id"]) if t["tender_id"] else []
    return t


@lru_cache(maxsize=8)
def _raw_rows(day, kind):
    from ingest import eop
    return eop.read_rows(day, kind)


def raw_records(entity, ref):
    """Every published version of a record, oldest first, read back from the raw files."""
    out = []
    for r in rows("SELECT day, kind, idx FROM live.source_record WHERE entity = %s AND ref = %s ORDER BY day, idx",
                  entity, ref):
        try:
            out.append({**r, "rec": _raw_rows(r["day"], r["kind"])[r["idx"]]})
        except (IndexError, OSError, ValueError):
            continue
    return out


# ---------- search ----------

LAT = [("sht", "щ"), ("zh", "ж"), ("ch", "ч"), ("sh", "ш"), ("yu", "ю"), ("ya", "я"), ("ts", "ц"),
       ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"),
       ("y", "й"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"),
       ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"), ("c", "ц"), ("w", "в"), ("x", "кс"), ("q", "к")]


def translit(q):
    """Latin -> Bulgarian Cyrillic (Streamlined System, reversed), so 'Ivanov' finds 'Иванов'."""
    s = q.lower()
    for a, b in LAT:
        s = s.replace(a, b)
    return s


def search(q, kind=None, limit=40):
    """Trigram search over live.search_item; each term is its own index-backed query."""
    q = q.strip()
    if not q:
        return []
    terms = [q.upper()]
    if any("a" <= ch.lower() <= "z" for ch in q):
        terms.append(translit(q).upper())
    kinds = [kind] if kind else ["buyer", "company", "person", "tender"]
    parts, args = ["SELECT kind, ref, label, sub, weight, 1.0 sim FROM live.search_item WHERE ref = ANY(%s) AND kind = ANY(%s)"], [[q, "eik:" + q], kinds]
    for t in terms:
        parts.append("SELECT kind, ref, label, sub, weight, similarity(key, %s) sim FROM live.search_item "
                     "WHERE kind = ANY(%s) AND key %% %s")
        args += [t, kinds, t]
        parts.append("SELECT kind, ref, label, sub, weight, 0.35 sim FROM live.search_item "
                     "WHERE kind = ANY(%s) AND key LIKE %s")
        args += [kinds, "%" + t.replace("%", "") + "%"]
    sql = f"""SELECT kind, ref, label, sub, max(sim) sim, max(weight) weight FROM ({' UNION ALL '.join(parts)}) x
              GROUP BY 1, 2, 3, 4 ORDER BY max(sim) DESC, max(weight) DESC LIMIT %s"""
    return rows(sql, *args, limit)


if __name__ == "__main__":
    assert translit("Ivanov") == "иванов" and translit("Shtilianov") == "щилианов"
    print("ok", dt.date.today())

"""Read-only queries against schema `live`. Money sums follow docs/methodology.md:
amount_eur, frameworks excluded from sums, one contract counted once."""
import datetime as dt
import re
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
                        / nullif(count(*) FILTER (WHERE offers_count IS NOT NULL), 0), 1) single_pct,
                  round(100.0 * sum(c.amount_eur) FILTER (WHERE offers_count = 1 AND NOT c.is_framework)
                        / nullif(sum(c.amount_eur) FILTER (WHERE offers_count IS NOT NULL AND NOT c.is_framework), 0), 1) single_eur_pct
                  FROM live.contract c WHERE cpv ~ '^[0-9]{{2}}' GROUP BY 1)
                SELECT s.d, round(s.eur) eur, s.n, s.single_pct, s.single_eur_pct,
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
      ), nodes AS (SELECT node, min(d) d FROM walk GROUP BY node ORDER BY min(d), node LIMIT %(cap)s)
      SELECT e.holder, e.company, e.holder_kind, e.holder_name, e.role, e.share, e.valid_from, e.valid_to,
             e.uncertain_after, hn.ref holder_ref, cn.label company_name, cn.ref company_ref,
             (SELECT count(*) FROM live.node_contract k WHERE k.node = e.company) company_contracts,
             (SELECT amount_eur FROM live.company_stats s WHERE s.key = cn.ref) company_eur,
             (SELECT amount_eur FROM live.company_stats s WHERE s.key = hn.ref AND hn.kind = 'company') holder_eur,
             (SELECT legal_form FROM live.company c2 WHERE c2.key = cn.ref) company_form,
             (SELECT legal_form FROM live.company c2 WHERE c2.key = hn.ref AND hn.kind = 'company') holder_form,
             (SELECT count(*) FROM live.edge x WHERE x.holder = e.holder OR x.company = e.holder) holder_links,
             (SELECT count(*) FROM live.edge x WHERE x.holder = e.company OR x.company = e.company) company_links,
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


def node_info(node):
    return one("""SELECT n.id, n.kind, n.label name, n.ref, cs.contracts, cs.amount_eur eur FROM live.node n
                  LEFT JOIN live.company_stats cs ON cs.key = n.ref AND n.kind = 'company' WHERE n.id = %s""", node)


# ---------- connect: how are these people and companies linked ----------

HUB = 100  # a node with more registry links than this (large boards, holdings) can be skipped on request


def _neighbours(conn, nodes, active):
    cond = "AND valid_to IS NULL AND uncertain_after IS NULL" if active else ""
    adj = {}
    for h, c in conn.execute(f"SELECT holder, company FROM live.edge WHERE (holder = ANY(%s) OR company = ANY(%s)) {cond}",
                             (nodes, nodes)).fetchall():
        adj.setdefault(h, set()).add(c)
        adj.setdefault(c, set()).add(h)
    return adj


def shortest_paths(conn, a, b, active=False, skip_hubs=False, offset=0, limit=50, budget=10.0):
    """Every shortest registry path between two nodes, by breadth-first search from both ends at once
    (each step reads the edges of the smaller frontier). No cap on the number of steps: the search runs
    until the two sides meet or one side has nothing left. The paths themselves can be combinatorially
    many, so they are counted exactly and listed in pages (offset/limit).
    ponytail: a time budget (seconds) stops very long searches; the result says so and can be re-run longer.
    Returns dict(paths, total, skipped, timed_out)."""
    res = {"paths": [], "total": 0, "skipped": [], "timed_out": False}
    if a == b:
        return res
    t0 = time.monotonic()
    dist, preds, front = [{a: 0}, {b: 0}], [{}, {}], [{a}, {b}]
    skipped, meet = set(), set()
    while front[0] and front[1] and not meet:
        if time.monotonic() - t0 > budget:
            res["timed_out"] = True
            break
        side = 0 if len(front[0]) <= len(front[1]) else 1
        adj = _neighbours(conn, list(front[side]), active)
        nxt = set()
        for x in front[side]:
            if skip_hubs and x not in (a, b) and len(adj.get(x, ())) > HUB:
                skipped.add(x)
                continue
            for y in adj.get(x, ()):
                d = dist[side][x] + 1
                if dist[side].get(y, d) < d:
                    continue
                if y not in dist[side]:
                    dist[side][y] = d
                    nxt.add(y)
                preds[side].setdefault(y, set()).add(x)
        front[side] = nxt
        meet = {y for y in nxt if y in dist[1 - side]}
    res["skipped"] = sorted(skipped)
    if not meet:
        return res
    best = min(dist[0][m] + dist[1][m] for m in meet)
    ends = (a, b)
    ways = [{}, {}]

    def count(node, side):  # number of shortest ways from node back to its side's end
        if node == ends[side]:
            return 1
        if node not in ways[side]:
            ways[side][node] = sum(count(p, side) for p in preds[side].get(node, ()))
        return ways[side][node]

    def back(node, side, acc):
        if node == ends[side]:
            yield acc
            return
        for p in sorted(preds[side].get(node, ())):
            yield from back(p, side, acc + [p])

    mids = sorted(x for x in meet if dist[0][x] + dist[1][x] == best)
    res["total"] = sum(count(m, 0) * count(m, 1) for m in mids)
    k = 0
    for m in mids:
        n = count(m, 0) * count(m, 1)
        if k + n <= offset:  # the whole page of this meeting node is before the offset
            k += n
            continue
        for left in back(m, 0, [m]):
            for right in back(m, 1, [m]):
                if k >= offset:
                    res["paths"].append(list(reversed(left)) + right[1:])
                    if len(res["paths"]) >= limit:
                        return res
                k += 1
    return res


def connections(ids, active=False, skip_hubs=False, offset=0, limit=50, budget=10.0):
    """Every shortest path between every pair of the chosen nodes (a page of each), with the roles on each step."""
    pairs = []
    with connect() as conn:
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                r = shortest_paths(conn, ids[i], ids[j], active, skip_hubs, offset, limit, budget)
                pairs.append({"a": ids[i], "b": ids[j], **r})
    nodes = sorted({n for p in pairs for path in p["paths"] for n in path} | set(ids))
    steps = {tuple(sorted((x, y))) for p in pairs for path in p["paths"] for x, y in zip(path, path[1:])}
    info = {r["id"]: r for r in rows("""SELECT n.id, n.kind, coalesce(n.label, (SELECT holder_name FROM live.edge WHERE holder = n.id LIMIT 1)) name,
               n.ref, cs.contracts, cs.amount_eur eur,
               (SELECT count(*) FROM live.edge e WHERE e.holder = n.id OR e.company = n.id) links
        FROM live.node n LEFT JOIN live.company_stats cs ON cs.key = n.ref AND n.kind = 'company' WHERE n.id = ANY(%s)""", nodes)}
    roles = {}
    if steps:
        flat = [x for s in steps for x in s]
        for r in rows("""SELECT holder, company, role, share, valid_from, valid_to, uncertain_after FROM live.edge
                         WHERE holder = ANY(%s) AND company = ANY(%s) ORDER BY valid_from""", flat, flat):
            key = tuple(sorted((r["holder"], r["company"])))
            if key in steps:
                roles.setdefault(key, []).append(r)
    return {"pairs": pairs, "nodes": info, "roles": {f"{k[0]}|{k[1]}": v for k, v in roles.items()}}


def top_buyers(nodes):
    """Main buyer of each company node, by contract value (for the reach table)."""
    return {r["node"]: r for r in rows("""SELECT DISTINCT ON (k.node) k.node, b.name, c.buyer_eik eik, round(sum(c.amount_eur)) eur
        FROM live.node_contract k JOIN live.contract c ON c.id = k.contract_id LEFT JOIN live.buyer b ON b.eik = c.buyer_eik
        WHERE k.node = ANY(%s) AND NOT c.is_framework GROUP BY k.node, b.name, c.buyer_eik
        ORDER BY k.node, sum(c.amount_eur) DESC NULLS LAST""", list(nodes))}


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


def tender_lots(t):
    """Per lot (0 = no lots): title, estimate, status, offers (cheapest first) and contracts, for the
    procedure page. Offers come from the procedure pages of ЦАИС ЕОП (live.offer); empty until read."""
    unp = t["unp"]
    has = one("SELECT to_regclass('live.offer') IS NOT NULL ok")["ok"]  # the first build after migration 0003 creates it
    offers = rows("""SELECT lot_no, round, bidder_name, bidder_eik, company_key, consortium, submitted_at, price_eur,
        price_opened, won FROM live.offer WHERE unp = %s ORDER BY lot_no, price_eur NULLS LAST, submitted_at""", unp) if has else []
    lots = {l["lot_no"]: dict(l, offers=[], contracts=[]) for l in t["lots"]}
    for o in offers:
        lots.setdefault(o["lot_no"], {"lot_no": o["lot_no"], "title": None, "estimated_eur": None, "status": None,
                                      "offers": [], "contracts": []})["offers"].append(o)
    for c in t["contracts"]:
        n = c["lot_no"] or 0
        lots.setdefault(n, {"lot_no": n, "title": None, "estimated_eur": None, "status": None, "offers": [], "contracts": []})["contracts"].append(c)
    if not t["lots"] and 0 in lots:
        lots[0].update(title=t["subject"], estimated_eur=t["estimated_eur"], status=t.get("state"))
    out = sorted(lots.values(), key=lambda l: l["lot_no"])
    for l in out:
        prices = [o["price_eur"] for o in l["offers"] if o["price_eur"] is not None]
        l["low"] = min(prices) if prices else None
        l["high"] = max(prices) if prices else None
        win = [o for o in l["offers"] if o["won"]]
        # the award went above the lowest opened price (a flag only where the criterion is the price alone)
        l["not_lowest"] = bool(win and l["low"] is not None and win[0]["price_eur"] is not None and win[0]["price_eur"] > l["low"] + 0.01)
    return out


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


WORD = re.compile(r"\w+")


def prefix_query(q):
    """'Иван петров' -> 'иван:* & петров:*' (every word a prefix, any order); None if no usable word."""
    words = [w.lower() for w in WORD.findall(q) if len(w) > 1 or q.strip().isalnum()]
    return " & ".join(w + ":*" for w in words) or None


def search(q, kind=None, limit=40, kinds=None):
    """Names: every query word is a prefix of a word of the name, in any order, any case, Latin or
    Cyrillic; ranked exact > all words at word starts (strict_word_similarity) > weight (money or
    links). Typos: trigram similarity, only when nothing matched. Digits: ЕИК / УНП exact, then substring."""
    q = q.strip()
    if not q:
        return []
    kinds = kinds or ([kind] if kind else ["buyer", "company", "person", "tender"])
    terms = [q]
    if any("a" <= ch.lower() <= "z" for ch in q):
        terms.append(translit(q))
    parts, args = ["SELECT kind, ref, label, sub, weight, 2.0 sim FROM live.search_item WHERE ref = ANY(%s) AND kind = ANY(%s)"], [[q, "eik:" + q], kinds]
    if re.fullmatch(r"[\d\s\-]+", q):  # ЕИК / УНП
        parts.append("SELECT kind, ref, label, sub, weight, 0.5 sim FROM live.search_item WHERE kind = ANY(%s) AND key LIKE %s")
        args += [kinds, "%" + q.replace("%", "") + "%"]
    else:
        for t in terms:
            tq = prefix_query(t)
            if tq:
                parts.append("""SELECT kind, ref, label, sub, weight,
                    CASE WHEN key = upper(%s) THEN 1.5 ELSE 0.5 + strict_word_similarity(upper(%s), key) END sim
                    FROM live.search_item WHERE kind = ANY(%s) AND words @@ to_tsquery('simple', %s)""")
                args += [t, t, kinds, tq]
    sql = f"""SELECT kind, ref, label, sub, max(sim) sim, max(weight) weight FROM ({' UNION ALL '.join(parts)}) x
              GROUP BY 1, 2, 3, 4 ORDER BY max(sim) DESC, max(weight) DESC LIMIT %s"""
    res = rows(sql, *args, limit)
    names = [k for k in kinds if k != "tender"]  # subjects are long: no fuzzy matching on them
    if not res and names and not re.fullmatch(r"[\d\s\-]+", q):
        fz = " UNION ALL ".join(["SELECT kind, ref, label, sub, weight, similarity(key, upper(%s)) sim FROM live.search_item "
                                 "WHERE kind = ANY(%s) AND key %% upper(%s)"] * len(terms))
        fa = [a for t in terms for a in (t, names, t)]
        res = rows(f"""SELECT kind, ref, label, sub, max(sim) sim, max(weight) weight FROM ({fz}) x
                       GROUP BY 1, 2, 3, 4 ORDER BY max(sim) DESC, max(weight) DESC LIMIT %s""", *fa, limit)
    return res


if __name__ == "__main__":
    assert translit("Ivanov") == "иванов" and translit("Shtilianov") == "щилианов"
    assert prefix_query("Иван  ПЕТРОВ") == "иван:* & петров:*" and prefix_query("a, b") is None and prefix_query("x") == "x:*"
    print("ok", dt.date.today())

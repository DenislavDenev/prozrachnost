"""Connected components of the network, per view and mode (methodology 1 and 6).

Full components are computed here with union-find over every edge; bounded, time-aware
traversal around one node is a query in app/queries.py. A component says that registry
links exist between its nodes; it does not say anyone controls anything.
"""
import datetime as dt

VIEWS = {"ownership": ("ownership",), "management": ("management",), "all": ("ownership", "management")}


def _find(parent, x):
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


def union_find(pairs):
    """{node: component representative} for an iterable of (a, b)."""
    parent = {}
    for a, b in pairs:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        ra, rb = _find(parent, a), _find(parent, b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {n: _find(parent, n) for n in parent}


def components(conn):
    today = dt.date.today()
    edges = conn.execute("SELECT holder, company, view, valid_to FROM stage.edge").fetchall()
    stats = {}
    ids = {}
    with conn.cursor().copy("COPY stage.network_component(view, mode, node, component) FROM STDIN") as cp:
        for view, kinds in VIEWS.items():
            for mode in ("current", "all_history"):
                pairs = [(h, c) for h, c, v, to in edges
                         if v in kinds and (mode == "all_history" or to is None or to > today)]
                comp = union_find(pairs)
                for node, rep in comp.items():
                    cid = ids.setdefault(rep, len(ids) + 1)
                    cp.write_row([view, mode, node, cid])
                stats[f"{view}/{mode}"] = len(set(comp.values()))
    conn.execute("CREATE INDEX ON stage.network_component (node)")
    conn.execute("""
        INSERT INTO stage.network_component_stats
        WITH nodes AS (
          SELECT view, mode, component, count(*) AS nodes,
                 count(*) FILTER (WHERE node LIKE 'p:%%' OR node LIKE 'l:%%') AS persons,
                 count(*) FILTER (WHERE node LIKE 'c:%%') AS companies
          FROM stage.network_component GROUP BY 1, 2, 3),
        cc AS (
          SELECT DISTINCT nc.view, nc.mode, nc.component, k.contract_id
          FROM stage.network_component nc JOIN stage.node_contract k ON k.node = nc.node),
        money AS (
          SELECT cc.view, cc.mode, cc.component, count(*) AS contracts,
                 sum(c.amount_eur) FILTER (WHERE NOT c.is_framework) AS amount_eur
          FROM cc JOIN stage.contract c ON c.id = cc.contract_id GROUP BY 1, 2, 3)
        SELECT n.view, n.mode, n.component, n.nodes, n.persons, n.companies,
               coalesce(m.contracts, 0), m.amount_eur
        FROM nodes n LEFT JOIN money m USING (view, mode, component)""")
    conn.execute("CREATE INDEX ON stage.network_component (view, mode, component)")
    return stats


if __name__ == "__main__":
    comp = union_find([("a", "b"), ("b", "c"), ("d", "e"), ("c", "a")])
    assert comp["a"] == comp["b"] == comp["c"] and comp["d"] == comp["e"] != comp["a"]
    print("ok")

"""Connected components of the network, per view and mode (methodology 1 and 6).

Full components are computed here with union-find over every edge; bounded, time-aware
traversal around one node is a query in app/queries.py. A component says that registry
links exist between its nodes; it does not say anyone controls anything.
"""
import datetime as dt
from array import array

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


KIND = {"ownership": 1, "management": 2}
STREAM = 50_000   # edges per fetch from the server


def read_edges(conn, today):
    """Every edge once, streamed from the server, as numbers: the node names stay in one list and an edge is two
    places in it. Holding the rows as Python tuples took 1,5 GB for 2,1 million edges, and on 25.11.2026 the kernel
    killed the build for it twice."""
    index, names = {}, []
    holders, companies, kinds, current = array("l"), array("l"), bytearray(), bytearray()
    with conn.cursor(name="network_edges") as cur:
        cur.itersize = STREAM
        cur.execute("SELECT holder, company, view, valid_to FROM tender_stage.edge")
        for h, c, v, to in cur:
            for node, out in ((h, holders), (c, companies)):
                i = index.get(node)
                if i is None:
                    i = index[node] = len(names)
                    names.append(node)
                out.append(i)
            kinds.append(KIND.get(v, 0))
            current.append(to is None or to > today)
    return names, holders, companies, kinds, current


def components(conn):
    names, holders, companies, kinds, current = read_edges(conn, dt.date.today())
    stats = {}
    ids = {}
    for view, wanted in VIEWS.items():
        codes = {KIND[k] for k in wanted}
        for mode in ("current", "all_history"):
            comp = union_find((holders[i], companies[i]) for i in range(len(holders))
                              if kinds[i] in codes and (mode == "all_history" or current[i]))
            with conn.cursor().copy("COPY tender_stage.network_component(view, mode, node, component) FROM STDIN") as cp:
                for node, rep in comp.items():
                    cid = ids.setdefault((view, mode, rep), len(ids) + 1)
                    cp.write_row([view, mode, names[node], cid])
            stats[f"{view}/{mode}"] = len(set(comp.values()))
            del comp
    conn.execute("CREATE INDEX ON tender_stage.network_component (node)")
    conn.execute("""
        INSERT INTO tender_stage.network_component_stats
        WITH nodes AS (
          SELECT view, mode, component, count(*) AS nodes,
                 count(*) FILTER (WHERE node LIKE 'p:%%' OR node LIKE 'l:%%') AS persons,
                 count(*) FILTER (WHERE node LIKE 'c:%%') AS companies
          FROM tender_stage.network_component GROUP BY 1, 2, 3),
        cc AS (
          SELECT DISTINCT nc.view, nc.mode, nc.component, k.contract_id
          FROM tender_stage.network_component nc JOIN tender_stage.node_contract k ON k.node = nc.node),
        money AS (
          SELECT cc.view, cc.mode, cc.component, count(*) AS contracts,
                 sum(c.amount_eur) FILTER (WHERE NOT c.is_framework) AS amount_eur
          FROM cc JOIN tender_stage.contract c ON c.id = cc.contract_id GROUP BY 1, 2, 3)
        SELECT n.view, n.mode, n.component, n.nodes, n.persons, n.companies,
               coalesce(m.contracts, 0), m.amount_eur
        FROM nodes n LEFT JOIN money m USING (view, mode, component)""")
    conn.execute("CREATE INDEX ON tender_stage.network_component (view, mode, component)")
    return stats


if __name__ == "__main__":
    comp = union_find([("a", "b"), ("b", "c"), ("d", "e"), ("c", "a")])
    assert comp["a"] == comp["b"] == comp["c"] and comp["d"] == comp["e"] != comp["a"]
    print("ok")

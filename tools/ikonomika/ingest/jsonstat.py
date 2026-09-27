"""JSON-stat 2.0 datasets from the Eurostat Statistics API.

The values are a dict keyed by the position in the row-major product of the dimensions (`id`, `size`); a
position is turned back into one category per dimension from `size`, never guessed. Anything that is not
the expected shape raises ShapeError and nothing is written.
"""
import json
import math


class ShapeError(ValueError):
    """The answer is not what the parser expects (not JSON, an error, a missing field, a new layout)."""


def parse(raw, pinned=()):
    """-> {"label", "updated", "rows": [(dims, geo, time, value, flag)], "labels": {dim: {code: label}}}.

    `pinned` are the dimensions the request filtered on. `unit` must be one of them: one answer can mix
    an index with its rates of change, and an unpinned unit would mix them silently.
    """
    try:
        d = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as e:
        raise ShapeError(f"not JSON: {e}") from None
    if not isinstance(d, dict):
        raise ShapeError("not a JSON object")
    if "error" in d:
        raise ShapeError(f"source error: {json.dumps(d['error'], ensure_ascii=False)[:300]}")
    for k, t in (("id", list), ("size", list), ("dimension", dict), ("value", (dict, list)), ("updated", str)):
        if not isinstance(d.get(k), t):
            raise ShapeError(f"missing or wrong field {k!r}")
    ids, size = d["id"], d["size"]
    if len(ids) != len(size) or not {"geo", "time"} <= set(ids):
        raise ShapeError(f"unexpected dimensions {ids} {size}")
    if "unit" in ids and "unit" not in pinned:
        raise ShapeError("the answer has a unit dimension but the request did not pin the unit")
    if 0 in size:
        raise ShapeError(f"an empty dimension (a filter value the source does not know?): {dict(zip(ids, size))}")
    codes, labels = [], {}
    for k, n in zip(ids, size):
        cat = (d["dimension"].get(k) or {}).get("category") or {}
        idx = cat.get("index")
        order = sorted(idx, key=idx.get) if isinstance(idx, dict) else idx
        if not isinstance(order, list) or len(order) != n:
            raise ShapeError(f"dimension {k!r} has {len(order or [])} categories, size says {n}")
        codes.append(order)
        if k != "time":
            labels[k] = {c: (cat.get("label") or {}).get(c, c) for c in order}
    values = d["value"] if isinstance(d["value"], dict) else {str(i): v for i, v in enumerate(d["value"]) if v is not None}
    status = d.get("status") or {}
    if isinstance(status, list):
        status = {str(i): s for i, s in enumerate(status) if s}
    total = math.prod(size)
    drop = {i for i, k in enumerate(ids) if k == "freq" and size[i] == 1}
    rows = []
    # positions with only a flag (": not available", "c confidential") are kept as "no data", never as 0
    for pos in sorted({*values, *status}, key=int):
        p = int(pos)
        if not 0 <= p < total:
            raise ShapeError(f"position {p} outside the {total} cells")
        v = values.get(pos)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise ShapeError(f"value at {p} is not a number: {v!r}")
        at = []
        for n in reversed(size):
            at.append(p % n)
            p //= n
        at.reverse()
        key = {k: codes[i][c] for i, (k, c) in enumerate(zip(ids, at)) if i not in drop}
        geo, time = key.pop("geo"), key.pop("time")
        rows.append((key, geo, time, v, status.get(pos) or None))
    flags = (((d.get("extension") or {}).get("status") or {}).get("label")) or {}
    if flags:
        labels["flag"] = flags
    return {"label": d.get("label"), "updated": d["updated"], "rows": rows, "labels": labels}

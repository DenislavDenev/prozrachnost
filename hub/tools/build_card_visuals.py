"""Draw the card pictures for tools in preparation, in the style of the procurement card.

Usage: python tools/build_card_visuals.py [path/to/bg-municipalities.json] [--check]

The pictures are shapes, not data: bars, lines and ranks are random but fixed by the tool's number, the
maps use the real outlines of the 265 municipalities (from Tender's bg-municipalities.json, geoBoundaries,
public domain). --check redraws in memory and fails if a file differs, so the output stays deterministic.
Needs shapely (already needed by build_road_preview.py).
"""
import json
import math
import random
import re
import sys
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "cards"
W, H = 420, 260
LIGHT, MID, DARK, GRID = "#b4d7bf", "#5cad82", "#08785c", "#d8e6dc"
TINTS = ["#e3efe6", "#c8e0cf", "#9fcdb3", "#5cad82"]


def svg(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">{body}</svg>\n'


def grid_lines():
    return f'<path d="M0 60H{W}M0 120H{W}M0 180H{W}" stroke="{GRID}" stroke-width="1" fill="none"/>'


def trend(r, top=40, bottom=190):
    pts = [(x, r.uniform(top, bottom)) for x in range(0, W + 1, 70)]
    pts.sort()
    d = f"M{pts[0][0]} {pts[0][1]:.0f}" + "".join(f" L{x} {y:.0f}" for x, y in pts[1:])
    return d


def bars(r):
    n, left, right, bottom = 7, 55, 46, 238
    step = (W - left - right) / n
    hi = set(r.sample(range(n), 2))
    out = [grid_lines()]
    for i in range(n):
        h = r.uniform(0.35, 0.92) * 190
        x = left + i * step + step * 0.12
        out.append(f'<rect x="{x:.0f}" y="{bottom - h:.0f}" width="{step * 0.76:.0f}" height="{h:.0f}" rx="5" fill="{MID if i in hi else LIGHT}"/>')
    out.append(f'<path d="{trend(r, 30, 150)}" stroke="{DARK}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    return svg("".join(out))


def line(r):
    out = [grid_lines()]
    base = trend(r, 110, 200)
    main = trend(r, 30, 140)
    out.append(f'<path d="{base}" stroke="{LIGHT}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    out.append(f'<path d="{main} L{W} {H} L0 {H} Z" fill="#e3efe6" opacity=".7"/>')
    out.append(f'<path d="{main}" stroke="{DARK}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    return svg("".join(out))


def ranking(r):
    out = []
    widths = sorted((r.uniform(0.3, 0.95) for _ in range(6)), reverse=True)
    for i, w in enumerate(widths):
        y = 34 + i * 34
        out.append(f'<rect x="40" y="{y}" width="{(W - 80) * w:.0f}" height="18" rx="5" fill="{MID if i == 0 else LIGHT}"/>')
    out.append(f'<path d="M40 22V{34 + 6 * 34}" stroke="{GRID}" stroke-width="2"/>')
    return svg("".join(out))


def timeline(r):
    out = [f'<path d="M24 170H{W - 24}" stroke="{GRID}" stroke-width="2"/>']
    for x0, x1, y in ((r.uniform(40, 120), r.uniform(170, 240), 196), (r.uniform(220, 260), r.uniform(330, 390), 210)):
        out.append(f'<rect x="{x0:.0f}" y="{y}" width="{x1 - x0:.0f}" height="10" rx="5" fill="{LIGHT}"/>')
    for i in range(10):
        x = 40 + i * 37 + r.uniform(-6, 6)
        h = r.uniform(30, 120)
        c = MID if r.random() < 0.3 else LIGHT
        out.append(f'<path d="M{x:.0f} 170V{170 - h:.0f}" stroke="{GRID}" stroke-width="2"/><circle cx="{x:.0f}" cy="{170 - h:.0f}" r="{r.choice((6, 7, 9))}" fill="{c}"/>')
    out.append(f'<circle cx="{40 + 9 * 37:.0f}" cy="170" r="8" fill="{DARK}"/>')
    return svg("".join(out))


def hemicycle(r):
    out, seats = [], []
    for ring in range(6):
        rad = 70 + ring * 22
        n = 14 + ring * 5
        for k in range(n):
            a = math.pi - math.pi * k / (n - 1)
            seats.append((a, W / 2 + rad * math.cos(a), 236 - rad * math.sin(a)))
    seats.sort(reverse=True)
    cuts = sorted(r.sample(range(20, len(seats) - 20), 4))
    colors = [DARK, MID, LIGHT, "#d3ddd8", "#9fcdb3"]
    part = 0
    for i, (_, x, y) in enumerate(seats):
        while part < len(cuts) and i >= cuts[part]:
            part += 1
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{colors[part]}"/>')
    return svg("".join(out))


def network(r):
    nodes = [(r.uniform(40, W - 40), r.uniform(30, H - 30)) for _ in range(15)]
    hubs = r.sample(range(15), 3)
    out = []
    for i, (x, y) in enumerate(nodes):
        for j in sorted(range(15), key=lambda k: (nodes[k][0] - x) ** 2 + (nodes[k][1] - y) ** 2)[1:3]:
            out.append(f'<path d="M{x:.0f} {y:.0f}L{nodes[j][0]:.0f} {nodes[j][1]:.0f}" stroke="#c9dccf" stroke-width="2"/>')
    for i, (x, y) in enumerate(nodes):
        big = i in hubs
        out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{13 if big else 7}" fill="{DARK if big else (MID if i % 3 == 0 else LIGHT)}" stroke="#fff" stroke-width="2"/>')
    return svg("".join(out))


def load_map(path):
    geo = json.loads(Path(path).read_text(encoding="utf-8"))
    shapes = []
    for v in geo["shapes"].values():
        polys = []
        for sub in re.findall(r"M[^MZ]*Z?", v["d"]):
            pts = [tuple(map(float, p.split(","))) for p in re.findall(r"-?[\d.]+,-?[\d.]+", sub)]
            if len(pts) >= 3:
                polys.append(Polygon(pts).buffer(0))
        if polys:
            shapes.append(unary_union(polys).simplify(1.8, preserve_topology=True))
    return shapes, geo["h"]


def to_d(g, sx, sy, ox, oy):
    out = []
    for p in getattr(g, "geoms", [g]):
        if p.geom_type == "Polygon" and not p.is_empty:
            out.append("M" + "L".join(f"{ox + x * sx:.1f} {oy + y * sy:.1f}" for x, y in p.exterior.coords) + "Z")
    return "".join(out)


def fit(h):
    s = min((W - 30) / 1000, (H - 20) / h)
    return s, s, (W - 1000 * s) / 2, (H - h * s) / 2


def choropleth(r, shapes, h):
    sx, sy, ox, oy = fit(h)
    return svg("".join(f'<path d="{to_d(g, sx, sy, ox, oy)}" fill="{r.choice(TINTS)}" stroke="#fff" stroke-width=".7"/>' for g in shapes))


def dots(r, shapes, h):
    sx, sy, ox, oy = fit(h)
    country = unary_union([g.buffer(0.5) for g in shapes]).simplify(2)
    out = [f'<path d="{to_d(country, sx, sy, ox, oy)}" fill="#e3efe6" stroke="#c9dccf" stroke-width="1.5"/>']
    placed = 0
    while placed < 45:
        x, y = r.uniform(0, 1000), r.uniform(0, h)
        if country.contains(Polygon([(x, y), (x + .1, y), (x, y + .1)])):
            size = r.choice((3.5, 3.5, 5, 7))
            out.append(f'<circle cx="{ox + x * sx:.1f}" cy="{oy + y * sy:.1f}" r="{size}" fill="{DARK if size == 7 else (MID if size == 5 else LIGHT)}"/>')
            placed += 1
    return svg("".join(out))


def build(map_path):
    tools = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))["tools"]
    shapes, h = load_map(map_path)
    drawn = {}
    for t in tools:
        if t["status"] != "soon":
            continue
        r = random.Random(t["no"])
        v = t["visual"]
        if v in ("choropleth", "dots"):
            drawn[t["slug"]] = (choropleth if v == "choropleth" else dots)(r, shapes, h)
        else:
            drawn[t["slug"]] = {"bars": bars, "line": line, "ranking": ranking, "timeline": timeline,
                                "hemicycle": hemicycle, "network": network}[v](r)
    return drawn


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    path = args[0] if args else ROOT.parent / "tender" / "app" / "static" / "bg-municipalities.json"
    drawn = build(path)
    if "--check" in sys.argv:
        bad = [s for s, text in drawn.items() if (OUT / f"{s}.svg").read_text(encoding="utf-8") != text]
        print("differs:", bad) if bad else print(f"ok, {len(drawn)} pictures")
        sys.exit(1 if bad else 0)
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, text in drawn.items():
        (OUT / f"{slug}.svg").write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(drawn)} pictures to {OUT}")

"""Fast, bounded map endpoints; no request fetches a remote source. Every answer is read from the serving layer in one
snapshot and says which publication it shows (X-Publication)."""
import datetime as dt
import gzip
import json
import math
import os
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import feedback, queries
from ingest.config import DATA

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Пътна обстановка", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/prozrachnost",
                                   Path(os.environ.get("PATNA_OBSTANOVKA_FEEDBACK", str(DATA / "feedback"))), "Пътна обстановка"))


def snap(request: Request):
    """The one snapshot of the serving layer a request reads, and the publication it shows."""
    with queries.snapshot() as c:
        request.state.publication = queries.version(c)
        yield c


@app.middleware("http")
async def publication_headers(request: Request, call_next):
    """Every answer built from the data says which publication it is of; the number moves only when the published
    content does, so a cache in front of the site can keep an answer until then."""
    response = await call_next(request)
    publication = getattr(request.state, "publication", None)
    if publication:
        response.headers["X-Publication"] = str(publication[0])
        response.headers["ETag"] = f'W/"pub-{publication[0]}"'
        response.headers["Last-Modified"] = format_datetime(publication[1].astimezone(dt.timezone.utc), usegmt=True)
    return response


@app.get("/", response_class=HTMLResponse)
def home():
    hub = os.getenv("HUB_URL", "http://localhost:8001")
    return ((ROOT / "index.html").read_text(encoding="utf-8").replace("{{HUB_URL}}", hub)
            .replace("{{FEEDBACK}}", feedback.BUTTON).replace("{{SUPPORT}}", feedback.support_link(hub)))


@app.get("/favicon.svg")
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz")
def health(c=Depends(snap)):
    return {"status": "ok", "sources": queries.healthy_sources(c)}


@app.get("/api/status")
def status(c=Depends(snap)):
    return queries.status(c)


@app.get("/api/traffic")
def traffic(c=Depends(snap)):
    return queries.traffic(c)


@app.get("/api/weather")
def weather(c=Depends(snap)):
    return queries.weather(c)


@app.get("/api/events")
def events(c=Depends(snap)):
    return queries.events(c)


_risk = {}


def risk_payload(version, c):
    """The complete network as GeoJSON, built once for each read of the sections (the key is the time of the read)."""
    if version not in _risk:
        parts = ['{"type":"FeatureCollection","features":[']
        for row in queries.risk_sections(c):
            if len(parts) > 1:
                parts.append(",")
            properties = {key: row[key] for key in ("id", "layer", "road", "risk_level", "condition", "reference", "district", "length_km")}
            parts.append('{"type":"Feature","geometry":{"type":"MultiLineString","coordinates":' + row["geometry"] + '},"properties":'
                         + json.dumps(properties, ensure_ascii=False, separators=(",", ":")) + "}")
        parts.append("]}")
        plain = "".join(parts).encode("utf-8")
        _risk.clear()
        _risk[version] = plain, gzip.compress(plain, compresslevel=5, mtime=0)
    return _risk[version]


@app.get("/api/risk/all")
def all_risk(request: Request, c=Depends(snap)):
    """Complete assessed network; MapLibre tiles/generalises this client-side."""
    plain, compressed = risk_payload(queries.risk_version(c), c)
    headers = {"Cache-Control": "public, max-age=3600", "Vary": "Accept-Encoding"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return Response(content=compressed, media_type="application/geo+json", headers=headers)
    return Response(content=plain, media_type="application/geo+json", headers=headers)


def period_start(period):
    if period == "all":
        return ""
    if period not in ("30", "365"):
        raise HTTPException(422, "Unknown period")
    return (datetime.now(ZoneInfo("Europe/Sofia")).date() - timedelta(days=int(period))).isoformat()


def checked_bounds(south, north, west, east):
    if south >= north or west >= east:
        raise HTTPException(422, "Invalid bounds")


def sqlite_sum(values):
    """SUM() as SQLite 3.43+ computes it for real numbers (Kahan-Babuska-Neumaier), so the totals stay what they were."""
    total = error = 0.0
    for value in values:
        step = total + value
        error += (total - step) + value if abs(total) > abs(value) else (value - step) + total
        total = step
    return total + error


@app.get("/api/road-conditions")
def road_conditions(c=Depends(snap)):
    by = {}
    for row in queries.road_conditions(c):
        by.setdefault(row["condition"], []).append(row["length_km"])
    rows = sorted(({"condition": k, "km": round(sqlite_sum(v), 3), "sections": len(v)} for k, v in by.items()),
                  key=lambda r: -r["km"])
    return {"reference": "2023", "total_km": round(sum(row["km"] for row in rows), 3), "conditions": rows}


@app.get("/api/crashes/summary")
def crash_summary(zoom: int = Query(7, ge=5, le=12), period: str = "365", south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), c=Depends(snap)):
    checked_bounds(south, north, west, east)
    since = period_start(period)
    world = 256 * (2 ** zoom)
    cells = {}
    for row in queries.crashes_in(c, south, north, west, east, since):
        lon, lat = row["lon"], row["lat"]
        x = (lon + 180) / 360 * world
        rad = math.radians(min(85, max(-85, lat)))
        y = (1 - math.asinh(math.tan(rad)) / math.pi) / 2 * world
        key = (int(x // 56), int(y // 56))
        cell = cells.setdefault(key, {"count": 0, "fatal": 0, "injury": 0, "uninjured": 0, "lat_sum": 0, "lon_sum": 0, "south": lat, "north": lat, "west": lon, "east": lon})
        cell["count"] += 1
        cell["fatal" if row["killed"] else "injury" if row["injured"] else "uninjured"] += 1
        cell["lat_sum"] += lat
        cell["lon_sum"] += lon
        cell["south"] = min(cell["south"], lat)
        cell["north"] = max(cell["north"], lat)
        cell["west"] = min(cell["west"], lon)
        cell["east"] = max(cell["east"], lon)
    features = []
    for (x, y), cell in cells.items():
        count = cell.pop("count")
        lon, lat = cell.pop("lon_sum") / count, cell.pop("lat_sum") / count
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {**cell, "count": count, "grid_id": f"{zoom}:{x}:{y}"}})
    return {"type": "FeatureCollection", "features": features, "total": sum(feature["properties"]["count"] for feature in features)}


@app.get("/api/crashes/points")
def crash_points(zoom: int = Query(..., ge=12, le=17), period: str = "365", south: float = Query(..., ge=40, le=45), north: float = Query(..., ge=40, le=45), west: float = Query(..., ge=20, le=30), east: float = Query(..., ge=20, le=30), c=Depends(snap)):
    checked_bounds(south, north, west, east)
    if (north - south) * (east - west) > 0.3:
        raise HTTPException(422, "Zoom in for individual records")
    return queries.crash_points(c, south, north, west, east, period_start(period))


@app.get("/api/dangerous-sections")
def dangerous_sections(period: str = "365", condition: str = "", c=Depends(snap)):
    since = period_start(period)
    total, matched = queries.crash_totals(c, since)
    features = []
    for row in queries.dangerous_sections(c, since, condition):
        properties = {key: row[key] for key in row.keys() if key != "geometry"}
        features.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": json.loads(row["geometry"])}, "properties": properties})
    return {"type": "FeatureCollection", "features": features, "matching": {"matched": matched, "total": total, "percent": round(100 * matched / total, 1) if total else 0}}


@app.get("/api/crashes")
def crashes(south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), since: str = "", limit: int = Query(2000, ge=1, le=5000), c=Depends(snap)):
    if south >= north or west >= east:
        return []
    return queries.crash_points(c, south, north, west, east, since, limit)

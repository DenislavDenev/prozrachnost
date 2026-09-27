"""Fast, bounded map endpoints; no request fetches a remote source."""
import os
import json
import gzip
import math
from contextlib import closing
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from db import DB, connect
import feedback

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Пътна обстановка", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(feedback.router("DenislavDenev/patna-obstanovka", DB.parent))


@app.get("/", response_class=HTMLResponse)
def home():
    hub = os.getenv("HUB_URL", "http://localhost:8001")
    return ((ROOT / "index.html").read_text(encoding="utf-8").replace("{{HUB_URL}}", hub)
            .replace("{{FEEDBACK}}", feedback.BUTTON).replace("{{SUPPORT}}", feedback.support_link(hub)))


@app.get("/favicon.svg")
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz")
def health():
    with closing(connect()) as db:
        return {"status": "ok", "sources": db.execute("SELECT count(*) FROM sources WHERE error IS NULL").fetchone()[0]}


@app.get("/api/status")
def status():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY key")]


@app.get("/api/traffic")
def traffic():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM traffic")]


@app.get("/api/weather")
def weather():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM weather")]


@app.get("/api/events")
def events():
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM events")]


@lru_cache(maxsize=2)
def risk_payload(version):
    """Cache the complete snapshot until ingestion records a new version."""
    parts = ['{"type":"FeatureCollection","features":[']
    db = connect()
    try:
        for row in db.execute("SELECT id,layer,road,risk_level,condition,reference,district,length_km,geometry FROM risk"):
            if len(parts) > 1:
                parts.append(",")
            properties = {key: row[key] for key in ("id", "layer", "road", "risk_level", "condition", "reference", "district", "length_km")}
            parts.append('{"type":"Feature","geometry":{"type":"MultiLineString","coordinates":' + row["geometry"] + '},"properties":' + json.dumps(properties, ensure_ascii=False, separators=(",", ":")) + "}")
    finally:
        db.close()
    parts.append("]}")
    plain = "".join(parts).encode("utf-8")
    return plain, gzip.compress(plain, compresslevel=5, mtime=0)


@app.get("/api/risk/all")
def all_risk(request: Request):
    """Complete assessed network; MapLibre tiles/generalises this client-side."""
    db = connect()
    try:
        version = db.execute("SELECT updated FROM sources WHERE key='risk'").fetchone()
    finally:
        db.close()
    plain, compressed = risk_payload(version[0] if version else None)
    headers = {"Cache-Control":"public, max-age=3600", "Vary":"Accept-Encoding"}
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


@app.get("/api/road-conditions")
def road_conditions():
    with closing(connect()) as db:
        rows = db.execute("SELECT condition,ROUND(SUM(length_km),3) km,COUNT(*) sections FROM risk WHERE layer=5 AND length_km>0 GROUP BY condition ORDER BY km DESC").fetchall()
        return {"reference": "2023", "total_km": round(sum(row["km"] for row in rows), 3), "conditions": [dict(row) for row in rows]}


@app.get("/api/crashes/summary")
def crash_summary(zoom: int = Query(7, ge=5, le=12), period: str = "365", south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30)):
    checked_bounds(south, north, west, east)
    since = period_start(period)
    world = 256 * (2 ** zoom)
    cells = {}
    with closing(connect()) as db:
        rows = db.execute("SELECT lat,lon,killed,injured FROM crashes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND date>=?", (south, north, west, east, since))
        for row in rows:
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
def crash_points(zoom: int = Query(..., ge=12, le=17), period: str = "365", south: float = Query(..., ge=40, le=45), north: float = Query(..., ge=40, le=45), west: float = Query(..., ge=20, le=30), east: float = Query(..., ge=20, le=30)):
    checked_bounds(south, north, west, east)
    if (north - south) * (east - west) > 0.3:
        raise HTTPException(422, "Zoom in for individual records")
    with closing(connect()) as db:
        return [dict(row) for row in db.execute("SELECT * FROM crashes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND date>=? ORDER BY date DESC", (south, north, west, east, period_start(period)))]


@app.get("/api/dangerous-sections")
def dangerous_sections(period: str = "365", condition: str = ""):
    since = period_start(period)
    with closing(connect()) as db:
        total = db.execute("SELECT COUNT(*) FROM crashes WHERE date>=?", (since,)).fetchone()[0]
        matched = db.execute("SELECT COUNT(*) FROM crash_matches m JOIN crashes c ON c.id=m.crash_id WHERE c.date>=?", (since,)).fetchone()[0]
        rows = db.execute("""SELECT r.id,r.road,r.risk_level,r.condition,r.reference,r.length_km,r.geometry,
            COUNT(*) crashes,SUM(CASE WHEN c.killed>0 THEN 1 ELSE 0 END) fatal,
            SUM(CASE WHEN c.killed=0 AND c.injured>0 THEN 1 ELSE 0 END) injury,
            SUM(c.killed) killed,SUM(c.injured) injured
            FROM crash_matches m JOIN crashes c ON c.id=m.crash_id JOIN risk r ON r.id=m.risk_id
            WHERE c.date>=? AND (?='' OR (r.layer=5 AND r.condition=?))
            GROUP BY r.id ORDER BY crashes DESC, fatal DESC, r.id LIMIT 25""", (since, condition, condition)).fetchall()
        features = []
        for row in rows:
            properties = {key: row[key] for key in row.keys() if key != "geometry"}
            features.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": json.loads(row["geometry"])}, "properties": properties})
        return {"type": "FeatureCollection", "features": features, "matching": {"matched": matched, "total": total, "percent": round(100 * matched / total, 1) if total else 0}}


@app.get("/api/crashes")
def crashes(south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), since: str = "", limit: int = Query(2000, ge=1, le=5000)):
    if south >= north or west >= east:
        return []
    with closing(connect()) as db:
        rows = db.execute("SELECT * FROM crashes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND date>=? ORDER BY date DESC LIMIT ?", (south,north,west,east,since,limit)).fetchall()
        return [dict(row) for row in rows]

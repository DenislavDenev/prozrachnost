"""Fast, bounded map endpoints; no request fetches a remote source."""
import os
import json
import gzip
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from db import connect

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Пътна обстановка", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "index.html").read_text(encoding="utf-8").replace("{{HUB_URL}}", os.getenv("HUB_URL", "http://localhost:8001"))


@app.get("/favicon.svg")
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/healthz")
def health():
    with connect() as db:
        return {"status": "ok", "sources": db.execute("SELECT count(*) FROM sources WHERE error IS NULL").fetchone()[0]}


@app.get("/api/status")
def status():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY key")]


@app.get("/api/traffic")
def traffic():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM traffic")]


@app.get("/api/weather")
def weather():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM weather")]


@app.get("/api/events")
def events():
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM events")]


@lru_cache(maxsize=2)
def risk_payload(version):
    """Cache the complete snapshot until ingestion records a new version."""
    parts = ['{"type":"FeatureCollection","features":[']
    db = connect()
    try:
        for row in db.execute("SELECT id,layer,road,risk_level,condition,reference,district,geometry FROM risk"):
            if len(parts) > 1:
                parts.append(",")
            properties = {key: row[key] for key in ("id", "layer", "road", "risk_level", "condition", "reference", "district")}
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


@app.get("/api/crashes")
def crashes(south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), since: str = "", limit: int = Query(2000, ge=1, le=5000)):
    if south >= north or west >= east:
        return []
    with connect() as db:
        rows = db.execute("SELECT * FROM crashes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND date>=? ORDER BY date DESC LIMIT ?", (south,north,west,east,since,limit)).fetchall()
        return [dict(row) for row in rows]

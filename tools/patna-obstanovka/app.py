"""Fast, bounded map endpoints; no request fetches a remote source."""
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from db import connect

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Пътна обстановка", docs_url=None, redoc_url=None)
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


@app.get("/api/risk")
def risk(south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), limit: int = Query(1500, ge=1, le=3000)):
    if south >= north or west >= east:
        return []
    with connect() as db:
        rows = db.execute("SELECT * FROM risk WHERE maxlat>=? AND minlat<=? AND maxlon>=? AND minlon<=? ORDER BY CASE WHEN layer=5 THEN 0 ELSE 1 END, (CAST(substr(id,instr(id,':')+1) AS INTEGER)*7919) % 16381 LIMIT ?", (south,north,west,east,limit)).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/crashes")
def crashes(south: float = Query(41.2, ge=40, le=45), north: float = Query(44.3, ge=40, le=45), west: float = Query(22.3, ge=20, le=30), east: float = Query(28.7, ge=20, le=30), since: str = "", limit: int = Query(2000, ge=1, le=5000)):
    if south >= north or west >= east:
        return []
    with connect() as db:
        rows = db.execute("SELECT * FROM crashes WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND date>=? ORDER BY date DESC LIMIT ?", (south,north,west,east,since,limit)).fetchall()
        return [dict(row) for row in rows]

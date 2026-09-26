"""The small public front door for the Prozrachnost tools."""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Прозрачност", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def home():
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    page = page.replace("{{ROAD_URL}}", os.getenv("ROAD_URL", "http://localhost:8002"))
    page = page.replace("{{TENDER_URL}}", os.getenv("TENDER_URL", "https://tender.denev.work"))
    from fastapi.responses import HTMLResponse
    return HTMLResponse(page)


@app.get("/healthz")
def health():
    return JSONResponse({"status": "ok"})


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")

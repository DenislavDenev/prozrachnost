"""The small public front door for the Prozrachnost tools. tools.json is the one list of tools."""
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Прозрачност", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


def load_registry(path=ROOT / "tools.json"):
    """Read and check tools.json once; a broken registry stops the start instead of showing a broken page."""
    reg = json.loads(Path(path).read_text(encoding="utf-8"))
    groups = {g["key"]: {**g, "tools": []} for g in reg["groups"]}
    slugs = set()
    for t in reg["tools"]:
        assert t["slug"] not in slugs, f"duplicate slug {t['slug']}"
        slugs.add(t["slug"])
        assert t["group"] in groups, f"unknown group for {t['slug']}"
        assert t["status"] in ("active", "new", "soon"), t["slug"]
        assert t["name"] and t["line"], t["slug"]
        if t["status"] == "soon":
            assert t["questions"] and t["sources"], f"{t['slug']} needs questions and sources"
            assert (ROOT / "static" / "illustrations" / f"{t['slug']}.svg").exists(), f"missing illustration for {t['slug']}"
        else:
            t["url"] = os.getenv(t["url_env"], t["url_default"])
        groups[t["group"]]["tools"].append(t)
    for g in groups.values():
        g["tools"].sort(key=lambda t: (t["status"] == "soon", t["no"]))
    return reg, list(groups.values()), {t["slug"]: t for t in reg["tools"]}


REGISTRY, GROUPS, TOOLS = load_registry()


@app.get("/", include_in_schema=False)
def home(request: Request):
    live = sum(t["status"] != "soon" for t in TOOLS.values())
    return templates.TemplateResponse(request, "index.html", {"groups": GROUPS, "live_count": live, "soon_count": len(TOOLS) - live})


@app.get("/instrumenti/{slug}", include_in_schema=False)
def tool(request: Request, slug: str):
    t = TOOLS.get(slug)
    if not t or t["status"] != "soon":
        raise HTTPException(404)
    group = next(g for g in GROUPS if g["key"] == t["group"])
    return templates.TemplateResponse(request, "tool.html", {"t": t, "group": group})


@app.get("/tools.json")
def tools_json():
    return JSONResponse(REGISTRY)


@app.get("/healthz")
def health():
    return JSONResponse({"status": "ok", "tools": len(TOOLS)})


@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")

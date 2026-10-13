"""Dashboard web server.

Start:  .venv\\Scripts\\python -m uvicorn web.app:app --port 8000
Open:   http://localhost:8000
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import threading
import time
from datetime import date as Date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from floodmap.config import FEATURED_RUN, RUNS_DIR  # also loads .env
from floodmap.grid import make_grid
from floodmap.pipeline import run_id
from web import helpdesk

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Flood damage from space")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def always_fresh(request: Request, call_next):
    """Browsers re-check pages and files each time (a quick 304 when unchanged), so edits show up at once."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path in ("/", "/app"):
        response.headers["Cache-Control"] = "no-cache"
    return response

jobs: "queue.Queue[dict]" = queue.Queue()
state = {"current": None}

# Help requests hold names, phone numbers and locations, so reading or changing them needs
# the rescue team PIN (RESCUE_PIN in .env). Empty = no PIN (fine on a private computer).
RESCUE_PIN = os.getenv("RESCUE_PIN", "").strip()
_pin_fails: dict[str, list[float]] = {}
MAX_GAP_KM = 3.0  # a typed or tapped place must be this close to the phone's GPS


# Limits per visitor, so one person cannot use up the free language-model quota or flood the queue.
LIMITS = {"ask": (20, 600), "run": (4, 3600), "help": (10, 3600)}   # (requests, seconds)
_hits: dict[tuple[str, str], list[float]] = {}


def _limit(request: Request, kind: str) -> None:
    n, window = LIMITS[kind]
    who = (kind, request.client.host if request.client else "?")
    now = time.time()
    recent = [t for t in _hits.get(who, []) if now - t < window]
    if len(recent) >= n:
        raise HTTPException(429, "Too many requests from this device. Please wait a few minutes and try again.")
    _hits[who] = recent + [now]


def _rescue_only(request: Request, pin: str | None) -> None:
    if not RESCUE_PIN:
        return
    ip = request.client.host if request.client else "?"
    now = time.time()
    fails = [t for t in _pin_fails.get(ip, []) if now - t < 600]
    if len(fails) >= 10:
        raise HTTPException(429, "Too many wrong PINs. Try again in 10 minutes.")
    if not pin:
        raise HTTPException(401, "Rescue team PIN needed.")
    if not hmac.compare_digest(pin.encode(), RESCUE_PIN.encode()):
        _pin_fails[ip] = fails + [now]
        raise HTTPException(401, "Wrong PIN.")
    _pin_fails.pop(ip, None)


class RunRequest(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    date: Date
    name: str | None = None
    time_utc: str | None = None
    source: str | None = None
    force: bool = False


class Ask(BaseModel):
    question: str
    lang: str = "en"


class FloodPathReq(BaseModel):
    lon: float
    lat: float
    date: Date | None = None


class SafetyReq(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    run_id: str | None = None


class HelpReq(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100000)
    how: str = Field("map", pattern="^(gps|typed|map)$")
    place_text: str | None = Field(None, max_length=120)
    name: str | None = Field(None, max_length=60)
    phone: str | None = Field(None, max_length=20, pattern=r"^[0-9+\-() ]*$")
    people: int = Field(1, ge=1, le=999)
    note: str | None = Field(None, max_length=300)
    lang: str = Field("en", pattern="^(en|ne)$")
    consent: bool
    run_id: str | None = None
    # the phone's own location, read again when Send is tapped (stops requests sent from far away)
    gps_lat: float = Field(..., ge=-90, le=90)
    gps_lon: float = Field(..., ge=-180, le=180)
    gps_accuracy_m: float | None = Field(None, ge=0, le=100000)


class StatusReq(BaseModel):
    status: str = Field(..., pattern="^(new|seen|team_sent|helped)$")


def worker():
    from floodmap.pipeline import run
    from floodmap.sitrep import write_reports
    while True:
        job = jobs.get()
        state["current"] = job["id"]
        try:
            out = run(job["bbox"], job["date"], source=job.get("source"), time_utc=job.get("time_utc"),
                      name=job.get("name"), force=job.get("force", False))
            info = json.loads((out / "run.json").read_text(encoding="utf-8"))
            info["message"] = "Writing situation reports"
            (out / "run.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            write_reports(out)
            info["message"] = "Done"
            (out / "run.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            try:  # new maps for this area: update the danger level of open help requests inside it
                helpdesk.recheck(bbox=list(job["bbox"]))
            except Exception as err:  # noqa: BLE001 - never let this break a finished run
                print("help request re-check failed:", err)
        except Exception as err:  # noqa: BLE001 - shown to the user through run.json
            print("run failed:", err)
        finally:
            state["current"] = None


def _mark_unfinished() -> None:
    """Runs left waiting or running when the server stopped will never finish: say so, so Run can start them again."""
    for f in RUNS_DIR.glob("*/run.json") if RUNS_DIR.exists() else []:
        try:
            i = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if i.get("status") in ("queued", "running"):
            i.update(status="error", error="the server restarted", message="Stopped when the server restarted. Press Run analysis again.")
            f.write_text(json.dumps(i), encoding="utf-8")


if not os.getenv("FLOOD_TESTS"):   # the tests never change real results
    _mark_unfinished()
threading.Thread(target=worker, daemon=True).start()


def _run_dir(rid: str) -> Path:
    d = RUNS_DIR / rid
    if not rid.isalnum() or not d.is_dir():
        raise HTTPException(404, "run not found")
    return d


# Links made before the home page existed (/?run=..., /?mode=help, /?request=...) still open the dashboard.
DASHBOARD_PARAMS = {"run", "mode", "request", "focus", "tab", "lat", "lon", "acc"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if DASHBOARD_PARAMS & set(request.query_params):
        return dashboard()
    return (STATIC / "home" / "home.html").read_text(encoding="utf-8")


@app.get("/app", response_class=HTMLResponse)
def dashboard():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/healthz")
def healthz():
    """For the live launcher: the server is up."""
    return {"ok": True}


@app.get("/report.pdf")
def report_pdf():
    f = STATIC.parents[1] / "report" / "report.pdf"
    if not f.exists():
        raise HTTPException(404, "report not found")
    return FileResponse(f, media_type="application/pdf")


@app.get("/api/runs")
def list_runs():
    out = []
    if RUNS_DIR.exists():
        for d in RUNS_DIR.iterdir():
            f = d / "run.json"
            if f.exists():
                try:
                    i = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                out.append({**{k: i.get(k) for k in ("run_id", "name", "bbox", "flood_date", "status", "progress",
                                                       "message", "started", "res_m", "source")},
                            "featured": i.get("run_id") == FEATURED_RUN})
    return sorted(out, key=lambda r: r.get("started") or "", reverse=True)


@app.post("/api/runs")
def start_run(req: RunRequest, request: Request):
    try:
        grid = make_grid(req.bbox)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    # Only this computer may redo a finished result, so visitors on the live link can't overwrite the saved ones
    if req.force and (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost"):
        req.force = False
    from floodmap.config import DATA_SOURCE
    rid = run_id(tuple(req.bbox), req.date, (req.source or DATA_SOURCE).lower(), grid.res, req.time_utc)
    d = RUNS_DIR / rid
    if not req.force:
        if (d / "facts.json").exists():
            return {"run_id": rid}                       # already done: opening it again is instant
        busy = state["current"] == rid or any(j["id"] == rid for j in list(jobs.queue))
        if busy:
            return {"run_id": rid}                       # already on its way (e.g. Run clicked twice)
    _limit(request, "run")
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"run_id": rid, "name": req.name, "bbox": req.bbox,
                                             "flood_date": req.date.isoformat(), "status": "queued",
                                             "progress": 0, "message": "Waiting in queue", "log": []}), encoding="utf-8")
    jobs.put({"id": rid, "bbox": req.bbox, "date": req.date, "name": req.name, "time_utc": req.time_utc,
              "source": req.source, "force": req.force})
    return {"run_id": rid}


@app.get("/api/runs/{rid}")
def run_status(rid: str):
    return json.loads((_run_dir(rid) / "run.json").read_text(encoding="utf-8"))


@app.get("/api/runs/{rid}/facts")
def run_facts(rid: str):
    f = _run_dir(rid) / "facts.json"
    if not f.exists():
        raise HTTPException(404, "not ready")
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.get("/api/runs/{rid}/layers/{name}")
def run_layer(rid: str, name: str):
    f = _run_dir(rid) / "layers" / f"{name}.geojson"
    if not name.replace("_", "").isalnum() or not f.exists():
        raise HTTPException(404, "no such layer")
    return FileResponse(f, media_type="application/geo+json")


@app.get("/api/runs/{rid}/overlays")
def run_overlays(rid: str):
    f = _run_dir(rid) / "overlays" / "overlays.json"
    return json.loads(f.read_text()) if f.exists() else {}


@app.get("/api/runs/{rid}/overlays/{name}.png")
def run_overlay_png(rid: str, name: str):
    f = _run_dir(rid) / "overlays" / f"{name}.png"
    if not name.replace("_", "").isalnum() or not f.exists():
        raise HTTPException(404, "no such overlay")
    return FileResponse(f, media_type="image/png")


@app.get("/api/runs/{rid}/sitrep/{lang}", response_class=HTMLResponse)
def run_sitrep(rid: str, lang: str):
    d = _run_dir(rid)
    if lang not in ("en", "ne"):
        raise HTTPException(404)
    f = d / f"sitrep_{lang}.html"
    if not f.exists():
        from floodmap.sitrep import write_reports
        write_reports(d)
    return f.read_text(encoding="utf-8").replace('src="sitrep_map.png"', f'src="/api/runs/{rid}/sitrep_map.png"')


@app.get("/api/runs/{rid}/sitrep_map.png")
def run_sitrep_map(rid: str):
    f = _run_dir(rid) / "sitrep_map.png"
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/png")


@app.post("/api/runs/{rid}/ask")
def run_ask(rid: str, q: Ask, request: Request):
    from floodmap.copilot import answer
    _limit(request, "ask")
    facts = json.loads((_run_dir(rid) / "facts.json").read_text(encoding="utf-8"))
    return answer(facts, q.question, "ne" if q.lang == "ne" else "en")


@app.post("/api/floodpath")
def flood_path(req: FloodPathReq):
    from floodmap.floodpath import trace
    try:
        return trace(req.lon, req.lat, req.date)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(400, str(err)) from err


# ---------------- "I need help" side ----------------
@app.post("/api/safety")
def safety_check(req: SafetyReq):
    from floodmap.safety import check
    return check(req.lon, req.lat, req.run_id if req.run_id and req.run_id.isalnum() else None)


@app.get("/api/places")
def places(q: str = ""):
    from floodmap.safety import search_places
    return search_places(q[:80])


@app.post("/api/help")
def help_request(req: HelpReq, request: Request):
    """Save a help request, but only from a phone that is inside a mapped flood area, and (for a typed
    or tapped place) close to that place. The phone's GPS becomes the request's location."""
    from floodmap.safety import NE_DIGITS, _km, check
    if not req.consent:
        raise HTTPException(400, "Please agree to share your location first.")
    _limit(request, "help")
    phone = check(req.gps_lon, req.gps_lat, req.run_id if req.run_id and req.run_id.isalnum() else None)
    if phone["level"] == "unknown":
        raise HTTPException(403, {
            "code": "outside",
            "en": "Your phone is outside every flood area we have mapped, so this request was not sent. "
                  "If you are in danger, call 112 (India) or 100 (Nepal).",
            "ne": "तपाईंको फोन हामीले नक्सा बनाएको बाढी क्षेत्रभन्दा बाहिर छ, त्यसैले यो अनुरोध पठाइएन। "
                  "खतरामा हुनुहुन्छ भने ११२ (भारत) वा १०० (नेपाल) मा फोन गर्नुहोस्।"})
    gap_km = None
    if req.how in ("typed", "map"):  # "read both locations": the chosen place must be where the phone is
        gap_km = round(_km(req.lat, req.lon, req.gps_lat, req.gps_lon), 1)
        if gap_km > max(MAX_GAP_KM, 2 * (req.gps_accuracy_m or 0) / 1000):
            raise HTTPException(403, {
                "code": "far",
                "en": f"Your phone is about {gap_km} km from the place you chose. Help requests can only be sent "
                      "from the place itself. If someone else needs help there, call 112 (India) or 100 (Nepal).",
                "ne": f"तपाईंको फोन तपाईंले छानेको ठाउँबाट करिब {str(gap_km).translate(NE_DIGITS)} किमी टाढा छ। "
                      "मद्दत अनुरोध त्यही ठाउँबाट मात्र पठाउन सकिन्छ। त्यहाँ अरू कसैलाई मद्दत चाहिएको छ भने "
                      "११२ (भारत) वा १०० (नेपाल) मा फोन गर्नुहोस्।"})
    clean = lambda v: v.strip() if isinstance(v, str) else v  # noqa: E731
    person = {
        "lat": req.gps_lat, "lon": req.gps_lon, "accuracy_m": req.gps_accuracy_m,  # where the phone really is
        "how": req.how, "place_text": clean(req.place_text),
        "chosen_lat": req.lat if req.how != "gps" else None, "chosen_lon": req.lon if req.how != "gps" else None,
        "gap_km": gap_km, "gps_checked": True,
        "name": clean(req.name), "phone": clean(req.phone), "people": req.people, "note": clean(req.note), "lang": req.lang,
    }
    rec = helpdesk.add(person, phone)
    return {"id": rec["id"], "status": rec["status"], "level": rec["level"]}


@app.get("/api/help")
def help_list(request: Request, x_rescue_pin: str | None = Header(None)):
    _rescue_only(request, x_rescue_pin)
    return helpdesk.all_requests()


@app.get("/api/help/summary")
def help_summary():
    """Counts only, for the "new" badges: no names, phones or places."""
    return {**helpdesk.summary(), "pin_required": bool(RESCUE_PIN)}


@app.post("/api/help/recheck")
def help_recheck(request: Request, x_rescue_pin: str | None = Header(None)):
    _rescue_only(request, x_rescue_pin)
    return helpdesk.recheck()


@app.get("/api/help/{hid}")
def help_one(hid: str):
    """What the sender may see about their own request: status only."""
    rec = helpdesk.get(hid.upper()) if hid.isalnum() else None
    if rec is None:
        raise HTTPException(404, "request not found")
    return {k: rec[k] for k in ("id", "status", "level", "created_utc", "history")}


@app.post("/api/help/{hid}/status")
def help_status(hid: str, req: StatusReq, request: Request, x_rescue_pin: str | None = Header(None)):
    _rescue_only(request, x_rescue_pin)
    rec = helpdesk.set_status(hid.upper(), req.status) if hid.isalnum() else None
    if rec is None:
        raise HTTPException(404, "request not found")
    return rec

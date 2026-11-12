"""HTTP API + web app + customer tracking page.

Run from backend/:
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

import html
import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine, hhmm, parse_dt
from .geocode import Geocoder
from .routing import OsrmRouter

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATA = Path(os.environ.get("DATA_DIR", BACKEND / "data"))
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
TEST_DATA = ROOT / "test-data"

geocoder = Geocoder(Path(os.environ.get("ADDR_DB", DATA / "addresses.sqlite")))
router = OsrmRouter(OSRM_URL, DATA / "cache",
                    min_interval_s=1.0 if "project-osrm.org" in OSRM_URL else 0.0)
engine = Engine(DATA, geocoder, router, TEST_DATA)

app = FastAPI(title="Dostava BG", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


def _run(fn, *a, save=True, **kw):
    with engine.lock:
        try:
            out = fn(*a, **kw)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e).strip("'"))
        if save:
            engine.save()
        return out


def parse_qr(raw: str) -> dict:
    """QR payload: JSON {"v":1,"id":..,"adresa":..} or 'id|ime|tel|adresa|mesto|od|do'."""
    raw = (raw or "").strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"QR nije ispravan JSON: {e}")
    if raw.count("|") >= 3:
        keys = ["id", "ime", "tel", "adresa", "mesto", "od", "do"]
        return dict(zip(keys, (x.strip() for x in raw.split("|"))))
    raise ValueError("Nepoznat format QR koda (očekujem JSON ili id|ime|tel|adresa|mesto|od|do)")


# ---------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"ok": True, "addresses": geocoder.count, "osrm": OSRM_URL}


@app.get("/api/state")
def state():
    return _run(engine.snapshot)  # saves too: refresh() may have sent notifications


@app.get("/api/geometry")
def geometry():
    """Road geometry of the remaining route; refetch when state.geom_version changes."""
    def go():
        try:
            return engine.geometry()
        except Exception as e:  # never break the UI because of the map line
            return {"version": engine.geometry_version(), "current": [], "rest": [],
                    "error": str(e)}
    return _run(go, save=False)


@app.post("/api/packages")
def add_package(body: dict = Body(...)):
    source = body.get("source") or ("qr" if "qr" in body else "manual")

    def go():
        data = parse_qr(body["qr"]) if "qr" in body else body
        pkg, new = engine.add_package(data, source)
        return {"package": pkg, "new": new}
    return _run(go)


@app.post("/api/packages/import")
def import_csv(body: dict = Body(...)):
    added, errors = _run(engine.import_csv, body.get("csv", ""))
    return {"added": added, "errors": errors}


@app.get("/api/datasets")
def datasets():
    return sorted(p.stem for p in TEST_DATA.glob("*.csv"))


@app.post("/api/demo/{name}")
def load_demo(name: str):
    added, errors = _run(engine.load_demo, name)
    return {"added": added, "errors": errors}


@app.put("/api/packages/{pid}/location")
def set_location(pid: str, body: dict = Body(...)):
    _run(engine.set_location, pid, float(body["lat"]), float(body["lon"]))
    return {"ok": True}


@app.delete("/api/packages/{pid}")
def delete_package(pid: str):
    _run(engine.delete_package, pid)
    return {"ok": True}


@app.post("/api/reset")
def reset():
    _run(engine.reset)
    return {"ok": True}


@app.put("/api/settings")
def settings(body: dict = Body(...)):
    _run(engine.update_settings, body)
    return engine.s["settings"]


@app.post("/api/plan")
def plan():
    _run(engine.plan, reason="ručno")
    return {"ok": True, "plan": {k: v for k, v in (engine.s["plan"] or {}).items() if k != "order"}}


@app.post("/api/day/start")
def start_day():
    _run(engine.start_day)
    return {"ok": True}


@app.post("/api/courier/position")
def position(body: dict = Body(...)):
    _run(engine.courier_position, float(body["lat"]), float(body["lon"]))
    return {"ok": True}


@app.post("/api/stops/{sid}/{action}")
def stop_action(sid: str, action: str):
    if action == "arrived":
        _run(engine.arrive, sid)
    elif action in ("delivered", "failed"):
        _run(engine.stop_action, sid, action)
    else:
        raise HTTPException(400, "akcija mora biti arrived | delivered | failed")
    return {"ok": True}


@app.post("/api/sim/start")
def sim_start(body: dict = Body(default={})):
    _run(engine.sim_start, body.get("seed"))
    return {"ok": True}


@app.post("/api/sim/step")
def sim_step(body: dict = Body(default={})):
    _run(engine.sim_step, float(body.get("minutes", 5)))
    return {"ok": True, "now": engine.s["sim"]["now"]}


@app.post("/api/sim/stop")
def sim_stop():
    _run(engine.sim_stop)
    return {"ok": True}


@app.get("/api/geocode")
def geocode(q: str, place: str = ""):
    return geocoder.geocode(q, place).to_dict()


@app.get("/api/track/{track}")
def track_json(track: str):
    v = _run(engine.customer_view, track)
    if not v:
        raise HTTPException(404, "Nepoznat kod pošiljke")
    return v


# ---------------------------------------------------------------- customer page
STATUS_TEXT = {"pending": "U isporuci", "delivered": "Isporučeno", "failed": "Neuspešna isporuka"}


@app.get("/t/{track}", response_class=HTMLResponse)
def track_page(track: str):
    v = _run(engine.customer_view, track)
    if not v:
        raise HTTPException(404, "Nepoznat kod pošiljke")
    p, st = v["package"], v["stop"]
    e = html.escape
    pr = p.get("promise")
    when = (f"{hhmm(parse_dt(pr['from']))}–{hhmm(parse_dt(pr['to']))}" if pr else "još nije planirano")
    if p["status"] == "delivered":
        when = f"isporučeno u {hhmm(parse_dt(p['delivered_at']))}"
    ahead = v["stops_before"]
    ahead_txt = ""
    if ahead is not None and p["status"] == "pending":
        ahead_txt = ("Kurir je na putu do vas." if ahead == 0
                     else f"Pre vas je još {ahead} {'dostava' if ahead == 1 else 'dostave' if ahead < 5 else 'dostava'}.")
    notes = "".join(f"<li><time>{hhmm(parse_dt(n['t']))}</time> {e(n['text'])}</li>"
                    for n in v["notifications"][:20])
    return f"""<!doctype html><html lang="sr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="20"><title>Pošiljka {e(p['id'])}</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--muted:#5d6470;--acc:#0b6bcb;--line:#e3e6ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111316;--card:#1a1d22;--ink:#e9ecf1;--muted:#9aa3ae;--acc:#5aa9ff;--line:#2a2f36}}}}
body{{margin:0;font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
main{{max-width:520px;margin:0 auto;padding:20px 16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:14px}}
h1{{font-size:15px;color:var(--muted);font-weight:600;margin:0 0 6px}}
.when{{font-size:30px;font-weight:700;letter-spacing:-.02em}}
.muted{{color:var(--muted)}} ul{{list-style:none;padding:0;margin:0}}
li{{padding:8px 0;border-top:1px solid var(--line)}} li:first-child{{border-top:0}}
time{{color:var(--acc);font-weight:600;margin-right:6px}}
.badge{{display:inline-block;padding:2px 10px;border-radius:999px;background:var(--line);font-size:13px}}
</style></head><body><main>
<div class="card"><h1>Pošiljka {e(p['id'])} · <span class="badge">{STATUS_TEXT.get(p['status'], p['status'])}</span></h1>
<div class="when">{e(when)}</div>
<p class="muted">{e(st['label'] if st else p['address'])}</p>
<p>{e(ahead_txt)}</p>
<p class="muted">Kurir će vas pozvati kad stigne. Izađite ispred zgrade, broj stana nije potreban.</p></div>
<div class="card"><h1>Obaveštenja</h1><ul>{notes or '<li class="muted">Još nema obaveštenja.</li>'}</ul></div>
<p class="muted" style="font-size:13px">Test stranica · {e(v['now'][:16].replace('T', ' '))} · osvežava se na 20 s</p>
</main></body></html>"""


# ---------------------------------------------------------------- static
app.mount("/test-data", StaticFiles(directory=TEST_DATA), name="test-data")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

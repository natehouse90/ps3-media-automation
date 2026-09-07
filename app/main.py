from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import Settings
from .db import Database
from .doctor import run as doctor_run
from .worker import Worker


settings = Settings().load_persisted()
settings.ensure_dirs()
db = Database(settings.db_path)
worker = Worker(settings, db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="PS3 Media Automation", version="0.2.0", lifespan=lifespan)


CSS = """body{font-family:system-ui,sans-serif;margin:0;background:#101827;color:#e8eef8}a{color:#8bd3ff}nav{padding:1rem;background:#172338}nav a{margin-right:1rem;text-decoration:none}main{max-width:1100px;margin:2rem auto;padding:0 1rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}.card{background:#1b2a40;border:1px solid #30435f;border-radius:10px;padding:1rem}.ok{color:#70e1a0}.warn{color:#ffd166}.fail{color:#ff8585}input,button{padding:.65rem;margin:.25rem 0;border-radius:6px;border:1px solid #667895;background:#0f1929;color:#fff}button{cursor:pointer;background:#2c78b8}.wide{width:100%;box-sizing:border-box}table{width:100%;border-collapse:collapse}td,th{padding:.5rem;border-bottom:1px solid #30435f;text-align:left}pre{white-space:pre-wrap;background:#0b1220;padding:1rem;border-radius:8px}"""


def page(title: str, body: str) -> str:
    nav = "<nav><a href='/'>Dashboard</a><a href='/jobs'>Incoming / Jobs</a><a href='/library'>Library</a><a href='/search'>Search</a><a href='/irds'>IRDs</a><a href='/doctor'>Doctor</a><a href='/settings'>Settings</a><a href='/logs'>Logs / Audits</a></nav>"
    return f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>{CSS}</style></head><body>{nav}<main><h1>{title}</h1>{body}</main></body></html>"


def esc(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": "0.2.0"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    if not settings.configured:
        return RedirectResponse("/setup", status_code=307)
    counts = db.counts()
    cards = "".join(f"<div class='card'><h3>{esc(key)}</h3><strong>{value}</strong></div>" for key, value in sorted(counts.items()))
    free = 0
    try:
        free = settings.state_root.stat().st_dev and __import__("shutil").disk_usage(settings.state_root).free // 1024**3
    except OSError:
        pass
    return page("Dashboard", f"<div class='grid'>{cards}<div class='card'><h3>Free space</h3><strong>{free} GiB</strong></div></div><p>Sources remain immutable. PS3 folder candidates are validated through the proven IRD-backed engine before ISO publication.</p>")


@app.get("/setup", response_class=HTMLResponse)
def setup_page() -> str:
    body = """<p>Configure storage and optional integrations. Secrets are not shown again after saving.</p><form method='post' action='/setup'>
    <label>Library root<input class='wide' name='library_root' required value='/srv/ps3-library'></label>
    <label>Incoming directory<input class='wide' name='incoming_dir' required value='/srv/ps3-library/incoming'></label>
    <label>Work directory<input class='wide' name='work_root' required value='/srv/ps3-library/.iso-build-work'></label>
    <label>IRD directory<input class='wide' name='ird_root' required value='/srv/ps3-library/.ingest/irds'></label>
    <label>PS3ISO directory<input class='wide' name='iso_root' required value='/srv/ps3-library/PS3ISO'></label>
    <label>PS3 IP or hostname<input class='wide' name='ps3_ip' placeholder='192.168.1.50'></label>
    <label>ps3netsrv host<input class='wide' name='ps3netsrv_host' placeholder='MEDIA-SERVER'></label>
    <label>ps3netsrv port<input class='wide' name='ps3netsrv_port' value='38008'></label>
    <label>Prowlarr URL (optional)<input class='wide' name='prowlarr_url' placeholder='http://prowlarr:9696'></label>
    <label>Prowlarr API key (optional; stored protected)<input class='wide' type='password' name='prowlarr_api_key'></label>
    <button type='submit'>Save and run Doctor</button></form>"""
    return page("First-run setup", body)


@app.post("/setup")
def setup_submit(library_root: str = Form(...), incoming_dir: str = Form(...), work_root: str = Form(...), ird_root: str = Form(...), iso_root: str = Form(...), ps3_ip: str = Form(""), ps3netsrv_host: str = Form(""), ps3netsrv_port: int = Form(38008), prowlarr_url: str = Form(""), prowlarr_api_key: str = Form("")) -> RedirectResponse:
    settings.save(locals())
    settings.ensure_dirs()
    return RedirectResponse("/doctor", status_code=303)


@app.get("/api/jobs")
def api_jobs() -> list[dict[str, Any]]:
    return db.jobs()


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int) -> JSONResponse:
    job = db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    db.update_job(job_id, state="QUEUED", stage="retry queued", reason="Manual retry requested")
    return JSONResponse({"ok": True, "job_id": job_id})


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page() -> str:
    rows = "".join(f"<tr><td>{j['id']}</td><td>{esc(j['title_id'] or 'Detecting')}</td><td>{esc(j['source'])}</td><td>{esc(j['state'])}</td><td>{esc(j['reason'])}</td><td><form method='post' action='/api/jobs/{j['id']}/retry'><button>Retry</button></form></td></tr>" for j in db.jobs())
    return page("Incoming / Jobs", f"<table><tr><th>ID</th><th>TITLE_ID</th><th>Source</th><th>State</th><th>Details</th><th>Action</th></tr>{rows}</table>")


@app.get("/doctor", response_class=HTMLResponse)
def doctor_page() -> str:
    checks = doctor_run(settings)
    body = "<p>Doctor checks the application without changing the live media stack.</p><table><tr><th>Check</th><th>Status</th><th>Detail</th><th>Fix</th></tr>" + "".join(f"<tr><td>{esc(c['name'])}</td><td class='{c['status'].lower()}'>{esc(c['status'])}</td><td>{esc(c['detail'])}</td><td>{esc(c.get('fix'))}</td></tr>" for c in checks) + "</table>"
    return page("Doctor", body)


@app.get("/api/doctor")
def api_doctor() -> list[dict[str, str]]:
    return doctor_run(settings)


@app.get("/library", response_class=HTMLResponse)
def library_page() -> str:
    items = []
    if settings.iso_root.exists():
        for iso in sorted(settings.iso_root.glob("*.iso")):
            items.append(f"<tr><td>{esc(iso.stem)}</td><td>{iso.stat().st_size / 1024**3:.2f} GiB</td><td>Published</td><td><form method='post' action='/library/mount'><input type='hidden' name='path' value='{esc(str(iso))}'><button>Mount</button></form></td></tr>")
    return page("Library", "<p>Mount requests are manual only; this application never auto-launches a game.</p><table><tr><th>Title</th><th>Size</th><th>Status</th><th>Action</th></tr>" + "".join(items) + "</table>")


@app.post("/library/mount")
def mount_library(path: str = Form(...)) -> RedirectResponse:
    resolved = Path(path).resolve()
    if settings.iso_root.resolve() not in resolved.parents or resolved.suffix.lower() != ".iso":
        return RedirectResponse("/library", status_code=303)
    db.log("INFO", f"manual mount requested: {resolved.name}")
    return RedirectResponse("/library", status_code=303)


def prowlarr_search(term: str, limit: int) -> list[dict[str, Any]]:
    if not settings.prowlarr_url or not settings.prowlarr_api_key:
        raise RuntimeError("Prowlarr is not configured")
    script = Path(__file__).parents[1] / "scripts" / "ps3-search.py"
    env = os.environ.copy()
    env.update({"PROWLARR_SEARCH_URL": settings.prowlarr_url.rstrip("/") + "/api/v1/search", "PROWLARR_API_KEY": settings.prowlarr_api_key})
    completed = subprocess.run([sys.executable, str(script), term, "--limit", str(limit), "--json"], capture_output=True, text=True, env=env, timeout=60)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Prowlarr search failed")
    return json.loads(completed.stdout)


@app.get("/search", response_class=HTMLResponse)
def search_page(q: str = "", limit: int = 25) -> str:
    body = "<form method='get'><input class='wide' name='q' placeholder='Search PS3 releases' value='" + esc(q) + "'><input name='limit' type='number' min='1' max='100' value='" + str(limit) + "'><button>Search</button></form>"
    if q:
        try:
            results = prowlarr_search(q, limit)
            body += "<table><tr><th>Title</th><th>Indexer</th><th>Size</th><th>Date</th></tr>" + "".join(f"<tr><td>{esc(item.get('title'))}</td><td>{esc(item.get('indexer'))}</td><td>{esc(item.get('size'))}</td><td>{esc(item.get('publishDate'))}</td></tr>" for item in results) + "</table>"
        except Exception as exc:
            body += f"<p class='warn'>Needs attention: {esc(exc)}</p>"
    return page("Search", body)


@app.get("/irds", response_class=HTMLResponse)
def irds_page() -> str:
    items = sorted(settings.ird_root.rglob("*.ird")) if settings.ird_root.exists() else []
    form = "<form method='post' action='/irds' enctype='multipart/form-data'><input type='file' name='ird' accept='.ird' required><button>Upload IRD</button></form>"
    return page("IRDs", "<p>Trusted IRDs are user-supplied and are never bundled by this project.</p>" + form + "<ul>" + "".join(f"<li>{esc(item.name)}</li>" for item in items) + "</ul>")


@app.post("/irds")
async def upload_ird(ird: UploadFile = File(...)) -> RedirectResponse:
    name = Path(ird.filename or "").name
    if not name.lower().endswith(".ird") or not name or name.startswith("."):
        return RedirectResponse("/irds", status_code=303)
    destination = settings.ird_root / name
    if destination.exists():
        return RedirectResponse("/irds", status_code=303)
    temporary = settings.ird_root / (".upload-" + name)
    with temporary.open("wb") as handle:
        while chunk := await ird.read(1024 * 1024):
            handle.write(chunk)
    temporary.replace(destination)
    return RedirectResponse("/irds", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> str:
    return page("Settings", f"<p>Configured: {settings.configured}</p><p>Storage root: {esc(settings.library_root)}</p><p>PS3 and Prowlarr secrets are never rendered in this page.</p><p><a href='/setup'>Open setup wizard</a></p>")


@app.get("/logs", response_class=HTMLResponse)
def logs_page() -> str:
    return page("Logs / Audits", "<pre>" + esc("\n".join(f"{e['created_at']} {e['level']} {e['message']}" for e in db.events())) + "</pre>")

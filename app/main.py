from __future__ import annotations

import html
import json
import os
import re
import socket
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
from .monitor import sab_queue
from .storage import configured_storage_usages, human_size
from .worker import Worker


ROOT = Path(__file__).parents[1]
try:
    APP_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = "0.2.3"

settings = Settings().load_persisted()
settings.ensure_dirs()
db = Database(settings.db_path)
worker = Worker(settings, db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="PS3 Media Automation", version=APP_VERSION, lifespan=lifespan)

CSS = """
:root{color-scheme:dark;--bg:#0b111c;--panel:#131e2e;--line:#293b55;--text:#e8eef8;--muted:#9fb0c7;--blue:#4ca7ee;--green:#67dc9a;--yellow:#ffd166;--red:#ff8585}
*{box-sizing:border-box}body{font-family:Inter,ui-sans-serif,system-ui,sans-serif;margin:0;background:var(--bg);color:var(--text);font-size:14px}a{color:#8bd3ff}nav{position:sticky;top:0;z-index:5;display:flex;gap:1.1rem;align-items:center;padding:.8rem max(1rem,calc((100% - 1400px)/2));background:#111b2aaa;border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}nav a{text-decoration:none;font-weight:650}.brand{margin-right:auto;color:#fff}main{max-width:1400px;margin:1.2rem auto;padding:0 1rem 2rem}h1{font-size:1.45rem;margin:.2rem 0 1rem}h2{font-size:1.08rem;margin:0}h3{font-size:.95rem;margin:.1rem 0 .6rem}.muted{color:var(--muted)}.statusbar,.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.7rem}.statusbar{margin-bottom:.9rem}.status,.card{background:var(--panel);border:1px solid var(--line);border-radius:10px}.status{padding:.65rem .8rem}.status strong{display:block;margin-top:.15rem}.card{padding:.9rem;margin-bottom:.85rem}.section-head{display:flex;align-items:center;justify-content:space-between;gap:.7rem;margin-bottom:.65rem}.ok{color:var(--green)}.warn{color:var(--yellow)}.fail{color:var(--red)}input,button,.button{padding:.58rem .72rem;border-radius:6px;border:1px solid #526984;background:#0d1726;color:#fff;font:inherit}button,.button{cursor:pointer;background:#246da7;text-decoration:none;display:inline-block;font-weight:650}.button.secondary{background:#26364c}.button.disabled{opacity:.5;cursor:not-allowed}.search-form{display:grid;grid-template-columns:1fr 90px auto;gap:.5rem}.wide{width:100%}table{width:100%;border-collapse:collapse}td,th{padding:.52rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{color:var(--muted);font-size:.76rem;text-transform:uppercase;letter-spacing:.04em}.table-wrap{overflow-x:auto}.title{font-weight:650;min-width:250px}.badge{display:inline-block;padding:.18rem .45rem;border-radius:999px;background:#253850;color:#cfe5ff;font-size:.75rem;white-space:nowrap}.progress{width:130px;height:7px;background:#07101c;border-radius:7px;overflow:hidden;margin-top:.3rem}.progress span{display:block;height:100%;background:var(--blue)}pre{white-space:pre-wrap;background:#07101c;padding:.8rem;border-radius:8px}.empty{padding:1rem;color:var(--muted);text-align:center}.actions{white-space:nowrap}.compact{margin:0}.counts{display:flex;gap:.4rem;flex-wrap:wrap}.page-form label{display:block;margin:.6rem 0}.stage{font-weight:700}.stage.fail{color:var(--red)}.stage.warn{color:var(--yellow)}
@media(max-width:720px){nav{overflow-x:auto}.brand{display:none}main{margin-top:.8rem}.search-form{grid-template-columns:1fr auto}.search-form input[type=number]{display:none}th:nth-child(3),td:nth-child(3),th:nth-child(5),td:nth-child(5){display:none}.title{min-width:190px}}
"""


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def page(title: str, body: str) -> str:
    nav = "<nav><a class='brand' href='/'>PS3 Media Automation</a><a href='/'>Home</a><a href='/jobs'>Jobs</a><a href='/irds'>IRDs</a><a href='/doctor'>Doctor</a><a href='/settings'>Settings</a><a href='/logs'>Logs</a></nav>"
    return f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head><body>{nav}<main>{body}</main></body></html>"


def tcp_status(host: str, port: int) -> tuple[str, str]:
    if not host:
        return "Not configured", "warn"
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return "Connected", "ok"
    except OSError:
        return "Unavailable", "fail"


def ps3_status() -> tuple[str, str]:
    if not settings.ps3_ip:
        return "Not configured", "warn"
    try:
        with urllib.request.urlopen(f"http://{settings.ps3_ip}/", timeout=1.2) as response:
            return ("Connected" if response.status < 500 else "HTTP error"), ("ok" if response.status < 500 else "fail")
    except Exception:
        return "Unavailable", "fail"


def title_id(text: str) -> str:
    match = re.search(r"\b([A-Z]{4}\d{5})\b", text.upper())
    return match.group(1) if match else ""


def item_key(text: str, tid: str = "") -> str:
    if tid:
        return tid.upper()
    name = Path(text).name
    name = re.sub(r"\.(iso|nzb|rar|zip)$", "", name, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def known_isos() -> dict[str, dict[str, Any]]:
    root = Path(os.environ.get("PS3_METADATA_ROOT", str(settings.library_root / ".ingest")))
    try:
        payload = json.loads((root / "known-isos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    records = payload if isinstance(payload, list) else payload.get("isos", []) if isinstance(payload, dict) else []
    found: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict) and record.get("path"):
            found[Path(str(record["path"])).name.casefold()] = record
    return found


def library_items() -> list[dict[str, Any]]:
    records = known_isos()
    mounted = os.environ.get("PS3_MOUNTED_TITLE_ID", "").upper()
    items: list[dict[str, Any]] = []
    if not settings.iso_root.exists():
        return items
    for iso in sorted(settings.iso_root.iterdir(), key=lambda p: p.name.casefold()):
        if not iso.is_file() or iso.suffix.casefold() != ".iso":
            continue
        stat = iso.stat()
        record = records.get(iso.name.casefold(), {})
        tid = str(record.get("title_id") or title_id(iso.name))
        expected = record.get("size")
        if record and (expected in (None, stat.st_size)):
            verified = "Verified"
        elif record:
            verified = "Size changed"
        else:
            verified = "Unverified"
        items.append({"path": iso, "title": re.sub(r"\s*\[[A-Z]{4}\d{5}\].*$", "", iso.stem).strip(), "title_id": tid, "size": stat.st_size, "verification": verified, "mounted": bool(mounted and tid == mounted)})
    return items


STATE_LABELS = {
    "QUEUED": "IMPORTING", "IDENTIFYING": "IMPORTING", "AUDITING": "VALIDATING",
    "WAITING_FOR_IRD": "WAITING FOR IRD", "RECONSTRUCTING": "RECONSTRUCTING",
    "BUILDING_ISO": "BUILDING ISO", "VERIFYING": "VERIFYING", "PUBLISHING": "PUBLISHING",
    "WAITING_FOR_PS3_IDLE": "REFRESHING PS3", "NEEDS_ATTENTION": "NEEDS ATTENTION", "FAILED": "FAILED",
}


def processing_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sab = sab_queue()
    combined: dict[str, dict[str, Any]] = {}
    for slot in sab.get("jobs", []):
        key = item_key(str(slot.get("title", "")))
        combined[key] = {**slot, "stage": f"DOWNLOADING {slot.get('progress', 0):.0f}%", "kind": "download", "reason": ""}
    for job in db.jobs(200):
        state = str(job.get("state") or "")
        if state == "READY":
            continue
        source = str(job.get("source") or "")
        tid = str(job.get("title_id") or "")
        source_key = item_key(source)
        key = source_key if source_key in combined else item_key(source, tid)
        item = combined.get(key, {})
        item.update({
            "id": job.get("id"), "title": job.get("title") or Path(source).name or tid or "PS3 title",
            "title_id": tid, "stage": STATE_LABELS.get(state, state.replace("_", " ")),
            "kind": "processing", "reason": job.get("reason") or job.get("stage") or "",
            "progress": item.get("progress"), "speed": item.get("speed", "—"), "eta": item.get("eta", "—"),
        })
        combined[key] = item
    return list(combined.values()), sab


def prowlarr_search(term: str, limit: int) -> dict[str, Any]:
    if not settings.prowlarr_url or not settings.prowlarr_api_key:
        raise RuntimeError("Prowlarr is not configured")
    script = ROOT / "scripts" / "ps3-search.py"
    env = os.environ.copy()
    env.update({"PROWLARR_SEARCH_URL": settings.prowlarr_url.rstrip("/") + "/api/v1/search", "PROWLARR_API_KEY": settings.prowlarr_api_key})
    completed = subprocess.run([sys.executable, str(script), term, "--limit", str(limit), "--json"], capture_output=True, text=True, env=env, timeout=90)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "Prowlarr search failed")
    payload = json.loads(completed.stdout)
    return payload if isinstance(payload, dict) else {"counts": {}, "results": payload}


def categories_text(item: dict[str, Any]) -> str:
    categories = item.get("categories") or []
    values = []
    for category in categories if isinstance(categories, list) else []:
        if isinstance(category, dict):
            values.append(str(category.get("name") or category.get("id") or ""))
        else:
            values.append(str(category))
    return ", ".join(v for v in values if v) or "—"


def prowlarr_result_url(item: dict[str, Any]) -> str:
    base = os.environ.get("PROWLARR_PUBLIC_URL", "").strip() or settings.prowlarr_url
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    query: dict[str, str] = {"query": str(item.get("title") or "")}
    if item.get("indexerId") is not None:
        query["indexerIds"] = str(item["indexerId"])
    if item.get("ps3SearchCategory") is not None:
        query["categories"] = str(item["ps3SearchCategory"])
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/search", urllib.parse.urlencode(query), ""))


def search_panel(q: str, limit: int) -> str:
    body = f"<section class='card' id='search'><div class='section-head'><h2>Search</h2><span class='muted'>Search only — nothing downloads automatically</span></div><form class='search-form' method='get' action='/'><input name='q' placeholder='Search PS3 releases' value='{esc(q)}' autofocus><input name='limit' type='number' min='1' max='100' value='{limit}'><button>Search</button></form>"
    if not q:
        return body + "</section>"
    try:
        payload = prowlarr_search(q, limit)
        results = payload.get("results", [])
        counts = payload.get("counts", {})
        badges = "".join(f"<span class='badge'>{esc(name)}: {count}</span>" for name, count in counts.items())
        rows = ""
        for item in results:
            size = human_size(int(item.get("size") or 0)) if item.get("size") else "—"
            age = item.get("age")
            date = str(item.get("publishDate") or item.get("releaseDate") or "")[:10]
            when = f"{age}d" if age not in (None, "") else date or "—"
            url = prowlarr_result_url(item)
            action = f"<a class='button' target='_blank' rel='noopener' href='{esc(url)}'>Open in Prowlarr</a>" if url else "<span class='button disabled'>Action unavailable</span>"
            rows += f"<tr><td class='title'>{esc(item.get('title'))}</td><td>{esc(item.get('indexer') or item.get('indexerName'))}</td><td>{esc(size)}</td><td>{esc(when)}</td><td>{esc(categories_text(item))}</td><td class='actions'>{action}</td></tr>"
        body += f"<div class='section-head'><div class='counts'><span class='badge'>{len(results)} merged results</span>{badges}</div></div><div class='table-wrap'><table><tr><th>Title</th><th>Indexer</th><th>Size</th><th>Age/date</th><th>Category</th><th>Action</th></tr>{rows}</table></div>" if rows else "<div class='empty'>No results.</div>"
    except Exception as exc:
        body += f"<p class='warn'>Needs attention: {esc(exc)}</p>"
    return body + "</section>"


def processing_panel() -> str:
    items, sab = processing_items()
    rows = ""
    for item in items:
        stage = str(item.get("stage") or "")
        cls = "fail" if stage == "FAILED" else "warn" if stage in {"WAITING FOR IRD", "NEEDS ATTENTION"} else ""
        progress = item.get("progress")
        bar = f"<div class='progress'><span style='width:{float(progress):.1f}%'></span></div>" if progress is not None else ""
        detail = esc(item.get("reason") or "")
        rows += f"<tr><td class='title'>{esc(item.get('title'))}<div class='muted'>{esc(item.get('title_id'))}</div></td><td><span class='stage {cls}'>{esc(stage)}</span>{bar}<div class='muted'>{detail}</div></td><td>{esc(item.get('speed') or '—')}</td><td>{esc(item.get('eta') or '—')}</td></tr>"
    status = "Connected" if sab.get("available") else sab.get("status", "Not configured")
    content = f"<div class='table-wrap'><table><tr><th>Title</th><th>Status</th><th>Speed</th><th>ETA</th></tr>{rows}</table></div>" if rows else "<div class='empty'>No active PS3 downloads or processing jobs.</div>"
    return f"<section class='card'><div class='section-head'><h2>Downloads &amp; Processing</h2><span class='badge'>SAB: {esc(status)}</span></div>{content}</section>"


def library_panel() -> str:
    rows = ""
    mount_template = os.environ.get("PS3_MOUNT_URL_TEMPLATE", "").strip()
    for item in library_items():
        mounted = "Mounted" if item["mounted"] else "Not reported"
        if mount_template and "{path}" in mount_template:
            url = mount_template.replace("{path}", urllib.parse.quote(str(item["path"]), safe=""))
            action = f"<a class='button secondary' href='{esc(url)}'>Mount</a>"
        else:
            action = "<span class='button disabled'>Mount unavailable</span>"
        rows += f"<tr><td class='title'>{esc(item['title'])}</td><td>{esc(item['title_id'] or 'Unknown')}</td><td>{esc(human_size(item['size']))}</td><td>{esc(item['verification'])}</td><td>{esc(mounted)}</td><td>{action}</td></tr>"
    content = f"<div class='table-wrap'><table><tr><th>Title</th><th>TITLE_ID</th><th>Size</th><th>Verification</th><th>Mounted</th><th>Action</th></tr>{rows}</table></div>" if rows else "<div class='empty'>No PS3 ISOs published yet.</div>"
    return f"<section class='card' id='library'><div class='section-head'><h2>Ready Library</h2><a class='button secondary' href='/#library'>Refresh library</a></div>{content}</section>"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/", response_class=HTMLResponse)
def home(q: str = "", limit: int = 25) -> Any:
    if not settings.configured:
        return RedirectResponse("/setup", status_code=307)
    limit = max(1, min(100, limit))
    ps3, ps3_cls = ps3_status()
    net, net_cls = tcp_status(settings.ps3netsrv_host, settings.ps3netsrv_port)
    usages = configured_storage_usages(settings)
    storage = " · ".join(f"{human_size(u.free)} free" for u in usages) or "Unavailable"
    body = f"<h1>PS3 Media Automation <span class='badge'>v{esc(APP_VERSION)}</span></h1><div class='statusbar'><div class='status'>PS3<strong class='{ps3_cls}'>{esc(ps3)}</strong></div><div class='status'>ps3netsrv<strong class='{net_cls}'>{esc(net)}</strong></div><div class='status'>PS3 storage<strong>{esc(storage)}</strong></div></div>"
    return page("Home", body + search_panel(q, limit) + processing_panel() + library_panel())


@app.get("/search")
def legacy_search(q: str = "", limit: int = 25) -> RedirectResponse:
    query = urllib.parse.urlencode({"q": q, "limit": limit}) if q else ""
    return RedirectResponse("/" + ("?" + query if query else "") + "#search", status_code=307)


@app.get("/library")
def legacy_library() -> RedirectResponse:
    return RedirectResponse("/#library", status_code=307)


@app.get("/setup", response_class=HTMLResponse)
def setup_page() -> str:
    body = """<h1>First-run setup</h1><section class='card'><p>Configure storage and optional integrations. Secrets are not shown again after saving.</p><form class='page-form' method='post' action='/setup'>
    <label>Library root<input class='wide' name='library_root' required value='/srv/ps3-library'></label><label>Incoming directory<input class='wide' name='incoming_dir' required value='/srv/ps3-library/incoming'></label><label>Work directory<input class='wide' name='work_root' required value='/srv/ps3-library/.iso-build-work'></label><label>IRD directory<input class='wide' name='ird_root' required value='/srv/ps3-library/.ingest/irds'></label><label>PS3ISO directory<input class='wide' name='iso_root' required value='/srv/ps3-library/PS3ISO'></label><label>PS3 IP or hostname<input class='wide' name='ps3_ip' placeholder='192.168.1.50'></label><label>ps3netsrv host<input class='wide' name='ps3netsrv_host' placeholder='MEDIA-SERVER'></label><label>ps3netsrv port<input class='wide' name='ps3netsrv_port' value='38008'></label><label>Prowlarr URL (optional)<input class='wide' name='prowlarr_url' placeholder='http://prowlarr:9696'></label><label>Prowlarr API key (optional; stored protected)<input class='wide' type='password' name='prowlarr_api_key'></label><button>Save and run Doctor</button></form></section>"""
    return page("First-run setup", body)


@app.post("/setup")
def setup_submit(library_root: str = Form(...), incoming_dir: str = Form(...), work_root: str = Form(...), ird_root: str = Form(...), iso_root: str = Form(...), ps3_ip: str = Form(""), ps3netsrv_host: str = Form(""), ps3netsrv_port: int = Form(38008), prowlarr_url: str = Form(""), prowlarr_api_key: str = Form("")) -> RedirectResponse:
    settings.save(locals()); settings.ensure_dirs()
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
    return page("Jobs", f"<h1>Jobs</h1><section class='card'><div class='table-wrap'><table><tr><th>ID</th><th>TITLE_ID</th><th>Source</th><th>State</th><th>Details</th><th>Action</th></tr>{rows}</table></div></section>")


@app.get("/doctor", response_class=HTMLResponse)
def doctor_page() -> str:
    checks = doctor_run(settings)
    rows = "".join(f"<tr><td>{esc(c['name'])}</td><td class='{esc(c['status'].lower())}'>{esc(c['status'])}</td><td>{esc(c['detail'])}</td><td>{esc(c.get('fix'))}</td></tr>" for c in checks)
    return page("Doctor", f"<h1>Doctor</h1><section class='card'><p>Read-only health checks for this application and its integrations.</p><div class='table-wrap'><table><tr><th>Check</th><th>Status</th><th>Detail</th><th>Fix</th></tr>{rows}</table></div></section>")


@app.get("/api/doctor")
def api_doctor() -> list[dict[str, str]]:
    return doctor_run(settings)


@app.post("/library/mount")
def mount_library(path: str = Form(...)) -> RedirectResponse:
    resolved = Path(path).resolve()
    if settings.iso_root.resolve() in resolved.parents and resolved.suffix.lower() == ".iso":
        db.log("WARNING", f"mount unavailable without PS3_MOUNT_URL_TEMPLATE: {resolved.name}")
    return RedirectResponse("/#library", status_code=303)


@app.get("/irds", response_class=HTMLResponse)
def irds_page() -> str:
    items = sorted(settings.ird_root.rglob("*.ird")) if settings.ird_root.exists() else []
    form = "<form method='post' action='/irds' enctype='multipart/form-data'><input type='file' name='ird' accept='.ird' required><button>Upload IRD</button></form>"
    return page("IRDs", "<h1>IRDs</h1><section class='card'><p>Trusted IRDs are user-supplied and never bundled.</p>" + form + "<ul>" + "".join(f"<li>{esc(item.name)}</li>" for item in items) + "</ul></section>")


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
    return page("Settings", f"<h1>Settings</h1><section class='card'><p>Configured: {settings.configured}</p><p>Storage root: {esc(settings.library_root)}</p><p>Secrets are never rendered here.</p><a class='button' href='/setup'>Open setup wizard</a></section>")


@app.get("/logs", response_class=HTMLResponse)
def logs_page() -> str:
    content = "\n".join(f"{e['created_at']} {e['level']} {e['message']}" for e in db.events())
    return page("Logs", "<h1>Logs / Audits</h1><section class='card'><pre>" + esc(content) + "</pre></section>")

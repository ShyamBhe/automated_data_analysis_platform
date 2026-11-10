"""FastAPI app: REST API + dashboard + background scheduler.
Run:  uvicorn app.main:app --reload"""
import base64
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import datasets, db, pipeline

SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "30"))
ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SAMPLES = ROOT / "samples"
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")  # read-only public demo
APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")  # set to require a login (HTTP basic auth)
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))


@asynccontextmanager
async def lifespan(app):
    db.init()
    pipeline.INBOX.mkdir(parents=True, exist_ok=True)
    if DEMO_MODE:  # preload sample data so the public demo is not empty
        with db.conn() as c:
            empty = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        if empty and SAMPLES.exists():
            for f in sorted(SAMPLES.glob("*.csv")):
                shutil.copy(f, pipeline.INBOX / f.name)
            pipeline.scan_inbox(min_age=0)
    if DEMO_MODE:
        datasets.seed_demo()
    sched = BackgroundScheduler()
    sched.add_job(pipeline.scan_inbox, "interval", seconds=SCAN_SECONDS, max_instances=1)
    sched.start()
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="AutoAnalytics", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request, call_next):
    """If APP_PASSWORD is set, every page and API call (except /api/health) needs a login."""
    if APP_PASSWORD and request.url.path != "/api/health":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = (secrets.compare_digest(user.encode(), APP_USER.encode())
                      and secrets.compare_digest(pw.encode(), APP_PASSWORD.encode()))
            except Exception:
                ok = False
        if not ok:
            return Response("Authentication required", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="AutoAnalytics"'})
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {"demo": DEMO_MODE, "max_upload_mb": MAX_UPLOAD_MB}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    if DEMO_MODE:
        raise HTTPException(403, "Uploads are disabled in demo mode.")
    if not file.filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(400, "Upload a .csv or .xlsx file.")
    pipeline.INBOX.mkdir(parents=True, exist_ok=True)
    dest = pipeline.INBOX / f"{datetime.now():%Y%m%d%H%M%S}_{Path(file.filename).name}"
    part = dest.with_name(dest.name + ".part")  # not picked up by the scanner until complete
    limit, size, too_big = MAX_UPLOAD_MB * 1024 * 1024, 0, False
    with open(part, "wb") as f:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                too_big = True
                break
            f.write(chunk)
    if too_big:
        part.unlink(missing_ok=True)
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    part.rename(dest)
    return {"runs": pipeline.scan_inbox(min_age=0)}


@app.post("/api/scan")
def scan():
    if DEMO_MODE:
        raise HTTPException(403, "Disabled in demo mode.")
    return {"runs": pipeline.scan_inbox(min_age=0)}


@app.get("/api/summary")
def summary():
    with db.conn() as c:
        rows = c.execute("SELECT key, value, computed_at FROM results").fetchall()
    out = {r["key"]: json.loads(r["value"]) for r in rows}
    out["updated_at"] = max((r["computed_at"] for r in rows), default=None)
    return out


@app.get("/api/runs")
def runs(limit: int = 20):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


@app.get("/api/alerts")
def alerts(limit: int = 20):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]


@app.get("/api/quarantine")
def quarantine(limit: int = 100):
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM quarantine ORDER BY id DESC LIMIT ?", (limit,))]


app.include_router(datasets.router)


@app.get("/datasets")
def datasets_page():
    return FileResponse(STATIC / "datasets.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

"""FastAPI app: REST API + dashboard + background scheduler.
Run:  uvicorn app.main:app --reload"""
import json
import os
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, pipeline

SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "30"))
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app):
    db.init()
    pipeline.INBOX.mkdir(parents=True, exist_ok=True)
    sched = BackgroundScheduler()
    sched.add_job(pipeline.scan_inbox, "interval", seconds=SCAN_SECONDS, max_instances=1)
    sched.start()
    yield
    sched.shutdown(wait=False)


app = FastAPI(title="AutoAnalytics", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(400, "Upload a .csv or .xlsx file.")
    pipeline.INBOX.mkdir(parents=True, exist_ok=True)
    dest = pipeline.INBOX / f"{datetime.now():%Y%m%d%H%M%S}_{Path(file.filename).name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"runs": pipeline.scan_inbox(min_age=0)}


@app.post("/api/scan")
def scan():
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

"""Generic datasets: upload (CSV/TSV/JSON/XML/Excel) -> profile -> clean -> download."""
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import analysis as analysis_mod
from . import cleaning, db, loaders
from .pipeline import DATA

router = APIRouter(prefix="/api/datasets")
DATASETS = DATA / "datasets"
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))


class CleanRequest(BaseModel):
    auto: bool = False
    rules: Optional[dict] = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir(ds_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{10}", ds_id) or not (DATASETS / ds_id).exists():
        raise HTTPException(404, "Dataset not found.")
    return DATASETS / ds_id


def _name(ds_id: str) -> str:
    with db.conn() as c:
        row = c.execute("SELECT name FROM datasets WHERE id=?", (ds_id,)).fetchone()
    return row["name"] if row else ds_id


def _register(d: Path, ds_id: str, name: str, raw: Path):
    df = loaders.load_any(raw)
    df.to_csv(d / "original.csv", index=False)
    with db.conn() as c:
        c.execute("INSERT INTO datasets(id, name, fmt, rows, cols, created_at) VALUES (?,?,?,?,?,?)",
                  (ds_id, name, raw.suffix.lower().lstrip("."), len(df), len(df.columns), _now()))
    return {"id": ds_id, "name": name, "rows": len(df), "cols": len(df.columns)}


def _save_upload(file: UploadFile, dest: Path) -> bool:
    limit, size = MAX_UPLOAD_MB * 1024 * 1024, 0
    with open(dest, "wb") as f:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                return False
            f.write(chunk)
    return True


def seed_demo():
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]:
            return
    for f in sorted(SAMPLES.glob("messy_*")):
        ds_id = uuid.uuid4().hex[:10]
        d = DATASETS / ds_id
        d.mkdir(parents=True)
        raw = d / f"raw{f.suffix.lower()}"
        shutil.copy(f, raw)
        try:
            _register(d, ds_id, f.name, raw)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)


@router.post("")
def upload(file: UploadFile = File(...)):
    if DEMO_MODE:
        raise HTTPException(403, "Uploads are disabled in demo mode.")
    name = Path(file.filename).name
    ext = Path(name).suffix.lower()
    if ext not in loaders.SUPPORTED:
        raise HTTPException(400, "Supported files: CSV, TSV, JSON, JSONL, XML, XLSX.")
    ds_id = uuid.uuid4().hex[:10]
    d = DATASETS / ds_id
    d.mkdir(parents=True)
    raw = d / f"raw{ext}"
    if not _save_upload(file, raw):
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    try:
        return _register(d, ds_id, name, raw)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"Could not read this file: {e}")


@router.get("")
def list_datasets():
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM datasets ORDER BY created_at DESC")]


@router.get("/{ds_id}")
def detail(ds_id: str):
    d = _dir(ds_id)
    df, notes = cleaning.read_typed(d / "original.csv")
    prof = cleaning.profile(df, notes)
    last = json.loads((d / "log.json").read_text()) if (d / "log.json").exists() else None
    return {"id": ds_id, "name": _name(ds_id), "profile": prof,
            "suggested_rules": cleaning.suggest(prof), "last_clean": last}


@router.post("/{ds_id}/clean")
def clean(ds_id: str, req: CleanRequest):
    d = _dir(ds_id)
    df, notes = cleaning.read_typed(d / "original.csv")
    prof = cleaning.profile(df, notes)
    rules = cleaning.suggest(prof) if (req.auto or not req.rules) else req.rules
    kinds = {c: n["kind"] for c, n in notes.items()}
    out, log, counts = cleaning.apply_rules(df, kinds, rules)
    # odd values (e.g. "N/A", "ERR") were converted to nulls when the file was read: report them
    odd = [{"column": c, "action": "Odd values read as missing" +
            (f" (e.g. {', '.join(n['invalid_examples'])})" if n["invalid_examples"] else ""),
            "count": n["invalid"] + n["null_tokens"]}
           for c, n in notes.items() if n["invalid"] + n["null_tokens"]]
    cleaning.save_csv(out, d / "cleaned.csv")
    cdf, cnotes = cleaning.read_typed(d / "cleaned.csv")
    payload = {"log": odd + log, **counts, "rules": rules, "cleaned_at": _now(),
               "before": cleaning.summary(prof),
               "after": cleaning.summary(cleaning.profile(cdf, cnotes))}
    (d / "log.json").write_text(json.dumps(payload))
    with db.conn() as c:
        c.execute("UPDATE datasets SET cleaned_at=? WHERE id=?", (payload["cleaned_at"], ds_id))
    return payload


@router.get("/{ds_id}/preview")
def preview(ds_id: str, version: str = "original", limit: int = 50):
    d = _dir(ds_id)
    path = d / ("cleaned.csv" if version == "cleaned" else "original.csv")
    if not path.exists():
        raise HTTPException(404, "This version does not exist yet.")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, nrows=min(limit, 500))
    return {"columns": list(df.columns), "rows": df.values.tolist()}


@router.get("/{ds_id}/analysis")
def get_analysis(ds_id: str, version: str = "original"):
    d = _dir(ds_id)
    path = d / ("cleaned.csv" if version == "cleaned" else "original.csv")
    if not path.exists():
        raise HTTPException(404, "This version does not exist yet.")
    df, notes = cleaning.read_typed(path)
    return analysis_mod.analyze(df, notes)


@router.get("/{ds_id}/download")
def download(ds_id: str, version: str = "cleaned"):
    d = _dir(ds_id)
    path = d / ("cleaned.csv" if version == "cleaned" else "original.csv")
    if not path.exists():
        raise HTTPException(404, "This version does not exist yet.")
    base = Path(_name(ds_id)).stem
    return FileResponse(path, media_type="text/csv", filename=f"{base}_{version}.csv")


@router.delete("/{ds_id}")
def delete(ds_id: str):
    if DEMO_MODE:
        raise HTTPException(403, "Disabled in demo mode.")
    d = _dir(ds_id)
    shutil.rmtree(d, ignore_errors=True)
    with db.conn() as c:
        c.execute("DELETE FROM datasets WHERE id=?", (ds_id,))
    return {"deleted": ds_id}

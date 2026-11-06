"""Ingest -> validate -> upsert -> incremental aggregate -> analyze -> alert.

Key properties:
  * Raw files are kept untouched in data/raw (reprocessable).
  * Idempotent: a file with the same SHA-256 is never processed twice.
  * Upserts by order_id: new rows are inserted, changed rows are updated.
  * Incremental: only the days touched by a file are re-aggregated (handles late data).
  * Bad rows go to a quarantine table instead of breaking the run.
"""
import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import db

DATA = Path(os.environ.get("DATA_DIR", "data"))
INBOX, RAW, FAILED = DATA / "inbox", DATA / "raw", DATA / "failed"
REQUIRED = ["order_id", "order_date", "region", "product", "amount"]
DROP_PCT = float(os.environ.get("DROP_ALERT_PCT", "20"))
Z_LIMIT = float(os.environ.get("ANOMALY_Z", "3"))
_lock = threading.Lock()

UPSERT = """
INSERT INTO records(order_id, order_date, region, product, amount, source_file, updated_at)
VALUES (?,?,?,?,?,?,?)
ON CONFLICT(order_id) DO UPDATE SET
  order_date=excluded.order_date, region=excluded.region, product=excluded.product,
  amount=excluded.amount, source_file=excluded.source_file, updated_at=excluded.updated_at
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _chunks(seq, n=500):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def scan_inbox(min_age=2):
    """Process every new file in the inbox. min_age skips files still being written."""
    for d in (INBOX, RAW, FAILED):
        d.mkdir(parents=True, exist_ok=True)
    out = []
    with _lock:
        files = sorted(list(INBOX.glob("*.csv")) + list(INBOX.glob("*.xlsx")))
        for p in files:
            if time.time() - p.stat().st_mtime < min_age:
                continue
            out.append(_process(p))
    return out


def _process(p: Path):
    name = p.name
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    with db.conn() as c:
        if c.execute("SELECT 1 FROM files WHERE sha256=?", (digest,)).fetchone():
            p.unlink()
            return {"file": name, "status": "skipped", "message": "duplicate file, already processed"}
        run_id = c.execute("INSERT INTO runs(file, started_at, status) VALUES (?,?,'running')",
                           (name, _now())).lastrowid
    stats = {"inserted": 0, "updated": 0, "quarantined": 0}
    try:
        stats = _ingest(p, run_id)
        status, msg, dest = "success", "", RAW
    except Exception as e:  # keep the platform alive; surface the error to the user
        status, msg, dest = "failed", str(e), FAILED
        with db.conn() as c:
            c.execute("INSERT INTO alerts(created_at, level, message, run_id) VALUES (?,?,?,?)",
                      (_now(), "error", f"{name}: {msg}", run_id))
    shutil.move(str(p), dest / f"{datetime.now():%Y%m%d%H%M%S}_{name}")
    with db.conn() as c:
        if status == "success":
            c.execute("INSERT INTO files VALUES (?,?,?)", (digest, name, _now()))
        c.execute("""UPDATE runs SET finished_at=?, status=?, inserted=?, updated=?,
                     quarantined=?, message=? WHERE id=?""",
                  (_now(), status, stats["inserted"], stats["updated"],
                   stats["quarantined"], msg, run_id))
    return {"file": name, "status": status, "message": msg, **stats}


def _ingest(p: Path, run_id: int):
    df = pd.read_csv(p, dtype=str) if p.suffix == ".csv" else pd.read_excel(p, dtype=str)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:  # schema drift: refuse loudly rather than silently mis-analyze
        raise ValueError(f"schema check failed, missing columns: {missing}")
    df = df[REQUIRED]
    orig = df.astype(object).where(df.notna(), None)

    df = df.copy()
    df["order_id"] = df["order_id"].fillna("").str.strip()
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["region"] = df["region"].fillna("Unknown").str.strip()
    df["product"] = df["product"].fillna("Unknown").str.strip()

    masks = {
        "missing order_id": df["order_id"] == "",
        "bad date": df["order_date"].isna(),
        "bad or negative amount": df["amount"].isna() | (df["amount"] < 0),
    }
    reason = pd.Series("", index=df.index)
    for label, m in masks.items():
        reason = reason.where(~m, reason + label + "; ")
    bad = reason != ""

    good = df[~bad].drop_duplicates("order_id", keep="last").copy()
    good["day"] = good["order_date"].dt.strftime("%Y-%m-%d")
    now = _now()
    ids = good["order_id"].tolist()

    with db.conn() as c:
        existing, old_days = set(), set()
        for ch in _chunks(ids):
            q = ",".join("?" * len(ch))
            for oid, day in c.execute(
                    f"SELECT order_id, order_date FROM records WHERE order_id IN ({q})", ch):
                existing.add(oid)
                old_days.add(day)

        c.executemany(UPSERT, [(r.order_id, r.day, r.region, r.product, float(r.amount), p.name, now)
                               for r in good.itertuples()])
        c.executemany("INSERT INTO quarantine(run_id, reason, row_json) VALUES (?,?,?)",
                      [(run_id, reason[i].rstrip("; "), json.dumps(orig.loc[i].to_dict(), default=str))
                       for i in df.index[bad]])

        # incremental aggregation: only touched days (new rows + days that updated rows left)
        affected = set(good["day"]) | old_days
        for day in affected:
            c.execute("DELETE FROM daily_metrics WHERE day=?", (day,))
            c.execute("""INSERT INTO daily_metrics
                         SELECT order_date, SUM(amount), COUNT(*) FROM records
                         WHERE order_date=? GROUP BY order_date""", (day,))

        n_bad = int(bad.sum())
        if n_bad:
            c.execute("INSERT INTO alerts(created_at, level, message, run_id) VALUES (?,?,?,?)",
                      (now, "warning", f"{p.name}: {n_bad} row(s) quarantined for review", run_id))
        _refresh_results(c, run_id, affected)

    return {"inserted": len(ids) - len(existing), "updated": len(existing), "quarantined": n_bad}


def _refresh_results(c, run_id, affected):
    daily = pd.read_sql_query("SELECT day, revenue, orders FROM daily_metrics ORDER BY day", c)
    if daily.empty:
        return
    now = _now()
    total_rev, total_orders = float(daily["revenue"].sum()), int(daily["orders"].sum())
    kpis = {"revenue": round(total_rev, 2), "orders": total_orders,
            "avg_order_value": round(total_rev / total_orders, 2) if total_orders else 0,
            "days": int(len(daily)), "last_day": daily["day"].iloc[-1],
            "last_day_change_pct": None}

    # alert: latest day vs the average of the 7 days before it
    last, prev = daily.iloc[-1], daily.iloc[-8:-1]
    if len(prev) >= 3 and prev["revenue"].mean() > 0:
        change = (last["revenue"] - prev["revenue"].mean()) / prev["revenue"].mean() * 100
        kpis["last_day_change_pct"] = round(float(change), 1)
        if change <= -DROP_PCT and last["day"] in affected:
            c.execute("INSERT INTO alerts(created_at, level, message, run_id) VALUES (?,?,?,?)",
                      (now, "warning", f"Revenue on {last['day']} is {abs(change):.0f}% below the "
                                       f"previous 7-day average", run_id))

    # anomaly detection: z-score of daily revenue
    anomalies = []
    if len(daily) >= 7 and daily["revenue"].std() > 0:
        z = (daily["revenue"] - daily["revenue"].mean()) / daily["revenue"].std()
        for d, r, zz in zip(daily["day"], daily["revenue"], z):
            if abs(zz) >= Z_LIMIT:
                anomalies.append({"day": d, "revenue": round(float(r), 2), "z": round(float(zz), 2)})
                if d in affected:
                    c.execute("INSERT INTO alerts(created_at, level, message, run_id) VALUES (?,?,?,?)",
                              (now, "warning", f"Anomaly on {d}: revenue {r:,.0f} (z={zz:.1f})", run_id))

    def grouped(col):
        return [dict(r) for r in c.execute(
            f"SELECT {col} AS name, ROUND(SUM(amount),2) AS revenue, COUNT(*) AS orders "
            f"FROM records GROUP BY {col} ORDER BY revenue DESC")]

    payload = {"kpis": kpis, "daily": daily.round(2).to_dict("records"),
               "by_region": grouped("region"), "by_product": grouped("product"),
               "anomalies": anomalies}
    for k, v in payload.items():
        c.execute("""INSERT INTO results(key, value, computed_at) VALUES (?,?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                     computed_at=excluded.computed_at""", (k, json.dumps(v), now))

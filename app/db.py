"""SQLite storage. To move to PostgreSQL later, replace conn() with a SQLAlchemy/psycopg
connection and keep the same tables (the SQL used is standard upsert syntax)."""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    order_id TEXT PRIMARY KEY, order_date TEXT, region TEXT, product TEXT,
    amount REAL, source_file TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(order_date);
CREATE TABLE IF NOT EXISTS daily_metrics (day TEXT PRIMARY KEY, revenue REAL, orders INTEGER);
CREATE TABLE IF NOT EXISTS files (sha256 TEXT PRIMARY KEY, name TEXT, processed_at TEXT);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT, started_at TEXT, finished_at TEXT,
    status TEXT, inserted INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
    quarantined INTEGER DEFAULT 0, message TEXT);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, reason TEXT, row_json TEXT);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, level TEXT, message TEXT, run_id INTEGER);
CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT, computed_at TEXT);
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, name TEXT, fmt TEXT, rows INTEGER, cols INTEGER,
    created_at TEXT, cleaned_at TEXT);
"""


def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)

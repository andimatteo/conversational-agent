"""Thin SQLite store. JSON columns keep the schema flexible across verticals."""
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs      (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes    (id TEXT PRIMARY KEY, job_id TEXT, company_id TEXT,
                                      phase TEXT, data TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS calls     (id TEXT PRIMARY KEY, job_id TEXT, company_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_runs (id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_batches (id TEXT PRIMARY KEY, job_id TEXT, run_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learned_questions (id TEXT PRIMARY KEY, vertical TEXT, area_code TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users     (id TEXT PRIMARY KEY, email TEXT UNIQUE, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions  (id TEXT PRIMARY KEY, user_id TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_companies_job ON companies(job_id);
CREATE INDEX IF NOT EXISTS ix_quotes_job_company ON quotes(job_id, company_id);
CREATE INDEX IF NOT EXISTS ix_calls_job_company ON calls(job_id, company_id);
CREATE INDEX IF NOT EXISTS ix_call_runs_job ON call_runs(job_id);
CREATE INDEX IF NOT EXISTS ix_call_batches_run ON call_batches(run_id);
CREATE INDEX IF NOT EXISTS ix_learned_scope ON learned_questions(vertical, area_code);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
"""

_schema_ready = False
_schema_lock = threading.Lock()


@contextmanager
def conn():
    global _schema_ready
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA synchronous=NORMAL")
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(_SCHEMA)
                _schema_ready = True
    try:
        yield c
        c.commit()
    finally:
        c.close()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def put(table: str, id: str, data: dict, **cols):
    keys = ["id", "data"] + list(cols)
    vals = [id, json.dumps(data)] + list(cols.values())
    updates = ["data=excluded.data", *(f"{key}=excluded.{key}" for key in cols)]
    with conn() as c:
        c.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(updates)}",
            vals,
        )


def get(table: str, id: str) -> dict | None:
    with conn() as c:
        row = c.execute(f"SELECT data FROM {table} WHERE id=?", (id,)).fetchone()
    return json.loads(row[0]) if row else None


def where(table: str, **filters) -> list[dict]:
    clause = " AND ".join(f"{k}=?" for k in filters) or "1=1"
    with conn() as c:
        rows = c.execute(f"SELECT data FROM {table} WHERE {clause}", list(filters.values())).fetchall()
    return [json.loads(r[0]) for r in rows]


def increment_json_field(table: str, id: str, field: str, amount: int = 1) -> dict:
    """Atomically increment one top-level numeric JSON field and return the
    complete updated row. Used for knowledge versions shared by batch/demo
    workers across processes."""
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(f"SELECT data FROM {table} WHERE id=?", (id,)).fetchone()
        if not row:
            raise LookupError(f"{table} {id} not found")
        data = json.loads(row[0])
        data[field] = int(data.get(field, 0)) + amount
        c.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(data), id))
    return data


def compare_and_set_json(table: str, id: str, field: str, expected, updates: dict) -> dict | None:
    """Apply top-level JSON updates only while ``field`` still equals the
    expected value. Returns the updated row, or ``None`` after a lost race."""
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(f"SELECT data FROM {table} WHERE id=?", (id,)).fetchone()
        if not row:
            raise LookupError(f"{table} {id} not found")
        data = json.loads(row[0])
        if data.get(field) != expected:
            return None
        data.update(updates)
        c.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(data), id))
    return data

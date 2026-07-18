"""Thin SQLite store. JSON columns keep the schema flexible across verticals."""
import json
import sqlite3
import uuid
from contextlib import contextmanager

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs      (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, job_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes    (id TEXT PRIMARY KEY, job_id TEXT, company_id TEXT,
                                      phase TEXT, data TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS calls     (id TEXT PRIMARY KEY, job_id TEXT, company_id TEXT, data TEXT NOT NULL);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.executescript(_SCHEMA)
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
    with conn() as c:
        c.execute(
            f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))}) "
            f"ON CONFLICT(id) DO UPDATE SET data=excluded.data",
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

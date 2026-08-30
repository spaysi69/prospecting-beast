"""Small, dependency-free SQLite cache for public web/LLM calls.

Cache entries expire after CACHE_TTL_DAYS (default: 7). Keys are SHA-256 hashes of
stable JSON payloads so Tavily/Gemini inputs are safely namespaced and bounded.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("PB_CACHE_DB", str(BASE / "data" / "cache.sqlite3")))
DEFAULT_TTL = int(os.getenv("PB_CACHE_TTL_DAYS", "7")) * 86400


def _db_init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cache_entries (
                namespace TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY(namespace, cache_key)
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expiry ON cache_entries(expires_at)")


def _key(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_sync(namespace: str, cache_key: str, now: float) -> Any | None:
    _db_init()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT value_json, expires_at FROM cache_entries WHERE namespace=? AND cache_key=?",
            (namespace, cache_key),
        ).fetchone()
        if not row:
            return None
        if float(row[1]) <= now:
            conn.execute("DELETE FROM cache_entries WHERE namespace=? AND cache_key=?", (namespace, cache_key))
            return None
        try:
            return json.loads(row[0])
        except Exception:
            conn.execute("DELETE FROM cache_entries WHERE namespace=? AND cache_key=?", (namespace, cache_key))
            return None


def _set_sync(namespace: str, cache_key: str, value: Any, ttl: int, now: float) -> None:
    _db_init()
    payload = json.dumps(value, ensure_ascii=True, default=str)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache_entries(namespace, cache_key, value_json, created_at, expires_at) VALUES(?,?,?,?,?)",
            (namespace, cache_key, payload, now, now + max(1, ttl)),
        )


async def get(namespace: str, payload: Any, ttl: int | None = None) -> Any | None:
    return await asyncio.to_thread(_get_sync, namespace, _key(payload), time.time())


async def set(namespace: str, payload: Any, value: Any, ttl: int | None = None) -> None:
    await asyncio.to_thread(_set_sync, namespace, _key(payload), value, int(ttl or DEFAULT_TTL), time.time())


async def purge() -> None:
    def _purge():
        _db_init()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (time.time(),))
    await asyncio.to_thread(_purge)

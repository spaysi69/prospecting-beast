"""Small zero-dependency SQLite cache for Tavily/Gemini calls."""
from __future__ import annotations
import hashlib, json, os, sqlite3, threading, time
from pathlib import Path

DEFAULT_TTL = 7 * 24 * 3600

class ResponseCache:
    def __init__(self, path: str | None = None, ttl_seconds: int = DEFAULT_TTL):
        root = Path(__file__).resolve().parents[1]
        self.path = Path(path or os.getenv("PB_CACHE_DB", str(root / "data" / "cache.sqlite3")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = int(ttl_seconds)
        self._lock = threading.Lock()
        self._init()

    @staticmethod
    def key(namespace: str, payload) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return namespace + ":" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def _init(self):
        with self._lock, self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS responses (k TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_responses_created ON responses(created)")

    def get(self, namespace: str, payload):
        k = self.key(namespace, payload)
        cutoff = time.time() - self.ttl
        with self._lock, self._conn() as c:
            row = c.execute("SELECT value, created FROM responses WHERE k=?", (k,)).fetchone()
            if not row:
                return None
            if row[1] < cutoff:
                c.execute("DELETE FROM responses WHERE k=?", (k,))
                return None
            try:
                return json.loads(row[0])
            except Exception:
                c.execute("DELETE FROM responses WHERE k=?", (k,))
                return None

    def set(self, namespace: str, payload, value):
        k = self.key(namespace, payload)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO responses(k,value,created) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET value=excluded.value, created=excluded.created", (k, encoded, time.time()))

    def clear_expired(self):
        cutoff = time.time() - self.ttl
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM responses WHERE created < ?", (cutoff,))

CACHE = ResponseCache()

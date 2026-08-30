import asyncio, hashlib, json, os, sqlite3, time
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
DB_PATH=Path(os.getenv('PB_CACHE_DB', str(BASE/'.cache'/'osint.sqlite3')))
TTL_SECONDS=7*24*60*60

def cache_key_for(namespace: str, payload) -> str:
    raw=json.dumps(payload, sort_keys=True, separators=(',',':'), ensure_ascii=False, default=str)
    return namespace+':'+hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at)')
        con.commit()

def _get(key):
    _init()
    now=time.time()
    with sqlite3.connect(DB_PATH) as con:
        row=con.execute('SELECT value, created_at FROM cache WHERE key=?', (key,)).fetchone()
        if not row: return None
        if now-float(row[1]) > TTL_SECONDS:
            con.execute('DELETE FROM cache WHERE key=?', (key,)); con.commit(); return None
        try: return json.loads(row[0])
        except Exception: return None

def _set(key,value):
    _init()
    with sqlite3.connect(DB_PATH) as con:
        con.execute('INSERT OR REPLACE INTO cache(key,value,created_at) VALUES(?,?,?)',(key,json.dumps(value,ensure_ascii=False),time.time()))
        con.commit()

async def cache_get_json(key):
    return await asyncio.to_thread(_get,key)

async def cache_set_json(key,value):
    await asyncio.to_thread(_set,key,value)

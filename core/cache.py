import sqlite3
import json
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache.db")
CACHE_TTL = 15 * 60  # 15 minutes in seconds


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _key(dish: str, location: str) -> str:
    return f"{dish.strip().lower()}|{location.strip().lower()}"


def get_cached(dish: str, location: str):
    conn = _conn()
    row = conn.execute(
        "SELECT data, timestamp FROM cache WHERE key = ?", (_key(dish, location),)
    ).fetchone()
    conn.close()

    if not row:
        return None

    data, timestamp = row
    if time.time() - timestamp > CACHE_TTL:
        return None  # expired

    return json.loads(data)


def set_cache(dish: str, location: str, data: list):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, data, timestamp) VALUES (?, ?, ?)",
        (_key(dish, location), json.dumps(data, ensure_ascii=False), time.time()),
    )
    conn.commit()
    conn.close()
"""Durable key-value store: Postgres when DATABASE_URL is set, otherwise a local SQLite file."""
import json
import os
import sqlite3
import threading

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get("SQLITE_PATH", "mailroom.db")
_lock = threading.Lock()


class Store:
    def __init__(self):
        self.pg = DATABASE_URL.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg
            self._psycopg = psycopg
            with self._conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        else:
            with self._conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    def _conn(self):
        if self.pg:
            return self._psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=10)
        conn = sqlite3.connect(SQLITE_PATH, timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, key):
        with _lock, self._conn() as c:
            q = "SELECT v FROM kv WHERE k = %s" if self.pg else "SELECT v FROM kv WHERE k = ?"
            row = c.execute(q, (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_many(self, items):
        """Write several keys in one transaction."""
        if not items:
            return
        with _lock, self._conn() as c:
            if self.pg:
                with c.transaction():
                    for k, v in items.items():
                        c.execute("INSERT INTO kv (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                                  (k, json.dumps(v)))
            else:
                c.execute("BEGIN")
                try:
                    for k, v in items.items():
                        c.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                                  (k, json.dumps(v)))
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise

    def put(self, key, value):
        self.put_many({key: value})

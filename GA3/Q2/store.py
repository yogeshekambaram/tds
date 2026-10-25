"""SQLite persistence: tasks, message idempotency records, and cached package decisions.

All mutations go through Store.lock so state transitions (result vs cancel) are atomic.
"""
import json
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).with_name("a2a_state.db")


class Store:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, context_id TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS tasks_by_principal ON tasks(principal, created);
            CREATE TABLE IF NOT EXISTS messages (
                principal TEXT NOT NULL, message_id TEXT NOT NULL, hash TEXT NOT NULL,
                task_id TEXT NOT NULL, PRIMARY KEY (principal, message_id));
            CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, body TEXT NOT NULL);
        """)
        self.lock = threading.RLock()

    # tasks -----------------------------------------------------------------------------
    def get_task(self, principal, task_id):
        row = self.db.execute("SELECT body FROM tasks WHERE id=? AND principal=?", (task_id, principal)).fetchone()
        return json.loads(row[0]) if row else None

    def list_tasks(self, principal):
        rows = self.db.execute("SELECT body FROM tasks WHERE principal=? ORDER BY created DESC", (principal,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def put_task(self, principal, task, created):
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)",
                        (task["id"], principal, task["contextId"], task["status"]["state"], created, json.dumps(task)))

    def task_created(self, task_id):
        row = self.db.execute("SELECT created FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row[0] if row else None

    # idempotency -----------------------------------------------------------------------
    def get_message(self, principal, message_id):
        row = self.db.execute("SELECT hash, task_id FROM messages WHERE principal=? AND message_id=?",
                              (principal, message_id)).fetchone()
        return row

    def put_message(self, principal, message_id, digest, task_id):
        self.db.execute("INSERT INTO messages VALUES (?,?,?,?)", (principal, message_id, digest, task_id))

    # decision cache --------------------------------------------------------------------
    def get_decision(self, key):
        row = self.db.execute("SELECT body FROM decisions WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_decision(self, key, decision):
        self.db.execute("INSERT OR REPLACE INTO decisions VALUES (?,?)", (key, json.dumps(decision)))

    def transaction(self):
        return _Txn(self)


class _Txn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.store.lock.acquire()
        self.store.db.execute("BEGIN IMMEDIATE")
        return self.store

    def __exit__(self, exc_type, exc, tb):
        try:
            self.store.db.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self.store.lock.release()
        return False

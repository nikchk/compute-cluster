import contextlib
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .config import DATABASE_URL

pool: ThreadedConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    params JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'У черзі',
    result JSONB,
    error TEXT,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    server_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS tasks_user_idx ON tasks(user_id, created_at);
CREATE TABLE IF NOT EXISTS servers (
    id TEXT PRIMARY KEY,
    capacity INT NOT NULL,
    running INT NOT NULL DEFAULT 0,
    cpu REAL NOT NULL DEFAULT 0,
    mem REAL NOT NULL DEFAULT 0,
    dynamic BOOLEAN NOT NULL DEFAULT false,
    draining BOOLEAN NOT NULL DEFAULT false,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    message TEXT NOT NULL
);
"""


def init_pool():
    global pool
    for _ in range(60):
        try:
            pool = ThreadedConnectionPool(1, 30, DATABASE_URL)
            return
        except psycopg2.OperationalError:
            time.sleep(1)
    raise RuntimeError("База даних недоступна")


@contextlib.contextmanager
def cursor():
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_schema():
    # advisory lock - кілька серверів можуть стартувати одночасно
    with cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(42)")
        cur.execute(SCHEMA)

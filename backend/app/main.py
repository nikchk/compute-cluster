import logging
import math
from contextlib import asynccontextmanager

import psycopg2
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from psycopg2.extras import Json
from pydantic import BaseModel, Field

from . import db
from .auth import admin_user, current_user, hash_password, make_token, verify_password
from .config import (ADMIN_PASSWORD, ADMIN_USER, MAX_ACTIVE_PER_USER, MAX_HISTORY_PER_USER,
                     MAX_QUEUE_LENGTH, MAX_TASK_SECONDS, SERVER_ID)
from .limits import TASKS, validate
from .worker import Worker

logging.basicConfig(level=logging.INFO)
worker = Worker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    db.init_schema()
    with db.cursor() as cur:
        cur.execute("""INSERT INTO users(username,password_hash,is_admin) VALUES (%s,%s,true)
                       ON CONFLICT (username) DO NOTHING""", (ADMIN_USER, hash_password(ADMIN_PASSWORD)))
    worker.start()
    yield
    worker.shutdown()


app = FastAPI(title="Compute cluster", lifespan=lifespan)


@app.middleware("http")
async def add_server_header(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Served-By"] = SERVER_ID      # видно, який сервер обробив запит
    return resp


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)


@app.post("/api/register")
def register(c: Credentials):
    try:
        with db.cursor() as cur:
            cur.execute("INSERT INTO users(username,password_hash) VALUES (%s,%s) RETURNING id,username,is_admin",
                        (c.username, hash_password(c.password)))
            user = cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Користувач з таким ім'ям вже існує")
    return {"token": make_token(user), "username": user["username"], "is_admin": user["is_admin"]}


@app.post("/api/login")
def login(c: Credentials):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username=%s", (c.username,))
        user = cur.fetchone()
    if not user or not verify_password(c.password, user["password_hash"]):
        raise HTTPException(401, "Невірний логін або пароль")
    return {"token": make_token(user), "username": user["username"], "is_admin": user["is_admin"]}


# ---------------------------------------------------------------- tasks
@app.get("/api/limits")
def limits():
    return {"tasks": TASKS, "max_task_seconds": MAX_TASK_SECONDS, "max_active_per_user": MAX_ACTIVE_PER_USER,
            "max_history_per_user": MAX_HISTORY_PER_USER, "max_queue_length": MAX_QUEUE_LENGTH}


def queue_info(cur, task: dict) -> dict | None:
    """Позиція в черзі та орієнтовний час очікування."""
    if task["status"] != "queued":
        return None
    cur.execute("SELECT count(*) AS c FROM tasks WHERE status='queued' AND (created_at,id) < (%s,%s)",
                (task["created_at"], task["id"]))
    ahead = cur.fetchone()["c"]
    cur.execute("""SELECT coalesce(sum(capacity),0) AS cap, coalesce(sum(running),0) AS run FROM servers
                   WHERE heartbeat > now() - interval '10 seconds' AND NOT draining""")
    s = cur.fetchone()
    cur.execute("""SELECT avg(extract(epoch FROM finished_at - started_at)) AS avg FROM tasks
                   WHERE type=%s AND status='done' AND finished_at > now() - interval '1 day'""", (task["type"],))
    avg = cur.fetchone()["avg"] or 15.0
    cap, free = int(s["cap"]), max(int(s["cap"]) - int(s["run"]), 0)
    if cap == 0:
        eta = None
    elif ahead < free:
        eta = 2.0
    else:
        eta = (math.floor((ahead - free) / cap) + 1) * float(avg)
    return {"position": ahead + 1, "eta_seconds": eta, "all_busy": free == 0, "free_slots": free, "total_slots": cap}


def public_task(cur, row: dict, with_result=False) -> dict:
    out = {k: row[k] for k in ("id", "type", "params", "status", "progress", "stage", "error", "server_id",
                               "created_at", "started_at", "finished_at")}
    out["progress"] = round(float(out["progress"]), 1)
    out["queue"] = queue_info(cur, row)
    if with_result:
        out["result"] = row["result"]
    return out


class NewTask(BaseModel):
    type: str
    params: dict = {}


@app.post("/api/tasks", status_code=201)
def create_task(body: NewTask, user: dict = Depends(current_user)):
    try:
        params = validate(body.type, body.params)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (user["id"],))  # серіалізація запитів одного користувача
        cur.execute("SELECT count(*) AS c FROM tasks WHERE user_id=%s AND status IN ('queued','running')", (user["id"],))
        if cur.fetchone()["c"] >= MAX_ACTIVE_PER_USER:
            raise HTTPException(429, f"Ліміт: не більше {MAX_ACTIVE_PER_USER} активних задач одночасно")
        cur.execute("SELECT count(*) AS c FROM tasks WHERE status='queued'")
        if cur.fetchone()["c"] >= MAX_QUEUE_LENGTH:
            raise HTTPException(503, "Черга переповнена, спробуйте пізніше")
        # обмеження історії: видаляємо найстаріші завершені записи
        cur.execute("""DELETE FROM tasks WHERE id IN (
                         SELECT id FROM tasks WHERE user_id=%s AND status NOT IN ('queued','running')
                         ORDER BY created_at DESC OFFSET %s)""", (user["id"], MAX_HISTORY_PER_USER - 1))
        cur.execute("INSERT INTO tasks(user_id,type,params) VALUES (%s,%s,%s) RETURNING *",
                    (user["id"], body.type, Json(params)))
        row = cur.fetchone()
        return public_task(cur, row)


@app.get("/api/tasks")
def list_tasks(user: dict = Depends(current_user)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
                    (user["id"], MAX_HISTORY_PER_USER))
        return [public_task(cur, r) for r in cur.fetchall()]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, user: dict = Depends(current_user)):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM tasks WHERE id=%s AND (user_id=%s OR %s)", (task_id, user["id"], user["is_admin"]))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Задачу не знайдено")
        return public_task(cur, row, with_result=True)


def do_cancel(task_id: int, user_id: int | None):
    with db.cursor() as cur:
        cur.execute("""UPDATE tasks SET status='cancelled', stage='Скасовано', finished_at=now()
                       WHERE id=%s AND status='queued' AND (%s::int IS NULL OR user_id=%s) RETURNING id""",
                    (task_id, user_id, user_id))
        if cur.fetchone():
            return {"status": "cancelled"}
        cur.execute("""UPDATE tasks SET cancel_requested=true, stage='Скасування…'
                       WHERE id=%s AND status='running' AND (%s::int IS NULL OR user_id=%s) RETURNING id""",
                    (task_id, user_id, user_id))
        if cur.fetchone():
            return {"status": "cancelling"}
    raise HTTPException(409, "Задача не існує або вже завершена")


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: int, user: dict = Depends(current_user)):
    return do_cancel(task_id, user["id"])


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, user: dict = Depends(current_user)):
    with db.cursor() as cur:
        cur.execute("""DELETE FROM tasks WHERE id=%s AND user_id=%s AND status NOT IN ('queued','running')
                       RETURNING id""", (task_id, user["id"]))
        if not cur.fetchone():
            raise HTTPException(409, "Видалити можна лише завершену задачу")
    return {"deleted": True}


# ---------------------------------------------------------------- admin
@app.get("/api/admin/overview")
def admin_overview(_: dict = Depends(admin_user)):
    with db.cursor() as cur:
        cur.execute("""SELECT *, heartbeat > now() - interval '10 seconds' AS alive,
                       extract(epoch FROM now() - started_at) AS uptime FROM servers ORDER BY dynamic, id""")
        servers = cur.fetchall()
        cur.execute("""SELECT t.id,t.type,t.status,t.progress,t.stage,t.server_id,t.created_at,t.started_at,
                              t.finished_at,u.username FROM tasks t JOIN users u ON u.id=t.user_id
                       WHERE t.status IN ('queued','running') ORDER BY t.created_at""")
        active = cur.fetchall()
        cur.execute("""SELECT t.id,t.type,t.status,t.server_id,t.error,t.finished_at,u.username,
                              extract(epoch FROM t.finished_at - t.started_at) AS duration
                       FROM tasks t JOIN users u ON u.id=t.user_id
                       WHERE t.status IN ('done','failed','cancelled') ORDER BY t.finished_at DESC NULLS LAST LIMIT 15""")
        recent = cur.fetchall()
        cur.execute("SELECT id, ts, message FROM events ORDER BY id DESC LIMIT 25")
        events = cur.fetchall()
        cur.execute("SELECT count(*) AS c FROM users")
        users = cur.fetchone()["c"]
    alive = [s for s in servers if s["alive"]]
    return {"servers": servers, "active": active, "recent": recent, "events": events, "users": users,
            "totals": {"servers": len(alive), "slots": sum(s["capacity"] for s in alive),
                       "running": sum(1 for a in active if a["status"] == "running"),
                       "queued": sum(1 for a in active if a["status"] == "queued")},
            "served_by": SERVER_ID}


@app.post("/api/admin/tasks/{task_id}/cancel")
def admin_cancel(task_id: int, _: dict = Depends(admin_user)):
    return do_cancel(task_id, None)


app.mount("/", StaticFiles(directory="static", html=True), name="static")

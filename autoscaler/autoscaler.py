"""Горизонтальне масштабування: запускає/зупиняє контейнери app-серверів залежно від навантаження.

Правила:
  * є черга і вільних слотів немає довше UP_AFTER секунд  -> +1 сервер (не більше MAX_SERVERS);
  * черги немає, а динамічний сервер простоює DOWN_AFTER секунд -> позначаємо draining,
    коли на ньому завершаться задачі - контейнер зупиняється.
Статичні сервери (з docker-compose) ніколи не зупиняються."""
import os
import time

import docker
import psycopg2
from psycopg2.extras import RealDictCursor

DB = os.getenv("DATABASE_URL", "postgresql://app:app@db:5432/app")
IMAGE = os.getenv("APP_IMAGE", "lab-app:latest")
NETWORK = os.getenv("NETWORK", "lab-net")
MAX_SERVERS = int(os.getenv("MAX_SERVERS", "5"))
UP_AFTER = float(os.getenv("SCALE_UP_AFTER", "5"))
DOWN_AFTER = float(os.getenv("SCALE_DOWN_AFTER", "45"))
PASS_ENV = ["DATABASE_URL", "SECRET_KEY", "SERVER_CAPACITY", "MAX_TASK_SECONDS", "MAX_ACTIVE_PER_USER",
            "MAX_HISTORY_PER_USER", "MAX_QUEUE_LENGTH", "ADMIN_USER", "ADMIN_PASSWORD"]

client = docker.from_env()


def connect():
    while True:
        try:
            c = psycopg2.connect(DB)
            c.autocommit = True
            return c
        except psycopg2.OperationalError:
            time.sleep(2)


conn = connect()


def query(sql, args=None):
    global conn
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else []
    except psycopg2.Error:
        conn = connect()
        return []


def event(msg):
    print(msg, flush=True)
    query("INSERT INTO events(message) VALUES (%s)", (msg,))


def spawn():
    name = f"app-dyn-{int(time.time())}"
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env.update({"DYNAMIC": "1", "SERVER_ID": name})
    api = client.api
    cid = api.create_container(
        IMAGE, name=name, environment=env, labels={"autoscaler": "1"},
        host_config=api.create_host_config(network_mode=NETWORK, restart_policy={"Name": "no"}),
        networking_config=api.create_networking_config({NETWORK: api.create_endpoint_config(aliases=["app"])}),
    )["Id"]
    api.start(cid)
    return name


def stop(name):
    try:
        c = client.containers.get(name)
        c.stop(timeout=10)
        c.remove()
    except docker.errors.NotFound:
        pass


pending: dict[str, float] = {}     # контейнери, що стартують, але ще не зареєструвались
pressure_since = None
idle_since = None

event(f"Автоскейлер запущено (максимум серверів: {MAX_SERVERS})")
while True:
    time.sleep(2)
    now = time.time()
    servers = query("SELECT * FROM servers WHERE heartbeat > now() - interval '10 seconds'")
    if not servers and not pending:
        continue
    queued = (query("SELECT count(*) AS c FROM tasks WHERE status='queued'") or [{"c": 0}])[0]["c"]
    ids = {s["id"] for s in servers}
    for n in list(pending):
        if n in ids or now - pending[n] > 60:
            del pending[n]
    active = [s for s in servers if not s["draining"]]
    free = sum(s["capacity"] - s["running"] for s in active)

    # ---- масштабування вгору
    if queued > 0 and free <= 0:
        pressure_since = pressure_since or now
        idle_since = None
        if now - pressure_since >= UP_AFTER and not pending:
            draining = [s for s in servers if s["draining"]]
            if draining:
                query("UPDATE servers SET draining=false WHERE id=%s", (draining[0]["id"],))
                event(f"Навантаження зросло: сервер {draining[0]['id']} знову приймає задачі")
            elif len(servers) + len(pending) < MAX_SERVERS:
                try:
                    name = spawn()
                    pending[name] = now
                    event(f"Черга: {queued} задач, вільних слотів немає → запущено новий сервер {name} "
                          f"({len(servers) + 1}/{MAX_SERVERS})")
                except Exception as e:
                    event(f"Не вдалося запустити сервер: {e}")
            pressure_since = now
    else:
        pressure_since = None

    # ---- масштабування вниз
    dyn_idle = [s for s in active if s["dynamic"] and s["running"] == 0]
    if queued == 0 and dyn_idle:
        idle_since = idle_since or now
        if now - idle_since >= DOWN_AFTER:
            query("UPDATE servers SET draining=true WHERE id=%s", (dyn_idle[0]["id"],))
            event(f"Простій: сервер {dyn_idle[0]['id']} виведено з обслуговування (draining)")
            idle_since = now
    else:
        idle_since = None
    for s in servers:
        if s["dynamic"] and s["draining"] and s["running"] == 0:
            stop(s["id"])
            query("DELETE FROM servers WHERE id=%s", (s["id"],))
            event(f"Сервер {s['id']} зупинено та видалено")

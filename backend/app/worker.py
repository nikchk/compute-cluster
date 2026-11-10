"""Диспетчер задач application-сервера.

Кожен сервер:
  * реєструється в таблиці servers і шле heartbeat (CPU/RAM/кількість задач);
  * забирає задачі з спільної черги в PostgreSQL (SELECT ... FOR UPDATE SKIP LOCKED),
    поки є вільні слоти (SERVER_CAPACITY);
  * виконує кожну задачу в окремому процесі, стежить за прогресом, скасуванням
    та ліміт часу;
  * якщо інший сервер «помер» - повертає його задачі назад у чергу.
"""
import logging
import multiprocessing as mp
import threading
import time

import psutil
from psycopg2.extras import Json

from . import db
from .config import DYNAMIC, MAX_TASK_SECONDS, SERVER_CAPACITY, SERVER_ID
from .tasks import child_main

log = logging.getLogger("worker")
ctx = mp.get_context("spawn")


def kill(proc):
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
            proc.join(2)


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(name="worker", daemon=True)
        self.stop_ev = threading.Event()
        self.running: dict[int, dict] = {}
        self.draining = False
        self._last_hb = 0.0

    # ---------- реєстрація ----------
    def register(self):
        with db.cursor() as cur:
            cur.execute("""UPDATE tasks SET status='queued', server_id=NULL, progress=0,
                           stage='Повторна постановка у чергу' WHERE status='running' AND server_id=%s""", (SERVER_ID,))
            cur.execute("""INSERT INTO servers(id,capacity,running,dynamic,draining,started_at,heartbeat)
                           VALUES (%s,%s,0,%s,false,now(),now())
                           ON CONFLICT (id) DO UPDATE SET capacity=EXCLUDED.capacity, running=0,
                             dynamic=EXCLUDED.dynamic, draining=false, started_at=now(), heartbeat=now()""",
                        (SERVER_ID, SERVER_CAPACITY, DYNAMIC))
            cur.execute("INSERT INTO events(message) VALUES (%s)",
                        (f"Сервер {SERVER_ID} запущено (слотів: {SERVER_CAPACITY})",))

    def run(self):
        psutil.cpu_percent(None)
        try:
            self.register()
        except Exception:
            log.exception("register failed")
        while not self.stop_ev.is_set():
            try:
                self._supervise()
                if not self.draining:
                    self._claim()
                if time.time() - self._last_hb >= 2:
                    self._heartbeat()
            except Exception:
                log.exception("worker tick failed")
            self.stop_ev.wait(0.3)

    # ---------- heartbeat / відновлення ----------
    def _heartbeat(self):
        self._last_hb = time.time()
        with db.cursor() as cur:
            cur.execute("""UPDATE servers SET heartbeat=now(), running=%s, cpu=%s, mem=%s WHERE id=%s
                           RETURNING draining""",
                        (len(self.running), psutil.cpu_percent(None), psutil.virtual_memory().percent, SERVER_ID))
            row = cur.fetchone()
            if row is None:  # запис видалено (наприклад автоскейлером) - реєструємось знову
                cur.execute("""INSERT INTO servers(id,capacity,running,dynamic) VALUES (%s,%s,%s,%s)
                               ON CONFLICT DO NOTHING""", (SERVER_ID, SERVER_CAPACITY, len(self.running), DYNAMIC))
            else:
                self.draining = row["draining"]
            # виявлення мертвих серверів
            cur.execute("""DELETE FROM servers WHERE heartbeat < now() - interval '15 seconds' AND id <> %s
                           RETURNING id""", (SERVER_ID,))
            dead = [r["id"] for r in cur.fetchall()]
            if dead:
                cur.execute("""UPDATE tasks SET status='queued', server_id=NULL, progress=0,
                               stage='Сервер недоступний - повторна постановка' WHERE status='running'
                               AND server_id = ANY(%s)""", (dead,))
                cur.execute("INSERT INTO events(message) VALUES (%s)",
                            (f"Сервер(и) {', '.join(dead)} не відповідають - їх задачі повернуто в чергу",))

    # ---------- взяття задач ----------
    def _claim(self):
        while len(self.running) < SERVER_CAPACITY and not self.stop_ev.is_set():
            with db.cursor() as cur:
                cur.execute("""UPDATE tasks SET status='running', started_at=now(), server_id=%s,
                               progress=0, stage='Запуск обчислення'
                               WHERE id = (SELECT id FROM tasks WHERE status='queued'
                                           ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
                               RETURNING id, type, params""", (SERVER_ID,))
                task = cur.fetchone()
            if not task:
                return
            reader, writer = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=child_main, args=(task["type"], task["params"], writer), daemon=True)
            proc.start()
            writer.close()
            self.running[task["id"]] = {"proc": proc, "conn": reader, "started": time.time(),
                                        "last_db": 0.0, "progress": 0.0, "stage": "Запуск обчислення"}

    # ---------- нагляд за процесами ----------
    def _supervise(self):
        for tid, t in list(self.running.items()):
            finished = None
            try:
                while t["conn"].poll():
                    msg = t["conn"].recv()
                    if msg[0] == "progress":
                        t["progress"], t["stage"] = msg[1], msg[2]
                    elif msg[0] == "result":
                        finished = ("done", msg[1], None)
                    elif msg[0] == "error":
                        finished = ("failed", None, msg[1])
            except (EOFError, OSError):
                if finished is None:
                    finished = ("failed", None, "Процес аварійно завершився (можливо, не вистачило пам'яті)")
            now = time.time()
            if finished is None and now - t["started"] > MAX_TASK_SECONDS:
                finished = ("failed", None, f"Перевищено ліміт часу виконання ({MAX_TASK_SECONDS} с)")
            if finished:
                self._finish(tid, t, *finished)
                continue
            if now - t["last_db"] >= 1.0:
                t["last_db"] = now
                with db.cursor() as cur:
                    cur.execute("""UPDATE tasks SET progress=%s, stage=%s WHERE id=%s AND status='running'
                                   RETURNING cancel_requested""", (t["progress"], t["stage"], tid))
                    row = cur.fetchone()
                if row is None:          # задачу змінили ззовні
                    kill(t["proc"]); del self.running[tid]
                elif row["cancel_requested"]:
                    self._finish(tid, t, "cancelled", None, None)

    def _finish(self, tid, t, status, result, error):
        kill(t["proc"])
        try:
            t["conn"].close()
        except OSError:
            pass
        self.running.pop(tid, None)
        with db.cursor() as cur:
            if status == "done":
                cur.execute("""UPDATE tasks SET status='done', progress=100, stage='Завершено', result=%s,
                               finished_at=now() WHERE id=%s AND status='running'""", (Json(result), tid))
            elif status == "cancelled":
                cur.execute("""UPDATE tasks SET status='cancelled', stage='Скасовано', finished_at=now()
                               WHERE id=%s AND status='running'""", (tid,))
            else:
                cur.execute("""UPDATE tasks SET status='failed', stage='Помилка', error=%s, finished_at=now()
                               WHERE id=%s AND status='running'""", (error, tid))

    # ---------- завершення ----------
    def shutdown(self):
        self.stop_ev.set()
        self.join(5)
        for t in self.running.values():
            kill(t["proc"])
        try:
            with db.cursor() as cur:
                cur.execute("""UPDATE tasks SET status='queued', server_id=NULL, progress=0,
                               stage='Сервер зупинено - повторна постановка' WHERE status='running' AND server_id=%s""",
                            (SERVER_ID,))
                cur.execute("DELETE FROM servers WHERE id=%s", (SERVER_ID,))
                cur.execute("INSERT INTO events(message) VALUES (%s)", (f"Сервер {SERVER_ID} зупинено",))
        except Exception:
            log.exception("shutdown cleanup failed")

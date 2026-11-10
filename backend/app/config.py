import os
import socket

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@db:5432/app")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-please")

# Ідентифікатор application-сервера (hostname контейнера, або задається явно)
SERVER_ID = os.getenv("SERVER_ID") or socket.gethostname()
# Скільки задач один сервер виконує одночасно (кожна - в окремому процесі)
SERVER_CAPACITY = int(os.getenv("SERVER_CAPACITY", "2"))
# Чи запущений сервер автоскейлером (може бути зупинений при простої)
DYNAMIC = os.getenv("DYNAMIC", "0") == "1"

# ---- Обмеження системи ----
MAX_TASK_SECONDS = int(os.getenv("MAX_TASK_SECONDS", "120"))       # жорсткий ліміт часу однієї задачі
MAX_ACTIVE_PER_USER = int(os.getenv("MAX_ACTIVE_PER_USER", "3"))   # queued+running на користувача
MAX_HISTORY_PER_USER = int(os.getenv("MAX_HISTORY_PER_USER", "20"))  # записів в історії на користувача
MAX_QUEUE_LENGTH = int(os.getenv("MAX_QUEUE_LENGTH", "50"))        # довжина глобальної черги

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin12345")
TOKEN_TTL_SECONDS = 12 * 3600

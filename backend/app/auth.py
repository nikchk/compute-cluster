import hashlib
import hmac
import os
import time

import jwt
from fastapi import Depends, Header, HTTPException

from .config import SECRET_KEY, TOKEN_TTL_SECONDS


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, dk_hex = stored.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(dk.hex(), dk_hex)


def make_token(user: dict) -> str:
    payload = {
        "sub": str(user["id"]),
        "usr": user["username"],
        "adm": user["is_admin"],
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Потрібна авторизація")
    try:
        p = jwt.decode(authorization[7:], SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Недійсний або прострочений токен")
    return {"id": int(p["sub"]), "username": p["usr"], "is_admin": p["adm"]}


def admin_user(user: dict = Depends(current_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Лише для адміністратора")
    return user

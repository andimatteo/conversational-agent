"""Users + bearer-token auth. Hackathon-grade but real: salted PBKDF2
password hashes, opaque session tokens in SQLite, a FastAPI dependency
that resolves the caller. Every /api job route is scoped to its owner;
/agent-tools/* stays machine-to-machine (ElevenLabs calls it, not users).
"""
import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Header, HTTPException

from . import db


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public(user: dict) -> dict:
    """The user as the API returns it — never the hash or salt."""
    return {k: user.get(k, "") for k in ("id", "email", "name", "created_at")}


def create_user(email: str, password: str, name: str = "") -> dict:
    email = email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "invalid email")
    if len(password) < 6:
        raise HTTPException(422, "password must be at least 6 characters")
    if db.where("users", email=email):
        raise HTTPException(409, "email already registered")
    salt = secrets.token_hex(16)
    user = {"id": db.new_id("user"), "email": email, "name": name.strip() or email.split("@")[0],
            "salt": salt, "password_hash": _hash(password, salt), "created_at": _now()}
    db.put("users", user["id"], user, email=email)
    return user


def verify_user(email: str, password: str) -> dict | None:
    rows = db.where("users", email=email.strip().lower())
    if not rows:
        return None
    u = rows[0]
    return u if secrets.compare_digest(u["password_hash"], _hash(password, u["salt"])) else None


def issue_token(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    db.put("sessions", token, {"user_id": user["id"], "created_at": _now()}, user_id=user["id"])
    return token


def revoke_token(token: str):
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE id=?", (token,))


def current_user(authorization: str = Header("")) -> dict:
    """FastAPI dependency: resolves `Authorization: Bearer <token>` to a user."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Missing bearer token — login first")
    sess = db.get("sessions", token)
    user = db.get("users", sess["user_id"]) if sess else None
    if not user:
        raise HTTPException(401, "Invalid or expired token")
    return user

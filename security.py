from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path(os.environ.get("GOODS_API_DATA_DIR", "/var/lib/lightpanda-goods-api"))
DB_FILE = DATA_DIR / "keys.sqlite3"
ADMIN_FILE = DATA_DIR / "admin.json"
BOOTSTRAP_FILE = DATA_DIR / "bootstrap-password.txt"
SESSION_SECONDS = 12 * 60 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _private_write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def init_state() -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                prefix TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL
            );
            """
        )
    if not ADMIN_FILE.exists():
        password = secrets.token_urlsafe(18)
        salt = secrets.token_bytes(16)
        _private_write(ADMIN_FILE, json.dumps({"salt": salt.hex(), "hash": _password_hash(password, salt)}))
        _private_write(BOOTSTRAP_FILE, password + "\n")


@contextmanager
def connect_db():
    db = sqlite3.connect(DB_FILE, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def verify_admin_password(password: str) -> bool:
    try:
        data = json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
        actual = _password_hash(password, bytes.fromhex(data["salt"]))
        return hmac.compare_digest(actual, data["hash"])
    except (OSError, ValueError, KeyError):
        return False


def create_admin_session() -> str:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect_db() as db:
        db.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute(
            "INSERT INTO admin_sessions(token_hash, expires_at) VALUES (?, ?)",
            (digest, int(time.time()) + SESSION_SECONDS),
        )
    BOOTSTRAP_FILE.unlink(missing_ok=True)
    return token


def valid_admin_session(token: str | None) -> bool:
    if not token:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect_db() as db:
        row = db.execute("SELECT expires_at FROM admin_sessions WHERE token_hash=?", (digest,)).fetchone()
    return bool(row and row["expires_at"] > int(time.time()))


def revoke_admin_session(token: str | None) -> None:
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with connect_db() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (digest,))


def create_api_key(label: str) -> tuple[dict, str]:
    token = "lp_live_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    created = _utc_now()
    with connect_db() as db:
        cursor = db.execute(
            "INSERT INTO api_keys(label,prefix,key_hash,created_at) VALUES (?,?,?,?)",
            (label, token[:16], digest, created),
        )
        key_id = cursor.lastrowid
    return {"id": key_id, "label": label, "prefix": token[:16], "createdAt": created}, token


def list_api_keys() -> list[dict]:
    with connect_db() as db:
        rows = db.execute(
            "SELECT id,label,prefix,created_at,last_used_at,revoked_at FROM api_keys ORDER BY id DESC"
        ).fetchall()
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "prefix": row["prefix"],
            "createdAt": row["created_at"],
            "lastUsedAt": row["last_used_at"],
            "revokedAt": row["revoked_at"],
        }
        for row in rows
    ]


def revoke_api_key(key_id: int) -> bool:
    with connect_db() as db:
        cursor = db.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (_utc_now(), key_id),
        )
        return cursor.rowcount > 0


def verify_api_key(token: str | None) -> bool:
    if not token or not token.startswith("lp_live_"):
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect_db() as db:
        row = db.execute(
            "SELECT id FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (digest,)
        ).fetchone()
        if row:
            db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (_utc_now(), row["id"]))
    return bool(row)

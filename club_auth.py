#!/usr/bin/env python3
"""Shared club login helpers for the HTML login server and the Streamlit app.

Usernames and passwords live only here. Both processes import this module so
credentials and the stay-logged-in cookie/session format stay in sync.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DD_DB_PATH") or (BASE_DIR / "duck_hunt.db"))

AUTH_COOKIE = "dd_stay"
AUTH_DAYS = 30
# Short-lived server expiry when "Stay logged in" is unchecked (session cookie).
SESSION_HOURS = 12

CLUB_USERS = {
    "jeff": {"pw": "duckhunt", "role": "admin"},
    "jeffery.utley@gmail.com": {"pw": "duckhunt", "role": "admin"},
    "admin@ddhunt.com": {"pw": "duckhunt", "role": "admin"},
    "andrew": {"pw": "andrew123", "role": "admin"},
    "kyle": {"pw": "kyle123", "role": "admin"},
    "adam": {"pw": "adam123", "role": "admin"},
    "justin": {"pw": "justin123", "role": "admin"},
    "mcguire": {"pw": "mcguire123", "role": "admin"},
    "caleb": {"pw": "caleb123", "role": "admin"},
    "calebjutley@icloud.com": {"pw": "caleb123", "role": "admin"},
    "penick": {"pw": "penick123", "role": "admin"},
    "penick.river@icloud.com": {"pw": "penick123", "role": "admin"},
    "river": {"pw": "river123", "role": "admin"},
}
LOGIN_DISPLAY_NAMES = {
    "jeffery.utley@gmail.com": "jeff",
    "admin@ddhunt.com": "jeff",
    "calebjutley@icloud.com": "caleb",
    "penick.river@icloud.com": "penick",
}

_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def club_display_name(login_key: str) -> str:
    return LOGIN_DISPLAY_NAMES.get(login_key, login_key)


def club_role(username: str) -> str | None:
    rec = CLUB_USERS.get((username or "").strip().lower())
    return rec["role"] if rec else None


def hash_auth_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_iso_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text_v = str(value).strip()
        if not text_v:
            return None
        if text_v.endswith("Z"):
            text_v = text_v[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text_v)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_token(raw) -> str | None:
    if not raw:
        return None
    token = str(raw).strip()
    if len(token) < 16 or any(c not in _TOKEN_CHARS for c in token):
        return None
    return token


def authenticate(username: str, password: str) -> tuple[str, str, str] | None:
    """Return (login_key, display_name, role) if the credentials match."""
    key = (username or "").strip().lower()
    rec = CLUB_USERS.get(key)
    if rec and password is not None and rec["pw"] == password:
        return key, club_display_name(key), rec["role"]
    return None


def _supabase_env():
    """Return (url, key) if configured, else (None, None). Never log the key."""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if url and key:
        return url, key
    return None, None


def use_supabase() -> bool:
    url, key = _supabase_env()
    return bool(url and key)


@lru_cache(maxsize=1)
def get_supabase_client():
    from supabase import create_client

    url, key = _supabase_env()
    if not url or not key:
        raise RuntimeError("Supabase env not configured")
    return create_client(url, key)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_sqlite_auth_sessions(conn) -> None:
    conn.cursor().execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def record_login(username: str, login_key: str | None = None) -> None:
    """Best-effort log of a successful club sign-in (Supabase when configured)."""
    try:
        if use_supabase():
            client = get_supabase_client()
            row = {"username": username}
            if login_key:
                row["login_key"] = login_key
            client.table("login_events").insert(row).execute()
            return
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                login_key TEXT,
                logged_in_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            "INSERT INTO login_events (username, login_key) VALUES (?, ?)",
            (username, login_key),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Never block login on logging failures
        pass


def issue_auth_session(username: str, days: float | None = None) -> str | None:
    """Create a session and return the raw token (store only the hash)."""
    try:
        lifetime = AUTH_DAYS if days is None else float(days)
        token = secrets.token_urlsafe(32)
        token_hash = hash_auth_token(token)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=lifetime)
        row = {
            "username": username,
            "token_hash": token_hash,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "last_seen_at": now.isoformat(),
            "revoked": False,
        }
        if use_supabase():
            get_supabase_client().table("auth_sessions").insert(row).execute()
        else:
            conn = get_db_connection()
            _ensure_sqlite_auth_sessions(conn)
            conn.cursor().execute(
                """
                INSERT INTO auth_sessions
                    (username, token_hash, created_at, expires_at, last_seen_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (username, token_hash, row["created_at"], row["expires_at"], row["last_seen_at"]),
            )
            conn.commit()
            conn.close()
        return token
    except Exception:
        return None


def lookup_auth_session(token: str) -> dict | None:
    """Return a valid (unexpired, not revoked) session for this token, or None."""
    try:
        token_hash = hash_auth_token(token)
        now = datetime.now(timezone.utc)
        row = None
        if use_supabase():
            resp = (
                get_supabase_client()
                .table("auth_sessions")
                .select("id,username,expires_at,revoked")
                .eq("token_hash", token_hash)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            row = rows[0] if rows else None
        else:
            conn = get_db_connection()
            _ensure_sqlite_auth_sessions(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT id, username, expires_at, revoked FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            )
            found = cur.fetchone()
            conn.close()
            if found:
                row = {
                    "id": found[0],
                    "username": found[1],
                    "expires_at": found[2],
                    "revoked": found[3],
                }
        if not row:
            return None
        if row.get("revoked") in (True, 1, "1", "t", "true"):
            return None
        expires = parse_iso_dt(row.get("expires_at"))
        if expires is None or expires <= now:
            return None
        if not club_role(row.get("username") or ""):
            return None
        return row
    except Exception:
        return None


def touch_auth_session(session_id) -> None:
    try:
        now = datetime.now(timezone.utc).isoformat()
        if use_supabase():
            get_supabase_client().table("auth_sessions").update({"last_seen_at": now}).eq("id", session_id).execute()
        else:
            conn = get_db_connection()
            _ensure_sqlite_auth_sessions(conn)
            conn.cursor().execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (now, session_id))
            conn.commit()
            conn.close()
    except Exception:
        pass


def revoke_auth_session(token: str | None) -> None:
    if not token:
        return
    try:
        token_hash = hash_auth_token(token)
        if use_supabase():
            get_supabase_client().table("auth_sessions").update({"revoked": True}).eq("token_hash", token_hash).execute()
        else:
            conn = get_db_connection()
            _ensure_sqlite_auth_sessions(conn)
            conn.cursor().execute("UPDATE auth_sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,))
            conn.commit()
            conn.close()
    except Exception:
        pass


def set_cookie_header(token: str, stay: bool, secure: bool) -> str:
    parts = [f"{AUTH_COOKIE}={token}", "Path=/", "SameSite=Lax", "HttpOnly"]
    if stay:
        parts.append(f"Max-Age={AUTH_DAYS * 86400}")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(secure: bool) -> str:
    parts = [
        f"{AUTH_COOKIE}=",
        "Path=/",
        "SameSite=Lax",
        "HttpOnly",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)

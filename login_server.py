#!/usr/bin/env python3
"""Tiny stdlib HTTP server for the native HTML club login page.

Serves GET/POST /login and GET /logout on loopback. nginx proxies those
paths here so iOS can save passwords after a real form POST + 303 redirect.
"""

from __future__ import annotations

import html
import os
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from club_auth import (
    AUTH_COOKIE,
    SESSION_HOURS,
    authenticate,
    clear_cookie_header,
    issue_auth_session,
    lookup_auth_session,
    normalize_token,
    record_login,
    revoke_auth_session,
    set_cookie_header,
)

LOGIN_HTML_PATH = Path(__file__).parent / "login.html"
INVALID_LOGIN_MSG = "Invalid login. Please use your assigned username and password."
SESSION_ERROR_MSG = "Could not start a signed-in session. Please try again."


def _load_login_html() -> str:
    return LOGIN_HTML_PATH.read_text(encoding="utf-8")


def render_login_page(error: str | None = None, username: str = "") -> bytes:
    page = _load_login_html()
    if error:
        err_block = f'<p class="error" role="alert">{html.escape(error)}</p>\n'
    else:
        err_block = ""
    page = page.replace("__ERROR_BLOCK__", err_block)
    page = page.replace("__USERNAME_VALUE__", html.escape(username, quote=True))
    return page.encode("utf-8")


def cookie_is_secure(handler: BaseHTTPRequestHandler) -> bool:
    proto = (handler.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    return handler.headers.get("X-Forwarded-Ssl", "").lower() == "on"


def read_auth_cookie(handler: BaseHTTPRequestHandler) -> str | None:
    raw = handler.headers.get("Cookie") or ""
    cookies = SimpleCookie()
    try:
        cookies.load(raw)
    except Exception:
        return None
    morsel = cookies.get(AUTH_COOKIE)
    if not morsel:
        return None
    return normalize_token(morsel.value)


class LoginHandler(BaseHTTPRequestHandler):
    server_version = "DDLogin/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging query strings or form bodies; path + status only.
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie_header: str | None = None) -> None:
        body = b""
        self.send_response(303)
        self.send_header("Location", location)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/login/health"):
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/logout":
            self._handle_logout()
            return
        if path in ("/login", "/login/"):
            token = read_auth_cookie(self)
            if token and lookup_auth_session(token):
                self._redirect("/")
                return
            self._send_html(200, render_login_page())
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/logout":
            self._handle_logout()
            return
        if path not in ("/login", "/login/"):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        # Login form is tiny; reject anything oversized.
        if length < 0 or length > 4096:
            self._send_html(200, render_login_page(INVALID_LOGIN_MSG))
            return
        raw = self.rfile.read(length) if length else b""
        try:
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except Exception:
            form = {}

        username = (form.get("username") or [""])[0]
        password = (form.get("password") or [""])[0]
        stay_vals = form.get("stay_logged_in") or []
        stay = any(v in ("1", "on", "true", "yes") for v in stay_vals)

        result = authenticate(username, password)
        if not result:
            self._send_html(200, render_login_page(INVALID_LOGIN_MSG, username=username))
            return

        login_key, display, _role = result
        old = read_auth_cookie(self)
        if old:
            revoke_auth_session(old)

        days = None if stay else (SESSION_HOURS / 24)
        token = issue_auth_session(display, days=days)
        if not token:
            self._send_html(200, render_login_page(SESSION_ERROR_MSG, username=username))
            return

        record_login(display, login_key=login_key)
        self._redirect("/", set_cookie_header(token, stay=stay, secure=cookie_is_secure(self)))

    def _handle_logout(self) -> None:
        token = read_auth_cookie(self)
        revoke_auth_session(token)
        self._redirect("/login", clear_cookie_header(cookie_is_secure(self)))

    def do_HEAD(self) -> None:
        # Used only so simple health probes work; body omitted.
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/login/health", "/login", "/login/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        self.send_error(404)


def main() -> None:
    host = os.environ.get("LOGIN_HOST", "127.0.0.1")
    port = int(os.environ.get("LOGIN_PORT", "8502"))
    if not LOGIN_HTML_PATH.is_file():
        sys.stderr.write(f"Missing login page: {LOGIN_HTML_PATH}\n")
        sys.exit(1)
    server = ThreadingHTTPServer((host, port), LoginHandler)
    sys.stderr.write(f"Login server listening on {host}:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

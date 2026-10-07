from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .execution import run_exercise, verify_fastapi_app, LANGUAGES, select_backend

# Minimal production server layer (stdlib-only, deps stay []).
#
# Security model (fail closed):
#   - Binds 127.0.0.1 by default. Binding beyond loopback REQUIRES a token.
#   - Every /api/* call requires `Authorization: Bearer <token>` unless the server
#     was explicitly started with allow_anon=True AND is bound to loopback.
#   - Token comparison is constant-time (hmac.compare_digest).
#   - Request bodies are capped (default 512 KiB); JSON only.
#   - /health is unauthenticated (liveness only, no data).
# Untrusted multi-tenant code execution still belongs in ContainerBackend
# (ADAPTIVE_EXECUTOR=container); this server is the API surface, not the sandbox.

MAX_BODY = 512 * 1024
VERSION = "0.6.0"


class ServerConfig:
    def __init__(self, *, host: str = "127.0.0.1", port: int = 8777,
                 token: str | None = None, allow_anon: bool = False,
                 ui_file: Path | None = None, prefer_backend: str | None = None,
                 max_concurrent_runs: int = 4):
        self.host = host
        self.port = port
        self.token = token if token is not None else os.environ.get("ADAPTIVE_API_TOKEN")
        self.allow_anon = allow_anon
        self.ui_file = ui_file
        self.prefer_backend = prefer_backend
        self.max_concurrent_runs = max(1, int(max_concurrent_runs))
        if host not in {"127.0.0.1", "localhost", "::1"} and not self.token:
            raise ValueError("binding beyond loopback requires a token (set ADAPTIVE_API_TOKEN)")
        if not self.token and not self.allow_anon:
            raise ValueError("no token configured: set ADAPTIVE_API_TOKEN or pass allow_anon for loopback-only use")


def _handler(cfg: ServerConfig):
    # Bounded execution concurrency: each /api run holds a slot; beyond the cap the
    # request is refused with 429 instead of queueing unbounded compilers.
    run_slots = threading.BoundedSemaphore(cfg.max_concurrent_runs)

    class Handler(BaseHTTPRequestHandler):
        server_version = "alearn/" + VERSION
        # Socket inactivity timeout: a slow client (slowloris) cannot pin a worker
        # thread forever; BaseHTTPRequestHandler applies this to the connection.
        timeout = 30

        # --- helpers -----------------------------------------------------
        def _send(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            if cfg.allow_anon and self.client_address[0] in {"127.0.0.1", "::1"}:
                return True
            if not cfg.token:
                return False
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            return hmac.compare_digest(auth[7:].strip(), cfg.token)

        def _body(self):
            if self.headers.get("Transfer-Encoding"):
                return None, (411, {"error": "length_required", "detail": "chunked bodies not accepted"})
            cl = self.headers.get("Content-Length")
            if cl is None:
                return None, (411, {"error": "length_required"})
            try:
                n = int(cl)
            except ValueError:
                return None, (400, {"error": "bad_content_length"})
            if n < 0:
                return None, (400, {"error": "bad_content_length"})
            if n > MAX_BODY:
                # Drain (bounded) so the client can finish sending and read the 413
                # instead of hitting a broken pipe mid-request.
                remaining = min(n, MAX_BODY * 4)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                return None, (413, {"error": "body_too_large", "max_bytes": MAX_BODY})
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8")), None
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return None, (400, {"error": "invalid_json", "detail": str(e)[:200]})

        def log_message(self, fmt, *args):  # no request bodies / tokens in logs
            pass

        # --- routes ------------------------------------------------------
        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "version": VERSION})
            if self.path in {"/", "/index.html"}:
                if cfg.ui_file and cfg.ui_file.is_file():
                    body = cfg.ui_file.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                return self._send(200, {"service": "adaptive-learning-os", "version": VERSION,
                                        "endpoints": ["/health", "/api/languages", "/api/run", "/api/verify-fastapi"]})
            if self.path == "/api/languages":
                if not self._authed():
                    return self._send(401, {"error": "unauthorized"})
                return self._send(200, {"languages": [LANGUAGES[k].to_dict() for k in sorted(LANGUAGES)]})
            return self._send(404, {"error": "not_found"})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            data, err = self._body()
            if err:
                return self._send(*err)
            if not run_slots.acquire(blocking=False):
                return self._send(429, {"error": "too_many_concurrent_runs",
                                        "max_concurrent": cfg.max_concurrent_runs})
            try:
                return self._dispatch(data)
            finally:
                run_slots.release()

        def _dispatch(self, data):
            backend = select_backend(cfg.prefer_backend)
            if self.path == "/api/run":
                lang = data.get("lang"); code = data.get("code")
                if lang not in LANGUAGES or not isinstance(code, str) or not code:
                    return self._send(400, {"error": "need lang (known) and code (string)"})
                res = run_exercise(lang, code, stdin=data.get("stdin"),
                                   expect_stdout=data.get("expect_stdout"), backend=backend)
                return self._send(200, res)
            if self.path == "/api/verify-fastapi":
                code = data.get("code"); checks = data.get("checks")
                if not isinstance(code, str) or not isinstance(checks, list):
                    return self._send(400, {"error": "need code (string) and checks (array)"})
                return self._send(200, verify_fastapi_app(code, checks, backend=backend))
            return self._send(404, {"error": "not_found"})

    return Handler


def make_server(cfg: ServerConfig) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((cfg.host, cfg.port), _handler(cfg))
    srv.daemon_threads = True   # a hung worker never blocks shutdown
    return srv


def run_server(cfg: ServerConfig):  # pragma: no cover - blocking entrypoint
    srv = make_server(cfg)
    mode = "anon-loopback" if (cfg.allow_anon and not cfg.token) else "token"
    print(f"alearn serve on http://{cfg.host}:{srv.server_address[1]}  auth={mode}  "
          f"ui={'yes' if cfg.ui_file else 'no'}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()

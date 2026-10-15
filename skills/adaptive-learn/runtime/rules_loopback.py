"""Loopback-only HTTP client + in-process server harness for the rules gate (probes of R6 and R10).

The gate must prove staged content is unreachable from learner routes and that the loopback guard holds, which needs
real HTTP requests against the real server handler. This is the ONLY module of the gate that speaks HTTP: it binds
127.0.0.1 on an ephemeral port inside the rules-check process, refuses any other host and never opens a connection
to anything but the server it just started. ``studio_*.py``, ``rules_check.py`` and ``rules_probes/*.py`` stay free
of network client imports (R10 static scan).
"""
from __future__ import annotations

import http.client
import threading
from pathlib import Path

from .server import ServerConfig, make_server

LOOPBACK = "127.0.0.1"


class LoopbackServer:
    """Context manager: the course server for ONE session on 127.0.0.1:<ephemeral>, anonymous loopback mode; with
    ``owner_token`` the OWNER server of the session instead (token required, no anonymous mode)."""

    def __init__(self, workspace: Path, sid: str, ui_dir: Path | None = None, owner_token: str | None = None):
        if owner_token:
            self.cfg = ServerConfig(host=LOOPBACK, port=0, workspace=Path(workspace), ui_dir=ui_dir,
                                    owner_session=sid, owner_token=owner_token)
        else:
            self.cfg = ServerConfig(host=LOOPBACK, port=0, allow_anon=True, workspace=Path(workspace), ui_dir=ui_dir,
                                    course_session=sid)
        self.srv = None
        self.thread = None
        self.port = None

    def __enter__(self):
        self.srv = make_server(self.cfg)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)

    def request(self, method: str, path: str, *, headers: dict | None = None, body: bytes | None = None):
        """(status, text). Raw paths are sent unnormalised so traversal attempts reach the handler as written."""
        conn = http.client.HTTPConnection(LOOPBACK, self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            merged = {"Host": f"{LOOPBACK}:{self.port}", **(headers or {})}
            for key, value in merged.items():
                conn.putheader(key, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            return resp.status, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

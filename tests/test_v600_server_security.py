"""Anon-mode DNS-rebinding / CSRF guard for the code-executing API."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.server import ServerConfig, make_server  # noqa: E402


def _raw(port, method, path, headers=None, body=None):
    """Send a request with explicit control over Host / Origin headers."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    hdrs = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body)
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdrs)
    r = conn.getresponse()
    status = r.status
    r.read()
    conn.close()
    return status


class AnonGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # This class exercises the local executor path (CSRF/host/validation), so it
        # explicitly opts into unsandboxed host exec; the sandbox GATE is covered by
        # SandboxGateTests below.
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True,
                                           workspace=Path(cls.tmp.name), prefer_backend="local",
                                           allow_unsandboxed_exec=True))
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def test_loopback_host_allowed(self):
        s = _raw(self.port, "GET", "/api/languages", {"Host": f"127.0.0.1:{self.port}"})
        self.assertEqual(s, 200)

    def test_non_object_json_is_bad_request(self):
        for body in ([], "text", 7):
            self.assertEqual(_raw(self.port, "POST", "/api/run", body=body), 400)

    def test_malformed_language_is_bad_request(self):
        self.assertEqual(_raw(self.port, "POST", "/api/run", body={"lang": [], "code": "print(1)"}), 400)

    def test_rebinding_host_rejected(self):
        # Browser pointed at a rebind domain sends that domain as Host -> blocked.
        s = _raw(self.port, "GET", "/api/languages", {"Host": "evil.example.com"})
        self.assertEqual(s, 401)

    def test_cross_origin_post_to_run_rejected(self):
        # CSRF: a page on evil.com POSTing code to the local executor -> blocked.
        s = _raw(self.port, "POST", "/api/run",
                 {"Host": f"127.0.0.1:{self.port}", "Origin": "http://evil.example.com"},
                 {"lang": "python", "code": "print(1)"})
        self.assertEqual(s, 401)

    def test_same_origin_post_allowed(self):
        s = _raw(self.port, "POST", "/api/run",
                 {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}"},
                 {"lang": "python", "code": "print(1)"})
        self.assertEqual(s, 200)

    def test_no_origin_curl_allowed(self):
        # Non-browser client (no Origin) is not a CSRF vector.
        s = _raw(self.port, "POST", "/api/run",
                 {"Host": f"127.0.0.1:{self.port}"},
                 {"lang": "python", "code": "print(1)"})
        self.assertEqual(s, 200)


_GATE_TOKEN = "sandbox-gate-token-abcdefghijklmnop"  # >=24 chars


class SandboxGateTests(unittest.TestCase):
    """Anon multi-language exec is fail-closed unless container/opt-in; token trusted."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # anon + host backend, NO opt-in -> untrusted exec must be refused.
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True,
                                           workspace=Path(cls.tmp.name), prefer_backend="local"))
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        # token server: the caller holds the secret -> trusted, exec not gated.
        cls.ttmp = tempfile.TemporaryDirectory()
        cls.tsrv = make_server(ServerConfig(host="127.0.0.1", port=0, token=_GATE_TOKEN,
                                            workspace=Path(cls.ttmp.name), prefer_backend="local"))
        cls.tport = cls.tsrv.server_address[1]
        cls.tt = threading.Thread(target=cls.tsrv.serve_forever, daemon=True)
        cls.tt.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()
        cls.tsrv.shutdown()
        cls.ttmp.cleanup()

    def test_anon_unsandboxed_run_is_refused(self):
        s = _raw(self.port, "POST", "/api/run",
                 {"Host": f"127.0.0.1:{self.port}"},
                 {"lang": "python", "code": "print(1)", "expect_stdout": "1"})
        self.assertEqual(s, 503)

    def test_anon_verify_fastapi_is_refused(self):
        s = _raw(self.port, "POST", "/api/verify-fastapi",
                 {"Host": f"127.0.0.1:{self.port}"},
                 {"code": "x", "checks": []})
        self.assertEqual(s, 503)

    def test_anon_sandboxed_python_verifier_not_gated(self):
        # The AST+builtins-sandboxed python verifier stays available on the anon surface.
        s = _raw(self.port, "POST", "/api/verify-python",
                 {"Host": f"127.0.0.1:{self.port}"},
                 {"session": "sess_does_not_exist", "code": "print(1)"})
        self.assertNotEqual(s, 503)  # 404 unknown session, never the sandbox gate

    def test_token_server_exec_not_gated(self):
        s = _raw(self.tport, "POST", "/api/run",
                 {"Authorization": f"Bearer {_GATE_TOKEN}"},
                 {"lang": "python", "code": "print(1)", "expect_stdout": "1"})
        self.assertEqual(s, 200)


if __name__ == "__main__":
    unittest.main()

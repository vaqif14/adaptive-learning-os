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
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True,
                                           workspace=Path(cls.tmp.name), prefer_backend="local"))
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


if __name__ == "__main__":
    unittest.main()

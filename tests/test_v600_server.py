import json, sys, threading, unittest, urllib.request, urllib.error
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; SKILL = ROOT/"skills"/"adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.server import ServerConfig, make_server, MAX_BODY

TOKEN = "test-token-1234567890abcdef"

def _req(port, path, method="GET", body=None, token=TOKEN):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if token: req.add_header("Authorization", f"Bearer {token}")
    data=None
    if body is not None:
        data=json.dumps(body).encode(); req.add_header("Content-Type","application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, token=TOKEN))
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True); cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health_open(self):
        code, d = _req(self.port, "/health", token=None)
        self.assertEqual(code, 200); self.assertTrue(d["ok"])

    def test_api_requires_token(self):
        code, _ = _req(self.port, "/api/languages", token=None)
        self.assertEqual(code, 401)
        code, _ = _req(self.port, "/api/languages", token="wrong")
        self.assertEqual(code, 401)

    def test_languages_with_token(self):
        code, d = _req(self.port, "/api/languages")
        self.assertEqual(code, 200)
        self.assertIn("python", [l["id"] for l in d["languages"]])

    def test_run_python_real(self):
        code, d = _req(self.port, "/api/run", "POST", {"lang":"python","code":"print(2+3)","expect_stdout":"5"})
        self.assertEqual(code, 200); self.assertTrue(d["passed"], msg=d)

    def test_run_rejects_unknown_language_cleanly(self):
        code, d = _req(self.port, "/api/run", "POST", {"lang":"cobol","code":"x"})
        self.assertEqual(code, 400)

    def test_oversized_body_413(self):
        code, _ = _req(self.port, "/api/run", "POST", {"lang":"python","code":"#"+"x"*MAX_BODY})
        self.assertEqual(code, 413)

    def test_no_token_config_fails_closed(self):
        with self.assertRaises(ValueError):
            ServerConfig(host="127.0.0.1", port=0, token=None, allow_anon=False)
        with self.assertRaises(ValueError):
            ServerConfig(host="0.0.0.0", port=0, token=None, allow_anon=True)  # non-loopback needs token




class ResourceControlTests(unittest.TestCase):
    def test_missing_content_length_411(self):
        import http.client
        srv = make_server(ServerConfig(host="127.0.0.1", port=0, token=TOKEN))
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.putrequest("POST", "/api/run", skip_accept_encoding=True)
            c.putheader("Authorization", f"Bearer {TOKEN}")
            c.endheaders()  # no Content-Length, no body
            r = c.getresponse()
            self.assertEqual(r.status, 411)
        finally:
            srv.shutdown()

    def test_concurrency_cap_429(self):
        srv = make_server(ServerConfig(host="127.0.0.1", port=0, token=TOKEN, max_concurrent_runs=1))
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            results = []
            def slow():
                results.append(_req(port, "/api/run", "POST",
                                    {"lang": "python", "code": "import time\ntime.sleep(1.5)\nprint(1)"}))
            threads = [threading.Thread(target=slow) for _ in range(2)]
            for th in threads: th.start()
            for th in threads: th.join(timeout=30)
            codes = sorted(r[0] for r in results)
            self.assertIn(429, codes)          # one refused
            self.assertIn(200, codes)          # one served
        finally:
            srv.shutdown()

    def test_handler_has_socket_timeout(self):
        from runtime.server import _handler
        H = _handler(ServerConfig(host="127.0.0.1", port=0, token=TOKEN))
        self.assertEqual(H.timeout, 30)        # slowloris guard


if __name__ == "__main__":
    unittest.main()

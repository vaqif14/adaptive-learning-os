"""HTTP API for the pedagogical OS: session/route/verify/observability/inspect."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.server import ServerConfig, make_server  # noqa: E402

TOKEN = "test-token-os-1234567890"


def _req(port, path, method="GET", body=None, token=TOKEN):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


class OsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, token=TOKEN,
                                           workspace=Path(cls.tmp.name), prefer_backend="local"))
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def _start(self):
        code, d = _req(self.port, "/api/session/start", "POST",
                       {"topic": "python basics", "goal": "learn loops", "goal_mode": "learn", "mode": "practice"})
        self.assertEqual(code, 200, msg=d)
        return d["session_id"]

    def test_session_start(self):
        sid = self._start()
        self.assertTrue(sid.startswith("sess_"))

    def test_session_start_needs_topic_and_goal(self):
        code, _ = _req(self.port, "/api/session/start", "POST", {"topic": "x"})
        self.assertEqual(code, 400)

    def test_route_records_decision_and_trace(self):
        sid = self._start()
        code, dec = _req(self.port, "/api/route", "POST", {"session": sid, "message": "test me on loops"})
        self.assertEqual(code, 200, msg=dec)
        self.assertIn("route", dec)
        code, obs = _req(self.port, f"/api/observability?session={sid}")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(obs["traces"], 1)

    def test_verify_python_correct_is_strong(self):
        sid = self._start()
        code, d = _req(self.port, "/api/verify-python", "POST", {
            "session": sid, "code": "def add(a, b):\n    return a + b",
            "trusted_test_code": "assert add(2, 3) == 5",
            "capability": "addition", "independence": "unassisted",
            "evidence_format": "executable_code",
        })
        self.assertEqual(code, 200, msg=d)
        self.assertTrue(d.get("available"))
        vr = d["result"]
        self.assertTrue(vr["executed"] and vr["passed"] and vr["correctness_checked"])

    def test_verify_python_needs_session_and_code(self):
        code, _ = _req(self.port, "/api/verify-python", "POST", {"code": "x=1"})
        self.assertEqual(code, 400)

    def test_verify_python_preserves_support_on_recheck(self):
        sid = self._start()
        body = {"session": sid, "code": "print(42)", "expected_stdout": "42",
                "capability": "answer", "independence": "unassisted",
                "scope": "independent_reproduction", "attempt_id": "guided-task",
                "support_provenance": {"hints_count": 1}}
        status, result = _req(self.port, "/api/verify-python", "POST", body)
        self.assertEqual(status, 200, result)
        body.pop("support_provenance")
        status, result = _req(self.port, "/api/verify-python", "POST", body)
        self.assertEqual(status, 200, result)
        _, state = _req(self.port, f"/api/inspect?session={sid}")
        cap = state["learner_evidence"]["demonstrated_capabilities"]["answer"]
        self.assertEqual(cap["strong_unassisted_successes"], 0)

    def test_inspect_reports_integrity(self):
        sid = self._start()
        code, d = _req(self.port, f"/api/inspect?session={sid}")
        self.assertEqual(code, 200, msg=d)
        self.assertTrue(d["ledger_integrity"]["chain_ok"])

    def test_unknown_session_is_404(self):
        code, _ = _req(self.port, "/api/inspect?session=sess_deadbeefdeadbeef")
        self.assertEqual(code, 404)

    def test_api_still_requires_token(self):
        code, _ = _req(self.port, "/api/observability?session=x", token=None)
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()

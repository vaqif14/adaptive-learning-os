"""Local course bridge over real HTTP: preconditions, exam lock, resume, CSRF guard, static scope."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from course_fixture import IsolatedState, roadmap, lesson, items, manifest_for, EXAM_CORRECT  # noqa: E402


def call(port, path, body=None, *, origin=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST" if body is not None else "GET")
    if origin:
        req.add_header("Origin", origin)
    if host:
        req.add_header("Host", host)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type", "")
            return r.status, (json.loads(raw) if "json" in ctype else raw.decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw or b"{}"), dict(e.headers)


class CourseServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iso = IsolatedState().__enter__()
        cls.tmp = tempfile.TemporaryDirectory()
        ws = Path(cls.tmp.name)
        sk = SessionKernel(ws)
        cls.sid = sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığı", stated_background="Python"))["session_id"]
        k = CourseKernel(ws)
        d = sk.dir(cls.sid)
        k.init(cls.sid)
        k.record_sources(cls.sid, manifest_for(d / "sources"), [{"key": "r1", "kind": "url",
                                                                 "locator": "https://example.org/vectors"}])
        cls.version = k.propose_roadmap(cls.sid, roadmap())["roadmap_version"]
        cls.ui = ws / "ui"
        cls.ui.mkdir()
        (cls.ui / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
        cls.k, cls.d, cls.ws = k, d, ws
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=ws,
                                           ui_dir=cls.ui, course_session=cls.sid))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()
        cls.iso.__exit__()

    def post(self, path, body, **kw):
        return call(self.port, path, {"ui_schema": 2, **body}, **kw)

    def rev(self):
        return call(self.port, "/api/course/state")[1]["revision"]

    def test_full_flow_through_the_bridge(self):
        code, st, _ = call(self.port, "/api/course/state")
        self.assertEqual((code, st["phase"]), (200, "roadmap_awaiting_approval"))
        # preconditions
        self.assertEqual(call(self.port, "/api/course/approve", {"roadmap_version": self.version})[0], 409)   # no ui_schema
        self.assertEqual(self.post("/api/course/approve", {"roadmap_version": self.version})[0], 428)        # no revision
        code, body, _ = self.post("/api/course/approve", {"roadmap_version": "rm-0000000000000000",
                                                          "expected_revision": self.rev()})
        self.assertEqual((code, body["error"]), (409, "roadmap_version_mismatch"))
        code, body, _ = self.post("/api/course/approve", {"roadmap_version": self.version, "expected_revision": self.rev(),
                                                          "commitments": {"weekly_active_minutes": 60}})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["approval_event"]["channel"], "ui")
        self.k.install_items(self.sid, "vec-sim", items())
        self.k.install_lesson(self.sid, "vec-sim", lesson())
        code, body, _ = self.post("/api/course/mission/start", {"node": "vec-sim", "expected_revision": self.rev()})
        self.assertEqual(code, 200, body)
        code, lv, _ = call(self.port, "/api/course/lesson?node=vec-sim")
        self.assertEqual(code, 200)
        self.assertNotIn("reference_solution", json.dumps(lv))
        code, ex, _ = self.post("/api/course/exam/start", {"node": "vec-sim", "expected_revision": self.rev(),
                                                           "idempotency_key": "exam-start-0001"})
        self.assertEqual(code, 200, ex)
        att = ex["attempt"]["attempt_id"]
        self.assertNotIn("cosine([3,4],[6,8])", json.dumps(ex))
        # exam lock: lesson route, help, direct navigation all refused; restart/tab resumes same attempt
        self.assertEqual(call(self.port, "/api/course/lesson?node=vec-sim")[0], 423)
        code, body, _ = self.post("/api/course/help", {"node": "vec-sim", "kind": "explain", "expected_revision": self.rev()})
        self.assertEqual((code, body["error"]), (423, "exam_active"))
        code, again, _ = self.post("/api/course/exam/start", {"node": "vec-sim", "expected_revision": self.rev()})
        self.assertEqual((code, again["resumed"], again["attempt"]["attempt_id"]), (200, True, att))
        code, body, _ = self.post("/api/course/exam/submit", {"attempt_id": att, "expected_revision": self.rev() - 1})
        self.assertEqual((code, body["error"]), (409, "revision_conflict"))
        self.assertEqual(body["state"]["active_attempt"]["attempt_id"], att)
        Path(ex["attempt"]["file"]).write_text(EXAM_CORRECT, encoding="utf-8")
        code, done, _ = self.post("/api/course/exam/submit", {"attempt_id": att, "expected_revision": self.rev(),
                                                              "idempotency_key": "exam-submit-0001"})
        self.assertEqual((code, done["status"]), (200, "finalized"), done)
        code, _, _ = self.post("/api/course/exam/submit", {"attempt_id": att, "expected_revision": self.rev(),
                                                           "idempotency_key": "exam-submit-0001"})
        self.assertEqual(code, 200)
        st = call(self.port, "/api/course/state")[1]
        self.assertEqual(st["phase"], "feedback")
        self.assertEqual(len([e for e in st["evidence_refs"] if e["attempt_id"] == att]), 1)
        # activity heartbeat (no revision needed) is idempotent
        now = datetime.now(UTC)
        iv = {"interval_id": "iv_0000000000000abc", "kind": "exam", "attempt_id": att, "node_id": "vec-sim",
              "started_at": (now - timedelta(minutes=3)).isoformat(), "last_at": now.isoformat(),
              "active_seconds": 120, "idle_seconds": 20, "paused_seconds": 0, "seq": 1}
        self.assertEqual(self.post("/api/course/activity", {"interval": iv})[1]["status"], "recorded")
        self.assertEqual(self.post("/api/course/activity", {"interval": iv})[1]["status"], "duplicate_ignored")
        # a second server process (restart) sees the same finalized result
        srv2 = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=self.ws,
                                        ui_dir=self.ui, course_session=self.sid))
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            st2 = call(srv2.server_address[1], "/api/course/state")[1]
            self.assertEqual(st2["assessment_result"]["attempt_id"], att)
            self.assertEqual(st2["revision"], st["revision"])
        finally:
            srv2.shutdown()

    def test_cross_origin_rebinding_and_static_scope(self):
        code, _, _ = self.post("/api/course/resume", {}, origin="https://evil.example")
        self.assertEqual(code, 401)
        code, _, _ = call(self.port, "/api/course/state", host="evil.example:8787")
        self.assertEqual(code, 401)
        code, _page, headers = call(self.port, "/")
        self.assertEqual(code, 200)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        for probe in ("/../assessment-vault/vec-sim/cosine-a.json", "/%2e%2e/course-state.json",
                      "/..%2fledger.jsonl"):
            self.assertIn(call(self.port, probe)[0], {401, 404})
        code, html, _ = call(self.port, "/roadmap.html")
        self.assertEqual(code, 200)
        self.assertIn("roadmap-data", html)


if __name__ == "__main__":
    unittest.main()

"""Regressions for the adversarial review of the course runtime (one test per finding)."""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.safety import UnsafePathError, safe_input_path  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from course_fixture import lesson, manifest_for, roadmap  # noqa: E402
from test_v700_course_flow import CourseCase  # noqa: E402
from test_v700_course_server import call  # noqa: E402


class RoadmapDriftTests(CourseCase):
    def test_legacy_set_roadmap_is_refused_on_a_course(self):
        self.approve()
        changed = roadmap()
        changed["nodes"][0]["task"] = "Başqa tapşırıq"
        with self.assertRaises(ValueError) as ctx:
            self.sk.set_roadmap(self.sid, changed)
        self.assertIn("course propose-roadmap", str(ctx.exception))
        self.assertTrue(self.k.state(self.sid)["roadmap"]["approved"])

    def test_install_without_course_commit_reopens_approval(self):
        # Simulates a crash between the workspace install and the course-state commit.
        self.approve()
        changed = roadmap()
        changed["nodes"][0]["task"] = "Başqa tapşırıq"
        self.sk.set_roadmap(self.sid, changed, _course_internal=True)
        st = self.k.state(self.sid)
        self.assertEqual(st["phase"], "roadmap_awaiting_approval")
        self.assertFalse(st["roadmap"]["approved"])
        self.assertEqual(st["roadmap"]["review_required"]["reason"], "roadmap_changed_without_review")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "roadmap_not_approved")
        self.assertEqual(self.sk.next_action(self.sid)["action"], "await_roadmap_approval")


class ExamSafetyTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self.exam = self.k.start_exam(self.sid, "vec-sim")["attempt"]

    def test_abandon_writes_nothing_when_its_mission_cannot_be_derived(self):
        with patch.object(CourseKernel, "_mission_retry", side_effect=OSError("boom")), self.assertRaises(OSError):
            self.k.abandon_exam(self.sid, self.exam["attempt_id"])
        obs = [r for r in self.sk.ledger(self.sid).verified_records() if r["payload"].get("note") == "exam_abandoned"]
        self.assertEqual(obs, [])
        self.assertEqual(self.k.state(self.sid)["attempt_status"], "active")

    def test_state_view_during_exam_contains_no_teaching_material(self):
        st = self.k.state(self.sid)
        self.assertTrue(st["teaching_locked"])
        self.assertEqual({n["lesson"]["status"] for n in st["roadmap"]["nodes"]}, {"locked_during_exam"})
        self.assertIsNone(st["assessment_result"])
        self.assertIsNone(st["next_mission"])
        self.assertTrue(all(n["progress"] is None for n in st["roadmap"]["nodes"]))
        self.assertNotIn("Sıfır vektorda xəta yox", json.dumps(st, ensure_ascii=False))

    def test_cli_side_channels_are_locked_during_exam(self):
        import alearn
        common = ["--workspace", str(self.ws)]
        rc = alearn.main(common + ["evidence", "--session", self.sid, "--capability", "vec-sim", "--outcome", "correct",
                                   "--independence", "unassisted", "--strength", "medium"])
        self.assertEqual(rc, 1)
        rc = alearn.main(common + ["course", "sources-ask", "--session", self.sid, "--question", "cosine nədir?"])
        self.assertEqual(rc, 1)
        rc = alearn.main(common + ["scaffold-cards", "--session", self.sid, "--node", "vec-sim"])
        self.assertEqual(rc, 1)
        ev = [r for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == "evidence"
              and r["provenance"].get("source") == "cli_evidence_entry"]
        self.assertEqual(ev, [])

    def test_crafted_exception_names_do_not_leak_hidden_inputs(self):
        sneaky = "def cosine(a, b):\n    raise type('ARGS_' + '_'.join(str(x) for x in a + b), (Exception,), {})()\n"
        Path(self.exam["file"]).write_text(sneaky, encoding="utf-8")
        out = self.k.submit_and_finalize(self.sid, self.exam["attempt_id"])
        self.assertNotIn("ARGS_", json.dumps(out, ensure_ascii=False))
        self.assertEqual(out["assessment_result"]["outcome"], "incorrect")

    def test_replayed_exam_start_still_names_the_attempt(self):
        self.k.abandon_exam(self.sid, self.exam["attempt_id"])
        a = self.k.start_exam(self.sid, "vec-sim", idempotency_key="exam-start-replay-1")
        b = self.k.start_exam(self.sid, "vec-sim", idempotency_key="exam-start-replay-1")
        self.assertTrue(b["idempotent_replay"])
        self.assertEqual(b["result"]["attempt_id"], a["attempt_id"])


class StoreProtectionTests(CourseCase):
    def test_cli_paths_cannot_reach_ledger_or_vault(self):
        self.approve()
        self.author()
        for target in (self.d / "ledger.jsonl", self.d / "assessment-vault/vec-sim/cosine-a.json",
                       self.d / "course-state.json", self.d / "course/lessons/vec-sim.json"):
            with self.assertRaises(UnsafePathError):
                safe_input_path(self.ws, str(target), must_exist=False)
        ok = safe_input_path(self.ws, str(self.d / "workspace/modules/vec-sim/submission/practice.py"))
        self.assertTrue(ok.is_file())

    def test_export_never_overwrites(self):
        import alearn
        self.approve()
        out = self.ws / "export.json"
        out.write_text("{}", encoding="utf-8")
        rc = alearn.main(["--workspace", str(self.ws), "course", "export", "--session", self.sid, "--output", str(out)])
        self.assertEqual(rc, 1)
        self.assertEqual(out.read_text(encoding="utf-8"), "{}")
        rc = alearn.main(["--workspace", str(self.ws), "course", "export", "--session", self.sid,
                          "--output", str(self.d / "ledger.jsonl")])
        self.assertEqual(rc, 1)
        self.assertTrue(self.sk.ledger(self.sid).integrity()["chain_ok"])

    def test_refused_manifest_commit_leaves_grounding_cache_untouched(self):
        self.approve()
        before = (self.d / "sources/src-fixture-001.txt").read_text(encoding="utf-8")
        staging = self.d / "sources-staging" / "stg_test"
        fresh = manifest_for(staging)
        (staging / "src-fixture-001.txt").write_text(before + " changed", encoding="utf-8")
        stale = self.k.state(self.sid)["revision"] - 1
        with self.assertRaises(CourseStateError):
            self.k.record_sources(self.sid, fresh, [], staged_cache=staging, expected_revision=stale)
        self.assertEqual((self.d / "sources/src-fixture-001.txt").read_text(encoding="utf-8"), before)
        self.k.install_lesson(self.sid, "vec-sim", lesson())   # grounding still verifies

    def test_uncommitted_lesson_file_is_not_served(self):
        self.approve()
        self.author()
        committed = read_json(self.d / "course-state.json")["content_versions"]["vec-sim"]
        stray = read_json(self.d / "course/lessons/vec-sim.json")
        stray["content_version"] = "lc-0000000000000000"   # file left by an install that never committed
        (self.d / "course/lessons/vec-sim.json").write_text(json.dumps(stray), encoding="utf-8")
        self.assertNotEqual(committed, stray["content_version"])
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "lesson_missing")


class BridgeHardeningTests(CourseCase):
    def test_other_local_ports_and_non_json_bodies_are_refused(self):
        self.approve()
        srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=self.ws,
                                       course_session=self.sid))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertEqual(call(port, "/api/course/resume", {"ui_schema": 1}, origin="http://localhost:3000")[0], 401)
            self.assertEqual(call(port, "/api/course/resume", {"ui_schema": 1}, origin=f"http://127.0.0.1:{port}")[0], 200)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/course/resume", method="POST",
                                         data=b'{"ui_schema": 1}', headers={"Content-Type": "text/plain"})
            try:
                urllib.request.urlopen(req, timeout=10)
                status = 200
            except urllib.error.HTTPError as e:
                status = e.code
            self.assertEqual(status, 415)
            rev = self.k.state(self.sid)["revision"]
            code, _, _ = call(port, "/api/course/commitments", {"ui_schema": 1, "expected_revision": rev,
                                                                "commitments": "not-an-object"})
            self.assertIn(code, {400, 409})
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()

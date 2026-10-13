"""Due reviews must be enforced by the runtime, including direct routes/restart.

Synthetic fixtures and simulated scheduling time test workflow, not retention.
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.course import CourseKernel
from runtime.course_state import CourseStateError
from course_fixture import NEAREST_REF
from test_v700_course_flow import CourseCase
from test_v710_audit_regressions import Clock, pass_exam


class ReviewEnforcementTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock(datetime.now(UTC) - timedelta(days=3))
        self.k = CourseKernel(self.ws, clock=self.clock)
        self.approve()
        self.author()
        pass_exam(self)

    def refuse(self, action):
        with self.assertRaises(CourseStateError) as ctx:
            action()
        self.assertEqual(ctx.exception.code, "review_required")

    def test_direct_mission_and_ordinary_exam_cannot_bypass_due_review(self):
        self.clock.advance(hours=25)
        for action in (lambda: self.k.start_mission(self.sid, "vec-sim"),
                       lambda: self.k.start_exam(self.sid, "vec-sim", fast_track=True)):
            self.refuse(action)
        self.assertEqual(self.k.state(self.sid)["active_attempt"], None)

    def test_review_becoming_due_in_open_lesson_blocks_help_and_practice(self):
        self.k.start_mission(self.sid, "vec-sim")
        self.clock.advance(hours=25)
        for action in (lambda: self.k.help(self.sid, "vec-sim", "explain"),
                       lambda: self.k.begin_practice(self.sid, "vec-sim"),
                       lambda: self.k.lesson_view(self.sid, "vec-sim"),
                       lambda: self.k.self_explanation(self.sid, "vec-sim", "Mən öz sözlərimlə izah edirəm.")):
            self.refuse(action)

    def test_due_review_survives_restart_and_hides_teaching_actions(self):
        self.clock.advance(hours=25)
        self.k = CourseKernel(self.ws, clock=self.clock)
        state = self.k.state(self.sid)
        self.assertEqual(state["next_mission"]["type"], "due_review")
        for action in ("start_mission", "help", "begin_practice", "start_exam"):
            self.assertNotIn(action, state["permitted_actions"])
        self.assertTrue(all(n["lesson"]["status"] == "review_required" for n in state["roadmap"]["nodes"]))
        self.refuse(lambda: self.k.start_mission(self.sid, "vec-sim"))
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        resumed = CourseKernel(self.ws, clock=self.clock).start_exam(self.sid, "vec-sim")
        self.assertEqual(resumed["attempt_id"], att["attempt_id"])
        self.assertTrue(resumed["resumed"])

    def test_failed_review_allows_remediation_until_next_retry_is_due(self):
        self.clock.advance(hours=25)
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        Path(att["file"]).write_text("def most_similar(query, docs): return -1\n", encoding="utf-8")
        out = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.assertNotEqual(out["assessment_result"]["outcome"], "correct")
        self.k.advance(self.sid)
        self.k.start_mission(self.sid, "vec-sim")
        self.assertIn("content", self.k.help(self.sid, "vec-sim", "alternative"))
        self.clock.advance(hours=13)
        self.refuse(lambda: self.k.help(self.sid, "vec-sim", "explain"))

    def test_successful_review_unlocks_teaching_and_preserves_freshness(self):
        self.clock.advance(hours=25)
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        self.assertEqual(att["item"]["item_id"], "nearest-b")
        Path(att["file"]).write_text(NEAREST_REF, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.k.advance(self.sid)
        self.assertIn("start_mission", self.k.state(self.sid)["permitted_actions"])
        self.k.start_mission(self.sid, "vec-sim")
        self.assertIn("content", self.k.help(self.sid, "vec-sim", "explain"))

    def spacing(self, event=None, node="vec-sim", hours=25):
        # Simulated journal timestamps: these are validation cases, not live retention evidence.
        base = datetime(2026, 10, 1, tzinfo=UTC)
        records = [{"seq": 0, "record_type": "evidence", "timestamp": base.isoformat(),
                    "record_id": "source", "payload": {"evidence_id": "ev_exam_first"}}]
        if event:
            records.append({"seq": 1, "record_type": "event", "record_id": "reexposure",
                            "timestamp": (base + timedelta(hours=24.75)).isoformat(),
                            "payload": {"event_type": event, "node_id": node}})
        records.append({"seq": 2, "record_type": "event", "record_id": "start",
                        "timestamp": (base + timedelta(hours=hours)).isoformat(),
                        "payload": {"event_type": "course_review_started", "attempt_id": "review"}})
        state = {"review_queue": [{"review_id": "r1", "source_attempt_id": "first", "interval_days": 1}]}
        return self.k._review_evidence(SimpleNamespace(verified_records=lambda: records), state,
                                       {"review_id": "r1", "attempt_id": "review", "node_id": "vec-sim"})

    def test_help_or_retrieval_before_review_resets_observed_spacing(self):
        for event in ("course_help_delivered", "course_practice_started", "course_practice_checked",
                      "course_self_explanation", "course_exam_started"):
            with self.subTest(event=event):
                spacing = self.spacing(event)
                self.assertFalse(spacing["counts_as_delayed"])
                self.assertEqual(spacing["observed_spacing_hours"], 0.25)

    def test_unrelated_teaching_does_not_reset_the_review_gap(self):
        spacing = self.spacing("course_help_delivered", node="other-node")
        self.assertTrue(spacing["counts_as_delayed"])
        self.assertEqual(spacing["observed_spacing_hours"], 25)

    def test_display_rounding_cannot_meet_a_retention_threshold(self):
        self.assertFalse(self.spacing(hours=19.9999)["counts_as_delayed"])

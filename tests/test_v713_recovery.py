"""Recovery regressions on isolated persisted fixture sessions, not learning efficacy."""
import sys
import unittest
import copy
from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.course import CourseKernel
from runtime.course_recovery import guidance, repeated_plan, validate_plan
from runtime.course_state import CourseStateError
from test_v700_course_flow import CourseCase
import test_v712_journey as journey_tests
import test_v710_review_fixes as review_tests
from course_fixture import items


PLAN = {"blocker": "task_interpretation", "learner_agreed": True,
        "plan": "Read the task in your own words, then predict one small example before coding."}


class RecoveryPolicyTests(unittest.TestCase):
    def test_malformed_payloads_return_controlled_error(self):
        for payload in (None, [], {**PLAN, "blocker": []}, {**PLAN, "blocker": {}},
                        {**PLAN, "plan": []}, {**PLAN, "plan": "!" * 30},
                        {**PLAN, "learner_agreed": 1}, {**PLAN, "prerequisite_node_id": []}):
            with self.subTest(payload=payload), self.assertRaises(CourseStateError):
                validate_plan(payload)

    def test_cosmetic_edits_are_not_a_new_plan(self):
        history = [{"capability_version": "cap-a", "plan": PLAN["plan"]}]
        cosmetic = "  READ the task in your own words!\nthen   predict one small example before coding...  "
        self.assertTrue(repeated_plan(history, cosmetic, "cap-a"))
        self.assertFalse(repeated_plan(history, cosmetic, "cap-b"))
        self.assertFalse(repeated_plan(history, "Check a prerequisite using a diagram before reinterpreting the task.", "cap-a"))

    def test_each_blocker_has_provisional_next_step_and_failed_plan_escalates(self):
        for blocker in ("prerequisite", "task_interpretation", "workload", "accessibility", "tooling", "other"):
            with self.subTest(blocker=blocker):
                first, again = guidance(blocker), guidance(blocker, 1)
                self.assertTrue(first["completion_evidence_required"])
                self.assertFalse(first["calibrated"])
                self.assertFalse(first["review_recommended"])
                self.assertTrue(again["review_recommended"])
                self.assertTrue(again["review_reason_az"])


class PersistedRecoveryTests(CourseCase):
    fail = journey_tests.RecoveryJourneyTests.fail

    def setUp(self):
        super().setUp()
        self.approve()
        self.author()

    def open_recovery(self, start=1):
        for index in range(start, start + 3):
            self.fail(index)

    def recovery_events(self):
        return [r for r in self.sk.ledger(self.sid).verified_records()
                if r["payload"].get("event_type") == "course_recovery_agreed"]

    def progress(self):
        return next(n["progress"] for n in self.k.state(self.sid)["roadmap"]["nodes"] if n["id"] == "vec-sim")

    def test_malformed_blocker_at_kernel_boundary_is_controlled(self):
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_recovery(self.sid, "vec-sim", {**PLAN, "blocker": []})
        self.assertEqual(ctx.exception.code, "recovery_required")

    def test_agreement_survives_restart_and_does_not_award_progress(self):
        self.open_recovery()
        self.k.record_recovery(self.sid, "vec-sim", PLAN, idempotency_key="recovery-agreement-1")
        self.k = CourseKernel(self.ws)
        progress = self.progress()
        self.assertFalse(progress["passed"])
        self.assertEqual(self.strong(), 0)
        self.assertEqual(progress["recovery"]["status"], "agreed")
        self.assertTrue(progress["recovery"]["guidance"]["completion_evidence_required"])
        replay = self.k.record_recovery(self.sid, "vec-sim", PLAN, idempotency_key="recovery-agreement-1")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(self.recovery_events()), 1)
        self.k.start_mission(self.sid, "vec-sim")

    def test_crash_after_recovery_commit_replays_once(self):
        self.open_recovery()
        with patch("runtime.local_state.write_json", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                self.k.record_recovery(self.sid, "vec-sim", PLAN, idempotency_key="recovery-crash-1")
        self.k = CourseKernel(self.ws)
        progress = self.progress()
        self.assertEqual(progress["recovery"]["status"], "agreed")
        self.assertEqual(len(progress["recovery_history"]), 1)
        replay = self.k.record_recovery(self.sid, "vec-sim", PLAN, idempotency_key="recovery-crash-1")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(self.recovery_events()), 1)

    def test_concurrent_recovery_replay_commits_only_once(self):
        self.open_recovery()
        def agree(_):
            return CourseKernel(self.ws).record_recovery(self.sid, "vec-sim", PLAN,
                                                        idempotency_key="recovery-concurrent-1")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(agree, range(2)))
        self.assertEqual(sum(bool(r.get("idempotent_replay")) for r in results), 1)
        self.assertEqual(len(self.progress()["recovery_history"]), 1)
        self.assertEqual(len(self.recovery_events()), 1)

    def test_reused_recovery_key_with_different_plan_is_rejected(self):
        self.open_recovery()
        self.k.record_recovery(self.sid, "vec-sim", PLAN, idempotency_key="recovery-different-1")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_recovery(self.sid, "vec-sim", {**PLAN, "plan": "Try a smaller worked example, then independently predict a new result."},
                                   idempotency_key="recovery-different-1")
        self.assertEqual(ctx.exception.code, "phase_forbids_action")
        self.assertEqual(len(self.recovery_events()), 1)

    def test_failed_changed_plan_needs_another_actual_change(self):
        self.open_recovery()
        self.k.record_recovery(self.sid, "vec-sim", PLAN)
        self.open_recovery(start=4)
        cosmetic = {**PLAN, "plan": "READ the task in your own words!\nthen   predict one small example before coding..."}
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_recovery(self.sid, "vec-sim", cosmetic)
        self.assertEqual(ctx.exception.code, "recovery_required")
        changed = {**PLAN, "blocker": "prerequisite",
                   "plan": "Check vector length using a small visual example, then solve a separate dot product task."}
        out = self.k.record_recovery(self.sid, "vec-sim", changed)
        self.assertTrue(out["recovery"]["guidance"]["review_recommended"])
        self.assertEqual(len(self.progress()["recovery_history"]), 2)
        self.assertEqual(self.strong(), 0)

    def test_non_prerequisite_recovery_target_is_rejected(self):
        self.open_recovery()
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_recovery(self.sid, "vec-sim", {**PLAN, "prerequisite_node_id": "vec-search"})
        self.assertEqual(ctx.exception.code, "phase_forbids_action")

    def test_abandonment_is_not_a_third_failure(self):
        self.fail(1)
        self.fail(2)
        attempt = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        out = self.k.abandon_exam(self.sid, attempt["attempt_id"])
        self.assertEqual(out["next_mission"]["type"], "retry_later")
        self.assertFalse(out["counted_as_success"])
        self.assertNotIn("recovery", self.progress())

    def test_due_review_cannot_bypass_repeated_failure_recovery(self):
        clock = review_tests.Clock(datetime.now(UTC))
        self.k = CourseKernel(self.ws, clock=clock)
        review_tests.pass_exam(self)
        for index in range(1, 4):
            item = copy.deepcopy(items()["items"][0])
            item["item_id"] = f"review-failure-{index}"
            item["prompt_md"] = f"Yeni review fixture {index}: cosine(a,b) funksiyasını yaz."
            for i, check in enumerate(item["checks"]):
                check["code"] = f"assert cosine([{index + i + 11},0], [1,0]) == 1.0"
            installed = self.k.install_items(self.sid, "vec-sim", {"node_id": "vec-sim", "items": [item]})
            if installed["freshness"][item["item_id"]]["status"] == "needs_review":
                self.k.review_freshness(self.sid, "vec-sim", item["item_id"], "distinct",
                                        "Bu sintetik review fixture-da qiymətlər və kontekst fərqlidir.")
            clock.advance(days=10)
            att = self.k.start_review(self.sid, "vec-sim")["attempt"]
            Path(att["file"]).write_text("def cosine(a,b): return -999\n")
            self.k.submit_and_finalize(self.sid, att["attempt_id"])
            self.k.advance(self.sid)
        self.assertEqual(self.progress()["recovery"]["status"], "open")
        clock.advance(days=10)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_review(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "recovery_required")


if __name__ == "__main__":
    unittest.main()

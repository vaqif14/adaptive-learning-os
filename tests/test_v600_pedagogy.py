import sys, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.mastery import estimate, BKTParams, bkt_step
from runtime.scheduling import schedule, half_life_days, due_queue
from runtime.policy import TeachingContext, choose_move


def _obs(correct, day, scope="independent_reproduction"):
    return {"correct": correct, "timestamp": (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=day)).isoformat(), "scope": scope}


class BKTTests(unittest.TestCase):
    def test_correct_raises_p(self):
        p0 = 0.3
        self.assertGreater(bkt_step(p0, True, BKTParams()), p0)

    def test_incorrect_lowers_relative_to_correct(self):
        self.assertLess(bkt_step(0.5, False, BKTParams()), bkt_step(0.5, True, BKTParams()))

    def test_mastery_requires_breadth_and_transfer(self):
        # many correct but all same non-transfer scope -> not mastered (no transfer)
        obs = [_obs(True, d, "independent_reproduction") for d in range(6)]
        est = estimate("cap", obs)
        self.assertFalse(est.mastered)
        self.assertIn("needs_transfer_or_delayed_demonstration", est.reasons)

    def test_mastery_met_with_transfer_and_breadth(self):
        obs = [_obs(True, 0, "independent_reproduction"), _obs(True, 2, "near_transfer"),
               _obs(True, 5, "near_transfer"), _obs(True, 9, "delayed_independent_performance")]
        est = estimate("cap", obs, now=datetime(2026, 1, 10, tzinfo=timezone.utc))
        self.assertTrue(est.mastered, msg=est.reasons)

    def test_forgetting_decays_over_long_gap(self):
        recent = estimate("c", [_obs(True,0,"near_transfer"), _obs(True,1,"independent_reproduction")]).p_known
        stale = estimate("c", [_obs(True,0,"near_transfer"), _obs(True,400,"independent_reproduction")]).p_known
        self.assertLess(stale, recent + 1e-9)


class SchedulingTests(unittest.TestCase):
    def test_interval_grows_with_recalls(self):
        self.assertGreater(half_life_days(4, 0), half_life_days(1, 0))

    def test_lapse_shrinks_interval(self):
        self.assertLess(half_life_days(2, 2), half_life_days(2, 0))

    def test_due_when_overdue(self):
        now = datetime(2026,6,1,tzinfo=timezone.utc)
        st = schedule("cap", [_obs(True, 0)], now=now)  # last review Jan 1, interval small -> overdue by June
        self.assertTrue(st.due)

    def test_queue_sorted_soonest_first(self):
        now = datetime(2026,1,10,tzinfo=timezone.utc)
        q = due_queue({"a": [_obs(True,0)], "b": [_obs(True,0),_obs(True,1),_obs(True,2)]}, now=now)
        self.assertLessEqual(q[0]["next_review_at"], q[1]["next_review_at"])


class FeedbackMoveTests(unittest.TestCase):
    def test_feedback_move_on_graded_repeated_error(self):
        d = choose_move(TeachingContext(mode="learn", route="teach",
                                        repeated_observable_error=True, graded_attempt_available=True))
        self.assertEqual(d.move, "feedback")

    def test_wheel_spin_change_approach(self):
        d = choose_move(TeachingContext(mode="learn", route="teach",
                                        attempts_without_progress=3, wheel_spin_threshold=3))
        self.assertEqual(d.move, "change_approach")

    def test_no_feedback_without_graded_attempt(self):
        d = choose_move(TeachingContext(mode="learn", route="teach", repeated_observable_error=True))
        self.assertNotEqual(d.move, "feedback")


if __name__ == "__main__":
    unittest.main()

import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.intake import next_question, intake_ready_to_teach, intake_state, INTAKE_QUESTIONS


class IntakeTests(unittest.TestCase):
    def test_first_question_is_goal(self):
        self.assertEqual(next_question({}).field, "goal")

    def test_one_at_a_time_order_goal_then_level(self):
        c = {"goal": "build a fastapi service"}
        self.assertEqual(next_question(c).field, "stated_background")

    def test_not_ready_until_goal_and_level(self):
        self.assertFalse(intake_ready_to_teach({}))
        self.assertFalse(intake_ready_to_teach({"goal": "x"}))
        self.assertTrue(intake_ready_to_teach({"goal": "x", "stated_background": "beginner"}))

    def test_state_reports_single_next(self):
        st = intake_state({"goal": "x"})
        self.assertTrue(st["ask_one_at_a_time"])
        self.assertEqual(st["next_question"]["field"], "stated_background")
        self.assertFalse(st["ready_to_teach"])

    def test_optional_questions_follow_required(self):
        c = {"goal": "x", "stated_background": "beginner"}
        self.assertTrue(intake_ready_to_teach(c))
        # still offers optional refinements one at a time
        self.assertIsNotNone(next_question(c))
        self.assertIn(next_question(c).field, {"application_context", "time_horizon", "desired_independence"})


if __name__ == "__main__":
    unittest.main()

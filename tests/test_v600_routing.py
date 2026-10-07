import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.intents import infer_study_intent, _normalize
from runtime.router import route_message, RouteContext, infer_mode


class NormalizationTests(unittest.TestCase):
    def test_turkic_i_folding(self):
        self.assertEqual(_normalize("İmtahan"), _normalize("imtahan"))
        self.assertEqual(_normalize("QISA"), _normalize("qısa"))
        self.assertEqual(_normalize("QISA"), "qisa")


class IntentGoldenTests(unittest.TestCase):
    def case(self, msg, expected):
        self.assertEqual(infer_study_intent(msg), expected, msg=f"{msg!r}")

    def test_learn_about_is_not_quick(self):
        self.case("learn about abstract syntax trees", "learn_concept")

    def test_teach_me_understand_is_learn(self):
        self.case("Teach me how quicksort works so I understand it", "learn_concept")

    def test_master_then_what_is_is_learn(self):
        self.case("I want to master Python — what is a closure?", "learn_concept")

    def test_practice_not_shadowed_by_critique(self):
        self.case("məni yoxla", "practice_retrieval")
        self.case("test me on this", "practice_retrieval")

    def test_check_my_understanding_is_practice(self):
        self.case("check my understanding", "practice_retrieval")

    def test_critique_still_works(self):
        self.case("review my essay", "critique_artifact")
        self.case("check my code please", "critique_artifact")

    def test_plain_quick_reference(self):
        self.case("what is the default value of dict.get", "quick_reference")

    def test_azerbaijani_practice_variants(self):
        self.case("YADIMDAN SORUŞ", "practice_retrieval")


class ModeGoldenTests(unittest.TestCase):
    def test_imtahan_ver_is_assess(self):
        self.assertEqual(infer_mode("İmtahan ver mənə"), "assess")

    def test_qisa_is_quick(self):
        self.assertEqual(infer_mode("QISA de"), "quick_answer")

    def test_preview_is_not_review(self):
        # "preview" must not trigger review mode (word-boundary, not substring)
        m = infer_mode("give me a preview of the architecture")
        self.assertNotEqual(m, "review")

    def test_review_word_is_review(self):
        self.assertEqual(infer_mode("review this with me"), "review")


if __name__ == "__main__":
    unittest.main()

import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; SKILL = ROOT/"skills"/"adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.prompts import render_prompt, list_prompts
from runtime.learning_workspace import normalize_plan


class PromptTests(unittest.TestCase):
    def test_roadmap_prompt_fills_subject(self):
        r = render_prompt("roadmap", "FastAPI")
        self.assertIn("Map out FastAPI", r["prompt"])
        self.assertIn("where people usually get stuck", r["prompt"])

    def test_intake_prompt_one_at_a_time(self):
        self.assertIn("One at a time", render_prompt("intake")["prompt"])

    def test_unknown_stage_raises(self):
        with self.assertRaises(KeyError):
            render_prompt("nope")


class RoadmapPitfallsTests(unittest.TestCase):
    def test_pitfalls_accepted_and_normalized(self):
        plan = normalize_plan("FastAPI", "build an API", {"sources": ["https://roadmap.sh/python"], "nodes": [
            {"id": "routing", "title": "Routing", "task": "define routes",
             "pitfalls": ["confusing path vs query params", "forgetting response models"]}]})
        self.assertEqual(plan["nodes"][0]["pitfalls"], ["confusing path vs query params", "forgetting response models"])

    def test_pitfalls_optional(self):
        plan = normalize_plan("X", "g", {"sources": [], "nodes": [{"id": "a", "title": "A", "task": "t"}]})
        self.assertEqual(plan["nodes"][0]["pitfalls"], [])

    def test_bad_pitfalls_rejected(self):
        with self.assertRaises(ValueError):
            normalize_plan("X", "g", {"sources": [], "nodes": [{"id": "a", "title": "A", "task": "t", "pitfalls": [123]}]})




class LearningModesTests(unittest.TestCase):
    def test_all_modes_present(self):
        from runtime.prompts import STAGE_PROMPTS
        for m in ["intake", "roadmap", "socratic", "checker", "listener", "examiner",
                  "sparring", "roleplay", "realistic_task", "practice_app",
                  "leveler", "three_levels", "resource_finder", "clerk",
                  "explainer", "diagnostician", "flashcards"]:
            self.assertIn(m, STAGE_PROMPTS)
            self.assertIn("use_when", STAGE_PROMPTS[m])
        self.assertEqual(len(STAGE_PROMPTS), 17)

    def test_clerk_is_delegable_not_mastery(self):
        self.assertIn("Don't add anything I didn't write", render_prompt("clerk")["prompt"])
        self.assertIn("not mastery", render_prompt("clerk")["maps_to"].lower())

    def test_sparring_pushes_back(self):
        self.assertIn("Push back hard", render_prompt("sparring")["prompt"])

    def test_socratic_wording(self):
        self.assertIn("Ask me questions until I find the gap", render_prompt("socratic")["prompt"])

    def test_checker_no_rewrite(self):
        self.assertIn("Don't rewrite it", render_prompt("checker")["prompt"])

    def test_listener_grade_against_source(self):
        self.assertIn("Grade it against [source]", render_prompt("listener")["prompt"])

    def test_examiner_harder_each_time(self):
        r = render_prompt("examiner", "Python")
        self.assertIn("Quiz me on Python", r["prompt"])
        self.assertIn("harder when I get it right", r["prompt"])

    def test_aliases_resolve(self):
        self.assertEqual(render_prompt("interviewer")["stage"], "intake")
        self.assertEqual(render_prompt("mapmaker", "Go")["stage"], "roadmap")

    def test_every_mode_has_use_when_and_mapping(self):
        from runtime.prompts import list_prompts
        for m in list_prompts():
            r = render_prompt(m["stage"])
            self.assertTrue(r["use_when"]); self.assertTrue(r["maps_to"]); self.assertTrue(r["name"])



    def test_explainer_is_constrained_last_resort(self):
        r = render_prompt("explainer")
        self.assertIn("got stuck at [step]", r["prompt"])
        self.assertIn("lock-in", r["maps_to"].lower())

    def test_diagnostician_prompt_and_runtime_mapping(self):
        r = render_prompt("diagnostician")
        self.assertIn("What misunderstanding do they have in common", r["prompt"])
        self.assertIn("diagnose-history", r["maps_to"])

    def test_flashcards_maps_to_spaced_review(self):
        r = render_prompt("flashcards")
        self.assertIn("One idea per card", r["prompt"])
        self.assertIn("review-due", r["maps_to"])


if __name__ == "__main__":
    unittest.main()

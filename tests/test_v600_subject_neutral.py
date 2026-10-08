"""The skill teaches any subject, so its generated workspace and check guidance must
not assume the learner is writing code. Code-specific wording is fine only on paths
that are code-specific by nature (execution backends, the Python verifier)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.contracts import LearningContract
from runtime.execution import LocalToolchainBackend
from runtime.session import SessionKernel
from runtime.workspace_check import check_module

ROADMAP = {"sources": ["https://en.wikipedia.org/wiki/Ottoman_Empire"], "status": "proposed",
           "nodes": [{"id": "rise", "title": "Rise of the Ottomans", "task": "Write a 300-word argument.",
                      "requires": []}]}


def _history_session(td):
    k = SessionKernel(Path(td))
    s = k.start("Ottoman history", LearningContract(goal="learn", goal_mode="learn"), roadmap=ROADMAP)
    return Path(td) / ".learning" / "sessions" / s["session_id"]


class NonExecutableWorkTests(unittest.TestCase):
    def test_missing_spec_points_non_code_work_to_the_right_path(self):
        with tempfile.TemporaryDirectory() as td:
            sd = _history_session(td)
            r = check_module(sd, "rise", backend=LocalToolchainBackend())
            self.assertEqual(r["status"], "no_check_spec")
            self.assertIn("listener-check", r["detail"])
            self.assertIn("alearn evidence", r["detail"])

    def test_non_executable_kind_is_not_treated_as_code(self):
        with tempfile.TemporaryDirectory() as td:
            sd = _history_session(td)
            mod = sd / "workspace" / "modules" / "rise"
            (mod / "checks" / "check.json").write_text(json.dumps({"kind": "essay"}), encoding="utf-8")
            (mod / "submission" / "answer.md").write_text("The Ottomans rose because...", encoding="utf-8")
            r = check_module(sd, "rise", backend=LocalToolchainBackend())
            self.assertEqual(r["status"], "not_runtime_checkable")
            self.assertNotIn("evidence", r)
            self.assertIn("listener-check", r["detail"])


class GeneratedWorkspaceWordingTests(unittest.TestCase):
    CODE_ONLY_PHRASES = ("kod practice", "IDE-də işləyəcəyin", "Agent kodu icra edib")

    def test_workspace_readme_and_lesson_ui_are_subject_neutral(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _history_session(td) / "workspace"
            readme = (ws / "README.md").read_text(encoding="utf-8")
            lesson = (ws / "frontend" / "src" / "components" / "Lesson.jsx").read_text(encoding="utf-8")
            for phrase in self.CODE_ONLY_PHRASES:
                with self.subTest(phrase=phrase):
                    self.assertNotIn(phrase, readme)
                    self.assertNotIn(phrase, lesson)


if __name__ == "__main__":
    unittest.main()

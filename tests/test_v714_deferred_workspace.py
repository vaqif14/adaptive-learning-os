"""CLI-first starts and staged roadmap assets on isolated local sessions."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract
from runtime.session import SessionKernel
from runtime.utils import read_json
from course_fixture import IsolatedState


def plan(*ids):
    return {"sources": ["https://example.org/official"], "nodes": [
        {"id": node, "title": f"Capability {node}", "task": f"Demonstrate {node} independently",
         "requires": list(ids[:index]), "outcomes": [f"Explain and apply {node}"]}
        for index, node in enumerate(ids)]}


class DeferredWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.iso = IsolatedState().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.k = SessionKernel(self.ws)
        self.contract = LearningContract(goal="Apply a concept independently", stated_background="Unknown")

    def tearDown(self):
        self.tmp.cleanup()
        self.iso.__exit__()

    def start(self, candidate=None):
        s = self.k.start("Concepts", self.contract, roadmap=candidate, defer_workspace=True)
        return s, self.k.dir(s["session_id"])

    def test_deferred_start_has_only_state_and_no_placeholder_plan(self):
        s, d = self.start()
        self.assertEqual(s["workflow"], "cli_first")
        self.assertNotIn("learning_workspace", s)
        self.assertNotIn("candidate_roadmap", s)
        self.assertFalse((d / "workspace").exists())
        self.assertEqual(list(d.rglob("*.html")), [])
        self.assertTrue((d / "ledger.jsonl").is_file())
        self.assertTrue(self.k.ledger(s["session_id"]).integrity()["chain_ok"])
        # CLI-first must enter guarded onboarding before it can even request
        # generation; the generic legacy driver cannot bypass the course gates.
        self.assertEqual(self.k.next_action(s["session_id"])["action"], "course_onboarding")

    def test_explicit_plan_remains_candidate_data_without_rendering(self):
        s, d = self.start(plan("alpha"))
        self.assertEqual(s["candidate_roadmap"]["nodes"][0]["id"], "alpha")
        self.assertFalse((d / "workspace").exists())
        self.assertEqual(self.k.inspect(s["session_id"])["session"]["candidate_roadmap"], s["candidate_roadmap"])
        self.assertEqual(list(d.rglob("*.html")), [])

    def test_legacy_python_api_preserves_workspace_with_explicit_label(self):
        s = self.k.start("Concepts", self.contract, roadmap=plan("alpha"))
        self.assertEqual(s["workflow"], "legacy_workspace")
        root = self.k.dir(s["session_id"]) / "workspace"
        self.assertTrue((root / "frontend").is_dir())
        self.assertTrue((root / "modules/alpha/TASK.md").is_file())

    def test_public_set_roadmap_cannot_bypass_course_flow_before_initialization(self):
        s, d = self.start()
        with self.assertRaisesRegex(ValueError, "course propose-roadmap"):
            self.k.set_roadmap(s["session_id"], plan("alpha"))
        self.assertFalse((d / "workspace").exists())

    def test_staged_roadmap_contains_only_plan_and_clickable_preview(self):
        s, d = self.start(plan("alpha"))
        out = self.k.set_roadmap(s["session_id"], plan("alpha", "beta"),
                                 _course_internal=True, roadmap_only=True)
        root = d / "workspace"
        self.assertEqual({p.name for p in root.iterdir()}, {"index.html", "roadmap.json"})
        html = (root / "index.html").read_text()
        self.assertIn('href="#node-alpha"', html)
        self.assertIn('id="node-beta"', html)
        self.assertNotIn("modules/", html)
        self.assertNotIn("frontend_directory", out["workspace"])
        current = self.k.inspect(s["session_id"])["session"]
        self.assertNotIn("frontend_directory", current["learning_workspace"])
        self.assertNotIn("candidate_roadmap", current)

    def test_internal_materialization_adds_teaching_assets_after_staging(self):
        s, d = self.start()
        self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True, roadmap_only=True)
        self.assertFalse((d / "workspace/modules").exists())
        self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True, roadmap_only=False)
        self.assertTrue((d / "workspace/modules/alpha/submission").is_dir())
        self.assertTrue((d / "workspace/frontend/src/components/Lesson.jsx").is_file())
        self.assertEqual(self.k.inspect(s["session_id"])["session"]["workflow"], "cli_first")

    def test_revised_proposal_preserves_old_learner_files_without_new_modules(self):
        s, d = self.start()
        self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True)
        root = d / "workspace"
        files = {"modules/alpha/submission/work.txt": "my explanation",
                 "modules/alpha/checks/check.json": "{}",
                 "modules/alpha/notes.txt": "my notes",
                 "resources/private-notes.txt": "my source annotations"}
        for rel, text in files.items():
            (root / rel).write_text(text)
        self.k.set_roadmap(s["session_id"], plan("alpha", "beta"), _course_internal=True, roadmap_only=True)
        for rel, text in files.items():
            self.assertEqual((root / rel).read_text(), text)
        self.assertFalse((root / "modules/beta").exists())
        self.k.set_roadmap(s["session_id"], plan("alpha", "beta"), _course_internal=True)
        for rel, text in files.items():
            self.assertEqual((root / rel).read_text(), text)

    def test_committed_map_recovers_after_install_interruption_without_creating_frontend(self):
        s, d = self.start()
        with patch("runtime.local_state.recover_state", side_effect=OSError("fixture power loss")):
            with self.assertRaises(OSError):
                self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True, roadmap_only=True)
        current = self.k.inspect(s["session_id"])["session"]
        self.assertTrue((d / "workspace/index.html").is_file())
        self.assertFalse((d / "workspace/frontend").exists())
        self.assertNotIn("frontend_directory", current["learning_workspace"])
        self.assertEqual(read_json(d / "workspace/roadmap.json")["nodes"][0]["id"], "alpha")
        self.assertEqual(list(d.glob("workspace-stage-*")), [])

    def test_invalid_candidate_is_rejected_before_any_session_is_created(self):
        with self.assertRaises(ValueError):
            self.start({"nodes": [{"id": "../unsafe", "title": "x", "task": "x"}]})
        self.assertFalse(self.k.sessions_dir.exists())

    def test_prepare_only_leaves_active_workspace_and_journal_unchanged(self):
        s, d = self.start()
        self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True, roadmap_only=True)
        before = {p.name: p.read_bytes() for p in (d / "workspace").iterdir() if p.is_file()}
        session_before = (d / "session.json").read_bytes()
        journal_before = (d / "ledger.jsonl").read_bytes()
        prepared = self.k.set_roadmap(s["session_id"], plan("alpha", "beta"),
                                     _course_internal=True, prepare_only=True)
        self.assertEqual({p.name: p.read_bytes() for p in (d / "workspace").iterdir() if p.is_file()}, before)
        self.assertFalse((d / "workspace/frontend").exists())
        self.assertEqual((d / "session.json").read_bytes(), session_before)
        self.assertEqual((d / "ledger.jsonl").read_bytes(), journal_before)
        stage = d / prepared["workspace_install"]
        self.assertTrue((stage / "frontend").is_dir())
        self.assertTrue((stage / "modules/beta/submission").is_dir())
        self.assertEqual(prepared["files"]["session.json"]["learning_workspace"]["directory"], str((d / "workspace").resolve()))

    def test_prepared_initial_map_does_not_publish_or_remove_candidate(self):
        s, d = self.start(plan("alpha"))
        journal_before = (d / "ledger.jsonl").read_bytes()
        prepared = self.k.set_roadmap(s["session_id"], plan("alpha"), _course_internal=True,
                                     roadmap_only=True, prepare_only=True)
        self.assertFalse((d / "workspace").exists())
        self.assertEqual(read_json(d / "session.json")["candidate_roadmap"], s["candidate_roadmap"])
        self.assertEqual((d / "ledger.jsonl").read_bytes(), journal_before)
        self.assertNotIn("candidate_roadmap", prepared["files"]["session.json"])
        self.assertFalse((d / prepared["workspace_install"] / "frontend").exists())


if __name__ == "__main__":
    unittest.main()

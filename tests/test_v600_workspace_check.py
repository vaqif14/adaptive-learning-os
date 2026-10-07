import sys, unittest, tempfile, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; SKILL = ROOT/"skills"/"adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.workspace_check import check_module
from runtime.execution import LocalToolchainBackend

ROADMAP = {"sources": ["https://roadmap.sh/python"], "status": "proposed",
           "nodes": [{"id": "sum-func", "title": "Sum", "task": "print 2+3", "requires": [], "pitfalls": ["newline"]}]}

def _session(td):
    k = SessionKernel(Path(td))
    s = k.start("Python", LearningContract(goal="learn", goal_mode="learn"), roadmap=ROADMAP)
    return k, s, Path(td) / ".learning" / "sessions" / s["session_id"]


class WorkspaceCheckTests(unittest.TestCase):
    def _spec(self, mod, **kw):
        base = {"kind": "code", "language": "python", "entry": "main.py", "expect_stdout": "5", "capability": "sum"}
        base.update(kw)
        (mod / "checks" / "check.json").write_text(json.dumps(base), encoding="utf-8")

    def test_no_spec_reported(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            r = check_module(sd, "sum-func", backend=LocalToolchainBackend())
            self.assertEqual(r["status"], "no_check_spec")

    def test_empty_submission_reported(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            self._spec(sd / "workspace" / "modules" / "sum-func")
            r = check_module(sd, "sum-func", backend=LocalToolchainBackend())
            self.assertEqual(r["status"], "empty_submission")

    def test_correct_submission_passes_and_yields_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            mod = sd / "workspace" / "modules" / "sum-func"
            self._spec(mod)
            (mod / "submission" / "main.py").write_text("print(2+3)", encoding="utf-8")
            r = check_module(sd, "sum-func", backend=LocalToolchainBackend())
            self.assertEqual(r["status"], "checked"); self.assertTrue(r["passed"])
            self.assertEqual(r["outcome"], "correct")
            self.assertIsNotNone(r["evidence"]); self.assertTrue(r["evidence"]["mastery_eligible"])
            self.assertTrue((mod / "feedback" / "result.json").exists())

    def test_wrong_submission_incorrect(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            mod = sd / "workspace" / "modules" / "sum-func"
            self._spec(mod)
            (mod / "submission" / "main.py").write_text("print(2+4)", encoding="utf-8")
            r = check_module(sd, "sum-func", backend=LocalToolchainBackend())
            self.assertEqual(r["outcome"], "incorrect"); self.assertFalse(r["passed"])

    def test_unchecked_spec_is_not_mastery(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            mod = sd / "workspace" / "modules" / "sum-func"
            self._spec(mod, expect_stdout=None)  # no correctness criterion
            (mod / "submission" / "main.py").write_text("print(2+3)", encoding="utf-8")
            r = check_module(sd, "sum-func", backend=LocalToolchainBackend())
            self.assertEqual(r["outcome"], "unknown"); self.assertIsNone(r["evidence"])

    def test_bad_module_id_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            k, s, sd = _session(td)
            with self.assertRaises((ValueError, FileNotFoundError)):
                check_module(sd, "../etc", backend=LocalToolchainBackend())


if __name__ == "__main__":
    unittest.main()

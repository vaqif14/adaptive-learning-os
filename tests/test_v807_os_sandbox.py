"""OS-level containment of verifier children (runtime/os_sandbox.py) and the guards built on it.

Author code (possibly from an opt-in runner steered by an injected source) is executed during studio admission and the
rules gate. The in-process defences are not a boundary, so the child runs under Seatbelt (macOS) or bubblewrap (Linux)
when available; runners refuse to dispatch without it, and R10 proves the containment with a canary and a control.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import os_sandbox  # noqa: E402
from runtime import studio_agents as agents  # noqa: E402
from runtime.rules_probes import r10  # noqa: E402
from runtime.rules_probes.base import Ctx  # noqa: E402
from runtime.verifiers.python_runner import PythonVerifier  # noqa: E402

HAS_OS_SANDBOX = os_sandbox.mode() is not None
PY_SUBJECT = [{"node": "n", "origin": "staged", "lesson": {"practice": {"checks": [], "reference_solution": "x"}}, "items": None}]


class ProfileTests(unittest.TestCase):
    def test_the_seatbelt_profile_denies_network_exec_writes_and_private_reads(self):
        run = tempfile.mkdtemp(prefix="alearn-prof-")
        prof = os_sandbox.seatbelt_profile(run)
        for rule in ("(deny network*)", "(deny process-exec*)", "(deny file-write*)", "(deny file-read-data"):
            self.assertIn(rule, prof)
        self.assertIn(os.path.realpath(run), prof)
        self.assertLess(prof.index("(deny file-read-data"), prof.index("(allow file-read-data"))   # later rules win
        self.assertNotIn("(allow network", prof)
        home = os.path.realpath(os.path.expanduser("~"))
        deny_line = next(line for line in prof.splitlines() if line.startswith("(deny file-read-data"))
        self.assertIn(home, deny_line)

    def test_private_roots_include_home_temp_and_state_dir(self):
        with mock.patch.dict(os.environ, {"ALEARN_STATE_DIR": "/tmp/alearn-state-x"}):
            roots = os_sandbox.private_roots()
        self.assertIn(os.path.realpath(os.path.expanduser("~")), roots)
        self.assertIn(os.path.realpath(tempfile.gettempdir()), roots)
        self.assertIn(os.path.realpath("/tmp/alearn-state-x"), roots)

    def test_bwrap_hides_private_roots_and_binds_only_the_run_dir_writable(self):
        run = os.path.realpath(tempfile.mkdtemp(prefix="alearn-bw-"))
        args = os_sandbox.bwrap_prefix(run)
        for flag in ("--unshare-net", "--die-with-parent", "--new-session"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--ro-bind") + 1:args.index("--ro-bind") + 3], ["/", "/"])
        self.assertEqual(args[args.index("--bind") + 1:args.index("--bind") + 3], [run, run])
        self.assertEqual(args[-1], "--")


@unittest.skipUnless(HAS_OS_SANDBOX, "no working OS sandbox (Seatbelt / bubblewrap) on this platform")
class ContainmentTests(unittest.TestCase):
    def test_a_wrapped_child_is_contained_and_the_control_is_not(self):
        proof = os_sandbox.containment_check()
        self.assertTrue(proof["control_ok"], proof["unwrapped"])
        self.assertTrue(proof["contained"], proof["wrapped"])
        for action in os_sandbox.MUST_DENY:
            self.assertTrue(proof["wrapped"][action].startswith("denied"), (action, proof["wrapped"]))
        self.assertEqual(proof["unwrapped"]["read_canary"], "allowed")

    def test_a_profile_without_the_read_deny_is_caught(self):
        # mutant: drop the private-roots read deny; the canary becomes readable and the check must say so
        original = os_sandbox.seatbelt_profile
        if os_sandbox.mode() != "seatbelt":
            self.skipTest("the profile mutant is Seatbelt-specific")

        def leaky(run_dir, extra_private=()):
            return "\n".join(line for line in original(run_dir, extra_private).splitlines()
                             if not line.startswith("(deny file-read-data"))
        with mock.patch.object(os_sandbox, "seatbelt_profile", leaky):
            proof = os_sandbox.containment_check()
        self.assertFalse(proof["contained"])
        self.assertEqual(proof["wrapped"]["read_canary"], "allowed")

    def test_the_verifier_still_runs_ordinary_code_inside_the_boundary(self):
        res = PythonVerifier(timeout_sec=5.0).verify("import math, json\nprint(math.sqrt(16), json.dumps([1]))",
                                                     trusted_test_code="assert math.sqrt(9) == 3")
        self.assertTrue(res.passed, res.stderr)
        self.assertEqual(res.stdout.strip(), "4.0 [1]")

    def test_verifier_children_are_wrapped(self):
        seen = []
        real = os_sandbox.wrap

        def spy(argv, run_dir, extra_private=()):
            out = real(argv, run_dir, extra_private)
            seen.append(out)
            return out
        with mock.patch.object(os_sandbox, "wrap", spy):
            PythonVerifier(timeout_sec=5.0).verify("print(1)")
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0][0], sys.executable)          # the wrapper, not the bare interpreter, is started


class RunnerGateTests(unittest.TestCase):
    def test_runners_need_an_os_sandbox(self):
        with mock.patch.object(os_sandbox, "mode", return_value=None):
            self.assertFalse(agents.os_sandbox_ready())
        with mock.patch.object(os_sandbox, "mode", return_value="seatbelt"):
            self.assertTrue(agents.os_sandbox_ready())


def status(res):
    return {True: "pass", False: "fail", None: "not_applicable"}[res.passed]


class R10ProbeTests(unittest.TestCase):
    def ctx(self, proof, job=None, subjects=PY_SUBJECT):
        return Ctx.synthetic(subjects=subjects, job=job, hooks={"os_containment": lambda: proof})

    def test_contained_passes(self):
        res = r10.os_sandbox_contains(self.ctx({"mode": "seatbelt", "contained": True, "control_ok": True, "wrapped": {}}))
        self.assertEqual(status(res), "pass", res.detail)

    def test_a_leaking_sandbox_or_a_blind_control_fails(self):
        leak = {"mode": "seatbelt", "contained": False, "control_ok": True, "wrapped": {"read_canary": "allowed"}}
        self.assertEqual(status(r10.os_sandbox_contains(self.ctx(leak))), "fail")
        blind = {"mode": "seatbelt", "contained": True, "control_ok": False, "wrapped": {}}
        self.assertEqual(status(r10.os_sandbox_contains(self.ctx(blind))), "fail")

    def test_no_os_sandbox_is_a_failure_only_for_runner_authored_code(self):
        none = {"mode": None, "contained": False, "control_ok": False}
        self.assertEqual(status(r10.os_sandbox_contains(self.ctx(none))), "not_applicable")
        job = {"orders": {"ord_1": {"taken_by": "runner:claude", "status": "submitted"}}}
        self.assertEqual(status(r10.os_sandbox_contains(self.ctx(none, job=job))), "fail")

    def test_no_code_no_requirement(self):
        res = r10.os_sandbox_contains(self.ctx({"mode": None}, subjects=[{"node": "n", "origin": "staged", "lesson": {}, "items": None}]))
        self.assertEqual(status(res), "not_applicable")

    def test_the_negative_control_fails(self):
        res = r10.os_sandbox_contains(r10._control_os_sandbox())
        self.assertEqual(status(res), "fail")


if __name__ == "__main__":
    unittest.main()

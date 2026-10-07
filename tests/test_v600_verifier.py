import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.verifiers.python_runner import PythonVerifier


class VerifierHardeningTests(unittest.TestCase):
    def setUp(self):
        self.v = PythonVerifier(timeout_sec=5.0)

    def test_runs_on_this_platform(self):
        r = self.v.verify("print(2 + 3)", expected_stdout="5")
        self.assertTrue(r.executed, msg=r.stderr)
        self.assertTrue(r.passed)
        self.assertTrue(r.correctness_checked)

    def test_systemexit_cannot_skip_trusted_tests(self):
        # Wrong add() + early SystemExit: the trusted assert MUST still run and fail.
        code = "def add(a, b):\n    return a - b\nraise SystemExit(0)\n"
        trusted = "assert add(2, 3) == 5\n"
        r = self.v.verify(code, trusted_test_code=trusted)
        self.assertFalse(r.passed)          # forgery blocked
        self.assertTrue(r.correctness_checked)

    def test_correct_code_passes_trusted_tests(self):
        code = "def add(a, b):\n    return a + b\n"
        trusted = "assert add(2, 3) == 5\nassert add(-1, 1) == 0\n"
        r = self.v.verify(code, trusted_test_code=trusted)
        self.assertTrue(r.passed)

    def test_stdout_spoof_with_failing_test_does_not_pass(self):
        code = "print('[1, 2]')\nraise SystemExit(0)\n"
        trusted = "assert False, 'should fail'\n"
        r = self.v.verify(code, trusted_test_code=trusted)
        self.assertFalse(r.passed)

    def test_open_is_unreachable_builtin(self):
        # open() is a forbidden NAME (AST pre-filter) -> policy_valid False.
        r = self.v.verify("open('/etc/passwd').read()\n")
        self.assertFalse(r.policy_valid)

    def test_builtins_open_via_attr_blocked(self):
        # __builtins__ dunder access is blocked by the AST visitor.
        r = self.v.verify("__builtins__.open('/tmp/x','w')\n")
        self.assertFalse(r.policy_valid)

    def test_unchecked_run_is_not_correctness_checked(self):
        r = self.v.verify("x = 1 + 1\n")
        self.assertTrue(r.executed)
        self.assertFalse(r.correctness_checked)  # nothing to verify -> not a pass
        self.assertFalse(r.passed)

    def test_runtime_error_fails(self):
        r = self.v.verify("1/0\n", expected_stdout="")
        self.assertTrue(r.executed)
        self.assertFalse(r.passed)
        self.assertIn("ZeroDivision", r.stderr)

    def test_forbidden_import_rejected(self):
        r = self.v.verify("import os\nprint(os.getcwd())\n")
        self.assertFalse(r.policy_valid)

    def test_allowed_import_works(self):
        r = self.v.verify("import math\nprint(math.floor(3.7))\n", expected_stdout="3")
        self.assertTrue(r.passed, msg=r.stderr)


if __name__ == "__main__":
    unittest.main()

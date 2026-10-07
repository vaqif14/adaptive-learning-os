import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.execution import (
    run_exercise, language, LANGUAGES, LocalToolchainBackend, ContainerPolicy, verify_fastapi_app,
)

def _have(lang): return LocalToolchainBackend().available_for(language(lang))


class RegistryTests(unittest.TestCase):
    def test_core_languages_present(self):
        for l in ["python","javascript","go","java","c","ruby","sql","kotlin","rust","typescript"]:
            self.assertIn(l, LANGUAGES)

    def test_unknown_language_raises(self):
        with self.assertRaises(KeyError):
            language("cobol")

    def test_every_spec_has_container_image(self):
        for spec in LANGUAGES.values():
            self.assertTrue(spec.image)


class LocalExecutionTests(unittest.TestCase):
    def test_python_runs_and_checks_stdout(self):
        r = run_exercise("python", "print(2+3)", expect_stdout="5", backend=LocalToolchainBackend())
        self.assertTrue(r["available"]); self.assertTrue(r["passed"])

    def test_wrong_output_fails(self):
        r = run_exercise("python", "print(2+4)", expect_stdout="5", backend=LocalToolchainBackend())
        self.assertFalse(r["passed"])

    def test_runtime_error_surfaced_not_hidden(self):
        r = run_exercise("python", "raise ValueError('boom')", expect_stdout="5", backend=LocalToolchainBackend())
        self.assertFalse(r["ran"]); self.assertIn("boom", r["stderr"])

    @unittest.skipUnless(_have("javascript"), "node not installed")
    def test_javascript_runs(self):
        r = run_exercise("javascript", "console.log(6*7)", expect_stdout="42", backend=LocalToolchainBackend())
        self.assertTrue(r["passed"], msg=r.get("stderr"))

    @unittest.skipUnless(_have("go"), "go not installed")
    def test_go_runs(self):
        # first `go run` on a cold GOCACHE (CI runners) compiles the toolchain shims
        # and can take well over the default timeout — give it room.
        r = run_exercise("go", 'package main\nimport "fmt"\nfunc main(){ fmt.Println("hi") }',
                         expect_stdout="hi", backend=LocalToolchainBackend(timeout=120))
        self.assertTrue(r["passed"], msg=r)

    def test_missing_local_toolchain_is_honest_not_unsupported(self):
        # force local-only; pick a language unlikely to be installed
        target = next((l for l in ["php","rust","kotlin"] if not _have(l)), None)
        if target is None:
            self.skipTest("all candidate toolchains happen to be installed")
        r = run_exercise(target, "x", backend=LocalToolchainBackend())
        self.assertFalse(r["available"])
        self.assertIn("toolchain_not_installed_locally", r["detail"])
        self.assertIn(LANGUAGES[target].image, r["detail"])  # tells you the container image


class ContainerPolicyTests(unittest.TestCase):
    def test_policy_is_locked_down(self):
        args = ContainerPolicy().docker_args()
        self.assertIn("--network", args); self.assertIn("none", args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args); self.assertIn("ALL", args)


class FastAPITests(unittest.TestCase):
    @unittest.skipUnless(_have("python"), "python missing")
    def test_fastapi_or_clear_unavailable(self):
        app = 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/")\ndef r(): return {"ok": True}'
        res = verify_fastapi_app(app, [{"name":"root","path":"/","expect_status":200,"expect_json":{"ok":True}}],
                                 backend=LocalToolchainBackend())
        if res["available"]:
            self.assertTrue(res["all_passed"], msg=res)
        else:
            # if fastapi isn't installed, the message must say so (not "python broken")
            self.assertIn("fastapi", res["detail"].lower())


if __name__ == "__main__":
    unittest.main()

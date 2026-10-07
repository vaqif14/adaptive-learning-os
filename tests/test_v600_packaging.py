import sys, unittest, tomllib, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PackagingTests(unittest.TestCase):
    def test_cli_resolves_skill_in_tree(self):
        from adaptive_learning_os import cli
        script = cli._skill_script()
        self.assertTrue(script.is_file())
        self.assertEqual(script.name, "alearn.py")

    def test_env_override(self):
        from adaptive_learning_os import cli
        skill_dir = ROOT / "skills" / "adaptive-learn"
        os.environ["ALEARN_SKILL_DIR"] = str(skill_dir)
        try:
            self.assertEqual(cli._skill_script(), skill_dir / "scripts" / "alearn.py")
        finally:
            del os.environ["ALEARN_SKILL_DIR"]

    def test_pyproject_ships_skill_package(self):
        cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        pkgs = cfg["tool"]["setuptools"]["packages"]
        self.assertIn("alearn_skill", pkgs)
        self.assertIn("alearn_skill.runtime", pkgs)
        self.assertEqual(cfg["project"]["version"], "0.6.0")

    def test_version_consistency(self):
        import adaptive_learning_os
        self.assertEqual(adaptive_learning_os.__version__, "0.6.0")


if __name__ == "__main__":
    unittest.main()

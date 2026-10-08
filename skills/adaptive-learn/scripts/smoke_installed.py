"""Run using the installed environment's Python, from any directory.

No checkout imports or environment overrides; exercises shipped assets and a
real session backup/restore rather than falling back to --help on failure.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "ALEARN_SKILL_DIR"}}
    with tempfile.TemporaryDirectory(prefix="alearn-installed-") as tmp:
        root = Path(tmp)

        def run(*args):
            result = subprocess.run([sys.executable, "-c", "from adaptive_learning_os.cli import main; main()", *args],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=30, check=True)
            return json.loads(result.stdout)

        location = subprocess.run([sys.executable, "-c", "import adaptive_learning_os; print(adaptive_learning_os.__file__)"],
                                  cwd=root, env=env, text=True, capture_output=True, check=True).stdout.strip()
        if "site-packages" not in Path(location).parts:
            raise RuntimeError("smoke test must use an installed package in site-packages")
        assert run("local-doctor")["status"] == "ok"
        started = run("start", "--topic", "Logic", "--goal", "Learn reasoning", "--without-notebooklm")
        # start prints its session nested in the output envelope.
        sid = started.get("session_id") or started["session"]["session_id"]
        run("add-card", "--session", sid, "--node", "first-attempt", "--front", "Explain a claim")
        archive = root / "backup.zip"
        run("backup-session", "--session", sid, "--output", str(archive))
        destination = root / "restored"
        destination.mkdir()
        shutil.copyfile(archive, destination / "backup.zip")
        restored = run("--workspace", str(destination), "restore-session", "--backup-file", str(destination / "backup.zip"))
        assert restored["session_id"] == sid
        assert run("--workspace", str(destination), "local-doctor")["status"] == "ok"
        assert run("--workspace", str(destination), "cards-due", "--session", sid)["total"] == 1
    print("Installed package: assets, start, cards, backup, restore and integrity OK")


if __name__ == "__main__":
    main()

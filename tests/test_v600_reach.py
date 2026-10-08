"""Optional Agent-Reach source adapter: availability-only, zero-dep, fail-safe."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime import reach_cli  # noqa: E402


def test_unavailable_when_not_installed(monkeypatch):
    monkeypatch.setattr(reach_cli.shutil, "which", lambda name: None)
    d = reach_cli.doctor()
    assert d["status"] == "unavailable"
    assert d["provider"] == "agent-reach"
    assert "Agent-Reach" in d["action"]
    assert reach_cli.available() is False


def _fake_run(stdout="", stderr="", returncode=0):
    def run(argv, **kw):
        return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return run


def test_available_lists_ready_platforms(monkeypatch):
    monkeypatch.setattr(reach_cli.shutil, "which", lambda name: "/usr/local/bin/agent-reach")
    monkeypatch.setattr(reach_cli.subprocess, "run",
                        _fake_run(stdout="web: ok\nyoutube: ok\ngithub: ready\n"))
    d = reach_cli.doctor()
    assert d["status"] == "available"
    assert "web" in d["platforms_mentioned"]
    assert "youtube" in d["platforms_mentioned"]
    assert "github" in d["platforms_mentioned"]


def test_configuration_required_is_not_raw_leaked(monkeypatch):
    monkeypatch.setattr(reach_cli.shutil, "which", lambda name: "/usr/local/bin/agent-reach")
    monkeypatch.setattr(reach_cli.subprocess, "run",
                        _fake_run(stdout="please login / configure twitter cookie expired", returncode=1))
    d = reach_cli.doctor()
    assert d["status"] == "configuration_required"
    # coarse status only — no raw diagnostic text echoed back
    assert "cookie" not in str(d).lower()


def test_timeout_is_handled(monkeypatch):
    import subprocess
    monkeypatch.setattr(reach_cli.shutil, "which", lambda name: "/usr/local/bin/agent-reach")
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(cmd="agent-reach", timeout=30)
    monkeypatch.setattr(reach_cli.subprocess, "run", boom)
    d = reach_cli.doctor()
    assert d["status"] == "timeout"

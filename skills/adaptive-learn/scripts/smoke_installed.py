"""Run using the installed environment's Python, from any directory.

No checkout imports or environment overrides; exercises shipped assets and a
real session backup/restore rather than falling back to --help on failure.
"""
import json
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "ALEARN_SKILL_DIR", "ALEARN_LEDGER_KEY", "ALEARN_STATE_DIR",
                        "ADAPTIVE_API_TOKEN", "ALEARN_ALLOW_UNSANDBOXED_EXEC"}}
    with tempfile.TemporaryDirectory(prefix="alearn-installed-") as tmp:
        root = Path(tmp)
        # Smoke sessions and trust receipts must not touch the learner's real state.
        env["ALEARN_STATE_DIR"] = str(root / "private-state")
        entrypoint = Path(sys.executable).parent / "alearn"
        if not entrypoint.is_file():
            raise RuntimeError("installed alearn console entry point is missing")
        checks = []

        def run(*args, error=None):
            result = subprocess.run([str(entrypoint), *args], cwd=root, env=env, text=True,
                                    capture_output=True, timeout=30, check=False)
            if error is not None:
                if result.returncode == 0 or error not in result.stdout + result.stderr:
                    raise RuntimeError(f"expected {error}: {result.stdout} {result.stderr}")
                checks.append({"command": list(args), "expected_error": error, "exit_code": result.returncode})
                return None
            result.check_returncode()
            checks.append({"command": list(args), "exit_code": result.returncode})
            return json.loads(result.stdout)

        location = subprocess.run([sys.executable, "-c", "import adaptive_learning_os; print(adaptive_learning_os.__file__)"],
                                  cwd=root, env=env, text=True, capture_output=True, check=True).stdout.strip()
        if "site-packages" not in Path(location).parts:
            raise RuntimeError("smoke test must use an installed package in site-packages")
        # Resolve the packaged CLI and the new journey module without a checkout fallback.
        assets = subprocess.run([sys.executable, "-c",
                                 "from adaptive_learning_os.cli import _skill_script; "
                                 "from pathlib import Path; import json; p=_skill_script(); "
                                 "print(json.dumps({'cli': str(p), 'journey': str(p.parent.parent / 'runtime/course_journey.py'), "
                                 "'recovery': str(p.parent.parent / 'runtime/course_recovery.py'), "
                                 "'built_ui': str(p.parent.parent / 'templates/react/dist/index.html'), "
                                 "'react': str(p.parent.parent / 'templates/react/src/App.jsx')}))"],
                                cwd=root, env=env, text=True, capture_output=True, check=True)
        assets = json.loads(assets.stdout)
        for path in assets.values():
            if "site-packages" not in Path(path).parts or not Path(path).is_file():
                raise RuntimeError(f"missing installed asset: {path}")
        assert run("local-doctor")["status"] == "ok"
        started = run("start", "--topic", "Logic", "--goal", "Learn reasoning",
                      "--stated-background", "No prior formal logic practice", "--without-notebooklm")
        # start prints its session nested in the output envelope.
        sid = started.get("session_id") or started["session"]["session_id"]
        session_dir = root / ".learning" / "sessions" / sid
        assert not (session_dir / "workspace").exists()
        assert not list(session_dir.rglob("*.html")), "start must create state only, never an early course"
        initial = run("course", "init", "--session", sid)
        readiness = initial["generation_readiness"]
        assert readiness["required"] is True and readiness["ready"] is False
        assert {"diagnostic_required", "goal_clarification_required", "sources_required"} <= set(readiness["blockers"])
        assert initial["roadmap"]["approved"] is False
        assert not (session_dir / "workspace").exists()
        run("course", "serve", "--session", sid, "--allow-anon", error="Course interface waits for roadmap approval")
        # Optional intake/roadmap preview serves neutral installed assets. It is
        # not evidence of a generated or approved personalized teaching workspace.
        ui = Path(assets["built_ui"]).parent
        server_smoke = r'''
import json, re, sys, threading, time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
T0 = time.monotonic()
def step(name):
    print(f"[{time.monotonic() - T0:6.2f}s] {name}", file=sys.stderr, flush=True)
# Loopback only: never consult system proxy settings (macOS framework builds read them).
urlopen = build_opener(ProxyHandler({})).open
step("import runtime")
from adaptive_learning_os.cli import _load_runtime_cli
_load_runtime_cli()
from runtime.server import ServerConfig, make_server
srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True,
    workspace=Path(sys.argv[1]), course_session=sys.argv[2], ui_dir=Path(sys.argv[3])))
worker = threading.Thread(target=srv.serve_forever, daemon=True)
worker.start()
base = "http://127.0.0.1:" + str(srv.server_address[1])
step("server listening " + base)
try:
    with urlopen(base + "/", timeout=5) as response:
        html = response.read().decode()
        assert response.status == 200 and '<div id="root">' in html
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
    assert assets, "bundled index must reference compiled assets"
    step("index served")
    for asset in assets:
        with urlopen(base + asset, timeout=5) as response:
            assert response.status == 200 and response.read()
    step("assets served")
    with urlopen(base + "/api/course/state", timeout=5) as response:
        state = json.load(response)
        assert state["session_id"] == sys.argv[2]
        assert state["generation_readiness"]["ready"] is False
        assert state["roadmap"]["approved"] is False
    step("state served")
    try:
        urlopen(Request(base + "/api/course/resume", data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://untrusted.invalid"}), timeout=5)
        raise AssertionError("cross-origin write was accepted")
    except HTTPError as response:
        assert response.code in {401, 403}, "cross-origin writes must fail authentication/authorization"
        response.close()
    step("cross-origin write refused")
    print(json.dumps({"status": "passed", "port": srv.server_address[1],
        "checks": ["optional neutral intake preview assets", "installed blocked generation state", "cross-origin write refusal"]}))
finally:
    srv.shutdown()
    srv.server_close()
    worker.join(timeout=5)
    step("server stopped")
'''
        try:
            server_result = subprocess.run([sys.executable, "-c", server_smoke, str(root), sid, str(ui)],
                                          cwd=root, env=env, text=True, capture_output=True, timeout=120,
                                          check=False)
        except subprocess.TimeoutExpired as exc:   # keep the per-step trace for diagnosis
            raise RuntimeError(f"installed HTTP smoke timed out; stderr trace: {exc.stderr!r} stdout: {exc.stdout!r}")
        if server_result.returncode:
            raise RuntimeError("installed HTTP smoke failed: " + server_result.stdout + server_result.stderr)
        server_report = json.loads(server_result.stdout)
        assert run("course", "diagnostic-next", "--session", sid)["action"] == "performance_probe"
        probe = root / "diagnostic-probe.json"
        response = root / "diagnostic-response.txt"
        judgment = root / "diagnostic-judgment.json"
        probe.write_text(json.dumps({"id": "smoke-dx-first", "node_id": "logic", "kind": "explanation",
            "prompt": "Give a counterexample to: every animal can fly.",
            "criteria": ["Use a relevant example that tests the universal claim."]}), encoding="utf-8")
        run("course", "diagnostic-probe", "--session", sid, "--file", str(probe))
        response.write_text("A dog is an animal and cannot fly.", encoding="utf-8")
        run("course", "diagnostic-answer", "--session", sid, "--response-file", str(response))
        assert run("course", "diagnostic-next", "--session", sid)["action"] == "await_judgment"
        judgment.write_text(json.dumps({"outcome": "correct", "assessor": "human", "confidence": "medium",
            "rationale": "The learner gives a relevant counterexample to the universal claim."}), encoding="utf-8")
        judge_args = ("course", "diagnostic-judge", "--session", sid, "--file", str(judgment),
                      "--idempotency-key", "smoke-judgment-first")
        run(*judge_args)
        diagnostic = run("course", "diagnostic-next", "--session", sid)
        assert diagnostic["action"] == "fresh_transfer_probe" and diagnostic["provisional"] is True
        assert diagnostic["calibrated_probability"] is None
        # An idempotent judgment replay cannot invent a second demonstration.
        run(*judge_args)
        assert run("course", "diagnostic-next", "--session", sid)["observations"] == 1
        probe.write_text(json.dumps({"id": "smoke-dx-second", "node_id": "logic", "kind": "scenario",
            "prompt": "A shop claims every key it sells opens the same door. How would you test that claim?",
            "criteria": ["Describe a concrete test with a potentially disconfirming result."]}), encoding="utf-8")
        run("course", "diagnostic-probe", "--session", sid, "--file", str(probe))
        response.write_text("Test several different keys and look for one that does not open the door.", encoding="utf-8")
        run("course", "diagnostic-answer", "--session", sid, "--response-file", str(response))
        judgment.write_text(json.dumps({"outcome": "correct", "assessor": "human", "confidence": "medium",
            "rationale": "The response describes a relevant attempt to disconfirm the universal product claim."}), encoding="utf-8")
        run("course", "diagnostic-judge", "--session", sid, "--file", str(judgment))
        diagnostic = run("course", "diagnostic-next", "--session", sid)
        assert diagnostic["action"] == "ready_to_plan" and diagnostic["observations"] == 2
        after_diagnostic = run("course", "state", "--session", sid)
        assert after_diagnostic["generation_readiness"]["diagnostic_ready"] is True
        assert after_diagnostic["generation_readiness"]["ready"] is False
        assert {"goal_clarification_required", "sources_required"} <= set(after_diagnostic["generation_readiness"]["blockers"])
        assert not (session_dir / "workspace").exists()
        invalid_recovery = root / "recovery.json"
        invalid_recovery.write_text(json.dumps({"learner_agreed": False}), encoding="utf-8")
        run("course", "recovery", "--session", sid, "--node", "logic", "--file", str(invalid_recovery),
            error="recovery_required")
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
        assert not (destination / ".learning" / "sessions" / sid / "workspace").exists()
        restored_diagnostic = run("--workspace", str(destination), "course", "diagnostic-next", "--session", sid)
        assert restored_diagnostic == diagnostic
        report = {"status": "passed", "package": "adaptive-learning-os-runtime",
                  "version": importlib.metadata.version("adaptive-learning-os-runtime"),
                  "python": sys.version.split()[0], "package_location": location,
                  "entrypoint": str(entrypoint), "assets": assets,
                  "working_directory_outside_checkout": str(root), "checks": checks,
                  "http_smoke": server_report,
                  "state_only_start": True, "unapproved_course_serve_refused": True,
                  "limits": ["synthetic fixture; no NotebookLM request or learning-efficacy measurement",
                             "recovery refusal checked; agreed recovery lifecycle covered by runtime suite"]}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

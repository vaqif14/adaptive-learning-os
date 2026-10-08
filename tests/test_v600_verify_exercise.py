"""Multi-language verify-exercise: records strong evidence; advances the driver.

Uses Python through the execution backend (always available on the host) to prove
the path end-to-end; the same path serves Kotlin/Go/Java where a toolchain exists.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402

ROADMAP = {
    "sources": ["https://roadmap.sh/python"],
    "nodes": [
        {"id": "n1", "title": "Node 1", "task": "x", "requires": []},
        {"id": "n2", "title": "Node 2", "task": "x", "requires": ["n1"]},
    ],
}


def _start(ws):
    k = SessionKernel(ws)
    s = k.start("subject", LearningContract(goal="g", goal_mode="learn",
                                            stated_background="some basics"), mode="practice")
    return k, s["session_id"]


def test_checked_pass_is_strong_and_masters(tmp_path):
    k, sid = _start(tmp_path)
    res = k.verify_exercise(sid, "python", "print(2 + 3)", capability="n1",
                            expected_stdout="5", independence="unassisted")
    assert res.get("available") and res.get("ran") and res.get("passed")
    proj = json.loads((tmp_path / ".learning" / "sessions" / sid / "learner-evidence.json").read_text())
    cap = proj["demonstrated_capabilities"]["n1"]
    assert cap["strong_unassisted_successes"] >= 1  # a real verified pass counts


def test_unchecked_run_is_not_strong(tmp_path):
    k, sid = _start(tmp_path)
    # No expected_stdout -> nothing verified correctness -> observation, not evidence.
    k.verify_exercise(sid, "python", "print(123)", capability="n1", independence="unassisted")
    proj = json.loads((tmp_path / ".learning" / "sessions" / sid / "learner-evidence.json").read_text())
    cap = proj["demonstrated_capabilities"].get("n1", {})
    assert cap.get("strong_unassisted_successes", 0) == 0


def test_failed_check_is_not_strong(tmp_path):
    k, sid = _start(tmp_path)
    k.verify_exercise(sid, "python", "print(99)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    proj = json.loads((tmp_path / ".learning" / "sessions" / sid / "learner-evidence.json").read_text())
    cap = proj["demonstrated_capabilities"].get("n1", {})
    assert cap.get("strong_unassisted_successes", 0) == 0
    assert cap.get("last_outcome") == "incorrect"


def test_set_roadmap_then_verify_advances_driver(tmp_path):
    k, sid = _start(tmp_path)
    k.set_roadmap(sid, ROADMAP)
    assert k.next_action(sid)["node"] == "n1"
    k.verify_exercise(sid, "python", "print(2 + 3)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    nxt = k.next_action(sid)
    assert nxt["action"] in {"teach", "verify"}
    assert nxt["node"] == "n2"

"""Learning-outcome metrics for an efficacy pilot (computed from the real ledger)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.efficacy import summarize, learning_curve, cohort  # noqa: E402

ROADMAP = {"sources": ["https://roadmap.sh/python"],
           "nodes": [{"id": "n1", "title": "N1", "task": "x", "requires": []}]}


def _session(ws):
    k = SessionKernel(ws)
    s = k.start("python", LearningContract(goal="g", goal_mode="learn",
                                           stated_background="basics"), mode="practice")
    sid = s["session_id"]
    k.set_roadmap(sid, ROADMAP)
    return k, sid


def test_strong_pass_shows_in_metrics(tmp_path):
    k, sid = _session(tmp_path)
    k.verify_exercise(sid, "python", "print(2 + 3)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    recs = k.ledger(sid).verified_records()
    s = summarize(recs)
    assert s["first_trials"] >= 1
    assert s["capabilities_reaching_strong"] == 1
    assert s["independent_success_rate"] == 1.0
    assert "control group" in s["caveat"].lower()


def test_failed_attempt_lowers_accuracy(tmp_path):
    k, sid = _session(tmp_path)
    k.verify_exercise(sid, "python", "print(99)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    s = summarize(k.ledger(sid).verified_records())
    assert s["capabilities_reaching_strong"] == 0
    assert s["accuracy_first_trial"] == 0.0


def test_learning_curve_is_ordered(tmp_path):
    k, sid = _session(tmp_path)
    k.verify_exercise(sid, "python", "print(5)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    curve = learning_curve(k.ledger(sid).verified_records())
    assert curve and curve[0]["order"] == 0
    assert curve[0]["strong"] is True


def test_cohort_aggregates(tmp_path):
    k, sid = _session(tmp_path)
    k.verify_exercise(sid, "python", "print(5)", capability="n1",
                      expected_stdout="5", independence="unassisted")
    c = cohort({sid: k.ledger(sid).verified_records()})
    assert c["learners"] == 1
    assert c["mean_independent_success_rate"] == 1.0
    assert "controlled" in c["caveat"].lower()

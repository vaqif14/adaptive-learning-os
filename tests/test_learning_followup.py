"""Audit regressions: cohort isolation, evidence boundaries and actionable states."""
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/adaptive-learn"))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.ledger import EventLedger
from runtime.projection import counts_as_strong
from runtime.mastery import bkt_step, BKTParams
from runtime.mastery import observations_from_projection_caps
from runtime.scheduling import schedule
from runtime.learner_model import aggregate_learner_state
from runtime.diagnostician import analyze
from runtime.workspace_check import check_module

PLAN = {"nodes": [{"id": "cap", "title": "Capability", "task": "Demonstrate", "requires": []}]}


def start(root, learner="A", topic="Python"):
    k = SessionKernel(root)
    sid = k.start(topic, LearningContract(goal="learn", stated_background="beginner"), learner_id=learner, roadmap=PLAN)["session_id"]
    return k, sid


def evidence(k, sid, outcome="correct", **extra):
    payload = dict(capability_id="cap", outcome=outcome, strength="strong", independence="unassisted",
                   scope="near_transfer", evidence_format="artifact_execution", correctness_checked=True,
                   mastery_eligible=True, support_provenance={})
    payload.update(extra)
    return k.ledger(sid).append("evidence", payload, {"source": "python_verifier"})


@pytest.mark.parametrize("tail", ["null\n", "[]\n", '"text"\n', '{"partial":'])
def test_malformed_ledger_is_reported_and_cannot_accept_new_results(tmp_path, tail):
    ledger = EventLedger(tmp_path / "ledger.jsonl", "s")
    ledger.append("event", {})
    with ledger.path.open("a") as f:
        f.write(tail)
    before = ledger.path.read_bytes()
    assert not ledger.integrity()["chain_ok"]
    assert len(ledger.verified_records()) == 1
    with pytest.raises(ValueError):
        ledger.append("event", {})
    assert ledger.path.read_bytes() == before


def test_foreign_session_chain_is_not_trusted(tmp_path):
    ledger = EventLedger(tmp_path / "ledger.jsonl", "A")
    ledger.append("event", {})
    other = EventLedger(ledger.path, "B")
    assert not other.verified_records()
    assert not other.integrity()["chain_ok"]


def test_future_evidence_cannot_advance_or_enter_learner_state(tmp_path):
    k, sid = start(tmp_path)
    with patch("runtime.ledger.now_iso", return_value=(datetime.now(timezone.utc)+timedelta(days=10)).isoformat()):
        evidence(k, sid)
    assert k.next_action(sid)["action"] == "teach"
    assert not aggregate_learner_state(k.sessions_dir, "A")["capabilities"]


@pytest.mark.parametrize("fmt", ["unknown", "invented", "recognition_mcq", "plan_output", "ai_generated_output"])
def test_readers_enforce_format_ceiling(fmt):
    assert not counts_as_strong(dict(outcome="correct", strength="strong", independence="unassisted",
        scope="near_transfer", evidence_format=fmt, correctness_checked=True, mastery_eligible=True))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_scores_rejected(value):
    with pytest.raises(ValueError):
        bkt_step(.2, value, BKTParams())
    with pytest.raises(ValueError):
        schedule("cap", [{"correct": value, "timestamp": datetime.now(timezone.utc).isoformat()}])


def test_cross_learner_aggregation_requires_selection(tmp_path):
    k, a = start(tmp_path, "A")
    _, b = start(tmp_path, "B")
    evidence(k, a)
    evidence(k, b)
    for reader in (aggregate_learner_state, analyze):
        with pytest.raises(ValueError, match="learner"):
            reader(k.sessions_dir)
    assert aggregate_learner_state(k.sessions_dir, "A")["sessions_aggregated"] == 1


def test_same_node_id_in_different_topics_needs_topic_selection(tmp_path):
    k, a = start(tmp_path, topic="Python")
    _, b = start(tmp_path, topic="History")
    evidence(k, a)
    evidence(k, b, "incorrect")
    with pytest.raises(ValueError, match="topic"):
        aggregate_learner_state(k.sessions_dir, "A")
    selected = aggregate_learner_state(k.sessions_dir, "A", topic="Python")
    assert selected["sessions_aggregated"] == 1
    assert not selected["uncertainties"]


def test_recurring_failure_after_success_is_detected(tmp_path):
    k, first = start(tmp_path)
    evidence(k, first)
    for _ in range(2):
        _, sid = start(tmp_path)
        evidence(k, sid, "incorrect")
    result = analyze(k.sessions_dir, "A")
    assert result["persistent_failures"][0]["capability"] == "cap"
    assert result["persistent_failures"][0]["failures"] == 2


def test_inspect_rebuilds_a_stale_projection(tmp_path):
    k, sid = start(tmp_path)
    evidence(k, sid)
    assert k.inspect(sid)["learner_evidence"]["demonstrated_capabilities"]["cap"]["evidence_count"] == 1


def test_listener_requests_assessment_instead_of_reteaching(tmp_path):
    k, sid = start(tmp_path)
    k.ledger(sid).append("observation", {"capability_id": "cap", "verification": "term_coverage",
        "outcome": "unknown"}, {"source": "listener"})
    assert k.next_action(sid)["action"] == "request_assessment"


def test_unrun_workspace_check_is_not_a_learner_failure(tmp_path):
    k, sid = start(tmp_path)
    mod = k.dir(sid) / "workspace/modules/cap"
    (mod / "checks/check.json").write_text(json.dumps({"language": "python", "expect_stdout": "42"}))
    (mod / "submission/main.py").write_text("print(42)")
    with patch("runtime.workspace_check.run_exercise", return_value={"available": True, "ran": False, "passed": False}):
        result = check_module(k.dir(sid), "cap")
    assert result["outcome"] == "unknown" and result["evidence"] is None


def test_renaming_same_artifact_does_not_create_learning_trials(tmp_path):
    k, sid = start(tmp_path)
    for i in range(4):
        k.verify_python(sid, "print(42)", expected_stdout="42", capability="cap",
            independence="unassisted", scope="near_transfer" if i else "independent_reproduction",
            attempt_id=f"renamed-{i}")
    assert len(observations_from_projection_caps(k.ledger(sid).verified_records(), "cap")) == 1
    assert k.rebuild_projection(sid)["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"] == 1
    assert k.next_action(sid)["action"] != "complete"


def test_changing_task_name_cannot_erase_artifact_support(tmp_path):
    k, sid = start(tmp_path)
    k.verify_python(sid, "print(42)", expected_stdout="42", capability="cap", independence="assisted", attempt_id="old")
    k.verify_python(sid, "print(42)", expected_stdout="42", capability="cap", independence="unassisted",
                    scope="near_transfer", attempt_id="renamed")
    assert k.rebuild_projection(sid)["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"] == 0


def test_delayed_review_returns_a_resumption_time(tmp_path):
    k, sid = start(tmp_path)
    k.verify_python(sid, "print(42)", expected_stdout="42", capability="cap", independence="unassisted",
                    scope="independent_reproduction", attempt_id="first")
    k.verify_python(sid, "print(43)", expected_stdout="43", capability="cap", independence="unassisted",
                    scope="far_transfer", attempt_id="fresh")
    decision = k.next_action(sid)
    assert decision["action"] == "wait_for_review"
    assert datetime.fromisoformat(decision["resume_at"]) > datetime.now(timezone.utc)


def test_workspace_rejects_escaped_submission_symlink(tmp_path):
    k, sid = start(tmp_path)
    mod = k.dir(sid) / "workspace/modules/cap"
    outside = tmp_path / "outside.txt"
    outside.write_text("private material")
    (mod / "checks/check.json").write_text(json.dumps({"language": "python", "expect_stdout": "42"}))
    (mod / "submission/main.py").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        check_module(k.dir(sid), "cap")


def test_failure_persistence_counts_only_failed_sessions(tmp_path):
    k, first = start(tmp_path)
    evidence(k, first, "unknown")
    _, sid = start(tmp_path)
    evidence(k, sid, "incorrect")
    evidence(k, sid, "incorrect")
    assert not analyze(k.sessions_dir, "A")["persistent_failures"]

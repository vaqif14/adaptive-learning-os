"""Behavioral regressions from the learning-mechanism audit."""
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "scripts"))
import alearn
from runtime.contracts import LearningContract
from runtime.evidence_bridge import apply_evidence_to_context
from runtime.listener import grade_explanation
from runtime.mastery import estimate
from runtime.mastery import observations_from_projection_caps, BKTParams
from runtime.policy import TeachingContext, choose_move
from runtime.projection import counts_as_strong
from runtime.scheduling import schedule
from runtime.session import SessionKernel
from runtime.workspace_check import check_module

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PLAN = {"nodes": [{"id": "cache", "title": "Caching", "task": "Demonstrate caching", "requires": []}]}


def start(tmp_path):
    k = SessionKernel(tmp_path)
    s = k.start("Python caching", LearningContract(goal="Learn caching", stated_background="beginner"), roadmap=PLAN)
    return k, s["session_id"]


def check(k, sid, correct=True, **kwargs):
    return k.verify_python(sid, "print(42)", capability="cache", expected_stdout="42" if correct else "43",
                           independence="unassisted", scope="independent_reproduction", **kwargs)


def test_negation_and_word_lists_cannot_prove_understanding(tmp_path):
    source = "Caching does improve latency by storing results."
    for answer in ("Caching does not improve latency by storing results.", "caching improve latency storing results"):
        g = grade_explanation(answer, source)
        assert g["outcome"] == "unknown"
        assert g["coverage"] == 1.0
    k, sid = start(tmp_path)
    (tmp_path / "source.txt").write_text(source)
    (tmp_path / "answer.txt").write_text(source)
    with redirect_stdout(io.StringIO()):
        alearn.main(["--workspace", str(tmp_path), "listener-check", "--session", sid,
                    "--capability", "cache", "--source-file", str(tmp_path / "source.txt"),
                    "--explanation-file", str(tmp_path / "answer.txt"), "--independence", "unassisted"])
    assert not any(r["record_type"] == "evidence" for r in k.ledger(sid).verified_records())
    assert k.next_action(sid)["action"] != "complete"


def test_legacy_listener_is_not_strong():
    e = dict(outcome="correct", independence="unassisted", strength="strong", scope="near_transfer",
             mastery_eligible=True, correctness_checked=True)
    assert not counts_as_strong(e, {"source": "listener"})


def test_legacy_listener_cannot_create_review_obligation(tmp_path):
    k, sid = start(tmp_path)
    k.ledger(sid).append("evidence", dict(evidence_id="legacy", capability_id="cache",
        outcome="correct", independence="unassisted", strength="strong", scope="near_transfer",
        mastery_eligible=True, correctness_checked=True), {"source": "listener"})
    assert k.next_action(sid)["action"] == "teach"
    out = io.StringIO()
    with redirect_stdout(out):
        alearn.main(["--workspace", str(tmp_path), "review-due", "--session", sid])
    assert json.loads(out.getvalue())["review_queue"] == []


def test_one_success_allows_progress_but_not_course_completion(tmp_path):
    k, sid = start(tmp_path)
    check(k, sid)
    assert k.next_action(sid)["action"] == "consolidate"


def test_latest_failure_reopens_a_passed_node(tmp_path):
    k, sid = start(tmp_path)
    check(k, sid)
    check(k, sid, False)
    assert k.next_action(sid)["action"] == "feedback"


def test_three_failures_change_approach(tmp_path):
    k, sid = start(tmp_path)
    for _ in range(3):
        check(k, sid, False)
    assert k.next_action(sid)["action"] == "change_approach"


def test_policy_uses_failure_streak_from_evidence(tmp_path):
    k, sid = start(tmp_path)
    for _ in range(3):
        check(k, sid, False)
    ctx, _ = apply_evidence_to_context(TeachingContext(mode="practice", route="teach"), k.rebuild_projection(sid), "cache")
    assert choose_move(ctx).move == "change_approach"


def test_placeholder_is_not_a_researched_roadmap(tmp_path):
    k = SessionKernel(tmp_path)
    sid = k.start("x", LearningContract(goal="x", stated_background="beginner"))["session_id"]
    assert k.next_action(sid)["action"] == "build_roadmap"


def test_workspace_does_not_assume_independence(tmp_path):
    k, sid = start(tmp_path)
    mod = k.dir(sid) / "workspace/modules/cache"
    (mod / "checks/check.json").write_text(json.dumps({"language": "python", "expect_stdout": "42"}))
    (mod / "submission/main.py").write_text("print(42)")
    e = check_module(k.dir(sid), "cache")["evidence"]
    assert e["independence"] == "unknown"
    assert not counts_as_strong(e, {"source": "workspace_check"})


def test_support_survives_rechecking_same_attempt(tmp_path):
    k, sid = start(tmp_path)
    check(k, sid, attempt_id="task-1", support_provenance={"hints_count": 1})
    check(k, sid, attempt_id="task-1")
    cap = k.rebuild_projection(sid)["demonstrated_capabilities"]["cache"]
    assert cap["strong_unassisted_successes"] == 0
    k.verify_python(sid, "print(43)", expected_stdout="43", capability="cache",
                    independence="unassisted", scope="independent_reproduction", attempt_id="task-2")
    assert k.rebuild_projection(sid)["demonstrated_capabilities"]["cache"]["strong_unassisted_successes"] == 1


def test_assisted_label_and_unchecked_attempt_preserve_support(tmp_path):
    k, sid = start(tmp_path)
    k.verify_python(sid, "print(42)", capability="cache", independence="assisted",
                    scope="independent_reproduction", attempt_id="guided")
    check(k, sid, attempt_id="guided")
    assert k.rebuild_projection(sid)["demonstrated_capabilities"]["cache"]["strong_unassisted_successes"] == 0


def test_same_instant_repeats_do_not_inflate_review_interval():
    obs = [{"correct": True, "timestamp": NOW.isoformat()} for _ in range(10)]
    st = schedule("cache", obs, now=NOW)
    assert st.recalls == 1
    assert st.interval_days <= 2


def test_partial_cannot_postpone_an_overdue_review():
    obs = [{"correct": True, "timestamp": (NOW - timedelta(days=10)).isoformat()}]
    before = schedule("cache", obs, now=NOW)
    obs.append({"correct": .5, "timestamp": NOW.isoformat()})
    after = schedule("cache", obs, now=NOW)
    assert after.due
    assert after.next_review_at <= before.next_review_at


def test_mastery_decays_after_last_observation():
    obs = [{"correct": True, "qualified": True, "timestamp": NOW.isoformat(),
            "scope": "near_transfer" if i % 2 else "independent_reproduction"} for i in range(4)]
    fresh = estimate("cache", obs, now=NOW)
    old = estimate("cache", obs, now=NOW + timedelta(days=365))
    assert fresh.mastered
    assert not old.mastered
    assert old.p_known < fresh.p_known


def test_future_and_undated_observations_cannot_award_mastery():
    obs = [{"correct": True, "qualified": True, "timestamp": (NOW + timedelta(days=1)).isoformat(),
            "scope": "near_transfer" if i % 2 else "independent_reproduction"} for i in range(4)]
    assert not estimate("cache", obs, now=NOW).mastered
    for o in obs:
        o.pop("timestamp")
    assert not estimate("cache", obs, now=NOW).mastered


def test_delayed_scope_requires_elapsed_time():
    obs = [{"correct": True, "qualified": True, "timestamp": NOW.isoformat(),
            "scope": "delayed_independent_performance" if i % 2 else "independent_reproduction"} for i in range(4)]
    assert not estimate("cache", obs, now=NOW).has_transfer_or_delayed


def test_driver_rebuilds_instead_of_trusting_cached_success(tmp_path):
    k, sid = start(tmp_path)
    check(k, sid)
    path = k.dir(sid) / "ledger.jsonl"
    records = path.read_text().splitlines()
    last = json.loads(records[-1])
    last["payload"]["outcome"] = "incorrect"  # break the chain, leave projection stale
    records[-1] = json.dumps(last)
    path.write_text("\n".join(records) + "\n")
    assert k.next_action(sid)["action"] == "repair_ledger"


def test_rechecking_named_task_is_one_knowledge_observation(tmp_path):
    k, sid = start(tmp_path)
    for _ in range(4):
        check(k, sid, attempt_id="same-task")
    obs = observations_from_projection_caps(k.ledger(sid).verified_records(), "cache")
    assert len(obs) == 1
    cap = k.rebuild_projection(sid)["demonstrated_capabilities"]["cache"]
    assert cap["strong_unassisted_successes"] == 1
    check(k, sid, False, attempt_id="same-task")
    obs = observations_from_projection_caps(k.ledger(sid).verified_records(), "cache")
    assert len(obs) == 1 and obs[0]["correct"] == 0


def test_current_verified_transfer_can_complete_course(tmp_path):
    from unittest.mock import patch
    k, sid = start(tmp_path)
    now = datetime.now(timezone.utc)
    for i in range(4):
        stamp = (now - timedelta(days=3-i)).isoformat()
        with patch("runtime.ledger.now_iso", return_value=stamp):
            k.verify_python(sid, f"print({42+i})", expected_stdout=str(42+i), capability="cache",
                            independence="unassisted", attempt_id=f"new-task-{i}",
                            scope="near_transfer" if i else "independent_reproduction")
    assert k.next_action(sid)["action"] == "complete"


def test_cli_support_flags_reach_evidence(tmp_path):
    k, sid = start(tmp_path)
    with redirect_stdout(io.StringIO()):
        alearn.main(["--workspace", str(tmp_path), "verify-exercise", "--session", sid,
                    "--lang", "python", "--code", "print(42)", "--expected-stdout", "42",
                    "--capability", "cache", "--independence", "unassisted",
                    "--attempt-id", "guided-task", "--worked-example-shown"])
    e = k.ledger(sid).verified_records()[-1]["payload"]
    assert e["support_provenance"]["worked_example_shown"] is True
    assert not counts_as_strong(e)


def test_workspace_preserves_help_across_rechecks(tmp_path):
    k, sid = start(tmp_path)
    check(k, sid, attempt_id="guided", support_provenance={"ai_direct_answer_revealed": True})
    mod = k.dir(sid) / "workspace/modules/cache"
    (mod / "checks/check.json").write_text(json.dumps({"language": "python", "expect_stdout": "42",
        "attempt_id": "guided", "independence": "unassisted", "support_provenance": {}}))
    (mod / "submission/main.py").write_text("print(42)")
    e = check_module(k.dir(sid), "cache")["evidence"]
    assert e["support_provenance"]["ai_direct_answer_revealed"]
    assert not counts_as_strong(e)


@pytest.mark.parametrize("support", [{"hints_count": -1}, {"hints_count": "0"}, {"worked_example_shown": "false"}])
def test_invalid_support_rejected_before_execution(tmp_path, support):
    k, sid = start(tmp_path)
    with pytest.raises(ValueError):
        check(k, sid, support_provenance=support)
    assert len(k.ledger(sid).verified_records()) == 1


def test_spacing_is_chronological_and_resets_after_lapse():
    obs = [{"correct": True, "timestamp": (NOW-timedelta(days=d)).isoformat()} for d in (6, 4, 0)]
    assert schedule("c", obs, now=NOW) == schedule("c", obs[::-1], now=NOW)
    old = schedule("c", obs, now=NOW)
    obs.append({"correct": False, "timestamp": NOW.isoformat()})
    new = schedule("c", obs, now=NOW)
    assert new.interval_days < old.interval_days
    assert new.next_review_at < old.next_review_at


def test_unchecked_declaration_is_never_strong():
    assert not counts_as_strong(dict(outcome="correct", independence="unassisted", strength="strong",
                                    scope="near_transfer", mastery_eligible=True))


@pytest.mark.parametrize("value", [-1, 1.1, float("nan"), float("inf")])
def test_invalid_mastery_parameters_are_rejected(value):
    with pytest.raises(ValueError):
        BKTParams(p_forget_per_day=value)

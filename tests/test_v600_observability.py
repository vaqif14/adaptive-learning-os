"""SystemTrace observability: live traces in the ledger + aggregation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.governance import SystemTrace, summarize_traces  # noqa: E402


def _start(ws):
    k = SessionKernel(ws)
    session = k.start("python", LearningContract(goal="learn loops", goal_mode="learn"), mode="practice")
    return k, session["session_id"]


def test_record_trace_is_observable(tmp_path):
    k, sid = _start(tmp_path)
    k.record_trace(sid, SystemTrace(trace_id="t1", route="teach",
                                    pedagogical_intent="learn_concept", model_calls=2).to_dict())
    k.record_trace(sid, SystemTrace(trace_id="t2", route="teach",
                                    verifier_result="passed", tools_called=["verify-python"]).to_dict())
    report = k.observability(sid)
    assert report["traces"] == 2
    assert report["routes"]["teach"] == 2
    assert report["total_model_calls"] == 2
    assert report["verifier_results"]["passed"] == 1
    assert report["tools"]["verify-python"] == 1


def test_failures_and_fallbacks_counted(tmp_path):
    k, sid = _start(tmp_path)
    k.record_trace(sid, SystemTrace(trace_id="t1", route="deep",
                                    failure="verifier_timeout", fallback="review_required").to_dict())
    report = k.observability(sid)
    assert report["failures"] == 1
    assert report["failure_detail"] == ["verifier_timeout"]
    assert report["fallbacks"] == 1


def test_empty_session_has_zero_traces(tmp_path):
    k, sid = _start(tmp_path)
    report = k.observability(sid)
    assert report["traces"] == 0
    assert report["routes"] == {}


def test_summarize_traces_pure_function():
    traces = [
        {"route": "teach", "model_calls": 1, "input_tokens": 100, "output_tokens": 50},
        {"route": "direct", "model_calls": 2, "input_tokens": 20},
    ]
    r = summarize_traces(traces)
    assert r["traces"] == 2
    assert r["total_model_calls"] == 3
    assert r["total_input_tokens"] == 120
    assert r["total_output_tokens"] == 50
    assert r["total_latency_ms"] is None  # none provided -> stays None

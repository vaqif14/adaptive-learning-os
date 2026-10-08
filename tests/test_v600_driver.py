"""Agentic driver: deterministic next-action state machine + session wiring."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.driver import next_action  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402

NODES = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]


def base(**over):
    kw = dict(intake_complete=True, intake_next=None, has_roadmap=True,
              nodes=NODES, node_status={}, cards_due=0, reviews_due=0)
    kw.update(over)
    return next_action(**kw)


def test_intake_first():
    a = base(intake_complete=False, intake_next={"field": "goal"})
    assert a["action"] == "intake"


def test_roadmap_before_nodes():
    a = base(has_roadmap=False)
    assert a["action"] == "build_roadmap"


def test_reviews_before_new_teaching():
    a = base(reviews_due=2, node_status={"a": {"evidence_count": 0}})
    assert a["action"] == "review_capability"


def test_cards_before_new_teaching():
    a = base(cards_due=3, node_status={"a": {"evidence_count": 0}})
    assert a["action"] == "review_cards"


def test_teach_unattempted_node():
    a = base(node_status={"a": {"mastered": False, "evidence_count": 0}})
    assert a["action"] == "teach" and a["node"] == "a"


def test_verify_attempted_not_mastered():
    a = base(node_status={"a": {"mastered": False, "evidence_count": 2}})
    assert a["action"] == "verify" and a["node"] == "a"


def test_advances_past_mastered_node():
    a = base(node_status={"a": {"mastered": True, "evidence_count": 3},
                          "b": {"mastered": False, "evidence_count": 0}})
    assert a["action"] == "teach" and a["node"] == "b"


def test_complete_when_all_mastered():
    a = base(node_status={"a": {"mastered": True, "evidence_count": 1},
                          "b": {"mastered": True, "evidence_count": 1}})
    assert a["action"] == "complete"


# --- session wiring --------------------------------------------------------

def test_session_next_action_needs_level(tmp_path):
    k = SessionKernel(tmp_path)
    s = k.start("subject", LearningContract(goal="learn x", goal_mode="learn"), mode="practice")
    a = k.next_action(s["session_id"])
    assert a["action"] == "intake"


def test_session_next_action_researches_placeholder_after_intake(tmp_path):
    k = SessionKernel(tmp_path)
    s = k.start("subject", LearningContract(goal="learn x", goal_mode="learn",
                                            stated_background="heç toxunmamışam"), mode="practice")
    a = k.next_action(s["session_id"])
    assert a["action"] == "build_roadmap"

"""C1/C2 fixes: non-code nodes advance via attestation; reviews never deadlock.

- A node with no runtime verifier can be advanced by a qualified assessor
  (`attest`) — counts for progress, NEVER for mastery.
- Spaced review is scheduled only for roadmap nodes that earned a strong success,
  so an orphan capability or an unproven node never parks the driver on
  `review_capability`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.projection import counts_as_strong  # noqa: E402

ROADMAP = {"sources": ["https://roadmap.sh/frontend"],
           "nodes": [{"id": "n1", "title": "Concept node", "task": "x", "requires": []}]}


def _start(ws):
    k = SessionKernel(ws)
    s = k.start("history of jazz", LearningContract(goal="g", goal_mode="learn",
                                                    stated_background="some basics"), mode="practice")
    return k, s["session_id"]


def test_attestation_advances_non_code_node_without_mastery(tmp_path):
    k, sid = _start(tmp_path)
    k.set_roadmap(sid, ROADMAP)
    assert k.next_action(sid)["node"] == "n1"           # teach first
    res = k.attest(sid, "n1", outcome="pass", assessor="teacher-1")
    assert res["counts_as_mastery"] is False
    nxt = k.next_action(sid)
    # single node, attested -> course completes (not stuck on verify/consolidate)
    assert nxt["action"] == "complete"


def test_attestation_is_never_strong(tmp_path):
    k, sid = _start(tmp_path)
    k.set_roadmap(sid, ROADMAP)
    k.attest(sid, "n1", outcome="pass")
    proj = k.inspect(sid)["learner_evidence"]
    cap = proj["demonstrated_capabilities"]["n1"]
    assert cap["strong_unassisted_successes"] == 0      # advanced, not mastered


def test_attestation_record_fails_counts_as_strong():
    # The attestation evidence shape must never satisfy the strong predicate.
    e = {"outcome": "correct", "independence": "assessed", "strength": "medium",
         "scope": "assessor_attested", "evidence_format": "assessor_judgment",
         "mastery_eligible": False, "qualified_judgment": True}
    assert counts_as_strong(e, {"source": "assessor_attested"}) is False


def test_failed_attestation_does_not_advance(tmp_path):
    k, sid = _start(tmp_path)
    k.set_roadmap(sid, ROADMAP)
    k.attest(sid, "n1", outcome="fail")
    nxt = k.next_action(sid)
    assert nxt["action"] in {"teach", "verify", "feedback", "change_approach"}
    assert nxt.get("node") == "n1"                      # still on n1, not complete


def test_orphan_capability_does_not_block_teaching(tmp_path):
    k, sid = _start(tmp_path)
    k.set_roadmap(sid, ROADMAP)
    # Record a (verified, strong-looking) result on a capability that is NOT a node.
    k.verify_exercise(sid, "python", "print(1)", capability="orphan_not_a_node",
                      expected_stdout="1", independence="unassisted")
    nxt = k.next_action(sid)
    # Driver must still move the real node forward, not loop on the orphan review.
    assert nxt["action"] != "review_capability"
    assert nxt["node"] == "n1"

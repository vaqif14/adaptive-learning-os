"""Subject-neutral memory cards + session store + spaced scheduling reuse."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime import memory_cards as mc  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402


def test_new_card_validates():
    c = mc.new_card("n1", "Front?", "Back.")
    assert c.id.startswith("card_")
    assert c.node_id == "n1" and c.front == "Front?" and c.back == "Back."
    with pytest.raises(ValueError):
        mc.new_card("n1", "   ")
    with pytest.raises(ValueError):
        mc.new_card("", "Front?")


def test_scaffold_from_node_makes_main_plus_pitfalls():
    node = {"id": "nullsafety", "title": "Null safety", "lesson": "Use ?. and ?:",
            "pitfalls": ["using !!", "ignoring nullable"]}
    cards = mc.scaffold_from_node(node)
    assert len(cards) == 3  # 1 main + 2 pitfalls
    assert cards[0].back == "Use ?. and ?:"
    assert any("!!" in c.front for c in cards)


def test_due_uses_half_life_schedule():
    # A brand-new card (no reviews) is due now.
    c = mc.new_card("n1", "Q").to_dict()
    due = mc.due_cards([c])
    assert len(due) == 1 and due[0]["card_id"] == c["id"]


# --- session store ---------------------------------------------------------

def _start(ws):
    k = SessionKernel(ws)
    s = k.start("any subject", LearningContract(goal="learn x", goal_mode="learn"), mode="practice")
    return k, s["session_id"]


def test_add_and_review_card_round_trip(tmp_path):
    k, sid = _start(tmp_path)
    c = k.add_card(sid, "n1", "Explain closures", "A closure captures its scope.")
    assert c["node_id"] == "n1"
    st = k.review_card(sid, c["id"], correct=True)
    assert st["card_id"] == c["id"]
    assert st["recalls"] == 1  # one clean recall extends the interval
    stored = k._load_cards(sid)
    assert stored[0]["reviews"][0]["correct"] is True


def test_review_unknown_card_raises(tmp_path):
    k, sid = _start(tmp_path)
    with pytest.raises(FileNotFoundError):
        k.review_card(sid, "card_doesnotexist", correct=True)


def test_cards_due_reports_total_and_due(tmp_path):
    k, sid = _start(tmp_path)
    k.add_card(sid, "n1", "Q1")
    k.add_card(sid, "n1", "Q2")
    due = k.cards_due(sid)
    assert due["total"] == 2
    assert len(due["due"]) == 2  # unreviewed cards are due now

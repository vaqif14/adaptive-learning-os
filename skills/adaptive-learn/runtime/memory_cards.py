from __future__ import annotations

"""Subject-neutral memory cards (spaced-repetition flashcards).

The scheduler (``scheduling.py``) decides WHEN to review; this module is WHAT to
review. A card is a recall prompt (front) with its answer (back) tied to a roadmap
node, carrying its own review history. Due timing reuses the half-life scheduler,
so cards and capability review share one spacing model.

Deterministic and topic-agnostic: ``scaffold_from_node`` produces card stubs from a
node's structure (title + pitfalls) for any subject; the agent fills the back with
governed content. Card content is a study aid, never mastery evidence — only
verified performance counts for mastery (see evidence rules).
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

from .utils import new_id, now_iso
from .scheduling import schedule as _schedule


@dataclass
class Card:
    id: str
    node_id: str
    front: str
    back: str | None = None
    created_at: str = ""
    reviews: list[dict] = field(default_factory=list)  # [{correct: bool, timestamp: iso}]

    def to_dict(self):
        return asdict(self)


def new_card(node_id: str, front: str, back: str | None = None) -> Card:
    if not isinstance(node_id, str) or not node_id.strip():
        raise ValueError("card requires a node_id")
    if not isinstance(front, str) or not front.strip():
        raise ValueError("card requires a non-empty front")
    return Card(id=new_id("card"), node_id=node_id, front=front.strip(),
                back=(back.strip() if isinstance(back, str) and back.strip() else None),
                created_at=now_iso(), reviews=[])


def scaffold_from_node(node: dict) -> list[Card]:
    """Deterministic card stubs from a roadmap node (subject-neutral).

    One recall card for the node itself, plus one "what goes wrong" card per
    declared pitfall. Backs are filled from the node's lesson text when present;
    otherwise the agent supplies them. No topic logic — works for any subject.
    """
    nid = node.get("id")
    title = (node.get("title") or "").strip()
    if not nid or not title:
        raise ValueError("node needs id and title to scaffold cards")
    lesson = (node.get("lesson") or "").strip() or None
    cards = [new_card(nid, f"Explain in your own words: {title}", lesson)]
    for p in node.get("pitfalls", []) or []:
        if isinstance(p, str) and p.strip():
            cards.append(new_card(nid, f"What goes wrong here, and how do you avoid it? ({p.strip()})", None))
    return cards


def record_review(card: dict, correct: bool, *, timestamp: str | None = None) -> dict:
    card.setdefault("reviews", []).append({"correct": bool(correct), "timestamp": timestamp or now_iso()})
    return card


def card_state(card: dict, *, now: datetime | None = None) -> dict:
    """Due/interval state for one card via the shared half-life scheduler.

    A card with no reviews yet is due immediately (first study), after which the
    half-life scheduler takes over based on recall results.
    """
    reviews = card.get("reviews", [])
    if not reviews:
        now_dt = now or datetime.now(timezone.utc)
        st = {
            "capability": card["id"], "next_review_at": now_dt.isoformat(),
            "interval_days": 0.0, "recalls": 0, "lapses": 0, "due": True,
            "apply_by": None, "at_risk_48h": False, "new": True,
        }
    else:
        st = _schedule(card["id"], reviews, now=now).to_dict()
        st["new"] = False
    st["card_id"] = card["id"]
    st["node_id"] = card.get("node_id")
    st["front"] = card.get("front")
    return st


def due_cards(cards: list[dict], *, now: datetime | None = None) -> list[dict]:
    states = [card_state(c, now=now) for c in cards]
    due = [s for s in states if s.get("due")]
    due.sort(key=lambda s: s["next_review_at"])  # most overdue first
    return due

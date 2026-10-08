from __future__ import annotations

"""Agentic tutor driver — the OS as a deterministic state machine.

The host agent does not decide the pedagogy; it asks the OS "what now?" and the OS
returns the single next action, then the agent executes it (teach, probe, verify,
make/review cards) and records the result. Looping `next_action` until `complete`
is the whole agentic tutor. This keeps the loop deterministic and auditable while
the host explains and assesses work that needs a contextual rubric. Listener
coverage is diagnostic only. Subject-neutral: it reasons over node IDs and evidence.

Convention: a roadmap node's id IS its capability id, so mastery/evidence for a
node are looked up by that id. The agent must record evidence with
``capability = node_id`` (documented in SKILL.md).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from .evidence_history import parse_time


@dataclass
class NextAction:
    action: str
    detail: dict

    def to_dict(self):
        return {"action": self.action, **self.detail}


def next_action(
    *,
    intake_complete: bool,
    intake_next: dict | None,
    has_roadmap: bool,
    nodes: list[dict],                       # ordered [{id, title}]
    node_status: dict[str, dict],            # node_id -> {mastered: bool, evidence_count: int}
    cards_due: int = 0,
    reviews_due: int = 0,
    now: datetime | None = None,
) -> dict:
    """Return the single next step for the agent to execute (deterministic)."""
    now = now or datetime.now(timezone.utc)
    # 1. Can't teach before we know goal + level.
    if not intake_complete:
        return NextAction("intake", {
            "reason": "goal_and_level_required_before_teaching",
            "question": intake_next,
        }).to_dict()

    # 2. Need a source-grounded roadmap before any node work.
    if not has_roadmap or not nodes:
        return NextAction("build_roadmap", {
            "reason": "no_roadmap_yet",
            "instruction": (
                "Resolve the AUTHORITATIVE backbone first with `alearn roadmap-source --topic ...` "
                "(roadmap.sh for programming, expert research otherwise) — the path comes from the "
                "field's authority, NOT from the learner's uploads. Fetch it (Agent-Reach/web), adapt "
                "to goal/level into nodes/requires/pitfalls, then `alearn set-roadmap --session <id> --roadmap-file <file>`."
            ),
        }).to_dict()

    # Failures need feedback before another assessment or a retention drill.
    for n in nodes:
        st = node_status.get(n["id"], {})
        failures = st.get("consecutive_failures", 0)
        if failures:
            return NextAction("change_approach" if failures >= 3 else "feedback", {
                "node": n["id"], "title": n.get("title"),
                "reason": "repeated_failures_require_new_representation" if failures >= 3 else "correct_the_observed_error_before_retry",
                "attempts_without_progress": failures,
                "instruction": "Give specific feedback; change the example or revisit a prerequisite when stuck. Then use a fresh, smaller task and record all support.",
                "then": "verify",
            }).to_dict()

    # 3. Protect retention first: due spaced reviews and due cards beat new teaching.
    for n in nodes:
        if node_status.get(n["id"], {}).get("assessment_pending"):
            return NextAction("request_assessment", {
                "node": n["id"], "reason": "lexical_coverage_is_not_a_correctness_judgment",
                "instruction": "Assess the learner's reasoning against explicit criteria, contradictions and application. Record criterion scores with rubric-check; these are attributed judgments, not automatic mastery certification.",
            }).to_dict()
    if reviews_due > 0:
        return NextAction("review_capability", {
            "reason": "spaced_review_due_before_new_material",
            "due_count": reviews_due,
        }).to_dict()
    if cards_due > 0:
        return NextAction("review_cards", {
            "reason": "memory_cards_due",
            "due_count": cards_due,
        }).to_dict()

    # 4. Walk the roadmap in order; act on the first node not yet mastered.
    for n in nodes:
        nid = n.get("id")
        st = node_status.get(nid, {})
        if st.get("mastered") or st.get("ready_to_advance"):
            continue
        if st.get("evidence_count", 0) == 0:
            return NextAction("teach", {
                "node": nid, "title": n.get("title"),
                "reason": "node_not_yet_attempted",
                "then": "probe_for_evidence_with_capability_equal_to_node_id",
            }).to_dict()
        return NextAction("verify", {
            "node": nid, "title": n.get("title"),
            "reason": "attempted_but_not_yet_mastered",
            "instruction": "Use a fresh independent task recorded with capability=node_id. For code use verify-python or verify-exercise. For other work obtain a context-specific assessment; listener-check only reports lexical coverage and cannot certify correctness. Record support and do not claim mastery when a verifier is unavailable.",
        }).to_dict()

    # Progress through lessons is not course mastery. Seek remaining transfer /
    # retention evidence explicitly instead of claiming completion after one pass.
    for n in nodes:
        st = node_status.get(n["id"], {})
        if not st.get("mastered"):
            earliest = parse_time(st.get("review_not_before"))
            if st.get("evidence_target") == "delayed_independent_performance" and earliest and now < earliest:
                return NextAction("wait_for_review", {
                    "node": n["id"], "reason": "delayed_evidence_requires_elapsed_time",
                    "resume_at": earliest.isoformat(),
                    "instruction": "End this practice block and return at resume_at. Do not manufacture delayed evidence with immediate retests.",
                }).to_dict()
            return NextAction("consolidate", {
                "node": n["id"], "title": n.get("title"),
                "reason": "lessons_progressed_but_mastery_not_established",
                "evidence_target": st.get("evidence_target", "near_transfer"),
                "mastery": st.get("mastery"),
                "instruction": "Use a fresh transfer task, or schedule delayed practice at least 24 hours after the last attempt. Do not repeat the same check to manufacture mastery.",
            }).to_dict()

    # 5. Every node mastered.
    return NextAction("complete", {
        "reason": "all_nodes_mastered",
        "instruction": "Course goal met; keep only spaced review + cards running.",
    }).to_dict()

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ReviewContext:
    capability_id: str
    capability_kind: str
    known_failure_mode: str | None = None
    require_transfer: bool = False


@dataclass
class ReviewItem:
    capability_id: str
    representation: str
    intent: str
    evidence_target: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def choose_review_item(ctx: ReviewContext) -> ReviewItem:
    kind = ctx.capability_kind.lower().strip()
    if kind in {"terminology", "fact", "vocabulary", "definition"}:
        rep = "qa_card"
        intent = "retrieve_exact_association"
    elif kind in {"code", "architecture", "system", "debugging"}:
        rep = "debug_task" if ctx.known_failure_mode else "prediction_task"
        intent = "retrieve_mechanism_through_execution_reasoning"
    elif kind in {"theory", "concept", "causal_model"}:
        rep = "teach_back"
        intent = "reconstruct_explanation_without_source"
    elif kind in {"procedure", "workflow", "process"}:
        rep = "reconstruction_task"
        intent = "rebuild_procedure_from_memory"
    elif kind in {"judgment", "decision", "communication"}:
        rep = "scenario_decision"
        intent = "retrieve_and_apply_judgment_in_context"
    else:
        rep = "free_recall"
        intent = "retrieve_without_recognition_cues"
    target = "near_transfer" if ctx.require_transfer else "delayed_independent_performance"
    return ReviewItem(ctx.capability_id, rep, intent, target, [f"capability_kind:{kind or 'unknown'}"])

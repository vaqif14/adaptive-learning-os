from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class AnchorPlan:
    status: str
    reason: str
    probe_id: str | None = None
    prompt: str | None = None
    artifact: dict[str, Any] | None = None
    response_contract: dict[str, Any] | None = None
    verifier_id: str | None = None
    generation_contract: dict[str, Any] | None = None

    def to_dict(self):
        return asdict(self)


def plan_anchor(topic: str, mode: str | None, contract: dict, adapter_manifest: dict) -> AnchorPlan:
    mode = mode or "learn"
    if mode in {"quick_answer", "build_with_help"}:
        return AnchorPlan("skipped", f"interaction_mode={mode}_does_not_require_cold_start_anchor")

    if not contract.get("goal") and mode in {"learn", "practice", "assess", "review"}:
        return AnchorPlan("deferred", "goal_missing_anchor_must_be_goal_conditioned")

    anchor = adapter_manifest.get("anchor_probe") or {}
    topic_l = topic.lower()
    for candidate in anchor.get("candidates", []):
        keywords = [str(x).lower() for x in candidate.get("when_topic_contains", [])]
        if keywords and any(k in topic_l for k in keywords):
            return AnchorPlan(
                status="ready",
                reason="domain_adapter_supplied_high_information_anchor",
                probe_id=candidate.get("id"),
                prompt=candidate.get("prompt"),
                artifact=candidate.get("artifact"),
                response_contract=candidate.get("response_contract"),
                verifier_id=candidate.get("verifier_id"),
            )

    strategy = anchor.get("fallback_strategy", "goal_conditioned_runtime_generation")
    return AnchorPlan(
        status="generate",
        reason="no_validated_static_anchor_for_exact_goal",
        verifier_id=anchor.get("verifier_id"),
        generation_contract={
            "strategy": strategy,
            "goal": contract.get("goal"),
            "application_context": contract.get("application_context"),
            "constraints": anchor.get("constraints", [
                "one task only",
                "high information gain",
                "30-120 seconds expected effort",
                "no answer leakage",
                "prefer authentic or executable evidence",
                "do not infer stable ability from one result",
            ]),
        },
    )

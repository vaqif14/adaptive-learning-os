from __future__ import annotations

from dataclasses import dataclass, asdict, field


@dataclass
class AccessibilityContract:
    accommodations: list[str] = field(default_factory=list)
    learner_requested: bool = True

    def to_dict(self):
        return asdict(self)


def support_effect_on_independence(support_kind: str) -> dict:
    if support_kind == "accessibility":
        return {
            "changes_independence": False,
            "reason": "access_support_changes_access_not_target_cognition",
        }
    if support_kind == "pedagogical":
        return {
            "changes_independence": True,
            "reason": "pedagogical_support_may_reduce_independence_of_this_observation",
        }
    if support_kind == "cognitive_delegation":
        return {
            "changes_independence": True,
            "reason": "ai_performed_part_of_target_cognition",
        }
    if support_kind == "none":
        return {"changes_independence": False, "reason": "no_support"}
    raise ValueError(f"unknown support kind: {support_kind}")

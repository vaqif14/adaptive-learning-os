from __future__ import annotations

from dataclasses import dataclass, asdict


_SCOPE_ORDER = {
    "supported_completion": 0,
    "independent_reproduction": 1,
    "near_transfer": 2,
    "far_transfer": 3,
    "delayed_independent_performance": 4,
}


@dataclass
class SimulationFidelity:
    cognitive: str = "unknown"
    social: str = "unknown"
    physical: str = "unknown"
    temporal: str = "unknown"
    authentic_environment: bool = False


@dataclass
class SimulationEvidenceDecision:
    requested_scope: str
    maximum_scope: str
    normalized_scope: str
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def cap_simulation_scope(requested_scope: str, fidelity: SimulationFidelity) -> SimulationEvidenceDecision:
    if requested_scope not in _SCOPE_ORDER:
        raise ValueError(f"invalid scope: {requested_scope}")
    if fidelity.authentic_environment:
        maximum = "far_transfer"
        reasons = ["authentic_environment_present_but_delayed_durability_still_separate"]
    elif fidelity.cognitive == "high" and fidelity.social in {"high", "medium"}:
        maximum = "near_transfer"
        reasons = ["ai_simulation_can_support_near_transfer_not_real_world_mastery"]
    else:
        maximum = "independent_reproduction"
        reasons = ["low_or_unknown_simulation_fidelity_limits_transfer_claim"]
    normalized = requested_scope if _SCOPE_ORDER[requested_scope] <= _SCOPE_ORDER[maximum] else maximum
    return SimulationEvidenceDecision(requested_scope, maximum, normalized, reasons)

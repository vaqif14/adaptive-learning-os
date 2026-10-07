from __future__ import annotations

from dataclasses import dataclass

@dataclass
class DiagnosisContext:
    repeated_observable_error: bool = False
    root_cause_changes_intervention: bool = False
    candidate_causes: int = 0
    validated_domain_inventory_available: bool = False

def diagnostic_level(ctx: DiagnosisContext) -> int:
    if ctx.root_cause_changes_intervention and ctx.candidate_causes >= 2:
        return 2
    if ctx.repeated_observable_error:
        return 1
    return 0

def validate_hypotheses(hypotheses: list[dict]) -> list[str]:
    errors: list[str] = []
    required = {"hypothesis_id", "explanation", "status", "predicted_observation", "probe_action", "decision_impact"}
    for idx, h in enumerate(hypotheses):
        missing = required - set(h)
        if missing:
            errors.append(f"hypothesis[{idx}] missing {sorted(missing)}")
        if h.get("status") != "tentative":
            errors.append(f"hypothesis[{idx}] status must be tentative")
        if not str(h.get("predicted_observation", "")).strip():
            errors.append(f"hypothesis[{idx}] must predict an observable outcome")
        if not str(h.get("probe_action", "")).strip():
            errors.append(f"hypothesis[{idx}] must define a discriminating probe")
        if str(h.get("probe_action", "")).lower() in {"ask more", "observe more", "tbd"}:
            errors.append(f"hypothesis[{idx}] probe is not discriminating enough")
    return errors

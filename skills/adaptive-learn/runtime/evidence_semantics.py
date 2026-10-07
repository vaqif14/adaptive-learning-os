from __future__ import annotations

from dataclasses import dataclass


STRENGTH_ORDER = {"weak": 0, "medium": 1, "strong": 2}

# Format is not the evidence score. It only constrains the strongest defensible claim.
FORMAT_MAX_STRENGTH = {
    "recognition_mcq": "weak",
    "free_recall": "medium",
    "explanation": "strong",
    "step_execution": "strong",
    "artifact_execution": "strong",
    "artifact_performance": "strong",
    "scenario_performance": "strong",
    "oral_defense": "strong",
    "live_performance": "strong",
    "novel_unassisted_transfer": "strong",
    "compression_output": "weak",
    "plan_output": "weak",
    "ai_generated_output": "weak",
    "unknown": "strong",
}

NON_MASTERY_FORMATS = {"compression_output", "plan_output", "ai_generated_output"}


@dataclass(frozen=True)
class EvidenceSemantics:
    evidence_format: str
    requested_strength: str
    normalized_strength: str
    mastery_eligible: bool
    reasons: list[str]


def normalize_evidence_semantics(
    evidence_format: str,
    requested_strength: str,
    independence: str,
) -> EvidenceSemantics:
    if requested_strength not in STRENGTH_ORDER:
        raise ValueError(f"invalid strength: {requested_strength}")
    max_strength = FORMAT_MAX_STRENGTH.get(evidence_format, "strong")
    requested_rank = STRENGTH_ORDER[requested_strength]
    max_rank = STRENGTH_ORDER[max_strength]
    normalized = requested_strength if requested_rank <= max_rank else max_strength
    reasons: list[str] = []
    if normalized != requested_strength:
        reasons.append(f"format_ceiling:{evidence_format}->{max_strength}")
    mastery_eligible = evidence_format not in NON_MASTERY_FORMATS
    if evidence_format == "novel_unassisted_transfer" and independence != "unassisted":
        mastery_eligible = False
        reasons.append("novel_transfer_requires_unassisted_performance")
    if evidence_format in NON_MASTERY_FORMATS:
        reasons.append("generated_representation_or_planning_output_is_not_mastery_evidence")
    if evidence_format in {"artifact_performance", "scenario_performance", "oral_defense", "live_performance"}:
        reasons.append("performance_format_requires_context_specific_verifier_or_rubric")
    return EvidenceSemantics(
        evidence_format=evidence_format,
        requested_strength=requested_strength,
        normalized_strength=normalized,
        mastery_eligible=mastery_eligible,
        reasons=reasons,
    )

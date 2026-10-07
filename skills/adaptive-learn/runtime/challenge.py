from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ChallengeContext:
    capability_id: str
    fast_track_requested: bool = False
    learner_claims_existing_competence: bool = False
    authentic_task_available: bool = False
    deterministic_or_rubric_verifier_available: bool = False
    high_stakes: bool = False


@dataclass
class ChallengeDecision:
    use_challenge_gate: bool
    challenge_type: str | None
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def choose_challenge_gate(ctx: ChallengeContext) -> ChallengeDecision:
    if ctx.high_stakes:
        return ChallengeDecision(False, None, ["high_stakes_requires_domain_specific_validated_assessment"])
    if not (ctx.fast_track_requested or ctx.learner_claims_existing_competence):
        return ChallengeDecision(False, None, ["no_fast_track_signal"])
    if not ctx.authentic_task_available:
        return ChallengeDecision(False, None, ["no_authentic_challenge_available_use_anchor_instead"])
    challenge_type = "verified_authentic_task" if ctx.deterministic_or_rubric_verifier_available else "authentic_task_with_review"
    return ChallengeDecision(True, challenge_type, ["prove_it_first_avoids_redundant_instruction"])

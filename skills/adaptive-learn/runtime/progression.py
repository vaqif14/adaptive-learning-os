from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ProgressionContext:
    supported_completion: bool = False
    independent_reproduction: bool = False
    near_transfer: bool = False
    far_transfer: bool = False
    delayed_independent_performance: bool = False
    strong_independent_reasoning: bool = False
    learner_requested_challenge: bool = False
    recent_full_solution_seen: bool = False


@dataclass
class ProgressionDecision:
    next_scope: str
    skipped_scopes: list[str]
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def choose_next_scope(ctx: ProgressionContext) -> ProgressionDecision:
    if ctx.delayed_independent_performance:
        return ProgressionDecision("maintenance_or_new_frontier", [], ["durable_independent_evidence_already_present"])
    if ctx.far_transfer:
        return ProgressionDecision("delayed_independent_performance", [], ["far_transfer_verified_now_seek_delayed_evidence"])
    if ctx.near_transfer:
        return ProgressionDecision("far_transfer", [], ["near_transfer_verified"])
    if ctx.independent_reproduction:
        return ProgressionDecision("near_transfer", [], ["independent_reproduction_verified"])

    # Skip rote reproduction when there is strong independent reasoning and no answer exposure.
    if ctx.strong_independent_reasoning and not ctx.recent_full_solution_seen:
        return ProgressionDecision(
            "near_transfer",
            ["independent_reproduction"],
            ["strong_independent_reasoning_supports_adaptive_skip", "avoid_expertise_reversal_or_redundant_repetition"],
        )

    if ctx.supported_completion or ctx.recent_full_solution_seen:
        return ProgressionDecision(
            "independent_reproduction",
            [],
            ["supported_success_does_not_establish_independence"],
        )

    if ctx.learner_requested_challenge:
        return ProgressionDecision(
            "near_transfer",
            ["supported_completion"],
            ["challenge_requested_and_no_support_dependency_recorded"],
        )

    return ProgressionDecision("independent_reproduction", [], ["default_first_independent_evidence_target"])

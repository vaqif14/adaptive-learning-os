from __future__ import annotations

from dataclasses import dataclass, asdict, field


AI_ROLES = {
    "inform", "model", "probe", "scaffold", "critique", "verify", "delegate", "replace"
}
GOAL_MODES = {"learn", "perform_with_assistance", "deliver_artifact", "quick_reference"}
SUPPORT_KINDS = {"accessibility", "pedagogical", "cognitive_delegation", "none"}


@dataclass
class CognitiveWorkContract:
    target_capability: str
    protected_cognition: list[str] = field(default_factory=list)
    delegable_work: list[str] = field(default_factory=list)
    independence_required: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class DelegationContext:
    goal_mode: str
    ai_role: str
    requested_work: list[str] = field(default_factory=list)
    support_kind: str = "none"
    contract: CognitiveWorkContract | None = None


@dataclass
class DelegationDecision:
    allowed: bool
    action: str
    reasons: list[str]
    protected_overlap: list[str]

    def to_dict(self):
        return asdict(self)


def evaluate_delegation(ctx: DelegationContext) -> DelegationDecision:
    if ctx.goal_mode not in GOAL_MODES:
        raise ValueError(f"invalid goal_mode: {ctx.goal_mode}")
    if ctx.ai_role not in AI_ROLES:
        raise ValueError(f"invalid ai_role: {ctx.ai_role}")
    if ctx.support_kind not in SUPPORT_KINDS:
        raise ValueError(f"invalid support_kind: {ctx.support_kind}")

    contract = ctx.contract or CognitiveWorkContract(target_capability="unspecified")
    # Case/space-insensitive overlap so "Design" cannot evade "design".
    def _norm(items):
        return {str(x).strip().casefold() for x in items}
    protected_norm = _norm(contract.protected_cognition)
    overlap = sorted(_norm(ctx.requested_work) & protected_norm)

    learning_goal = ctx.goal_mode in {"learn", "perform_with_assistance"}

    # Delivery / quick-reference are not learning goals: no protected cognition.
    if not learning_goal:
        return DelegationDecision(True, "allow", [f"goal_mode:{ctx.goal_mode}"], overlap)

    # LEARNING GOAL. 'replace' means the AI performs the target capability itself.
    # Fail closed: block it whenever independence is required, and do NOT let an
    # 'accessibility' self-label launder it (accessibility changes access, not the
    # cognition being learned). An empty protected_cognition list does not make
    # replacement safe in a learning goal.
    if ctx.ai_role == "replace" and contract.independence_required:
        reasons = ["ai_would_replace_protected_cognition", "learning_goal_requires_learner_cognition"]
        if ctx.support_kind == "accessibility":
            reasons.append("accessibility_support_does_not_justify_cognitive_replacement")
        if not protected_norm:
            reasons.append("protected_cognition_undeclared_fail_closed")
        return DelegationDecision(False, "reduce_or_block", reasons, overlap)

    if ctx.ai_role == "delegate" and contract.independence_required and overlap:
        return DelegationDecision(
            False, "delegate_only_non_target_work",
            ["requested_delegation_overlaps_target_capability"], overlap,
        )

    # Genuine accessibility accommodation on a non-replacing role: no mastery penalty.
    if ctx.support_kind == "accessibility":
        return DelegationDecision(True, "allow_access_support", ["accessibility_support_is_not_mastery_penalty"], overlap)

    if ctx.ai_role in {"probe", "scaffold", "critique", "verify", "inform", "model", "delegate"}:
        return DelegationDecision(True, "allow_with_provenance", [f"ai_role:{ctx.ai_role}"], overlap)

    return DelegationDecision(True, "allow_with_provenance", ["no_protected_cognition_replacement_detected"], overlap)

from __future__ import annotations

# Bridge: turn RECORDED evidence (the projection) into the progress flags the
# policy engine consumes, so those flags are derived from the ledger rather than
# asserted fresh by the LLM each turn. This is what closes the "evidence loop":
# the host still supplies in-the-moment signals (stuck, ambiguity, high-stakes),
# but scope-availability and independence come from what was actually observed.

MASTERY_MIN_STRONG = 2  # strong unassisted successes needed to treat reasoning as established


def progress_flags(projection: dict, capability: str) -> dict:
    cap = (projection.get("demonstrated_capabilities") or {}).get(capability, {})
    ss = cap.get("scope_successes") or {}
    strong = int(cap.get("strong_unassisted_successes") or 0)
    evidence_count = int(cap.get("evidence_count") or 0)
    uncertain = capability in (projection.get("uncertainties") or [])
    if uncertain:
        ss = {}  # historical passes cannot authorize progression after a fresh failure
    return {
        "independent_reproduction_available": ss.get("independent_reproduction", 0) > 0,
        "near_transfer_available": ss.get("near_transfer", 0) > 0,
        "far_transfer_available": ss.get("far_transfer", 0) > 0,
        "delayed_independent_performance_available": ss.get("delayed_independent_performance", 0) > 0,
        "strong_independent_reasoning_available": strong >= MASTERY_MIN_STRONG and not uncertain,
        # Only recorded assistance establishes support dependence. Failures alone
        # do not imply the learner received help.
        "prior_support_heavy": cap.get("supported_attempts", 0) > 0 and strong == 0,
        "capability_uncertain": uncertain,
        "attempts_without_progress": cap.get("consecutive_failures", 0),
        "graded_attempt_available": evidence_count > 0,
    }


def apply_evidence_to_context(ctx, projection: dict, capability: str):
    """Overwrite the scope/independence flags on a TeachingContext from evidence.

    In-the-moment fields (learner_stuck, high_stakes, ambiguity, study_intent, mode,
    requested direct answer) are left untouched — only what the ledger can attest
    is overridden."""
    flags = progress_flags(projection, capability)
    ctx.independent_reproduction_available = flags["independent_reproduction_available"]
    ctx.near_transfer_available = flags["near_transfer_available"]
    ctx.far_transfer_available = flags["far_transfer_available"]
    ctx.delayed_independent_performance_available = flags["delayed_independent_performance_available"]
    ctx.strong_independent_reasoning_available = flags["strong_independent_reasoning_available"]
    ctx.prior_support_heavy = flags["prior_support_heavy"]
    ctx.attempts_without_progress = flags["attempts_without_progress"]
    ctx.graded_attempt_available = flags["graded_attempt_available"]
    ctx.repeated_observable_error = flags["attempts_without_progress"] > 0
    return ctx, flags

from __future__ import annotations

"""Epistemic Gate — "Can I trust this source?".

The Rights Gate (``source_governance.evaluate_source_use``) answers *may I use
this source this way?*. It does not ask whether the source is any good. A source
can be perfectly licensed and still be wrong, stale, anonymous, or retracted.

This module is the second half of Source Governance named in the design: an
epistemic check on credibility and freshness that runs *alongside* the rights
check. ``source_gate`` combines the two with deny > review > allow, so a source
has to clear both to be used without a human in the loop.

Nothing here reaches the network or guesses a date. It evaluates the signals it
is given and says plainly what is unknown; an unknown never silently upgrades to
trusted (fail-closed, consistent with the rest of the runtime).
"""

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone


# Credibility tier by declared source kind. Unknown/low-provenance kinds float to
# the bottom and need corroboration before they can stand alone.
SOURCE_TIER = {
    "primary": "high",
    "official": "high",
    "peer_reviewed": "high",
    "standard": "high",
    "authoritative": "medium",
    "documentation": "medium",
    "reputable_practitioner": "medium",
    "secondary": "medium",
    "textbook": "medium",
    "news": "low",
    "forum": "low",
    "user_generated": "low",
    "blog": "low",
    "ai_derived": "low",
    "unknown": "low",
    "web": "low",
}

TIER_RANK = {"high": 2, "medium": 1, "low": 0, "unknown": 0}

# How fast claims in a topic go stale. ``None`` = does not expire on age alone.
VOLATILITY_MAX_AGE_DAYS = {
    "stable": None,      # math proofs, historical facts, settled theory
    "slow": 1825,        # ~5y: established engineering practice, language core
    "volatile": 365,     # ~1y: framework/library APIs, tooling, prices
    "breaking": 30,      # fast-moving: security advisories, current events
}

VOLATILITY_CLASSES = set(VOLATILITY_MAX_AGE_DAYS)


@dataclass
class SourceCredibility:
    source_id: str
    source_type: str = "unknown"
    publication_date: str | None = None     # ISO date/datetime the content is from
    last_verified: str | None = None        # ISO date a human/runtime last re-checked it
    corroborating_source_count: int = 0     # independent sources making the same claim
    author_known: bool = False
    publisher_known: bool = False
    retracted: bool = False
    derived_from: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class EpistemicDecision:
    status: str                 # trusted | corroborate_required | stale | review_required | reject
    trust_tier: str             # high | medium | low
    freshness: str              # fresh | stale | unknown | not_applicable
    age_days: int | None
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def _parse_iso(value: str | None):
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text[:10])  # tolerate date-only + trailing junk
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_days(iso: str | None, as_of):
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return max(0, int((as_of - dt).total_seconds() // 86400))


def evaluate_source_trust(
    cred: SourceCredibility,
    claim_volatility: str = "slow",
    *,
    as_of: str | None = None,
) -> EpistemicDecision:
    """Judge a source's credibility and freshness for a claim of given volatility."""
    if claim_volatility not in VOLATILITY_CLASSES:
        raise ValueError(f"unknown claim_volatility: {claim_volatility}")

    now = _parse_iso(as_of) or datetime.now(timezone.utc)
    tier = SOURCE_TIER.get(cred.source_type, "low")
    reasons: list[str] = [f"source_type:{cred.source_type}", f"tier:{tier}"]

    # Hard stops first (reject beats everything).
    if cred.retracted:
        return EpistemicDecision("reject", tier, "not_applicable", None,
                                 reasons + ["source_retracted"])
    if cred.source_type == "ai_derived" and not cred.derived_from:
        return EpistemicDecision("review_required", "low", "unknown", None,
                                 reasons + ["ai_derived_without_provenance"])

    # Freshness.
    max_age = VOLATILITY_MAX_AGE_DAYS[claim_volatility]
    age = _age_days(cred.publication_date, now)
    verified_age = _age_days(cred.last_verified, now)
    if max_age is None:
        freshness = "not_applicable"
        reasons.append(f"volatility:{claim_volatility}_does_not_expire_on_age")
    elif age is None and verified_age is None:
        freshness = "unknown"
        reasons.append("no_publication_or_verification_date")
    else:
        effective_age = min(a for a in (age, verified_age) if a is not None)
        if effective_age > max_age:
            freshness = "stale"
            reasons.append(f"age_{effective_age}d_exceeds_{claim_volatility}_max_{max_age}d")
        else:
            freshness = "fresh"
            reasons.append(f"age_{effective_age}d_within_{claim_volatility}_max_{max_age}d")

    # A stale source on a volatility-sensitive claim must be refreshed, whatever
    # its tier: a 10-year-old "official" API doc is still wrong today.
    if freshness == "stale":
        return EpistemicDecision("stale", tier, freshness, age,
                                 reasons + ["re_verify_or_replace_before_use"])

    # Low-tier sources cannot stand alone: corroboration or human review.
    if tier == "low":
        if cred.corroborating_source_count >= 2:
            return EpistemicDecision("trusted", tier, freshness, age,
                                     reasons + ["low_tier_but_corroborated_by_2plus_independent_sources"])
        return EpistemicDecision("corroborate_required", tier, freshness, age,
                                 reasons + ["low_tier_source_needs_corroboration_or_human_review"])

    # Freshness unknown on a volatility-sensitive claim: don't silently trust.
    if freshness == "unknown":
        return EpistemicDecision("review_required", tier, freshness, age,
                                 reasons + ["freshness_unknown_for_volatility_sensitive_claim"])

    return EpistemicDecision("trusted", tier, freshness, age,
                             reasons + ["credible_tier_and_fresh_enough"])


# --- combined gate -------------------------------------------------------------

_EPISTEMIC_RANK = {"reject": 3, "stale": 2, "review_required": 2, "corroborate_required": 2, "trusted": 0}
_RIGHTS_RANK = {"deny": 3, "review_required": 2, "allow": 0}


@dataclass
class SourceGateDecision:
    status: str                 # allow | review_required | deny
    operation: str
    epistemic: dict
    rights: dict
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def source_gate(epistemic: EpistemicDecision, rights_decision) -> SourceGateDecision:
    """Combine the Epistemic Gate and the Rights Gate. Worst outcome wins.

    ``rights_decision`` is a ``source_governance.SourceUseDecision``. A source is
    usable without a human only when it is both trustworthy AND permitted.
    """
    e_rank = _EPISTEMIC_RANK.get(epistemic.status, 2)
    r_rank = _RIGHTS_RANK.get(rights_decision.status, 2)
    worst = max(e_rank, r_rank)
    status = {3: "deny", 2: "review_required", 0: "allow"}.get(worst, "review_required")
    reasons = [f"epistemic:{epistemic.status}", f"rights:{rights_decision.status}"]
    if status == "allow":
        reasons.append("trustworthy_and_permitted")
    elif status == "deny":
        reasons.append("rejected_by_epistemic_or_rights_gate")
    else:
        reasons.append("needs_human_review_before_use")
    return SourceGateDecision(
        status=status,
        operation=rights_decision.operation,
        epistemic=epistemic.to_dict(),
        rights=rights_decision.to_dict(),
        reasons=reasons,
    )

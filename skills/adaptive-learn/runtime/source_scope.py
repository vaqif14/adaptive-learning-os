from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Iterable

# Consumer-first runtime. NotebookLM may be used manually as a research workspace,
# but the core never requires an Enterprise connector or an uploaded corpus.
SOURCE_PROVIDERS = {"none", "local_bundle", "notebook_manual", "mixed"}
SOURCE_STRATEGIES = {
    "adaptive_hybrid",       # default: use what exists, discover what is missing, recommend acquisition only when needed
    "strict_closed_world",   # user explicitly restricts the session to available materials
    "auto_discovery",        # discover authoritative open sources for gaps
    "curated_acquisition",   # recommend sources the learner should obtain
}
OUT_OF_SCOPE_POLICIES = {"exclude_from_assessment", "mark_unknown"}

# Single source of truth: what each strategy is allowed to do. Both the persisted
# contract policy and the in-flight scope policy derive their booleans from here,
# so the two can no longer drift (previously SourcePolicy and SourceScopePolicy
# disagreed, and strict_closed_world still planned discovery).
STRATEGY_BEHAVIOR = {
    "adaptive_hybrid":      {"discover": True,  "ingest_ok": True,  "recommend": True},
    "auto_discovery":       {"discover": True,  "ingest_ok": True,  "recommend": False},
    "curated_acquisition":  {"discover": False, "ingest_ok": False, "recommend": True},
    "strict_closed_world":  {"discover": False, "ingest_ok": False, "recommend": False},
}


def behavior_for(strategy: str) -> dict:
    return STRATEGY_BEHAVIOR.get(strategy, STRATEGY_BEHAVIOR["adaptive_hybrid"])
COVERAGE_STATES = {"covered", "partial", "missing"}


@dataclass
class SourceScopePolicy:
    provider: str = "mixed"
    strategy: str = "adaptive_hybrid"
    out_of_scope_policy: str = "mark_unknown"
    allow_external_discovery: bool = True
    allow_automatic_ingestion: bool = False
    ask_before_discovery: bool = False
    require_user_material_each_session: bool = False
    discovery_priority: tuple[str, ...] = (
        "official",
        "primary",
        "peer_reviewed",
        "authoritative",
        "reputable_practitioner",
    )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.provider not in SOURCE_PROVIDERS:
            errors.append("invalid_source_provider")
        if self.strategy not in SOURCE_STRATEGIES:
            errors.append("invalid_source_strategy")
        if self.out_of_scope_policy not in OUT_OF_SCOPE_POLICIES:
            errors.append("invalid_out_of_scope_policy")
        if self.strategy in {"adaptive_hybrid", "auto_discovery"} and not self.allow_external_discovery:
            errors.append("discovery_strategy_requires_external_discovery_permission")
        if self.allow_automatic_ingestion and self.strategy not in {"adaptive_hybrid", "auto_discovery"}:
            errors.append("automatic_ingestion_requires_discovery_strategy")
        return errors

    def to_dict(self):
        obj = asdict(self)
        obj["discovery_priority"] = list(self.discovery_priority)
        return obj

    @classmethod
    def from_strategy(cls, strategy: str, *, out_of_scope_policy: str = "mark_unknown") -> "SourceScopePolicy":
        b = behavior_for(strategy)
        return cls(
            strategy=strategy,
            out_of_scope_policy=out_of_scope_policy,
            allow_external_discovery=b["discover"],
            allow_automatic_ingestion=b["ingest_ok"],
        )

    @classmethod
    def from_dict(cls, obj: dict | None) -> "SourceScopePolicy":
        obj = obj or {}
        return cls(
            provider=obj.get("provider", "mixed"),
            strategy=obj.get("strategy", "adaptive_hybrid"),
            out_of_scope_policy=obj.get("out_of_scope_policy", "mark_unknown"),
            allow_external_discovery=bool(obj.get("allow_external_discovery", True)),
            allow_automatic_ingestion=bool(obj.get("allow_automatic_ingestion", False)),
            ask_before_discovery=bool(obj.get("ask_before_discovery", False)),
            require_user_material_each_session=bool(obj.get("require_user_material_each_session", False)),
            discovery_priority=tuple(obj.get("discovery_priority") or cls().discovery_priority),
        )


@dataclass
class SourceBoundaryDecision:
    status: str
    strategy: str | None
    prompt: str | None
    options: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class CoverageClaim:
    capability_id: str
    status: str
    source_ids: list[str] = field(default_factory=list)
    note: str | None = None

    def validate(self) -> list[str]:
        errors = []
        if self.status not in COVERAGE_STATES:
            errors.append(f"invalid_coverage_state:{self.capability_id}")
        return errors


@dataclass
class SourceGapReport:
    required_count: int
    covered_count: int
    partial_count: int
    missing_count: int
    covered: list[str]
    partial: list[str]
    missing: list[str]
    assessment_eligible: list[str]
    out_of_scope: list[str]
    next_action: str
    strategy: str
    note: str = "corpus coverage is not learner mastery"

    def to_dict(self):
        return asdict(self)


@dataclass
class AcquisitionCandidate:
    title: str
    closes_gaps: list[str]
    priority: str = "P1"
    uri: str | None = None
    source_type: str = "unknown"
    access: str = "unknown"
    rights_status: str = "unknown"
    note: str | None = None

    def to_dict(self):
        return asdict(self)


def source_boundary_gate(policy: SourceScopePolicy, *, mode: str | None, has_supplied_resources: bool) -> SourceBoundaryDecision:
    """Resolve source behavior without forcing an onboarding question.

    In the default adaptive_hybrid mode, supplied materials are used when present,
    but their absence never blocks learning. Missing coverage is handled by the host
    agent through authoritative-source discovery, then acquisition recommendations
    only for unresolved/closed gaps.
    """
    if mode == "quick_answer":
        return SourceBoundaryDecision(
            "skipped", policy.strategy, None,
            reasons=["quick_reference_does_not_require_source_boundary_gate"],
        )

    errors = policy.validate()
    if errors:
        return SourceBoundaryDecision("invalid", policy.strategy, None, reasons=errors)

    if policy.strategy == "adaptive_hybrid":
        reasons = [
            "available_materials_are_used_when_present",
            "missing_materials_are_discovered_automatically",
            "user_material_is_not_required_each_session",
            "closed_or_unavailable_critical_sources_fall_back_to_acquisition_recommendation",
        ]
        if has_supplied_resources:
            reasons.append("learner_materials_registered_as_preferred_inputs")
        else:
            reasons.append("no_learner_materials_present_runtime_will_bootstrap_sources")
        return SourceBoundaryDecision("resolved", policy.strategy, None, reasons=reasons)

    if policy.strategy in {"strict_closed_world", "auto_discovery", "curated_acquisition"}:
        return SourceBoundaryDecision("resolved", policy.strategy, None, reasons=["explicit_source_strategy_selected"])

    return SourceBoundaryDecision("invalid", policy.strategy, None, reasons=["unhandled_source_strategy"])


def analyze_source_gaps(required_capabilities: Iterable[str], claims: Iterable[CoverageClaim], policy: SourceScopePolicy) -> SourceGapReport:
    required = list(dict.fromkeys(x for x in required_capabilities if x))
    claim_map = {c.capability_id: c for c in claims}
    for claim in claim_map.values():
        errors = claim.validate()
        if errors:
            raise ValueError(",".join(errors))

    covered: list[str] = []
    partial: list[str] = []
    missing: list[str] = []
    for cap in required:
        state = claim_map.get(cap, CoverageClaim(cap, "missing")).status
        if state == "covered":
            covered.append(cap)
        elif state == "partial":
            partial.append(cap)
        else:
            missing.append(cap)

    if policy.strategy == "strict_closed_world":
        eligible = list(covered)
        out_scope = partial + missing if policy.out_of_scope_policy == "exclude_from_assessment" else []
        next_action = "continue_with_covered_scope"
    elif policy.strategy == "auto_discovery":
        eligible = list(covered)
        out_scope = []
        next_action = "emit_discovery_request" if partial or missing else "continue"
    elif policy.strategy == "curated_acquisition":
        eligible = list(covered)
        out_scope = []
        next_action = "build_acquisition_plan" if partial or missing else "continue"
    elif policy.strategy == "adaptive_hybrid":
        eligible = list(covered)
        out_scope = []
        next_action = "discover_then_acquire_if_needed" if partial or missing else "continue"
    else:
        eligible = list(covered)
        out_scope = []
        next_action = "invalid_source_strategy"

    return SourceGapReport(
        required_count=len(required),
        covered_count=len(covered),
        partial_count=len(partial),
        missing_count=len(missing),
        covered=covered,
        partial=partial,
        missing=missing,
        assessment_eligible=eligible,
        out_of_scope=out_scope,
        next_action=next_action,
        strategy=policy.strategy,
    )


def discovery_request(report: SourceGapReport, topic: str, policy: SourceScopePolicy | None = None) -> dict:
    policy = policy or SourceScopePolicy()
    gaps = report.partial + report.missing
    return {
        "status": "discovery_required" if gaps else "not_required",
        "topic": topic,
        "gaps": gaps,
        "queries": [f"{topic} {gap} official specification documentation primary source" for gap in gaps],
        "priority": list(policy.discovery_priority),
        "constraints": [
            "prefer primary/official/peer-reviewed sources appropriate to the domain",
            "verify freshness for volatile claims",
            "record provenance and rights state",
            "do not invent access rights or licenses",
            "do not block the session merely because the learner supplied no files",
            "if authoritative open coverage remains insufficient, emit an acquisition recommendation",
        ],
    }


def build_acquisition_plan(candidates: Iterable[AcquisitionCandidate], report: SourceGapReport) -> dict:
    gaps = set(report.partial + report.missing)
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}
    selected = []
    for c in candidates:
        overlap = sorted(gaps.intersection(c.closes_gaps))
        if not overlap:
            continue
        item = c.to_dict()
        item["closes_gaps"] = overlap
        selected.append(item)
    selected.sort(key=lambda x: (priority_rank.get(x.get("priority", "P2"), 99), -len(x.get("closes_gaps", [])), x.get("title", "")))
    return {
        "status": "recommendations_ready" if selected else "no_candidates",
        "unresolved_gaps": sorted(gaps),
        "recommendations": selected,
        "rules": [
            "recommendation is not purchase automation",
            "paid or restricted sources must be obtained by the user through lawful access",
            "adding a source does not convert it into learner mastery evidence",
            "ask the learner only when a critical unavailable source truly requires their access or purchase",
        ],
    }


def hybrid_source_plan(
    report: SourceGapReport,
    topic: str,
    *,
    available_source_count: int,
    discovery_results: Iterable[dict] | None = None,
    strategy: str | None = None,
) -> dict:
    """Plan the two material classes the product exposes to the learner.

    1) available_materials: materials the learner already has or the runtime can lawfully use now.
    2) missing_materials: coverage gaps the runtime should resolve itself by discovery first;
       only unresolved closed/restricted gaps become acquisition recommendations.
    """
    strategy = strategy or report.strategy or "adaptive_hybrid"
    behavior = behavior_for(strategy)
    gaps = report.partial + report.missing

    # Strategies that forbid discovery (strict_closed_world, curated_acquisition)
    # never emit a discovery request; they stay within available materials and
    # (if allowed) recommend acquisition for the gaps instead.
    if not behavior["discover"]:
        return {
            "material_classes": {
                "available": {
                    "count": available_source_count,
                    "learner_supplied_or_existing": available_source_count,
                    "runtime_discovered": 0,
                },
                "missing": {"capabilities": gaps, "count": len(gaps)},
            },
            "automatic_actions": ["use_existing_materials_only_no_discovery_for_strategy"],
            "discovery_request": None,
            "phase": ("acquire_if_critical" if gaps and behavior["recommend"] else "continue_with_covered_scope"),
            "acquisition_required_for": gaps if (gaps and behavior["recommend"]) else [],
            "ask_user": bool(gaps) and behavior["recommend"],
            "ask_user_only_for": ("acquisition_recommendation_only" if gaps and behavior["recommend"] else None),
            "strategy": strategy,
            "note": "strategy restricts the session to available materials",
        }

    discovery_attempted = discovery_results is not None
    discovery_results = list(discovery_results or [])
    resolved_by_discovery: set[str] = set()
    discovery_sources: list[dict] = []
    for item in discovery_results:
        raw_closes = item.get("closes_gaps")
        # closes_gaps must be a list; a bare string is ignored, not char-split.
        closes = set(raw_closes) if isinstance(raw_closes, list) else set()
        usable = item.get("usable", True)
        if usable is not True:  # only a real boolean True counts as usable
            continue
        if True:
            resolved_by_discovery.update(closes.intersection(gaps))
            discovery_sources.append(item)

    unresolved = [g for g in gaps if g not in resolved_by_discovery]
    return {
        "material_classes": {
            "available": {
                "count": available_source_count + len(discovery_sources),
                "learner_supplied_or_existing": available_source_count,
                "runtime_discovered": len(discovery_sources),
            },
            "missing": {
                "capabilities": unresolved,
                "count": len(unresolved),
            },
        },
        "automatic_actions": [
            "use_existing_materials_first" if available_source_count else "bootstrap_from_authoritative_sources",
            "discover_authoritative_open_sources_for_partial_or_missing_capabilities" if gaps else "no_discovery_needed",
            "run_source_governance_before_persistence_or_reuse",
        ],
        "discovery_request": discovery_request(report, topic) if gaps else None,
        "phase": ("acquire_if_critical" if discovery_attempted and unresolved else "continue" if not unresolved else "discover"),
        "acquisition_required_for": unresolved if discovery_attempted else [],
        "ask_user": bool(unresolved) and discovery_attempted,
        "ask_user_only_for": "critical_unresolved_sources_that_require_private_or_paid_access" if unresolved and discovery_attempted else None,
        "strategy": strategy,
        "note": "the learner does not need to provide materials for every session",
    }

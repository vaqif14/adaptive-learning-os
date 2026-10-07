from __future__ import annotations

from dataclasses import dataclass, asdict, field

GOAL_MODES = {"learn", "perform_with_assistance", "deliver_artifact", "quick_reference"}


def _as_list(v):
    # A bare string must never be exploded into characters.
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


@dataclass
class LearningResources:
    # Materials already available to the learner/runtime. These are optional.
    learner_supplied: list[str] = field(default_factory=list)
    canonical: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class SourcePolicy:
    # Default behavior: use materials already available; discover missing coverage
    # automatically; recommend acquisition only for unresolved restricted/paid sources.
    strategy: str = "adaptive_hybrid"
    use_available_materials: bool = True
    discover_missing_materials: bool = True
    recommend_acquisition_for_unresolved: bool = True
    ask_before_discovery: bool = False
    require_user_material_each_session: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class LearningContract:
    goal: str | None = None
    application_context: str | None = None
    constraints: list[str] = field(default_factory=list)
    desired_independence: str | None = None
    time_horizon: str | None = None
    deadline: str | None = None
    resources: LearningResources = field(default_factory=LearningResources)
    source_policy: SourcePolicy = field(default_factory=SourcePolicy)
    stated_background: str | None = None
    target_evidence_scope: str | None = None

    # Cross-cutting semantics.
    goal_mode: str = "learn"
    fast_track_requested: bool = False
    protected_cognition: list[str] = field(default_factory=list)
    delegable_work: list[str] = field(default_factory=list)
    accessibility_needs: list[str] = field(default_factory=list)
    authentic_environment_required: bool | None = None
    ai_use_policy: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, obj: dict) -> "LearningContract":
        resources = obj.get("resources") or {}
        if not isinstance(resources, LearningResources):
            resources = LearningResources(
                learner_supplied=_as_list(resources.get("learner_supplied")),
                canonical=_as_list(resources.get("canonical")),
            )
        source_policy = obj.get("source_policy") or {}
        if not isinstance(source_policy, SourcePolicy):
            source_policy = SourcePolicy(
                strategy=source_policy.get("strategy", "adaptive_hybrid"),
                use_available_materials=bool(source_policy.get("use_available_materials", True)),
                discover_missing_materials=bool(source_policy.get("discover_missing_materials", True)),
                recommend_acquisition_for_unresolved=bool(source_policy.get("recommend_acquisition_for_unresolved", True)),
                ask_before_discovery=bool(source_policy.get("ask_before_discovery", False)),
                require_user_material_each_session=bool(source_policy.get("require_user_material_each_session", False)),
            )
        return cls(
            goal=obj.get("goal"),
            application_context=obj.get("application_context"),
            constraints=_as_list(obj.get("constraints")),
            desired_independence=obj.get("desired_independence"),
            time_horizon=obj.get("time_horizon"),
            deadline=obj.get("deadline"),
            resources=resources,
            source_policy=source_policy,
            stated_background=obj.get("stated_background"),
            target_evidence_scope=obj.get("target_evidence_scope"),
            goal_mode=(obj.get("goal_mode") if obj.get("goal_mode") in GOAL_MODES else "learn"),
            fast_track_requested=bool(obj.get("fast_track_requested", False)),
            protected_cognition=_as_list(obj.get("protected_cognition")),
            delegable_work=_as_list(obj.get("delegable_work")),
            accessibility_needs=_as_list(obj.get("accessibility_needs")),
            authentic_environment_required=obj.get("authentic_environment_required"),
            ai_use_policy=dict(obj.get("ai_use_policy", {})),
        )

    def decision_relevant_missing(self, mode: str) -> list[str]:
        missing = []
        if mode != "quick_answer" and not self.goal:
            missing.append("goal")
        return missing

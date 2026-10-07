from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .intents import INTENT_CATALOG, infer_default_mode, infer_study_intent, _normalize, _tokens, _matches


class Mode(str, Enum):
    QUICK_ANSWER = "quick_answer"
    LEARN = "learn"
    PRACTICE = "practice"
    BUILD_WITH_HELP = "build_with_help"
    REVIEW = "review"
    ASSESS = "assess"


class Route(str, Enum):
    DIRECT = "direct"
    TEACH = "teach"
    DEEP = "deep"


@dataclass
class RouteContext:
    mode: str | None = None
    study_intent: str | None = None
    conceptual_error: bool = False
    repeated_conceptual_failures: int = 0
    decision_sensitive_ambiguity: bool = False
    ordinary_intervention_failed: bool = False
    representation_insufficient: bool = False
    high_stakes: bool = False


@dataclass
class RouteDecision:
    mode: str
    route: str
    study_intent: str
    reasons: list[str]


QUICK_HINTS = (
    "quick", "quickly", "just tell", "syntax", "what is", "default value",
    "qısa", "tez de", "sadəcə de",
)
PRACTICE_HINTS = ("practice", "exercise", "quiz me", "məşq", "tapşırıq")
ASSESS_HINTS = ("assess", "test me", "evaluate me", "exam", "imtahan", "imtahan ver", "səviyyəmi yoxla", "biliyimi qiymətləndir")
REVIEW_HINTS = ("review", "revise", "revision", "təkrar", "təkrar et", "yadıma sal")
BUILD_HINTS = ("help me build", "work with me", "birlikdə", "layihə")
LEARN_HINTS = ("learn", "study", "understand", "master", "öyrən", "başa düş", "mənimsə")


def infer_mode(message: str, explicit_mode: str | None = None, study_intent: str | None = None) -> str:
    if explicit_mode:
        if explicit_mode not in {m.value for m in Mode}:
            raise ValueError(f"invalid mode: {explicit_mode}")
        return explicit_mode
    norm = _normalize(message)
    tokens = _tokens(norm)

    def hit(hints):
        return any(_matches(norm, tokens, h) for h in hints)

    if hit(ASSESS_HINTS):
        return Mode.ASSESS.value
    if hit(PRACTICE_HINTS):
        return Mode.PRACTICE.value
    if hit(REVIEW_HINTS):
        return Mode.REVIEW.value
    if hit(BUILD_HINTS):
        return Mode.BUILD_WITH_HELP.value
    if study_intent:
        return infer_default_mode(message, study_intent)
    if hit(LEARN_HINTS):
        return Mode.LEARN.value
    if len(norm) < 240 and hit(QUICK_HINTS):
        return Mode.QUICK_ANSWER.value
    return Mode.LEARN.value


def route_message(message: str, ctx: RouteContext) -> RouteDecision:
    intent = infer_study_intent(message, ctx.study_intent)
    mode = infer_mode(message, ctx.mode, intent)
    spec = INTENT_CATALOG[intent]

    # Intent semantics define a default path, but safety and explicit deep triggers can escalate it.
    if spec.default_route == Route.DIRECT.value and mode == Mode.QUICK_ANSWER.value and not ctx.high_stakes:
        return RouteDecision(mode, Route.DIRECT.value, intent, [f"study_intent:{intent}", "direct_output_intent"])

    deep_reasons: list[str] = []
    if intent == "simulate_performance":
        deep_reasons.append("scenario_simulation_is_expensive_interaction")
    if ctx.decision_sensitive_ambiguity:
        deep_reasons.append("decision_sensitive_ambiguity")
    if ctx.ordinary_intervention_failed:
        deep_reasons.append("ordinary_intervention_failed")
    if ctx.representation_insufficient:
        deep_reasons.append("current_representation_insufficient")
    if ctx.high_stakes:
        deep_reasons.append("high_stakes_requires_stronger_validation")

    repetition_reason = None
    if ctx.conceptual_error and ctx.repeated_conceptual_failures >= 2:
        repetition_reason = "repetition_recorded_but_not_sufficient_for_deep_diagnosis"

    if deep_reasons:
        reasons = [f"study_intent:{intent}", *deep_reasons]
        if repetition_reason:
            reasons.append(repetition_reason)
        return RouteDecision(mode, Route.DEEP.value, intent, reasons)

    reasons = [f"study_intent:{intent}", "standard_interaction_without_deep_trigger"]
    if repetition_reason:
        reasons.append(repetition_reason)
    return RouteDecision(mode, Route.TEACH.value, intent, reasons)

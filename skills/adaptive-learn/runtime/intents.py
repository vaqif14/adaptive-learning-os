from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StudyIntent(str, Enum):
    QUICK_REFERENCE = "quick_reference"
    COMPRESS_SOURCE = "compress_source"
    BUILD_PLAN = "build_plan"
    LEARN_CONCEPT = "learn_concept"
    DECOMPOSE_TASK = "decompose_task"
    BUILD_WITH_HELP = "build_with_help"
    CRITIQUE_ARTIFACT = "critique_artifact"
    INTERPRET_QUESTION = "interpret_question"
    SIMULATE_PERFORMANCE = "simulate_performance"
    PRACTICE_RETRIEVAL = "practice_retrieval"
    DELIVER_ARTIFACT = "deliver_artifact"


@dataclass(frozen=True)
class IntentSpec:
    intent: str
    family: str
    default_mode: str
    default_route: str
    mastery_evidence_from_output: bool
    description: str


INTENT_CATALOG: dict[str, IntentSpec] = {
    StudyIntent.QUICK_REFERENCE.value: IntentSpec(
        StudyIntent.QUICK_REFERENCE.value, "compress_map", "quick_answer", "direct", False,
        "Return a concise fact/reference without forcing a teaching loop.",
    ),
    StudyIntent.COMPRESS_SOURCE.value: IntentSpec(
        StudyIntent.COMPRESS_SOURCE.value, "compress_map", "quick_answer", "direct", False,
        "Compress supplied material. Compression itself is never mastery evidence.",
    ),
    StudyIntent.BUILD_PLAN.value: IntentSpec(
        StudyIntent.BUILD_PLAN.value, "compress_map", "quick_answer", "direct", False,
        "Create a goal/resource/deadline-aware plan. The plan itself is not mastery evidence.",
    ),
    StudyIntent.LEARN_CONCEPT.value: IntentSpec(
        StudyIntent.LEARN_CONCEPT.value, "learn_decompose", "learn", "teach", True,
        "Build independent conceptual capability.",
    ),
    StudyIntent.DECOMPOSE_TASK.value: IntentSpec(
        StudyIntent.DECOMPOSE_TASK.value, "learn_decompose", "build_with_help", "teach", False,
        "Interpret and split a task into executable parts without claiming mastery.",
    ),
    StudyIntent.BUILD_WITH_HELP.value: IntentSpec(
        StudyIntent.BUILD_WITH_HELP.value, "learn_decompose", "build_with_help", "teach", True,
        "Co-produce an artifact while preserving support provenance.",
    ),
    StudyIntent.CRITIQUE_ARTIFACT.value: IntentSpec(
        StudyIntent.CRITIQUE_ARTIFACT.value, "critique_check", "build_with_help", "teach", False,
        "Critique an artifact and let the learner revise it.",
    ),
    StudyIntent.INTERPRET_QUESTION.value: IntentSpec(
        StudyIntent.INTERPRET_QUESTION.value, "critique_check", "learn", "teach", False,
        "Clarify what a task/question is actually asking before diagnosing knowledge.",
    ),
    StudyIntent.SIMULATE_PERFORMANCE.value: IntentSpec(
        StudyIntent.SIMULATE_PERFORMANCE.value, "simulate_spar", "assess", "deep", True,
        "Run an authentic role/scenario simulation with an explicit evaluation contract.",
    ),
    StudyIntent.PRACTICE_RETRIEVAL.value: IntentSpec(
        StudyIntent.PRACTICE_RETRIEVAL.value, "simulate_spar", "practice", "teach", True,
        "Practice retrieval using a representation matched to the capability.",
    ),
    StudyIntent.DELIVER_ARTIFACT.value: IntentSpec(
        StudyIntent.DELIVER_ARTIFACT.value, "delivery", "build_with_help", "teach", False,
        "Deliver an artifact efficiently. Do not silently interpret delivery as learning evidence.",
    ),
}


import unicodedata


def _normalize(text: str) -> str:
    """Unicode- and Turkic-aware normalization for keyword matching.

    NFKC + casefold, then unify all i-variants (I/İ/ı/i and the combining dot
    left by casefolding İ) to plain "i" so Azerbaijani input like "İmtahan",
    "QISA" or "qısa" matches hints written with a plain i. Non-alphanumeric runs
    become single spaces so matching is on word boundaries, not substrings.
    """
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.replace("\u0307", "")          # combining dot above (from İ.casefold())
    t = t.replace("\u0131", "i")         # ı -> i
    out = []
    for ch in t:
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def _tokens(norm_text: str) -> set[str]:
    return set(norm_text.split())


def _matches(norm_text: str, tokens: set[str], raw_hint: str) -> bool:
    hint = _normalize(raw_hint)
    if not hint:
        return False
    if " " in hint:
        # phrase: word-boundary substring on the space-padded text
        return f" {hint} " in f" {norm_text} "
    return hint in tokens  # single word: exact token (no "review" in "preview")


# Ordered intent -> hint phrases. ORDER IS PRIORITY. Retrieval practice is listed
# before critique so "test me" / "məni yoxla" is not shadowed by a critique hint.
_HINTS: list[tuple[str, tuple[str, ...]]] = [
    (StudyIntent.DELIVER_ARTIFACT.value, (
        "make this for me", "create the final", "deliver this", "finish this for me",
        "hazırla mənə", "finalını hazırla", "mənim üçün düzəlt", "təslim üçün hazırla",
    )),
    (StudyIntent.COMPRESS_SOURCE.value, (
        "summarize", "summary", "compress", "tldr", "xülasə", "qısalt", "sıxlaşdır",
    )),
    (StudyIntent.BUILD_PLAN.value, (
        "study plan", "learning plan", "roadmap", "plan qur", "plan hazırla", "tədris planı",
    )),
    (StudyIntent.SIMULATE_PERFORMANCE.value, (
        "mock interview", "mock security interview", "interview me", "role play",
        "oral exam", "simulate", "simulation", "sparring",
        "müsahibə simulyasiyası", "rol oyna", "şifahi imtahan", "satış zəngi",
    )),
    (StudyIntent.PRACTICE_RETRIEVAL.value, (
        "quiz me", "test me", "test my knowledge", "practice recall", "flashcard practice",
        "check my understanding", "recall practice",
        "məni yoxla", "məşq etdir", "yadımdan soruş", "biliyimi yoxla", "geri çağırma",
    )),
    (StudyIntent.CRITIQUE_ARTIFACT.value, (
        "critique", "review my", "check my code", "check my work", "check my essay",
        "check my draft", "check my writing", "feedback on",
        "tənqid et", "rəy ver", "səhvlərimi tap", "koduma bax",
    )),
    (StudyIntent.INTERPRET_QUESTION.value, (
        "what is this question asking", "interpret this question", "what does this task mean",
        "sual nə istəyir", "tapşırıq nə tələb edir", "bunu necə başa düşüm",
    )),
    (StudyIntent.DECOMPOSE_TASK.value, (
        "break down", "decompose", "split this assignment", "steps for this task",
        "hissələrə böl", "tapşırığı böl", "addımlara böl",
    )),
    (StudyIntent.BUILD_WITH_HELP.value, (
        "help me build", "work with me", "build with me", "birlikdə quraq", "birlikdə yazaq",
    )),
]

# Learning intent (verbs). These OUTRANK the quick-reference fallback so
# "I want to master Python — what is a closure?" is a learning request.
_LEARNING_HINTS = (
    "learn", "study", "understand", "master", "deep dive",
    "öyrən", "öyrənmək", "başa düş", "mənimsə", "dərindən",
)

_QUICK_HINTS = (
    "quick", "quickly", "just tell", "syntax", "what is", "default value",
    "qısa", "tez de", "sadəcə de",
)


def infer_study_intent(message: str, explicit_intent: str | None = None) -> str:
    if explicit_intent:
        if explicit_intent not in INTENT_CATALOG:
            raise ValueError(f"invalid study intent: {explicit_intent}")
        return explicit_intent
    norm = _normalize(message)
    tokens = _tokens(norm)
    for intent, hints in _HINTS:
        if any(_matches(norm, tokens, h) for h in hints):
            return intent
    # Learning verbs beat the quick-reference fallback.
    if any(_matches(norm, tokens, h) for h in _LEARNING_HINTS):
        return StudyIntent.LEARN_CONCEPT.value
    if len(norm) < 240 and any(_matches(norm, tokens, h) for h in _QUICK_HINTS):
        return StudyIntent.QUICK_REFERENCE.value
    return StudyIntent.LEARN_CONCEPT.value


def infer_default_mode(message: str, intent: str) -> str:
    # Learning wording upgrades pure COMPRESSION into a teaching interaction.
    # (build_plan is NOT upgraded: "study plan" is a plan request, not a lesson.)
    norm = _normalize(message)
    tokens = _tokens(norm)
    if intent == StudyIntent.COMPRESS_SOURCE.value:
        if any(_matches(norm, tokens, h) for h in _LEARNING_HINTS):
            return "learn"
    return INTENT_CATALOG[intent].default_mode


def intent_spec(intent: str) -> IntentSpec:
    if intent not in INTENT_CATALOG:
        raise ValueError(f"unknown study intent: {intent}")
    return INTENT_CATALOG[intent]

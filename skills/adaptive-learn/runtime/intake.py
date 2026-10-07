from __future__ import annotations

from dataclasses import dataclass, asdict

# Opening protocol: BEFORE teaching anything, run a short intake — ask about the
# learner's goal and level, ONE question at a time, and record each answer into
# the Learning Contract. No lesson, plan or workspace content is produced until
# at least the goal and the level are known.

@dataclass
class IntakeQuestion:
    field: str
    question: str
    why: str
    options: list[str]
    required: bool

    def to_dict(self):
        return asdict(self)


# Ordered. Goal and level are REQUIRED before teaching; the rest refine the plan.
INTAKE_QUESTIONS: list[IntakeQuestion] = [
    IntakeQuestion(
        "goal",
        "Sonda konkret NƏ EDƏ bilmək istəyirsən? (mövzu adı yox — bir bacarıq, məs. "
        "\"sıfırdan kiçik bir FastAPI xidməti yazıb test edə bilim\")",
        "goal_defines_the_target_capability_and_evidence",
        [], True,
    ),
    IntakeQuestion(
        "stated_background",
        "Bu mövzuda hazırkı səviyyən hansıdır?",
        "level_sets_starting_point_and_scaffolding_not_treated_as_competence_evidence",
        ["heç toxunmamışam", "bəzi əsasları bilirəm", "istifadə edə bilirəm", "onunla qura bilirəm"],
        True,
    ),
    IntakeQuestion(
        "application_context",
        "Bunu harada/nə üçün tətbiq edəcəksən? (iş, layihə, imtahan, maraq)",
        "context_shapes_examples_and_authentic_tasks",
        [], False,
    ),
    IntakeQuestion(
        "time_horizon",
        "Nə qədər vaxtın var və son tarix varmı?",
        "time_and_deadline_shape_scope_and_pacing",
        [], False,
    ),
    IntakeQuestion(
        "desired_independence",
        "Hədəfin köməksiz bacarmaqdır, yoxsa kömeklə işi çatdırmaqdır?",
        "independence_target_decides_how_much_the_ai_may_do",
        ["köməksiz bacarmaq", "kömeklə işi bitirmək"], False,
    ),
]

_FILLED = {
    "goal": lambda c: bool(c.get("goal")),
    "stated_background": lambda c: bool(c.get("stated_background")),
    "application_context": lambda c: bool(c.get("application_context")),
    "time_horizon": lambda c: bool(c.get("time_horizon") or c.get("deadline")),
    "desired_independence": lambda c: bool(c.get("desired_independence")),
}


def _answered(field: str, contract: dict) -> bool:
    return _FILLED.get(field, lambda c: bool(c.get(field)))(contract)


def next_question(contract: dict) -> IntakeQuestion | None:
    """The single next unanswered intake question (required ones first).

    Returns one question at a time — the caller asks it, records the answer into
    the contract, then calls again. None means intake is done.
    """
    for q in INTAKE_QUESTIONS:
        if q.required and not _answered(q.field, contract):
            return q
    for q in INTAKE_QUESTIONS:
        if not q.required and not _answered(q.field, contract):
            return q
    return None


def intake_ready_to_teach(contract: dict) -> bool:
    """Teaching may begin once the REQUIRED fields (goal, level) are known."""
    return all(_answered(q.field, contract) for q in INTAKE_QUESTIONS if q.required)


def intake_state(contract: dict) -> dict:
    nxt = next_question(contract)
    return {
        "ready_to_teach": intake_ready_to_teach(contract),
        "answered": [q.field for q in INTAKE_QUESTIONS if _answered(q.field, contract)],
        "pending": [q.field for q in INTAKE_QUESTIONS if not _answered(q.field, contract)],
        "ask_one_at_a_time": True,
        "next_question": nxt.to_dict() if nxt else None,
    }

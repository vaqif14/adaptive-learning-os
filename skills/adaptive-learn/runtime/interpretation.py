from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InterpretationContext:
    task_ambiguity_signal: bool = False
    instruction_misread_signal: bool = False
    multiple_plausible_readings: bool = False
    learner_requested_clarification: bool = False


@dataclass
class InterpretationDecision:
    clarify_first: bool
    reasons: list[str]


def question_interpretation_gate(ctx: InterpretationContext) -> InterpretationDecision:
    reasons: list[str] = []
    if ctx.learner_requested_clarification:
        reasons.append("learner_requested_task_clarification")
    if ctx.task_ambiguity_signal:
        reasons.append("task_ambiguity_signal")
    if ctx.instruction_misread_signal:
        reasons.append("instruction_misread_signal")
    if ctx.multiple_plausible_readings:
        reasons.append("multiple_plausible_task_readings")
    return InterpretationDecision(bool(reasons), reasons)

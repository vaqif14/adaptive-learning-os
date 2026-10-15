"""R5 transfer scopes: core + unseen transfer coverage derived from the items; practice is never mastery; the delayed
claim needs real ledger spacing."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from .. import projection
from ..course import REVIEW_MIN_OBSERVED_HOURS, CourseKernel
from .. import studio_plan as plan_mod
from .base import Ctx, Result, na, probe, verdict


def coverage_problems(subjects: list[dict], targets: dict) -> tuple[list[str], int]:
    problems, checked = [], 0
    need_core = int(targets.get("min_core_items", plan_mod.MIN_CORE_FLOOR))
    need_transfer = int(targets.get("min_transfer_items", plan_mod.MIN_TRANSFER_FLOOR))
    for subject in subjects:
        items = subject.get("items")
        if items is None:
            continue
        checked += 1
        scopes = Counter(i.get("scope") for i in items if isinstance(i, dict))        # derived, never declared
        if scopes[plan_mod.CORE_SCOPE] < max(need_core, plan_mod.MIN_CORE_FLOOR):
            problems.append(f"{subject['node']}: no independent_reproduction (core) item")
        if sum(scopes[s] for s in plan_mod.TRANSFER_SCOPES) < max(need_transfer, plan_mod.MIN_TRANSFER_FLOOR):
            problems.append(f"{subject['node']}: no unseen near/far transfer item")
    return problems, checked


def _coverage(ctx: Ctx) -> Result:
    problems, checked = coverage_problems(ctx.subjects(), ctx.targets())
    if not checked:
        return na("no items in the build or in the live course")
    return verdict(problems, f"{checked} node(s) have core and unseen transfer items, derived from the items", nodes=checked)


def _control_coverage():
    items = [{"item_id": f"i{n}", "scope": "independent_reproduction"} for n in range(3)]
    return Ctx.synthetic(subjects=[{"node": "core-only", "origin": "staged", "lesson": None, "items": items}], job=None)


@probe("r05.scope_coverage", "R5", control=_control_coverage, title="core and unseen transfer coverage derived from the items")
def scope_coverage(ctx: Ctx) -> Result:
    return _coverage(ctx)


def _practice_not_mastery(ctx: Ctx) -> Result:
    problems, strong = [], ctx.hook("counts_as_strong", projection.counts_as_strong)
    for rec in ctx.records():
        if rec["record_type"] != "evidence":
            continue
        p = rec["payload"]
        if p.get("scope") == "supported_completion" and (p.get("mastery_eligible") or strong(p, rec["provenance"])):
            problems.append(f"seq {rec['seq']}: supported-completion evidence is mastery eligible")
        if p.get("scope") == "delayed_independent_performance":
            review = p.get("review") or {}
            hours = review.get("observed_spacing_hours")
            if not review.get("counts_as_delayed") or hours is None or hours < REVIEW_MIN_OBSERVED_HOURS:
                problems.append(f"seq {rec['seq']}: a delayed claim without {REVIEW_MIN_OBSERVED_HOURS}h of ledger spacing")
    return verdict(problems, "no supported-completion record is mastery eligible; every delayed claim has real spacing")


def _control_practice():
    rec = {"seq": 4, "record_type": "evidence", "provenance": {"source": "course_practice"},
           "payload": {"scope": "supported_completion", "mastery_eligible": True}}
    return Ctx.synthetic(records=[rec])


@probe("r05.practice_not_mastery", "R5", control=_control_practice, title="supported completion is never mastery eligible")
def practice_not_mastery(ctx: Ctx) -> Result:
    return _practice_not_mastery(ctx)


# ------------------------------------------------------------- adversarial (pure stubs)
class _StubLedger:
    def __init__(self, records):
        self.records, self.appended = records, []

    def verified_records(self):
        return self.records

    def append(self, record_type, payload, provenance=None):
        self.appended.append((record_type, payload, provenance))
        return {}


def _records(gap_hours: float):
    t0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    src = {"seq": 1, "record_id": "evd_src", "record_type": "evidence", "timestamp": t0.isoformat(),
           "payload": {"evidence_id": "ev_exam_att_src", "capability_id": "n"}, "provenance": {}}
    started = {"seq": 2, "record_id": "evt_start", "record_type": "event",
               "timestamp": (t0 + timedelta(hours=gap_hours)).isoformat(),
               "payload": {"event_type": "course_review_started", "attempt_id": "att_rev", "node_id": "n"}, "provenance": {}}
    return [src, started]


def _delayed(ctx: Ctx) -> Result:
    review = ctx.hook("review_evidence", lambda led, state, att: CourseKernel._review_evidence(
        CourseKernel.__new__(CourseKernel), led, state, att))
    state = {"review_queue": [{"review_id": "rv", "source_attempt_id": "att_src", "due_at": "x", "interval_days": 1}]}
    att = {"review_id": "rv", "attempt_id": "att_rev", "node_id": "n"}
    problems = []
    if review(_StubLedger(_records(1)), state, att)["counts_as_delayed"]:
        problems.append("a review completed 1 hour after the pass counted as delayed")
    if not review(_StubLedger(_records(REVIEW_MIN_OBSERVED_HOURS + 5)), state, att)["counts_as_delayed"]:
        problems.append("a review after a real gap did not count as delayed (the probe is inconclusive)")
    stub = _StubLedger([])
    kernel = CourseKernel.__new__(CourseKernel)
    practice = ctx.hook("append_practice", lambda: CourseKernel._append_practice_evidence(
        kernel, stub, "ev_prc_probe", "n", {"capabilities": {}}, True, False,
        {"hints_count": 0, "worked_example_shown": True, "ai_direct_answer_revealed": False, "explanation_shown": False,
         "alternative_explanation_shown": False}, {"practice_attempt_id": "pa"}, "fp", [], "artifact_execution", "medium"))
    practice()
    if stub.appended:
        payload, provenance = stub.appended[0][1], stub.appended[0][2]
        if payload["scope"] in projection.TRANSFER_SCOPES or payload.get("mastery_eligible") or \
                projection.counts_as_strong(payload, provenance):
            problems.append("ladder/practice completion was recorded as transfer or mastery evidence")
    else:
        problems.append("the practice recorder wrote nothing (the probe is inconclusive)")
    return verdict(problems, "a 1-hour review is not delayed; a ladder/practice completion is not transfer or mastery")


def _control_delayed():
    return Ctx.synthetic(hooks={"review_evidence": lambda led, state, att: {"counts_as_delayed": True},
                                "append_practice": lambda: None})


@probe("r05.delayed_and_ladder_adversarial", "R5", control=_control_delayed,
       title="1h review is not delayed; ladder completion is not transfer")
def delayed_and_ladder(ctx: Ctx) -> Result:
    return _delayed(ctx)


PROBES = [scope_coverage, practice_not_mastery, delayed_and_ladder]

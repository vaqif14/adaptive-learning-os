"""R2 evidence tiers: no item kind can mint more strength than the format table; learners cannot inject strength."""
from __future__ import annotations

from .. import course_content as cc
from .. import evidence_semantics as sem
from ..course import CourseKernel
from ..studio_store import digest_of
from .base import Ctx, Result, journey_obs, probe, verdict

RANK = sem.STRENGTH_ORDER
# Highest strength each item kind may mint (policy; the code under test must stay at or below it).
EXPECTED_MINT = {"python": "strong", "concept:selected": "weak", "concept:open": "medium", "scenario:selected": "weak",
                 "scenario:open": "medium", "free_text": "medium"}
# Formats whose ceiling must never rise above this (a weakened table is a failure, not a convenience).
PINNED_CEILINGS = {"recognition_mcq": "weak", "free_recall": "medium", "compression_output": "weak",
                   "plan_output": "weak", "ai_generated_output": "weak", "unknown": "medium"}


def table_hash(table=None) -> str:
    return digest_of(dict(table if table is not None else sem.FORMAT_MAX_STRENGTH))


def _mint(kind: str, questions: list[dict]) -> dict:
    """What CourseKernel._exam_evidence actually mints for a correct, unassisted, automatically fresh exam item."""
    att = {"result": {"outcome": "correct", "grader": "host_agent", "confidence": "high"}, "kind": kind,
           "support": {"hints_count": 0, "worked_example_shown": False, "ai_direct_answer_revealed": False,
                       "conceptual_scaffold": False},
           "node_id": "r2-probe", "attempt_id": "att_r2probe", "item_id": "r2-item", "item_version": "iv_r2",
           "capability_version": "cap-x", "content_fingerprint": "cf", "freshness_basis": "automatic", "fast_track": False,
           "submission_sha256": "0" * 64, "review_id": None}
    item = {"scope": "independent_reproduction", "questions": questions}
    return CourseKernel._exam_evidence(CourseKernel.__new__(CourseKernel), None, {"review_queue": []}, att, item, "ev_r2probe")


def _tiers(ctx: Ctx) -> Result:
    table = ctx.hook("format_table", sem.FORMAT_MAX_STRENGTH)
    normalize = ctx.hook("normalize", sem.normalize_evidence_semantics)
    selected = [{"type": "single_choice"}, {"type": "multi_choice"}]
    open_q = [{"type": "numeric"}, {"type": "ordering"}]
    cases = {"python": ("python", []), "concept:selected": ("concept", selected), "concept:open": ("concept", open_q),
             "scenario:selected": ("scenario", selected), "scenario:open": ("scenario", open_q), "free_text": ("free_text", [])}
    problems, minted = [], {}
    for label, (kind, questions) in cases.items():
        payload = _mint(kind, questions)
        minted[label] = [payload["evidence_format"], payload["strength"]]
        ceiling = normalize(payload["evidence_format"], "strong", "unassisted").normalized_strength
        if RANK[payload["strength"]] > RANK[EXPECTED_MINT[label]]:
            problems.append(f"{label} mints {payload['strength']}, above the expected {EXPECTED_MINT[label]}")
        if RANK[payload["strength"]] > RANK[ceiling]:
            problems.append(f"{label} mints {payload['strength']}, above its format ceiling {ceiling}")
    for fmt, limit in PINNED_CEILINGS.items():
        if RANK[table.get(fmt, "strong")] > RANK[limit]:
            problems.append(f"format {fmt} is allowed up to {table.get(fmt)}, above the pinned {limit}")
        if RANK[normalize(fmt, "strong", "unassisted").normalized_strength] > RANK[limit]:
            problems.append(f"normalizing a strong claim for {fmt} yields more than {limit}")
    for fmt in sem.NON_MASTERY_FORMATS:
        if normalize(fmt, "strong", "unassisted").mastery_eligible:
            problems.append(f"format {fmt} is mastery eligible")
    return verdict(problems, "every item kind mints at most its table strength", table_hash=table_hash(table), minted=minted)


def _control_tiers():
    weak_table = {**sem.FORMAT_MAX_STRENGTH, "recognition_mcq": "strong"}
    return Ctx.synthetic(hooks={"format_table": weak_table,
                                "normalize": lambda fmt, req, ind, **kw: sem.EvidenceSemantics(fmt, req, req, True, [])})


@probe("r02.format_ceilings", "R2", control=_control_tiers, title="max strength per item kind stays within the format table")
def format_ceilings(ctx: Ctx) -> Result:
    return _tiers(ctx)


# -------------------------------------------------------- learner payload cannot set strength
INJECTED = {"strength": "strong", "format": "free_recall", "evidence_format": "free_recall", "independence": "unassisted",
            "mastery_eligible": True, "scope": "far_transfer"}


def _practice_specs(ctx: Ctx) -> list[dict]:
    specs = []
    for subject in ctx.subjects():
        lesson = subject.get("lesson") or {}
        practice = lesson.get("practice") or {}
        if cc.item_kind(practice) != "python" and practice.get("questions"):
            specs.append(practice)
        for item in subject.get("items") or []:
            if cc.item_kind(item) in {"concept", "scenario"} and item.get("questions"):
                specs.append(item)
    return specs or [{"kind": "concept", "questions": [{"id": "q1", "type": "single_choice", "category": "core",
                                                        "prompt": "p", "options": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
                                                        "answer": "a"}]}]


def _injection(ctx: Ctx) -> Result:
    validate = ctx.hook("validate_answers", cc.validate_answers)
    problems = []
    for spec in _practice_specs(ctx):
        key = cc.answer_key(spec["questions"]) if hasattr(cc, "answer_key") else {}
        given = {q["id"]: key.get(q["id"]) for q in spec["questions"]}
        try:
            out = validate(spec, {**given, **INJECTED})
        except cc.ContentError:
            continue                                  # refused outright: the injected fields never reach grading
        leaked = sorted(k for k in INJECTED if isinstance(out, dict) and k in out)
        if leaked:
            problems.append(f"a learner payload carried {leaked} through validation")
    return verdict(problems, "client-supplied strength/format/independence fields are refused or dropped")


def _control_injection():
    return Ctx.synthetic(hooks={"validate_answers": lambda spec, answers: dict(answers)}, subjects=[])


@probe("r02.client_strength_ignored", "R2", control=_control_injection,
       title="strength/format injected into a learner payload is ignored")
def client_strength_ignored(ctx: Ctx) -> Result:
    return _injection(ctx)


# ---------------------------------------------------- authored payloads carry no strength/format
STRENGTH_KEYS = frozenset({"strength", "evidence_strength", "evidence_format", "format", "mastery_eligible", "independence",
                           "max_strength", "format_max_strength", "requested_strength"})


def _walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield (f"{path}.{key}" if path else str(key)), key
            yield from _walk_keys(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _walk_keys(value, f"{path}[{i}]")


def _payload_keys(ctx: Ctx) -> Result:
    problems, checked = [], 0
    for subject in ctx.subjects():
        for part in ("lesson", "items"):
            payload = subject.get(part)
            if payload is None:
                continue
            checked += 1
            problems += [f"{subject['node']}.{part}.{path}: an authored/learner payload carries '{key}'"
                         for path, key in _walk_keys(payload) if key in STRENGTH_KEYS]
    if not checked:
        return Result(None, "no lesson or items payload in the build or in the live course")
    return verdict(problems, f"{checked} payload(s): none carries a strength, format or independence field")


def _control_payload_keys():
    items = [{"item_id": "a", "kind": "concept", "strength": "strong", "questions": [{"id": "q", "evidence_format": "free_recall"}]}]
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": None, "items": items}])


@probe("r02.no_strength_in_payloads", "R2", control=_control_payload_keys,
       title="no authored payload carries a strength, format or independence field")
def no_strength_in_payloads(ctx: Ctx) -> Result:
    return _payload_keys(ctx)


# ------------------------------------------------------------- finalised per kind (scratch)
def _finalized(ctx: Ctx) -> Result:
    rows = journey_obs(ctx, "exam_passes")
    if not rows:
        return Result(None, "no exam could be finalised in the scratch copy (no python/concept/scenario item reachable)")
    problems = []
    for row in rows:
        ev = row["evidence"]
        ceiling = sem.normalize_evidence_semantics(ev["evidence_format"], "strong", "unassisted").normalized_strength
        label = f"{row['node']} ({row['kind']})"
        if RANK[ev["strength"]] > RANK[ceiling]:
            problems.append(f"{label}: finalised strength {ev['strength']} is above the table ceiling {ceiling}")
        if row["kind"] != "python" and ev["strength"] == "strong":
            problems.append(f"{label}: a non-code item finalised as strong")
        if row["kind"] == "free_text" and ev["strength"] != "medium":
            problems.append(f"{label}: free_text must stay medium (provisional)")
    return verdict(problems, f"{len(rows)} correct answer(s) finalised in the scratch copy within the table",
                   kinds=sorted({r["kind"] for r in rows}))


def _control_finalized():
    row = {"node": "n", "kind": "concept", "evidence": {"evidence_format": "recognition_mcq", "strength": "strong"}}
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"exam_passes": [row]}})())


@probe("r02.finalized_per_kind", "R2", kind="dynamic", cost="scratch", control=_control_finalized,
       title="a correct answer finalised on the scratch copy never exceeds the table")
def finalized_per_kind(ctx: Ctx) -> Result:
    return _finalized(ctx)


PROBES = [format_ceilings, client_strength_ignored, no_strength_in_payloads, finalized_per_kind]

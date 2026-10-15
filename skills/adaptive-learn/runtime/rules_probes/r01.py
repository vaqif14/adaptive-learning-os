"""R1 anti-fake-mastery: only runtime-verified, unassisted, checked evidence counts as strong; a studio build adds none."""
from __future__ import annotations

import copy

from .. import projection
from ..evidence_semantics import normalize_evidence_semantics
from ..session import SessionKernel
from .base import Ctx, Result, expect_refusal, probe, verdict

# Provenance sources whose evidence is a bare runtime check (not a declaration).
STRONG_SOURCES = frozenset({"course_exam_verifier", "python_verifier", "exercise_verifier", "workspace_check"})
PRACTICE_SOURCES = frozenset({"course_practice"})
AUTHOR_MARKERS = ("studio", "agent", "runner", "author")


def strong_fixture() -> dict:
    return {"outcome": "correct", "independence": "unassisted", "strength": "strong", "scope": "independent_reproduction",
            "evidence_format": "artifact_execution", "mastery_eligible": True, "correctness_checked": True,
            "support_provenance": {"hints_count": 0, "worked_example_shown": False, "ai_direct_answer_revealed": False,
                                   "conceptual_scaffold": False}}


def fabricated_fixtures() -> list[tuple[str, dict, dict]]:
    """(label, evidence payload, provenance): every one must NOT count as strong."""
    runtime = {"source": "course_exam_verifier"}
    out = []

    def add(label, provenance=None, **change):
        payload = strong_fixture()
        for key, value in change.items():
            if key == "support":
                payload["support_provenance"] = {**payload["support_provenance"], **value}
            else:
                payload[key] = value
        out.append((label, payload, provenance or runtime))
    add("self_reported_marker", verification="self_report")
    add("self_reported_source", provenance={"source": "cli_evidence_entry"})
    add("ai_generated_format", evidence_format="ai_generated_output")
    add("hinted_success", support={"hints_count": 1})
    add("revealed_solution", support={"ai_direct_answer_revealed": True})
    add("worked_example_shown", support={"worked_example_shown": True})
    add("attested", independence="assessed", strength="medium", scope="assessor_attested", mastery_eligible=False)
    add("no_runtime_check", correctness_checked=False)
    add("supported_completion_scope", scope="supported_completion")
    add("assisted_independence", independence="assisted")
    add("delegation_support", support_kind="cognitive_delegation")
    add("declared_not_strong", strength="medium")
    add("recognition_format_claims_strong", evidence_format="recognition_mcq")
    add("coverage_only", verification="term_coverage")
    return out


def _attest_call(ctx):
    """Call the real ``SessionKernel.attest`` with a disallowed assessor on a throwaway shell (no session needed:
    the assessor check raises before anything is read or written)."""
    def call():
        import tempfile
        from pathlib import Path

        class Shell(SessionKernel):
            def __init__(self):
                self.workspace = Path(tempfile.gettempdir())

            def dir(self, session_id):          # an existing, empty directory: attest never reaches the ledger
                return Path(tempfile.gettempdir())

            def _recover_state(self, session_id):
                return None
        SessionKernel.attest(Shell(), "rules-probe", "node", outcome="pass", assessor="self")
    return ctx.hook("attest_call", call)


def _fabricated(ctx: Ctx) -> Result:
    strong = ctx.hook("counts_as_strong", projection.counts_as_strong)
    problems = []
    if not strong(strong_fixture(), {"source": "course_exam_verifier"}):
        problems.append("a genuine strong fixture no longer counts as strong (the predicate is broken)")
    for label, payload, provenance in fabricated_fixtures():
        if strong(copy.deepcopy(payload), provenance):
            problems.append(f"fabricated fixture '{label}' counts as strong")
    refused, code = expect_refusal(_attest_call(ctx))
    if not refused:
        problems.append("attest accepted a disallowed assessor (self)")
    return verdict(problems, f"{len(fabricated_fixtures())} fabricated fixtures are rejected; attest refuses self-assessment")


def _control_fabricated():
    return Ctx.synthetic(hooks={"counts_as_strong": lambda e, p=None: True, "attest_call": lambda: None})


@probe("r01.fabricated_fixtures", "R1", control=_control_fabricated, title="fabricated evidence never counts as strong")
def fabricated_fixtures_probe(ctx: Ctx) -> Result:
    return _fabricated(ctx)


# ------------------------------------------------------------------ ledger replay
def _evidence(records):
    return [r for r in records if r["record_type"] == "evidence"]


def replay_problems(records: list[dict], state: dict | None, strong) -> list[str]:
    problems = []
    for rec in _evidence(records):
        p, prov = rec["payload"], rec["provenance"]
        source = str(prov.get("source"))
        if strong(p, prov):
            if source not in STRONG_SOURCES:
                problems.append(f"seq {rec['seq']}: strong evidence has no runtime-verifier source ({source})")
            if p.get("correctness_checked") is not True or p.get("independence") != "unassisted":
                problems.append(f"seq {rec['seq']}: strong evidence lacks a correctness check or unassisted independence")
        if source in PRACTICE_SOURCES or p.get("verification") == "walkthrough":
            if p.get("independence") == "unassisted" or p.get("scope") != "supported_completion" or p.get("mastery_eligible"):
                problems.append(f"seq {rec['seq']}: practice/walkthrough evidence claims independence or mastery")
            if strong(p, prov):
                problems.append(f"seq {rec['seq']}: practice/walkthrough evidence counts as strong")
        if any(m in source.lower() for m in AUTHOR_MARKERS) or p.get("authored_by") in {"studio", "agent"}:
            problems.append(f"seq {rec['seq']}: evidence written by '{source}' (a studio build must add none)")
    for rec in records:
        source = str(rec["provenance"].get("source")).lower()
        if rec["record_type"] in {"observation", "decision", "hypothesis"} and any(m in source for m in ("studio", "runner")):
            problems.append(f"seq {rec['seq']}: {rec['record_type']} written by '{source}'")
    for attempt in ((state or {}).get("attempts") or {}).values():
        if any(m in str(attempt.get("created_by", "")).lower() for m in ("studio", "agent")):
            problems.append(f"attempt {attempt.get('attempt_id')} was created by the studio")
    by_id = {r["payload"].get("evidence_id"): r for r in _evidence(records)}
    for nid, prog in ((state or {}).get("node_progress") or {}).items():
        if not prog.get("passed"):
            continue
        refs = [e for e in (state or {}).get("evidence_refs") or [] if e.get("node_id") == nid and e.get("outcome") == "correct"]
        rows = [by_id.get(e.get("evidence_id")) for e in refs]
        rows = [r for r in rows if r is not None]
        if not rows:
            problems.append(f"node {nid} is passed without a ledger evidence record")
            continue
        good = [r for r in rows if r["payload"].get("outcome") == "correct" and r["payload"].get("independence") == "unassisted"
                and str(r["provenance"].get("source")) in STRONG_SOURCES
                and (r["payload"].get("correctness_checked") is True or r["payload"].get("assessment_kind") == "free_text")]
        if not good:
            problems.append(f"node {nid} is passed without runtime-verified, unassisted evidence")
    return problems


def _replay(ctx: Ctx) -> Result:
    records = ctx.records()
    problems = replay_problems(records, ctx.state(), ctx.hook("counts_as_strong", projection.counts_as_strong))
    evidence = len(_evidence(records))
    return verdict(problems, f"{evidence} evidence record(s) replayed; none written by the studio", evidence_records=evidence)


def _control_replay():
    rec = {"seq": 3, "record_type": "evidence", "record_id": "evd_x", "payload": {**strong_fixture(), "evidence_id": "ev_x",
           "capability_id": "n"}, "provenance": {"source": "studio_runner"}}
    return Ctx.synthetic(records=[rec], state={"node_progress": {}, "evidence_refs": [], "attempts": {}})


@probe("r01.ledger_replay", "R1", control=_control_replay, title="every strong record is runtime-verified; studio added none")
def ledger_replay_probe(ctx: Ctx) -> Result:
    return _replay(ctx)


# ------------------------------------------------------------- adversarial (scratch)
def _inject(ledger, capability: str, **change):
    payload = {"evidence_id": f"ev_r1probe_{change.pop('tag')}", "capability_id": capability, **strong_fixture(), **change}
    provenance = payload.pop("_provenance", {"source": "cli_evidence_entry"})
    ledger.append("evidence", payload, provenance)


def _adversarial(ctx: Ctx) -> Result:
    scratch = ctx.require_scratch()
    led = scratch.led()
    cap = "r1-adversarial-probe"
    semantics = normalize_evidence_semantics("artifact_execution", "strong", "unassisted", self_reported=True)
    _inject(led, cap, tag="selfdeclared", verification="self_report", strength=semantics.normalized_strength)
    _inject(led, cap, tag="noverifier", strength="strong", correctness_checked=True)
    _inject(led, cap, tag="revealed", support_provenance={"hints_count": 0, "worked_example_shown": False,
                                                           "ai_direct_answer_revealed": True, "conceptual_scaffold": False})
    _inject(led, cap, tag="hinted", support_provenance={"hints_count": 2, "worked_example_shown": False,
                                                        "ai_direct_answer_revealed": False, "conceptual_scaffold": False})
    rebuild = ctx.hook("rebuild", lambda: projection.EvidenceProjector(led).rebuild())
    row = rebuild()["demonstrated_capabilities"].get(cap, {})
    problems = []
    if row.get("strong_unassisted_successes", 0) != 0:
        problems.append(f"{row['strong_unassisted_successes']} fabricated success(es) were projected as strong")
    if row.get("evidence_count") != 4:
        problems.append("the injected fabricated records were not all projected (probe is inconclusive)")
    return verdict(problems, "self-declared, unverified, revealed and hinted successes project as zero strong evidence")


class _StrongProjection:
    def __init__(self):
        self.led_records = []

    def led(self):
        return self

    def append(self, *a, **k):
        return {}


def _control_adversarial():
    scratch = type("S", (), {"led": lambda self: _StrongProjection()})()
    return Ctx.synthetic(scratch=scratch, hooks={"rebuild": lambda: {"demonstrated_capabilities": {
        "r1-adversarial-probe": {"strong_unassisted_successes": 4, "evidence_count": 4}}}})


@probe("r01.adversarial_injection", "R1", kind="dynamic", cost="scratch", control=_control_adversarial,
       title="injected self-declared / revealed / hinted claims project as zero strong evidence")
def adversarial_probe(ctx: Ctx) -> Result:
    return _adversarial(ctx)


PROBES = [fabricated_fixtures_probe, ledger_replay_probe, adversarial_probe]

"""R8 cognitive-delegation gate: no leak of solutions/keys, demos that do not solve the task, exam lock, hint ceiling."""
from __future__ import annotations

import re

from .. import course_content as cc
from .. import projection
from ..cognitive_delegation import CognitiveWorkContract, DelegationContext, evaluate_delegation
from .base import Ctx, Result, dumps, journey_obs, na, probe, verdict

MIN_SECRET = 12
FORBIDDEN_PUBLIC_KEYS = frozenset({"answer", "checks", "reference_solution", "rubric", "exemplar", "misconceptions",
                                   "wrong_examples", "detector", "hidden_checks", "answer_key"})


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def secrets_of(subject: dict) -> list[tuple[str, str]]:
    """What must never reach a learner: reference solutions, exam check code, detectors, wrong examples, exemplars."""
    out = []
    lesson = subject.get("lesson") or {}
    practice = lesson.get("practice") or {}
    if practice.get("reference_solution"):
        out.append((f"{subject['node']}: practice reference solution", practice["reference_solution"]))
    for mid, wrong in (practice.get("wrong_examples") or {}).items():
        out.append((f"{subject['node']}: practice wrong example {mid}", wrong))
    for item in subject.get("items") or []:
        iid = f"{subject['node']}: item {item.get('item_id')}"
        if item.get("reference_solution"):
            out.append((f"{iid}: reference solution", item["reference_solution"]))
        for check in item.get("checks") or []:
            out.append((f"{iid}: hidden check {check.get('id')}", check.get("code", "")))
        for mid, wrong in (item.get("wrong_examples") or {}).items():
            out.append((f"{iid}: wrong example {mid}", wrong))
        for m in item.get("misconceptions") or []:
            if isinstance(m, dict) and m.get("detector"):
                out.append((f"{iid}: detector {m.get('id')}", m["detector"]))
        if item.get("exemplar"):
            out.append((f"{iid}: rubric exemplar", item["exemplar"]))
    return [(label, text) for label, text in out if isinstance(text, str) and len(_norm(text)) >= MIN_SECRET]


def learner_view(subject: dict, public_item=None, public_ladder=None) -> dict:
    """Everything the learner can be shown for this node, assembled the way the runtime assembles it."""
    public_item = public_item or cc.public_item
    public_ladder = public_ladder or cc.public_ladder_rung
    lesson = subject.get("lesson") or {}
    view = {"lesson": {k: lesson.get(k) for k in ("objective", "success_criteria", "explanation", "why", "worked_example",
                                                   "alternative_explanation", "self_explanation_prompt")}}
    practice = lesson.get("practice") or {}
    pv = {k: practice.get(k) for k in ("prompt_md", "scenario_md", "starter_code", "entry", "hints")}
    pv["visible_checks"] = [{"id": c.get("id"), "name": c.get("name")} for c in practice.get("checks") or []]
    if practice.get("questions"):
        pv["questions"] = cc.public_questions(practice["questions"])
    view["practice"] = pv
    if isinstance(lesson.get("walkthrough"), dict):
        view["walkthrough"] = cc.public_walkthrough(lesson["walkthrough"])
    if isinstance(lesson.get("ladder"), dict):
        view["ladder"] = [public_ladder(lesson["ladder"], rung) for rung in cc.LADDER_RUNGS]
    view["items"] = [public_item(item) for item in subject.get("items") or []]
    return view


def leak_problems(subjects: list[dict], public_item=None, public_ladder=None) -> list[str]:
    problems = []
    for subject in subjects:
        try:
            view = learner_view(subject, public_item, public_ladder)
        except (KeyError, TypeError, AttributeError) as e:
            problems.append(f"{subject['node']}: the learner view could not be assembled ({type(e).__name__}: {e})")
            continue
        blob = _norm(dumps(view))
        for label, secret in secrets_of(subject):
            if _norm(secret) in blob or _norm(dumps(secret)[1:-1]) in blob:
                problems.append(f"{label} appears in a learner-visible payload")
        stack = [view]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                bad = FORBIDDEN_PUBLIC_KEYS & set(node)
                if bad:
                    problems.append(f"{subject['node']}: a learner payload carries key(s) {sorted(bad)}")
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return problems


def _public(ctx: Ctx) -> Result:
    subjects = [s for s in ctx.subjects() if s.get("lesson") or s.get("items")]
    if not subjects:
        return na("no content in the build or in the live course")
    problems = leak_problems(subjects, ctx.hook("public_item", None), ctx.hook("public_ladder", None))
    return verdict(problems, f"{len(subjects)} node(s): no reference solution, check code, key, rubric exemplar or distractor "
                             "map in any learner-visible payload")


def _control_public():
    ref = "def solve(values):\n    return sorted(values)[len(values) // 2]\n"
    lesson = {"explanation": {"short": "s", "body": "Here is the answer: " + ref}, "practice": {}}
    item = {"item_id": "x", "reference_solution": ref, "entry": "solution.py", "starter_code": "def solve(values):\n    pass\n",
            "checks": [{"id": "c", "category": "core", "code": "assert solve([3, 1, 2]) == 2"}],
            "title": "t", "prompt_md": "p", "scope": "near_transfer"}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": [item]}])


@probe("r08.public_payload_clean", "R8", control=_control_public,
       title="learner-visible payloads carry no solution, check, key, exemplar or distractor map")
def public_payload_clean(ctx: Ctx) -> Result:
    return _public(ctx)


# ------------------------------------------------------------------ answer leak (sandbox)
def leak_findings(subjects: list[dict], first_leak) -> list[str]:
    problems = []
    for s in subjects:
        lesson, items = s.get("lesson") or {}, [i for i in (s.get("items") or []) if isinstance(i, dict)]
        practice = lesson.get("practice")
        wt, ld = lesson.get("walkthrough"), lesson.get("ladder")
        targets = cc._leak_targets(practice, items)
        if isinstance(wt, dict) and wt.get("language") == "python":
            snippets = cc._walkthrough_snippets(wt["steps"], wt["mistake"]["step"], wt["mistake"].get("wrong_code"))
            leak = first_leak(snippets, [cc.walkthrough_text(wt)], targets)
            if leak:
                problems.append(f"{s['node']}: the walkthrough {leak[1]} {leak[0]}")
        if isinstance(ld, dict) and practice is not None:
            python = cc.item_kind(practice) == "python"
            if python:
                leak = first_leak(cc._ladder_snippets(ld, include_completion=False), cc._worked_blobs(ld),
                                  cc._leak_targets(practice, []))
                if leak:
                    problems.append(f"{s['node']}: the ladder's worked rung {leak[1]} {leak[0]}")
            leak = first_leak(cc._ladder_snippets(ld, include_completion=python), [cc.ladder_text(ld)], cc._leak_targets(None, items))
            if leak:
                problems.append(f"{s['node']}: a ladder rung {leak[1]} {leak[0]}")
    return problems


def _answer_leak(ctx: Ctx) -> Result:
    subjects = [s for s in ctx.subjects() if isinstance(s.get("lesson"), dict)
                and (s["lesson"].get("walkthrough") or s["lesson"].get("ladder"))]
    if not subjects:
        return na("no walkthrough or ladder to compare against the practice and exam items")
    problems = leak_findings(subjects, ctx.hook("first_leak", cc._first_leak))
    return verdict(problems, f"{len(subjects)} node(s): walkthrough and ladder code fail the practice and exam checks and "
                             "embed no reference solution (every cumulative step, mistake and rung)")


def _control_answer_leak():
    wt = {"language": "python", "title": "t", "scenario_md": "s", "transcript_md": "t",
          "steps": [{"explain_md": "e", "code": "print(1)"}], "mistake": {"step": 1, "symptom": "x", "fix_explain": "y"}}
    lesson = {"walkthrough": wt, "practice": {"checks": [{"id": "c", "code": "assert True"}], "reference_solution": "x = 1"}}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": None}],
                         hooks={"first_leak": lambda snippets, blobs, targets: ("the practice checks", "passes")})


@probe("r08.walkthrough_does_not_solve", "R8", cost="sandbox", control=_control_answer_leak,
       title="walkthrough and ladder code fail the practice and exam checks")
def walkthrough_does_not_solve(ctx: Ctx) -> Result:
    return _answer_leak(ctx)


# ------------------------------------------------------------------ delegation
def _delegation(ctx: Ctx) -> Result:
    evaluate = ctx.hook("evaluate_delegation", evaluate_delegation)
    contract = CognitiveWorkContract(target_capability="course node", protected_cognition=["solve the practice"],
                                     independence_required=True)
    problems = []
    for role, support in (("replace", "none"), ("replace", "accessibility")):
        decision = evaluate(DelegationContext(goal_mode="learn", ai_role=role, requested_work=["solve the practice"],
                                              support_kind=support, contract=contract))
        if decision.allowed:
            problems.append(f"delegation with role '{role}' and support '{support}' was allowed on protected cognition")
    decision = evaluate(DelegationContext(goal_mode="learn", ai_role="delegate", requested_work=["Solve the practice"],
                                          support_kind="none", contract=contract))
    if decision.allowed:
        problems.append("delegating the protected work (case-variant) was allowed")
    return verdict(problems, "a 'do it for me' on a protected cognition is blocked, also when labelled accessibility")


def _control_delegation():
    allow = lambda ctx: type("D", (), {"allowed": True})()      # noqa: E731
    return Ctx.synthetic(hooks={"evaluate_delegation": allow})


@probe("r08.delegation_blocked", "R8", control=_control_delegation, title="replacing protected cognition is blocked")
def delegation_blocked(ctx: Ctx) -> Result:
    return _delegation(ctx)


# ----------------------------------------------------------- scratch observations
def _obs(ctx: Ctx, key: str):
    return journey_obs(ctx, key)


def _exam_lock(ctx: Ctx) -> Result:
    rows = _obs(ctx, "exam_lock")
    if not rows:
        return na("no exam could be opened in the scratch copy")
    problems = [f"'{name}' was not refused during an active exam" for name, row in rows.items() if not row.get("refused")]
    return verdict(problems, f"{len(rows)} teaching/help/evidence/authoring channel(s) refuse during the exam lock",
                   channels=sorted(rows))


def _control_exam_lock():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"exam_lock": {"help": {"refused": False},
                                                                      "practice_reveal": {"refused": True}}}})())


@probe("r08.exam_lock", "R8", kind="dynamic", cost="scratch", control=_control_exam_lock,
       title="help, lessons, reveal, sources-ask, roadmap and studio refuse during an exam")
def exam_lock(ctx: Ctx) -> Result:
    return _exam_lock(ctx)


def _hints(ctx: Ctx) -> Result:
    obs = _obs(ctx, "hints")
    if obs.get("not_applicable"):
        return na(obs["not_applicable"])
    problems = []
    if not obs.get("ceiling_refused"):
        problems.append("a hint beyond the hint ceiling was served")
    if not obs.get("reveal_unconfirmed_refused"):
        problems.append("the solution was revealed without explicit confirmation")
    if not obs.get("revealed_check_refused"):
        problems.append("a revealed attempt could still earn practice credit")
    hinted = obs.get("hinted_evidence") or {}
    payload = hinted.get("payload")
    if payload is None and obs.get("hinted_independence") is None:
        problems.append("no evidence was recorded after a hint (the probe is inconclusive)")
    elif (payload or {}).get("independence", obs.get("hinted_independence")) in (None, "unassisted"):
        problems.append("evidence after a hint claims independence 'unassisted'")
    strong = ctx.hook("counts_as_strong", projection.counts_as_strong)
    if payload is not None and strong(payload, hinted.get("provenance")):
        problems.append("evidence after a hint counted as strong")
    return verdict(problems, "the hint ceiling holds, reveal needs confirmation and costs the credit, hints make evidence assisted")


def _control_hints():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"hints": {"ceiling_refused": False, "hinted_independence": "unassisted"}}})())


@probe("r08.hint_ceiling_and_reveal", "R8", kind="dynamic", cost="scratch", control=_control_hints,
       title="hint ceiling, confirmed reveal and assisted evidence")
def hint_ceiling_and_reveal(ctx: Ctx) -> Result:
    return _hints(ctx)


def _canary(ctx: Ctx) -> Result:
    obs = _obs(ctx, "canary")
    if not obs.get("planted"):
        return na("no vault or lesson file could carry a canary in the scratch copy")
    problems = []
    if not obs.get("payloads"):
        problems.append("no learner payload was collected (the probe is inconclusive)")
    for where in obs.get("found_in", []):
        problems.append(f"canary token leaked in learner payload '{where}'")
    for leak in obs.get("secret_leaks", []):
        problems.append(f"a reference solution / hidden check leaked in learner payload {leak}")
    return verdict(problems, f"canary tokens planted in {obs.get('planted')} vault/lesson file(s) never appear in "
                             f"{obs.get('payloads')} learner payload(s)")


def _control_canary():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"canary": {"planted": 3, "payloads": 5, "found_in": ["help.worked_example"]}}})())


@probe("r08.canary_tokens", "R8", kind="dynamic", cost="scratch", control=_control_canary,
       title="planted canary tokens never reach a learner payload")
def canary_tokens(ctx: Ctx) -> Result:
    return _canary(ctx)


PROBES = [public_payload_clean, walkthrough_does_not_solve, delegation_blocked, exam_lock, hint_ceiling_and_reveal,
          canary_tokens]

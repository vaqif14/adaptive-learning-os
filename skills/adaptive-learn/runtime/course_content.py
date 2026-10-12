"""Course content: lesson/item validation, private vault, CI-style checks.

Teaching content is authored by the host agent (the visible teacher), but it
only enters a course after deterministic validation: non-empty explanation,
a worked example that is NOT an assessment item, a hint ladder, misconception
probes and grounded source quotes. Assessment items (answer keys, hidden and
transfer checks, reference solutions) live in a private vault outside the
learner workspace and outside the UI's static directory; only the prompt and
starter file of an item are ever copied into the learner workspace, and only
when its exam starts. Checks run through the sandboxed Python verifier one by
one so results read like a CI job (one line per check).

Honest boundary: the vault is application-level separation on the learner's
own device. The device owner can open these files; separation prevents
accidental exposure and keeps the app/agent from leaking them, nothing more.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .course_state import digest
from .verifiers.python_runner import PythonVerifier

_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_ENTRY = re.compile(r"[a-z][a-z0-9_]{0,40}\.py")
CHECK_CATEGORIES = ("core", "edge", "transfer")
SCOPES = ("independent_reproduction", "near_transfer", "far_transfer")


class ContentError(ValueError):
    pass


def _text(obj, path, *, min_len=1):
    if not isinstance(obj, str) or len(obj.strip()) < min_len:
        raise ContentError(f"{path} must be non-empty text (>= {min_len} chars)")
    return obj


def _code(code, path):
    _text(code, path)
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise ContentError(f"{path} is not valid Python: {e.msg} (line {e.lineno})")
    return code


def _shingles(text: str, n: int = 3) -> set:
    words = re.findall(r"\w+", (text or "").casefold())
    return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _checks(checks, path, *, categories=False):
    if not isinstance(checks, list) or not checks:
        raise ContentError(f"{path} needs at least one check")
    seen = set()
    for i, c in enumerate(checks):
        if not isinstance(c, dict):
            raise ContentError(f"{path}[{i}] must be an object")
        cid = c.get("id")
        if not isinstance(cid, str) or not _ID.fullmatch(cid) or cid in seen:
            raise ContentError(f"{path}[{i}].id must be a unique slug")
        seen.add(cid)
        _text(c.get("name"), f"{path}[{i}].name")
        _code(c.get("code"), f"{path}[{i}].code")
        _text(c.get("fail_feedback"), f"{path}[{i}].fail_feedback")
        if c.get("probe") is not None:
            _code(c["probe"], f"{path}[{i}].probe")
        if categories and c.get("category") not in CHECK_CATEGORIES:
            raise ContentError(f"{path}[{i}].category must be one of {CHECK_CATEGORIES}")
    return checks


def _misconceptions(items, path):
    if not isinstance(items, list) or not items:
        raise ContentError(f"{path} needs at least one misconception probe")
    for i, m in enumerate(items):
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not _ID.fullmatch(m["id"]):
            raise ContentError(f"{path}[{i}].id must be a slug")
        _text(m.get("name"), f"{path}[{i}].name")
        _code(m.get("detector"), f"{path}[{i}].detector")
        _text(m.get("feedback"), f"{path}[{i}].feedback", min_len=20)
    return items


def _refs(refs, path, ground):
    """Each ref is a grounded source quote or an explicitly supplementary note."""
    if not isinstance(refs, list) or not refs:
        raise ContentError(f"{path}.refs needs at least one source quote or supplementary note")
    out = []
    for i, r in enumerate(refs):
        if not isinstance(r, dict):
            raise ContentError(f"{path}.refs[{i}] must be an object")
        if r.get("supplementary") is True:
            out.append({"supplementary": True, "note": _text(r.get("note"), f"{path}.refs[{i}].note"),
                        "grounding": "supplementary"})
            continue
        result = ground(r.get("source_id"), r.get("quote"))
        if not result.get("grounded"):
            raise ContentError(f"{path}.refs[{i}] not grounded: {result.get('reason')}")
        out.append({"source_id": r["source_id"], "quote": r["quote"], "grounding": "source",
                    "stale_source": bool(result.get("stale"))})
    return out


def validate_lesson(lesson: dict, node: dict, ground, items: list[dict]) -> dict:
    """Return a normalized lesson or raise ContentError. ``ground(sid, quote)`` checks quotes."""
    if not isinstance(lesson, dict) or lesson.get("node_id") != node["id"]:
        raise ContentError("lesson.node_id must match the roadmap node")
    out = {"node_id": node["id"], "title": node.get("title")}
    out["objective"] = _text(lesson.get("objective"), "objective", min_len=10)
    crit = lesson.get("success_criteria")
    if not isinstance(crit, list) or not crit or not all(isinstance(c, str) and c.strip() for c in crit):
        raise ContentError("success_criteria must list what a correct answer demonstrates")
    out["success_criteria"] = crit
    ex = lesson.get("explanation") or {}
    out["explanation"] = {"short": _text(ex.get("short"), "explanation.short", min_len=40),
                          "body": _text(ex.get("body"), "explanation.body", min_len=120),
                          "refs": _refs(ex.get("refs"), "explanation", ground)}
    why = lesson.get("why") or {}
    out["why"] = {"text": _text(why.get("text"), "why.text", min_len=40), "refs": _refs(why.get("refs"), "why", ground)}
    we = lesson.get("worked_example") or {}
    steps = we.get("steps")
    if not isinstance(steps, list) or len(steps) < 2 or not all(isinstance(s, str) and s.strip() for s in steps):
        raise ContentError("worked_example.steps needs at least two non-empty steps")
    out["worked_example"] = {"title": _text(we.get("title"), "worked_example.title"),
                             "problem": _text(we.get("problem"), "worked_example.problem", min_len=20),
                             "steps": steps, "result": _text(we.get("result"), "worked_example.result"),
                             "refs": _refs(we.get("refs"), "worked_example", ground)}
    alt = lesson.get("alternative_explanation") or {}
    out["alternative_explanation"] = {"text": _text(alt.get("text"), "alternative_explanation.text", min_len=60),
                                      "example": _text(alt.get("example"), "alternative_explanation.example"),
                                      "refs": _refs(alt.get("refs"), "alternative_explanation", ground)}
    out["self_explanation_prompt"] = _text(lesson.get("self_explanation_prompt"), "self_explanation_prompt", min_len=20)
    terms = lesson.get("self_explanation_key_terms")
    if not isinstance(terms, list) or len(terms) < 3 or not all(isinstance(t, str) and t.strip() for t in terms):
        raise ContentError("self_explanation_key_terms needs at least three terms")
    out["self_explanation_key_terms"] = terms
    pr = lesson.get("practice") or {}
    if not isinstance(pr.get("task_id"), str) or not _ID.fullmatch(pr["task_id"]):
        raise ContentError("practice.task_id must be a slug")
    entry = pr.get("entry", "practice.py")
    if not _ENTRY.fullmatch(entry):
        raise ContentError("practice.entry must be a simple .py filename")
    hints = pr.get("hints")
    if not isinstance(hints, list) or not hints or not all(isinstance(h, str) and h.strip() for h in hints):
        raise ContentError("practice.hints needs a non-empty hint ladder")
    out["practice"] = {
        "task_id": pr["task_id"], "entry": entry,
        "prompt_md": _text(pr.get("prompt_md"), "practice.prompt_md", min_len=40),
        "starter_code": _code(pr.get("starter_code"), "practice.starter_code"),
        "hints": hints,
        "checks": _checks(pr.get("checks"), "practice.checks"),
        "misconceptions": _misconceptions(pr.get("misconceptions"), "practice.misconceptions"),
        "reference_solution": _code(pr.get("reference_solution"), "practice.reference_solution"),
        "wrong_examples": pr.get("wrong_examples") or {},
    }
    # Separation: the worked example and practice must not be an assessment item.
    shown = " ".join([we.get("problem", ""), " ".join(steps), we.get("result", ""), pr.get("prompt_md", "")])
    for item in items:
        if similarity(shown, item["prompt_md"]) >= 0.35 or _norm(item["prompt_md"])[:80] in _norm(shown):
            raise ContentError(f"worked example/practice overlaps assessment item {item['item_id']}; use a different example")
    _self_test(out["practice"], "practice")
    out["content_version"] = digest("lc", out)
    return out


def validate_items(payload: dict, node: dict, lesson: dict | None) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("node_id") != node["id"]:
        raise ContentError("items.node_id must match the roadmap node")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ContentError("items must be a non-empty list")
    out, seen = [], set()
    for i, it in enumerate(items):
        path = f"items[{i}]"
        if not isinstance(it, dict) or not isinstance(it.get("item_id"), str) or not _ID.fullmatch(it["item_id"]):
            raise ContentError(f"{path}.item_id must be a slug")
        if it["item_id"] in seen:
            raise ContentError(f"{path}.item_id duplicated")
        seen.add(it["item_id"])
        if it.get("scope") not in SCOPES:
            raise ContentError(f"{path}.scope must be one of {SCOPES}")
        entry = it.get("entry", "solution.py")
        if not _ENTRY.fullmatch(entry):
            raise ContentError(f"{path}.entry must be a simple .py filename")
        checks = _checks(it.get("checks"), f"{path}.checks", categories=True)
        cats = {c["category"] for c in checks}
        if "core" not in cats or "transfer" not in cats:
            raise ContentError(f"{path} needs at least one core and one unseen transfer check")
        norm = {
            "item_id": it["item_id"], "node_id": node["id"], "scope": it["scope"],
            "difficulty": it.get("difficulty", 1), "entry": entry,
            "title": _text(it.get("title"), f"{path}.title"),
            "prompt_md": _text(it.get("prompt_md"), f"{path}.prompt_md", min_len=40),
            "starter_code": _code(it.get("starter_code"), f"{path}.starter_code"),
            "checks": checks,
            "misconceptions": _misconceptions(it.get("misconceptions"), f"{path}.misconceptions"),
            "reference_solution": _code(it.get("reference_solution"), f"{path}.reference_solution"),
            "wrong_examples": it.get("wrong_examples") or {},
        }
        if lesson is not None:
            shown = " ".join([lesson["worked_example"]["problem"], " ".join(lesson["worked_example"]["steps"]),
                              lesson["practice"]["prompt_md"]])
            if similarity(shown, norm["prompt_md"]) >= 0.35:
                raise ContentError(f"{path} overlaps the worked example/practice; an exam item must be new")
        _self_test(norm, path)
        norm["item_version"] = digest("it", norm)
        out.append(norm)
    return out


def _self_test(spec: dict, path: str) -> None:
    """Checks must pass on the reference; probes stay silent on it and fire on wrong examples."""
    ref = spec["reference_solution"]
    failing = [r["id"] for r in run_checks(ref, spec["checks"]) if not r["passed"]]
    if failing:
        raise ContentError(f"{path}: reference solution fails checks {failing}")
    fired = detect_misconceptions(ref, spec["misconceptions"])
    if fired:
        raise ContentError(f"{path}: misconception probes fire on the reference solution: {[m['id'] for m in fired]}")
    for mid, wrong in (spec.get("wrong_examples") or {}).items():
        if not any(m["id"] == mid for m in detect_misconceptions(wrong, spec["misconceptions"])):
            raise ContentError(f"{path}: probe {mid} does not detect its wrong example")


_VERIFIER = PythonVerifier(timeout_sec=3.0)
_REPORTABLE_ERRORS = frozenset({
    "ZeroDivisionError", "TypeError", "ValueError", "IndexError", "KeyError", "AttributeError", "NameError",
    "RecursionError", "OverflowError", "AssertionError", "StopIteration", "UnboundLocalError", "ImportError",
    "MemoryError", "RuntimeError", "NotImplementedError", "SyntaxError",
})


def _error_name(stderr: str) -> str:
    m = re.match(r"\s*(\w+)", stderr or "")
    name = m.group(1) if m else ""
    return f"{name} xətası" if name in _REPORTABLE_ERRORS else "Kod icra zamanı xəta verdi."


def _probe_value(code: str, probe: str) -> str | None:
    trusted = f"_probe = ({probe.strip()})\nraise AssertionError('PROBE:' + repr(_probe)[:160])"
    res = _VERIFIER.verify(code, trusted_test_code=trusted)
    m = re.search(r"PROBE:(.*)", res.stderr or "")
    return m.group(1).strip().splitlines()[0] if m else None


def run_checks(code: str, checks: list[dict], *, reveal: bool = True) -> list[dict]:
    """One verifier run per check (CI-style). ``reveal=False`` hides observed values."""
    _tree, syntax_error, policy_errors = _VERIFIER.syntax_and_policy(code)
    if syntax_error or policy_errors:
        detail = (f"Sintaksis xətası, sətir {syntax_error.get('line')}: {syntax_error.get('message')}" if syntax_error
                  else "İcazəsiz əməliyyat: " + ", ".join(policy_errors))
        return [{"id": "build", "name": "Kod oxunur və icra oluna bilir", "category": "build",
                 "passed": False, "feedback": detail, "error": None, "observed": None}]
    results = []
    for c in checks:
        res = _VERIFIER.verify(code, trusted_test_code=c["code"])
        passed = bool(res.passed and res.correctness_checked)
        error = None
        if not passed:
            if res.timed_out:
                error = "Vaxt limiti aşıldı (sonsuz dövr ola bilər)."
            elif res.stderr and "trusted_test_failed" not in res.stderr:
                error = res.stderr.strip().splitlines()[0][:200] if reveal else _error_name(res.stderr)
            elif res.stderr:
                inner = re.search(r"trusted_test_failed: (\w+)", res.stderr)
                # Only builtin names: a submission could raise a crafted exception class
                # whose name encodes hidden check inputs.
                if inner and inner.group(1) in _REPORTABLE_ERRORS and inner.group(1) != "AssertionError":
                    error = f"Kodun yoxlama zamanı {inner.group(1)} xətası verdi."
                elif inner and inner.group(1) != "AssertionError":
                    error = "Kodun yoxlama zamanı xəta verdi."
        row = {"id": c["id"], "name": c["name"], "category": c.get("category", "visible"), "passed": passed,
               "feedback": None if passed else c["fail_feedback"], "error": error, "observed": None}
        if not passed and reveal and c.get("probe"):
            row["observed"] = _probe_value(code, c["probe"])
            row["expected"] = c.get("expected_text")
        results.append(row)
    return results


def detect_misconceptions(code: str, probes: list[dict]) -> list[dict]:
    fired = []
    for m in probes:
        res = _VERIFIER.verify(code, trusted_test_code=m["detector"])
        if res.passed and res.correctness_checked:
            fired.append({"id": m["id"], "name": m["name"], "feedback": m["feedback"]})
    return fired


def public_item(item: dict) -> dict:
    """What the learner may see when the exam starts (no checks, keys or probes)."""
    return {"item_id": item["item_id"], "title": item["title"], "prompt_md": item["prompt_md"],
            "entry": item["entry"], "starter_code": item["starter_code"], "scope": item["scope"],
            "check_count": len(item["checks"]),
            "check_categories": sorted({c["category"] for c in item["checks"]})}


def public_lesson_summary(lesson: dict | None) -> dict:
    if not lesson:
        return {"status": "missing"}
    grounding = [r.get("grounding") for part in ("explanation", "why", "worked_example", "alternative_explanation")
                 for r in lesson[part]["refs"]]
    return {"status": "ready", "content_version": lesson["content_version"],
            "objective": lesson["objective"], "success_criteria": lesson["success_criteria"],
            "grounding": "source" if all(g == "source" for g in grounding)
            else ("supplementary_only" if all(g == "supplementary" for g in grounding) else "mixed")}


def write_private(path: Path, obj) -> None:
    from .utils import write_json
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, obj)

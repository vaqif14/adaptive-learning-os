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

Item kinds: ``python`` (hidden checks run by the sandboxed verifier), ``concept`` and
``scenario`` (selected/constructed responses graded deterministically against an
answer key) and ``free_text`` (an open response that is never keyword-scored: it
waits for an explicit rubric judgment whose every decision cites the response).

Freshness is content identity, not an id: every item gets a canonical content
fingerprint (ids, titles, order, check names and whitespace/markdown excluded) and
a behaviour fingerprint (the hidden checks / question keys / rubric). An exact or
behavioural match with something the learner has already been examined on is a
duplicate; a lexical or partial-check overlap is a near-duplicate that needs an
explicit review and never counts as fresh by default.

Honest boundary: the vault is application-level separation on the learner's
own device. The device owner can open these files; separation prevents
accidental exposure and keeps the app/agent from leaking them, nothing more.
"""
from __future__ import annotations

import ast
import hashlib
import math
import re
import unicodedata
from pathlib import Path

from .course_state import digest
from .verifiers.python_runner import PythonVerifier

_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_ENTRY = re.compile(r"[a-z][a-z0-9_]{0,40}\.py")
_OPT = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
CHECK_CATEGORIES = ("core", "edge", "transfer")
SCOPES = ("independent_reproduction", "near_transfer", "far_transfer")
KINDS = ("python", "concept", "scenario", "free_text")
QUESTION_TYPES = ("single_choice", "multi_choice", "ordering", "numeric")
# Evidence format per non-code kind. Strength is capped at medium in the course runtime:
# selected responses are recognition, open responses are judged by the host agent.
KIND_FORMAT = {"concept": "free_recall", "scenario": "scenario_performance", "free_text": "explanation"}
SELECTED_ONLY_FORMAT = "recognition_mcq"
FREE_TEXT_LIMITS = (20, 6000)
# Freshness heuristics (policy-versioned so thresholds can change without rewriting
# history). They are design heuristics to calibrate on real item pairs, not findings.
FRESHNESS_POLICY = "fresh-v1"
SIG_OVERLAP_REVIEW = 0.5
PROMPT_SIM_REVIEW = 0.5
CHAR_SIM_REVIEW = 0.6


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


def canonical_text(text) -> str:
    """Case/markdown/punctuation/whitespace-insensitive text (NFKC; dotted İ folds to i)."""
    t = unicodedata.normalize("NFKC", text if isinstance(text, str) else "").casefold().replace("\u0307", "")
    # Signed/decimal numbers stay whole tokens ("-1" != "1", "0.5" != "05"); other
    # punctuation and markdown are separators.
    return " ".join(re.findall(r"-?\d+(?:[.,]\d+)*|[^\W_]+", t))


def char_similarity(a: str, b: str, n: int = 5) -> float:
    ga = {a[i:i + n] for i in range(max(0, len(a) - n + 1))}
    gb = {b[i:i + n] for i in range(max(0, len(b) - n + 1))}
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def _sha16(obj) -> str:
    return hashlib.sha256(repr(obj).encode("utf-8")).hexdigest()[:16]


def check_signature(code: str) -> str:
    """Behaviour of one hidden check: its AST (comments, spacing, ids and names ignored)."""
    return _sha16(ast.dump(ast.parse(code)))


def item_kind(item: dict) -> str:
    kind = item.get("kind", "python")
    return "python" if kind in (None, "python", "code") else kind


def _question_signature(q: dict) -> str:
    texts = {o["id"]: canonical_text(o["text"]) for o in q.get("options") or []}
    if q["type"] == "numeric":
        answer = [q["answer"], q.get("tolerance", 0)]
    elif q["type"] == "single_choice":
        answer = texts.get(q["answer"])
    elif q["type"] == "multi_choice":
        answer = sorted(texts.get(a) for a in q["answer"])
    else:
        answer = [texts.get(a) for a in q["answer"]]
    return _sha16([q["type"], canonical_text(q["prompt"]), sorted(texts.values()), answer])


def fingerprint(item: dict) -> dict:
    """Identity-independent fingerprints of an assessment item.

    ``content``: what the learner sees and is checked on. ``behavior``: what is checked
    (hidden checks / answer keys / rubric) — two items with the same behaviour assess
    the same thing whatever their prompt wording.
    """
    kind = item_kind(item)
    prompt = canonical_text(item.get("prompt_md"))
    if kind == "python":
        sigs = sorted({check_signature(c["code"]) for c in item["checks"]})
        core = {"kind": kind, "checks": sigs}
    elif kind in ("concept", "scenario"):
        sigs = sorted({_question_signature(q) for q in item["questions"]})
        core = {"kind": kind, "questions": sigs, "scenario": canonical_text(item.get("scenario_md"))}
    else:
        sigs = sorted({_sha16(canonical_text(r["criterion"])) for r in item["rubric"]})
        core = {"kind": kind, "rubric": sigs, "prompt": prompt}
    return {"content": digest("ifp", {**core, "prompt": prompt}), "behavior": digest("ibh", core),
            "sigs": sigs, "prompt_norm": prompt, "surface": surface_text(item), "kind": kind,
            "policy": FRESHNESS_POLICY}


def surface_text(item: dict) -> str:
    """Canonical text of everything the learner reads in an item (prompt, scenario,
    question stems and options) — the basis for lexical near-duplicate detection."""
    parts = [item.get("prompt_md"), item.get("scenario_md")]
    for q in item.get("questions") or []:
        parts.append(q.get("prompt"))
        parts.extend(o.get("text") for o in q.get("options") or [])
    return canonical_text(" ".join(p for p in parts if isinstance(p, str)))


def practice_signatures(practice: dict) -> set:
    """Behaviour signatures of a lesson's guided practice (visible checks / question keys)."""
    if item_kind(practice) == "python":
        return {check_signature(c["code"]) for c in practice.get("checks") or []}
    return {_question_signature(q) for q in practice.get("questions") or []}


def classify_freshness(fp: dict, exposed: list[dict]) -> dict:
    """``fresh`` | ``needs_review`` | ``duplicate`` against what the learner was examined on.

    Uncertainty never establishes freshness: any heuristic overlap is ``needs_review``.
    Exposure records whose content is unknown (legacy, vault file missing) are listed
    as unverifiable so a reviewer can see them.
    """
    status, matches, unknown = "fresh", [], []
    mine = fp.get("surface") or fp["prompt_norm"]
    for e in exposed:
        if e.get("unknown"):
            unknown.append(e.get("item_id"))
            continue
        if fp["content"] == e.get("content_fingerprint") or fp["behavior"] == e.get("behavior_fingerprint"):
            matches.append({"item_id": e.get("item_id"), "relation": "duplicate"})
            status = "duplicate"
            continue
        a, b = set(fp["sigs"]), set(e.get("sigs") or [])
        overlap = len(a & b) / len(a | b) if a and b else 0.0
        theirs = e.get("surface") or e.get("prompt_norm", "")
        psim = similarity(mine, theirs)
        csim = char_similarity(mine, theirs)
        if overlap >= SIG_OVERLAP_REVIEW or psim >= PROMPT_SIM_REVIEW or csim >= CHAR_SIM_REVIEW:
            matches.append({"item_id": e.get("item_id"), "relation": "near_duplicate", "check_overlap": round(overlap, 3),
                            "prompt_similarity": round(psim, 3), "char_similarity": round(csim, 3)})
            if status == "fresh":
                status = "needs_review"
    if unknown and status == "fresh":
        # Something the learner saw cannot be compared (legacy record, vault file missing):
        # uncertainty must not establish freshness.
        status = "needs_review"
        matches.append({"item_id": None, "relation": "unverifiable_exposure", "exposures": unknown})
    return {"status": status, "matches": matches, "unverifiable_exposures": unknown, "policy": FRESHNESS_POLICY}


def exposure_record(item: dict, attempt_id: str | None, at: str) -> dict:
    fp = fingerprint(item)
    return {"item_id": item["item_id"], "item_version": item.get("item_version"), "kind": fp["kind"],
            "content_fingerprint": fp["content"], "behavior_fingerprint": fp["behavior"], "sigs": fp["sigs"],
            "prompt_norm": fp["prompt_norm"], "surface": fp["surface"], "attempt_id": attempt_id, "at": at,
            "policy": fp["policy"]}


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


def _named_misconceptions(items, path):
    """Non-code misconceptions: diagnosed from the chosen distractor, not a code detector."""
    if not isinstance(items, list) or not items:
        raise ContentError(f"{path} needs at least one misconception")
    out, seen = [], set()
    for i, m in enumerate(items):
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not _ID.fullmatch(m["id"]) or m["id"] in seen:
            raise ContentError(f"{path}[{i}].id must be a unique slug")
        seen.add(m["id"])
        out.append({"id": m["id"], "name": _text(m.get("name"), f"{path}[{i}].name"),
                    "feedback": _text(m.get("feedback"), f"{path}[{i}].feedback", min_len=20)})
    return out


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value) if abs(value) <= 10 ** 15 else None   # no OverflowError on huge ints
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*", value):
        parsed = float(value.strip().replace(",", "."))
        return parsed if math.isfinite(parsed) else None
    return None


def _questions(questions, path, misconception_ids: set, *, need_categories=True) -> list[dict]:
    if not isinstance(questions, list) or not questions:
        raise ContentError(f"{path} needs at least one question")
    out, seen = [], set()
    for i, q in enumerate(questions):
        p = f"{path}[{i}]"
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not _ID.fullmatch(q["id"]) or q["id"] in seen:
            raise ContentError(f"{p}.id must be a unique slug")
        seen.add(q["id"])
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            raise ContentError(f"{p}.type must be one of {QUESTION_TYPES}")
        category = q.get("category", "core")
        if category not in CHECK_CATEGORIES or (need_categories and "category" not in q):
            raise ContentError(f"{p}.category must be one of {CHECK_CATEGORIES}")
        norm = {"id": q["id"], "type": qtype, "category": category,
                "prompt": _text(q.get("prompt"), f"{p}.prompt", min_len=8),
                "fail_feedback": _text(q.get("fail_feedback"), f"{p}.fail_feedback", min_len=10)}
        if qtype == "numeric":
            answer, tol = _number(q.get("answer")), _number(q.get("tolerance", 0))
            if answer is None or tol is None or tol < 0:
                raise ContentError(f"{p} needs a finite numeric answer and tolerance >= 0")
            norm.update({"answer": answer, "tolerance": tol, "unit": q.get("unit") if isinstance(q.get("unit"), str) else None})
            out.append(norm)
            continue
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < (3 if qtype == "ordering" else 2):
            raise ContentError(f"{p}.options needs at least {3 if qtype == 'ordering' else 2} options")
        ids, texts = [], set()
        for j, o in enumerate(opts):
            if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not _OPT.fullmatch(o["id"]) or o["id"] in ids:
                raise ContentError(f"{p}.options[{j}].id must be a unique short slug")
            t = canonical_text(_text(o.get("text"), f"{p}.options[{j}].text"))
            if t in texts:
                raise ContentError(f"{p}.options[{j}] repeats another option")
            ids.append(o["id"])
            texts.add(t)
        answer = q.get("answer")
        if qtype == "single_choice" and answer not in ids:
            raise ContentError(f"{p}.answer must be one option id")
        if qtype == "multi_choice" and (not isinstance(answer, list) or not answer
                                        or not all(isinstance(a, str) for a in answer) or len(set(answer)) != len(answer)
                                        or not set(answer) <= set(ids) or set(answer) == set(ids)):
            raise ContentError(f"{p}.answer must be a proper, non-empty subset of option ids")
        if qtype == "ordering":
            if not isinstance(answer, list) or not all(isinstance(a, str) for a in answer) or sorted(answer) != sorted(ids):
                raise ContentError(f"{p}.answer must order every option exactly once")
            if answer == ids:
                raise ContentError(f"{p}.options must not be listed in the answer order")
        mis = q.get("misconceptions") or {}
        correct = {answer} if qtype == "single_choice" else set(answer)
        if not isinstance(mis, dict) or any(k not in ids or (qtype != "ordering" and k in correct)
                                            or v not in misconception_ids for k, v in mis.items()):
            raise ContentError(f"{p}.misconceptions must map wrong option ids to declared misconception ids")
        norm.update({"options": [{"id": o["id"], "text": o["text"]} for o in opts], "answer": answer,
                     "misconceptions": dict(mis)})
        out.append(norm)
    return out


def answer_key(questions: list[dict]) -> dict:
    return {q["id"]: q["answer"] for q in questions}


def _is_correct(q: dict, given) -> bool:
    t = q["type"]
    if t == "single_choice":
        return isinstance(given, str) and given == q["answer"]
    if t == "multi_choice":
        return (isinstance(given, list) and all(isinstance(x, str) for x in given)
                and len(set(given)) == len(given) and set(given) == set(q["answer"]))
    if t == "ordering":
        return isinstance(given, list) and given == q["answer"]
    value = _number(given)
    # Only the author's declared tolerance applies. An undeclared epsilon can turn
    # a genuinely different tiny quantity into an exact-answer success.
    return value is not None and abs(value - q["answer"]) <= q["tolerance"]


def validate_grading_result(rows, fired, item: dict) -> None:
    """Fail closed when an external verifier returns incomplete or malformed evidence.

    A nonempty, exact set of item checks/questions is required; truth must be a
    boolean, not a truthy string or score. Python syntax/policy refusal is the only
    shorter result permitted: one explicit failed ``build`` row. This validates
    protocol integrity, not the correctness or scientific reliability of a grader.
    """
    if not isinstance(rows, list) or not rows:
        raise ContentError("grader returned no check results")
    kind = item_kind(item)
    expected = item["checks"] if kind == "python" else item.get("questions", item.get("rubric", []))
    expected_categories = {r["id"]: r.get("category", "core") for r in expected}
    build_failure = (kind == "python" and len(rows) == 1 and isinstance(rows[0], dict)
                     and rows[0].get("id") == "build" and rows[0].get("category") == "build"
                     and rows[0].get("passed") is False)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContentError("grader check must be an object")
        rid = row.get("id")
        if not isinstance(rid, str) or rid in seen:
            raise ContentError("grader check ids must be unique strings")
        seen.add(rid)
        if not isinstance(row.get("passed"), bool):
            raise ContentError("grader check passed must be a boolean")
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ContentError("grader check needs a name")
        if not build_failure and (rid not in expected_categories or row.get("category") != expected_categories[rid]):
            raise ContentError("grader check is not part of the frozen assessment")
        for field in ("feedback", "error"):
            if field not in row or (row[field] is not None and not isinstance(row[field], str)):
                raise ContentError(f"grader check {field} must be text or null")
        if row["passed"] and (row["error"] is not None or row["feedback"] is not None):
            raise ContentError("grader pass contradicts its failure feedback")
    if not build_failure and seen != set(expected_categories):
        raise ContentError("grader did not report every frozen assessment check exactly once")
    if not isinstance(fired, list):
        raise ContentError("grader misconceptions must be a list")
    known = {m["id"] for m in item.get("misconceptions", [])}
    mids = set()
    for m in fired:
        if (not isinstance(m, dict) or not isinstance(m.get("id"), str) or m["id"] not in known
                or m["id"] in mids or not all(isinstance(m.get(k), str) for k in ("name", "feedback"))):
            raise ContentError("grader misconception is unknown, repeated or malformed")
        mids.add(m["id"])


def grade_questions(questions: list[dict], answers: dict, misconceptions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deterministic, CI-style rows (one per question) + misconceptions diagnosed from distractors."""
    answers = answers if isinstance(answers, dict) else {}
    by_id = {m["id"]: m for m in misconceptions}
    rows, fired = [], []
    for q in questions:
        given = answers.get(q["id"])
        ok = _is_correct(q, given)
        rows.append({"id": q["id"], "name": q["prompt"][:120], "category": q["category"], "passed": ok,
                     "feedback": None if ok else q["fail_feedback"],
                     "error": None if given not in (None, "", []) else "Cavab verilməyib.", "observed": None})
        if not ok and q["type"] in ("single_choice", "multi_choice"):
            chosen = [given] if isinstance(given, str) else [g for g in given if isinstance(g, str)] if isinstance(given, list) else []
            for c in chosen:
                mid = q.get("misconceptions", {}).get(c)
                if mid in by_id and all(f["id"] != mid for f in fired):
                    fired.append({"id": mid, "name": by_id[mid]["name"], "feedback": by_id[mid]["feedback"]})
    return rows, fired


def validate_answers(spec: dict, answers) -> dict:
    """Shape-check a learner's answers against the public questions (no grading)."""
    if not isinstance(answers, dict):
        raise ContentError("answers must be an object")
    if item_kind(spec) == "free_text":
        text = answers.get("response")
        lo, hi = FREE_TEXT_LIMITS
        if set(answers) != {"response"} or not isinstance(text, str) or not lo <= len(text.strip()) <= hi:
            raise ContentError(f"free-text answer must be {{'response': {lo}-{hi} characters}}")
        return {"response": text.strip()}
    qs = {q["id"]: q for q in spec["questions"]}
    unknown = set(answers) - set(qs)
    if unknown:
        raise ContentError(f"unknown question ids: {sorted(unknown)}")
    out = {}
    for qid, given in answers.items():
        t = qs[qid]["type"]
        if t == "single_choice" and isinstance(given, str) and len(given) <= 32:
            out[qid] = given
        elif t in ("multi_choice", "ordering") and isinstance(given, list) and len(given) <= 32 and all(
                isinstance(x, str) and len(x) <= 32 for x in given):
            out[qid] = list(given)
        elif t == "numeric" and (_number(given) is not None or given in (None, "")):
            if isinstance(given, str) and len(given.strip()) > 128:
                raise ContentError(f"numeric answer for {qid} exceeds 128 characters")
            # Preserve the actual submitted value; truncating digits silently changes
            # its magnitude and makes the score bind to an answer the learner did not give.
            out[qid] = given if not isinstance(given, str) else given.strip()
        else:
            raise ContentError(f"answer for {qid} has the wrong shape for a {t} question")
    return out


def public_questions(questions: list[dict]) -> list[dict]:
    """What the learner sees: prompt, type and options; never the key or the distractor map."""
    out = []
    for q in questions:
        row = {"id": q["id"], "type": q["type"], "prompt": q["prompt"]}
        if q["type"] == "numeric":
            row["unit"] = q.get("unit")
        else:
            row["options"] = [{"id": o["id"], "text": o["text"]} for o in q["options"]]
        out.append(row)
    return out


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
    hints = pr.get("hints")
    if not isinstance(hints, list) or not hints or not all(isinstance(h, str) and h.strip() for h in hints):
        raise ContentError("practice.hints needs a non-empty hint ladder")
    kind = item_kind(pr)
    if kind in ("concept", "scenario"):
        mis = _named_misconceptions(pr.get("misconceptions"), "practice.misconceptions")
        out["practice"] = {"task_id": pr["task_id"], "kind": kind, "hints": hints,
                           "prompt_md": _text(pr.get("prompt_md"), "practice.prompt_md", min_len=40),
                           "questions": _questions(pr.get("questions"), "practice.questions", {m["id"] for m in mis},
                                                   need_categories=False),
                           "misconceptions": mis}
        if kind == "scenario":
            out["practice"]["scenario_md"] = _text(pr.get("scenario_md"), "practice.scenario_md", min_len=60)
    elif kind == "python":
        entry = pr.get("entry", "practice.py")
        if not _ENTRY.fullmatch(entry):
            raise ContentError("practice.entry must be a simple .py filename")
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
    else:
        raise ContentError("practice.kind must be python, concept or scenario (open answers use self-explanation)")
    # Separation: the worked example and practice must not be an assessment item.
    shown = " ".join([we.get("problem", ""), " ".join(steps), we.get("result", ""), pr.get("prompt_md", "")])
    shown_sigs = practice_signatures(out["practice"])
    for item in items:
        _assert_separate(shown, shown_sigs, item, f"worked example/practice overlaps assessment item {item['item_id']}; "
                                                  "use a different example")
    if kind == "python":
        _self_test(out["practice"], "practice")
    else:
        _self_test_questions(out["practice"], "practice")
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
        kind = item_kind(it)
        if kind not in KINDS:
            raise ContentError(f"{path}.kind must be one of {KINDS}")
        if kind == "python":
            norm = _python_item(it, node, path)
        else:
            norm = _non_code_item(it, node, path, kind)
        if lesson is not None:
            shown = " ".join([lesson["worked_example"]["problem"], " ".join(lesson["worked_example"]["steps"]),
                              lesson["worked_example"].get("result", ""), lesson["practice"]["prompt_md"]])
            _assert_separate(shown, practice_signatures(lesson["practice"]), norm,
                             f"{path} overlaps the worked example/practice; an exam item must be new")
        if kind == "python":
            _self_test(norm, path)
        elif kind != "free_text":
            _self_test_questions(norm, path)
        norm["item_version"] = digest("it", norm)
        fp = fingerprint(norm)
        for prev in out:
            other = fingerprint(prev)
            if fp["content"] == other["content"] or fp["behavior"] == other["behavior"]:
                raise ContentError(f"{path} assesses the same thing as {prev['item_id']}; a renamed copy is not a new item")
        out.append(norm)
    return out


def _assert_separate(shown: str, shown_sigs: set, item: dict, message: str) -> None:
    """Teaching material may not reveal an exam item: no shared check/question key and
    no lexical overlap with anything the item shows (prompt, scenario, questions)."""
    surface = surface_text(item)
    if (shown_sigs & set(fingerprint(item)["sigs"]) or similarity(shown, item["prompt_md"]) >= 0.35
            or similarity(canonical_text(shown), surface) >= 0.35 or _norm(item["prompt_md"])[:80] in _norm(shown)):
        raise ContentError(message)


def _python_item(it, node, path) -> dict:
    entry = it.get("entry", "solution.py")
    if not _ENTRY.fullmatch(entry):
        raise ContentError(f"{path}.entry must be a simple .py filename")
    checks = _checks(it.get("checks"), f"{path}.checks", categories=True)
    cats = {c["category"] for c in checks}
    if "core" not in cats or "transfer" not in cats:
        raise ContentError(f"{path} needs at least one core and one unseen transfer check")
    # ``kind`` stays implicit for Python items so pre-v2 item versions are unchanged.
    return {
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


def _non_code_item(it, node, path, kind) -> dict:
    norm = {"item_id": it["item_id"], "node_id": node["id"], "kind": kind, "scope": it["scope"],
            "difficulty": it.get("difficulty", 1), "title": _text(it.get("title"), f"{path}.title"),
            "prompt_md": _text(it.get("prompt_md"), f"{path}.prompt_md", min_len=40),
            "misconceptions": _named_misconceptions(it.get("misconceptions"), f"{path}.misconceptions")}
    if kind == "free_text":
        rubric = it.get("rubric")
        if not isinstance(rubric, list) or len(rubric) < 2:
            raise ContentError(f"{path}.rubric needs at least two criteria")
        seen, crit = set(), []
        for j, r in enumerate(rubric):
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _ID.fullmatch(r["id"]) or r["id"] in seen:
                raise ContentError(f"{path}.rubric[{j}].id must be a unique slug")
            seen.add(r["id"])
            crit.append({"id": r["id"], "criterion": _text(r.get("criterion"), f"{path}.rubric[{j}].criterion", min_len=15)})
        norm["rubric"] = crit
        norm["exemplar"] = _text(it.get("exemplar"), f"{path}.exemplar", min_len=20)
        return norm
    norm["questions"] = _questions(it.get("questions"), f"{path}.questions", {m["id"] for m in norm["misconceptions"]})
    cats = {q["category"] for q in norm["questions"]}
    if "core" not in cats or "transfer" not in cats:
        raise ContentError(f"{path} needs at least one core and one unseen transfer question")
    if kind == "scenario":
        norm["scenario_md"] = _text(it.get("scenario_md"), f"{path}.scenario_md", min_len=60)
    return norm


def _self_test_questions(spec: dict, path: str) -> None:
    """The key scores full marks; every mapped distractor diagnoses its misconception."""
    rows, fired = grade_questions(spec["questions"], answer_key(spec["questions"]), spec["misconceptions"])
    if fired or not all(r["passed"] for r in rows):
        raise ContentError(f"{path}: answer key does not grade as fully correct")
    for q in spec["questions"]:
        for option, mid in (q.get("misconceptions") or {}).items():
            if q["type"] == "ordering":
                continue
            wrong = {q["id"]: option if q["type"] == "single_choice" else [option]}
            _, got = grade_questions([q], wrong, spec["misconceptions"])
            if not any(m["id"] == mid for m in got):
                raise ContentError(f"{path}: distractor {option} does not diagnose {mid}")


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
    """What the learner may see when the exam starts (no checks, keys, rubric or probes)."""
    kind = item_kind(item)
    base = {"item_id": item["item_id"], "kind": kind, "title": item["title"], "prompt_md": item["prompt_md"],
            "scope": item["scope"]}
    if kind == "python":
        return {**base, "entry": item["entry"], "starter_code": item["starter_code"],
                "check_count": len(item["checks"]), "check_categories": sorted({c["category"] for c in item["checks"]})}
    if kind == "free_text":
        lo, hi = FREE_TEXT_LIMITS
        return {**base, "response_limits": {"min_chars": lo, "max_chars": hi},
                "grading_note_az": ("Açıq cavab açar sözlərlə yox, rubrika ilə qiymətləndirilir; hər qərar cavabındakı "
                                    "konkret cümləyə istinad etməlidir.")}
    out = {**base, "questions": public_questions(item["questions"]), "check_count": len(item["questions"]),
           "check_categories": sorted({q["category"] for q in item["questions"]})}
    if kind == "scenario":
        out["scenario_md"] = item["scenario_md"]
    return out


LESSON_PARTS = ("explanation", "why", "worked_example", "alternative_explanation")


def cited_sources(lesson: dict) -> set:
    return {r.get("source_id") for part in LESSON_PARTS for r in lesson[part]["refs"] if r.get("grounding") == "source"}


def public_lesson_summary(lesson: dict | None, gate: dict | None = None) -> dict:
    """``gate`` (from the action layer) decides readiness: capability binding + live grounding."""
    if not lesson:
        return {"status": "missing"}
    grounding = [r.get("grounding") for part in LESSON_PARTS for r in lesson[part]["refs"]]
    gate = gate or {"status": "ready"}
    out = {"status": gate["status"], "content_version": lesson["content_version"],
           "objective": lesson["objective"], "success_criteria": lesson["success_criteria"],
           "practice_kind": lesson["practice"].get("kind", "python"),
           "grounding": "source" if all(g == "source" for g in grounding)
           else ("supplementary_only" if all(g == "supplementary" for g in grounding) else "mixed")}
    if gate["status"] != "ready":
        if gate["status"] == "grounding_invalid":
            out["grounding"] = "invalid"
            out["grounding_problems"] = gate.get("problems", [])
        out["historical_snapshot"] = {"content_version": lesson["content_version"],
                                      "installed_at": lesson.get("installed_at"),
                                      "label_az": gate.get("label_az")}
    return out


def write_private(path: Path, obj) -> None:
    from .utils import write_json
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, obj)

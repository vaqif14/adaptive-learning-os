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
import builtins
import hashlib
import itertools
import keyword
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
# Practice-first lesson parts (course-studio.md section 7).
WALKTHROUGH_LABEL_AZ = "Agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş"
WALKTHROUGH_LANGUAGES = ("python", "none")
WALKTHROUGH_MIN_STEPS = 3
WALKTHROUGH_MIN_CHECKPOINTS = 2
LADDER_BLANK = "___"
LADDER_RUNGS = ("worked", "completion")
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
    ceiling = lesson.get("hint_ceiling")
    if "hint_ceiling" in lesson and (type(ceiling) is not int or not 1 <= ceiling <= len(hints)):
        raise ContentError("hint_ceiling must be an integer between 1 and the number of practice hints")
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
    # Optional practice-first parts (validated whenever present; keys are added only then so the
    # content version of a legacy lesson is unchanged).
    if "hint_ceiling" in lesson:
        out["hint_ceiling"] = ceiling
    if lesson.get("walkthrough") is not None:
        out["walkthrough"] = _walkthrough(lesson["walkthrough"], out["practice"], items, ground)
    if lesson.get("ladder") is not None:
        out["ladder"] = _ladder(lesson["ladder"], out["practice"], items)
    _assert_text_no_leak(out, _leak_targets(out["practice"], items), "lesson")
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
            _assert_no_teaching_leak(lesson, norm, path)
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


# --------------------------------------------------------------------------- practice-first parts
_PERSON_KEYS = frozenset({"expert", "expert_name", "author", "author_name", "instructor", "presenter", "narrator",
                          "speaker", "teacher", "person", "by", "byline", "attribution", "credit", "credited_to",
                          "quote_author", "name", "persona"})
_ROLE = (r"(?:expert|instructor|author|presenter|narrator|speaker|teacher|professor|prof|dr|mr|mrs|ms|engineer|"
         r"ekspert|müəllim|müəllif|təlimçi|mühəndis|aparıcı|spiker)")
_NAME = r"[A-ZƏÖÜĞİŞÇ][\wəöüğışç'’-]+"
# A role word followed by a capitalised name ("expert Jane Roe", "Dr. Roe", "müəllim Əli Vəliyev").
# Heuristic: it catches the common attributions; the hard rule is the closed key set.
_PERSON_TEXT = re.compile(rf"(?i:\b{_ROLE}\b\.?)\s+{_NAME}(?:\s+{_NAME})?")
# A bare attributed name needs no role word: "Andrew Ng deyir ki ...", "... hazırlayıb: Jane Roe". Two capitalised
# words followed by an attribution verb or postposition, or two capitalised words in the middle of a sentence (after a
# lower-case word) that are not a known technical term. Heuristic like the role form; flag false positives for review.
_ATTRIBUTION = (r"(?:deyir|demişdir|danışır|izah\s+edir|izah\s+edib|göstərir|göstərib|hazırlayıb|hazırlamışdır|yazıb|"
                r"yazmışdır|təqdim\s+edir|təqdim\s+edib|söyləyir|bildirir|tərəfindən|adından|fikrincə|sözlərinə\s+görə)")
_PERSON_ATTRIBUTED = re.compile(rf"(?<![\wəöüğışç]){_NAME}\s+{_NAME}\s+{_ATTRIBUTION}\b")
_PERSON_MIDSENTENCE = re.compile(rf"(?<=[a-zəöüğışç,:;] )({_NAME})\s+({_NAME})(?![\wəöüğışç'’-])")
_TECH_TERMS = frozenset({"python", "numpy", "pandas", "scipy", "jupyter", "github", "google", "notebook", "notebooklm",
                         "machine", "learning", "deep", "data", "science", "open", "source", "linux", "windows", "macos",
                         "unix", "ubuntu", "docker", "kaggle", "colab", "openai", "anthropic", "claude", "chatgpt",
                         "stack", "overflow", "visual", "studio", "code", "pytorch", "tensorflow", "azərbaycan",
                         "bakı", "avropa", "asiya", "amerika", "birləşmiş", "ştatlar", "krallığı", "respublikası",
                         "dövləti", "universiteti", "təhsil", "nazirliyi", "internet", "web", "api", "http", "json",
                         "html", "css", "sql", "unicode", "ascii", "utf"})


def _names_a_person(text: str) -> bool:
    if _PERSON_TEXT.search(text) or _PERSON_ATTRIBUTED.search(text):
        return True
    for m in _PERSON_MIDSENTENCE.finditer(text):
        first, second = m.group(1), m.group(2)
        if first.casefold() in _TECH_TERMS or second.casefold() in _TECH_TERMS or first.isupper() or second.isupper():
            continue
        return True
    return False


_TEXT_SKIP_KEYS = frozenset({"refs", "code", "wrong_code", "expected_output", "label", "template", "blanks"})
_WALKTHROUGH_KEYS = frozenset({"title", "scenario_md", "language", "steps", "mistake", "transcript_md", "refs", "label"})
_STEP_KEYS = frozenset({"explain_md", "code", "checkpoint", "predict"})
_MISTAKE_KEYS = frozenset({"step", "wrong_code", "symptom", "fix_explain"})
_LADDER_KEYS = frozenset({"worked", "completion", "independent"})


def _closed_keys(obj, allowed, path):
    if not isinstance(obj, dict):
        raise ContentError(f"{path} must be an object")
    for key in obj:
        if str(key).casefold() in _PERSON_KEYS:
            raise ContentError(f"{path}.{key}: a walkthrough is agent-authored; no field may name a real person or expert")
    unknown = sorted(str(k) for k in obj if k not in allowed)
    if unknown:
        raise ContentError(f"{path} has unknown fields {unknown}")


def _reject_named_people(value, path):
    if isinstance(value, dict):
        for key, inner in value.items():
            if key not in _TEXT_SKIP_KEYS:
                _reject_named_people(inner, f"{path}.{key}")
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _reject_named_people(inner, f"{path}[{i}]")
    elif isinstance(value, str) and _names_a_person(value):
        raise ContentError(f"{path} names a real person or expert; walkthroughs must not be attributed to anyone")


def _out_norm(text) -> str:
    """Stdout comparison form: line endings unified, trailing whitespace per line and at the end removed."""
    return "\n".join(line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")).rstrip()


def _cumulative_code(steps: list, upto: int, replace: tuple | None = None) -> str:
    """Code of steps 1..upto joined in order; ``replace=(step_number, code)`` swaps one step's code."""
    parts = []
    for number, step in enumerate(steps[:upto], 1):
        code = replace[1] if replace and replace[0] == number else step.get("code")
        if isinstance(code, str) and code.strip():
            parts.append(code.rstrip("\n"))
    return "\n".join(parts) + "\n" if parts else ""


def walkthrough_code(walkthrough: dict) -> str:
    return _cumulative_code(walkthrough["steps"], len(walkthrough["steps"]))


def fill_blanks(template: str, blanks: list) -> str:
    parts = template.split(LADDER_BLANK)
    if len(parts) - 1 != len(blanks):
        raise ContentError("completion.blanks must have exactly one answer per blank")
    out = parts[0]
    for answer, tail in zip(blanks, parts[1:]):
        out += answer + tail
    return out


def _passes_all(code: str, checks: list) -> bool:
    """True only if the code parses under the sandbox policy and satisfies every check (stops at the first miss).

    Tri-state: a timeout or a sandbox that never produced a verdict (after one retry) is INCONCLUSIVE and raises
    ``ContentError`` instead of reading as "does not solve", so the answer-leak guards fail closed."""
    _tree, syntax_error, policy_errors = _VERIFIER.syntax_and_policy(code)
    if syntax_error or policy_errors:
        return False
    for check in checks:
        res = _VERIFIER.verify(code, trusted_test_code=check["code"])
        if res.timed_out or not res.executed:
            res = _VERIFIER.verify(code, trusted_test_code=check["code"])     # one retry before giving up
            if res.timed_out or not res.executed:
                raise ContentError("sandbox_inconclusive: the sandbox did not return a verdict for the answer-leak "
                                   "guard (timeout or crash); retry the validation")
        if not (res.passed and res.correctness_checked):
            return False
    return True


# --------------------------------------------------------------------------- answer-leak guard
_PY_RESERVED = frozenset(keyword.kwlist) | frozenset(dir(builtins))
_CODE_TOKEN = re.compile(
    r"""(?P<doc>\"\"\".*?\"\"\"|\'\'\'.*?\'\'\')"""
    r"""|(?P<str>[rRbBfFuU]{0,2}"(?:\\.|[^"\\\n])*"|[rRbBfFuU]{0,2}'(?:\\.|[^'\\\n])*')"""
    r"""|(?P<com>\#[^\n]*)|(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<id>[^\W\d]\w*)|(?P<op>\S)""", re.S)
_MIN_WINDOW = 12          # a reference shorter than this many tokens must equal a whole snippet instead


def _code_tokens(text: str) -> list[tuple[str, str]]:
    """Lexical tokens of code or of prose that embeds code: comments, docstrings and statement separators dropped."""
    out = []
    for m in _CODE_TOKEN.finditer(text or ""):
        kind = m.lastgroup
        if kind in ("doc", "com") or (kind == "op" and m.group() == ";"):
            continue
        out.append((kind, m.group()))
    return out


def _alpha(tokens: list) -> tuple:
    """Alpha-renamed form: every identifier that is not a keyword or builtin becomes its first-appearance index
    inside this window; numbers and strings keep their value (a different instance stays different)."""
    names: dict[str, int] = {}
    out = []
    for kind, text in tokens:
        if kind == "id" and text not in _PY_RESERVED:
            out.append(("i", names.setdefault(text, len(names))))
        else:
            out.append((kind, text))
    return tuple(out)


def _embeds_code(blobs: list[str], reference: str) -> bool:
    """True if any blob contains the reference solution up to identifier renaming, whitespace, comments and
    statement separators (an alpha-renamed or reformatted copy is still the answer)."""
    ref = _code_tokens(reference)
    if not ref:
        return False
    target, size = _alpha(ref), len(ref)
    for blob in blobs:
        toks = _code_tokens(blob)
        if size < _MIN_WINDOW:
            if len(toks) == size and _alpha(toks) == target:
                return True
            continue
        for start in range(0, len(toks) - size + 1):
            if toks[start][1] == ref[0][1] or ref[0][0] == "id":
                if _alpha(toks[start:start + size]) == target:
                    return True
    return False


def _leak_targets(practice: dict | None, items: list[dict]) -> list[tuple[str, dict]]:
    targets = []
    if practice is not None and item_kind(practice) == "python":
        targets.append(("the practice checks", practice))
    targets += [(f"exam item {it['item_id']}", it) for it in items if item_kind(it) == "python"]
    return targets


def _top_functions(code: str) -> dict[str, int]:
    """Top-level function name -> positional parameter count (empty when the code does not parse)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return {}
    return {n.name: len(n.args.posonlyargs) + len(n.args.args) for n in tree.body if isinstance(n, ast.FunctionDef)}


def _alias_variants(code: str, reference: str, limit: int = 4) -> list[str]:
    """Copies of ``code`` with the functions the target's checks call bound to its own functions of the same arity.

    A renamed, loop-restructured or otherwise rewritten solution defeats the lexical copy test, and under another name
    the hidden checks fail with a NameError: this makes the checks see it anyway (a behavioural equivalence test)."""
    wanted, mine = _top_functions(reference), _top_functions(code)
    missing = {n: k for n, k in wanted.items() if n not in mine}
    if not missing or not mine:
        return []
    options = []
    for name, arity in missing.items():
        found = [m for m, k in mine.items() if k == arity and m not in wanted]
        if not found:
            return []
        options.append((name, found))
    out = []
    for combo in itertools.islice(itertools.product(*(found for _n, found in options)), limit):
        out.append(code + "\n" + "\n".join(f"{name} = {fn}" for (name, _f), fn in zip(options, combo)) + "\n")
    return out


def _behaves_like(code: str, target: dict) -> bool:
    """True if ``code`` (renamed or restructured) satisfies the target's hidden checks once aliased to its signatures."""
    return any(_passes_all(v, target["checks"]) for v in _alias_variants(code, target["reference_solution"]))


def _first_leak(snippets: list[str], blobs: list[str], targets: list[tuple[str, dict]]) -> tuple[str, str] | None:
    """(target label, "passes"|"behaves like"|"copies") of the first target whose checks any snippet satisfies (as
    written or signature-aliased) or whose reference solution any blob embeds (renamed copies included); None if
    clean. May raise ``sandbox_inconclusive``."""
    unique = list(dict.fromkeys(c for c in snippets if isinstance(c, str) and c.strip()))
    for label, target in targets:
        for code in unique:
            if _passes_all(code, target["checks"]):
                return label, "passes"
            if _behaves_like(code, target):
                return label, "behaves like"
        if _embeds_code(unique + [b for b in blobs if b], target["reference_solution"]):
            return label, "copies"
    return None


def _walkthrough_snippets(steps: list, at: int, wrong: str | None) -> list[str]:
    """Every state of the demonstration the learner can see: each cumulative prefix, each step's own code, and the
    mistake's wrong code alone and spliced into the cumulative code."""
    out = []
    for number, step in enumerate(steps, 1):
        out.append(_cumulative_code(steps, number))
        out.append(step.get("code") or "")
    if wrong:
        out += [wrong, _cumulative_code(steps, at, replace=(at, wrong)), _cumulative_code(steps, len(steps), replace=(at, wrong))]
    return out


def _worked_blobs(ld: dict) -> list[str]:
    body = ld["worked"]["code_or_steps"]
    return [body if isinstance(body, str) else " ".join(body), ld["worked"]["explain_md"]]


def _walkthrough_leak(wt: dict, targets: list) -> tuple[str, str] | None:
    snippets = _walkthrough_snippets(wt["steps"], wt["mistake"]["step"], wt["mistake"].get("wrong_code"))
    return _first_leak(snippets, [walkthrough_text(wt)], targets)


def _ladder_snippets(ld: dict, *, include_completion: bool) -> list[str]:
    body = ld["worked"]["code_or_steps"]
    out = [body] if isinstance(body, str) else [*body, "\n".join(body)]
    if include_completion:
        comp = ld["completion"]
        out += [comp["template"], fill_blanks(comp["template"], comp["blanks"])]
    return out


def _run_demo(code: str, path: str):
    """Run walkthrough code in the sandbox (no extra output from the harness). Returns the verifier result."""
    res = _VERIFIER.verify(code)
    if not res.policy_valid or not res.syntax_valid:
        raise ContentError(f"{path}: the code is not allowed to run: {', '.join(res.policy_errors or ['syntax error'])}")
    if res.timed_out or not res.executed:
        raise ContentError(f"{path}: the code did not finish in the sandbox")
    return res


def walkthrough_text(wt: dict) -> str:
    parts = [wt["title"], wt["scenario_md"], wt["transcript_md"], wt["mistake"]["symptom"], wt["mistake"]["fix_explain"]]
    for step in wt["steps"]:
        parts += [step["explain_md"], step.get("code") or ""]
        parts += [(step.get("checkpoint") or {}).get("expected_output") or ""]
        parts += [(step.get("predict") or {}).get("prompt") or ""]
    parts.append(wt["mistake"].get("wrong_code") or "")
    return " ".join(p for p in parts if p)


def ladder_text(ld: dict) -> str:
    worked = ld["worked"]["code_or_steps"]
    parts = [worked if isinstance(worked, str) else " ".join(worked), ld["worked"]["explain_md"],
             ld["completion"]["template"], " ".join(ld["completion"]["blanks"]), ld["completion"]["explain_md"]]
    return " ".join(parts)


def _walkthrough(raw, practice: dict, items: list[dict], ground) -> dict:
    p = "walkthrough"
    _closed_keys(raw, _WALKTHROUGH_KEYS, p)
    _reject_named_people(raw, p)
    if raw.get("label") != WALKTHROUGH_LABEL_AZ:
        raise ContentError(f"{p}.label must be exactly: {WALKTHROUGH_LABEL_AZ}")
    language = raw.get("language")
    if language not in WALKTHROUGH_LANGUAGES:
        raise ContentError(f"{p}.language must be one of {WALKTHROUGH_LANGUAGES}")
    python = language == "python"
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) < WALKTHROUGH_MIN_STEPS:
        raise ContentError(f"{p}.steps needs at least {WALKTHROUGH_MIN_STEPS} steps")
    out_steps, checkpoints = [], 0
    for i, step in enumerate(steps):
        sp = f"{p}.steps[{i}]"
        _closed_keys(step, _STEP_KEYS, sp)
        norm = {"explain_md": _text(step.get("explain_md"), f"{sp}.explain_md", min_len=10)}
        code = step.get("code")
        if code is not None:
            if not python:
                raise ContentError(f"{sp}.code is not allowed in a walkthrough with language 'none'")
            norm["code"] = _text(code, f"{sp}.code")
        if step.get("checkpoint") is not None:
            cp = step["checkpoint"]
            _closed_keys(cp, {"expected_output"}, f"{sp}.checkpoint")
            if not python:
                raise ContentError(f"{sp}.checkpoint is only for python walkthroughs")
            norm["checkpoint"] = {"expected_output": _text(cp.get("expected_output"), f"{sp}.checkpoint.expected_output")}
            checkpoints += 1
        if step.get("predict") is not None:
            pr = step["predict"]
            _closed_keys(pr, {"prompt", "reveal"}, f"{sp}.predict")
            norm["predict"] = {"prompt": _text(pr.get("prompt"), f"{sp}.predict.prompt", min_len=5),
                               "reveal": _text(pr.get("reveal"), f"{sp}.predict.reveal", min_len=5)}
        out_steps.append(norm)
    if python and checkpoints < WALKTHROUGH_MIN_CHECKPOINTS:
        raise ContentError(f"{p}.steps needs at least {WALKTHROUGH_MIN_CHECKPOINTS} checkpoints with expected_output")
    mistake = raw.get("mistake")
    _closed_keys(mistake, _MISTAKE_KEYS, f"{p}.mistake")
    at = mistake.get("step")
    if type(at) is not int or not 1 <= at <= len(out_steps):
        raise ContentError(f"{p}.mistake.step must be a step number between 1 and {len(out_steps)}")
    norm_mistake = {"step": at, "symptom": _text(mistake.get("symptom"), f"{p}.mistake.symptom", min_len=10),
                    "fix_explain": _text(mistake.get("fix_explain"), f"{p}.mistake.fix_explain", min_len=10)}
    out = {"title": _text(raw.get("title"), f"{p}.title"),
           "scenario_md": _text(raw.get("scenario_md"), f"{p}.scenario_md", min_len=40),
           "language": language, "steps": out_steps, "mistake": norm_mistake,
           "transcript_md": _text(raw.get("transcript_md"), f"{p}.transcript_md", min_len=40),
           "refs": _refs(raw.get("refs"), p, ground), "label": WALKTHROUGH_LABEL_AZ}
    if python:
        _check_walkthrough_code(out, mistake)
        leak = _walkthrough_leak(out, _leak_targets(practice, items))
        if leak:
            label, how = leak
            raise ContentError(f"{p}: the walkthrough code {how} {label} in at least one step, prefix or the deliberate "
                               "mistake, or embeds its reference solution (answer-leak guard); "
                               "demonstrate a different instance")
    else:
        if mistake.get("wrong_code") is not None:
            raise ContentError(f"{p}.mistake.wrong_code is only for python walkthroughs")
    shown = walkthrough_text({**out, "mistake": {**norm_mistake, "wrong_code": mistake.get("wrong_code")}})
    for item in items:
        _assert_separate(shown, set(), item, f"walkthrough overlaps assessment item {item['item_id']}; use a different instance")
    return out


def _check_walkthrough_code(out: dict, mistake: dict) -> None:
    """Python walkthrough: every checkpoint's cumulative code prints exactly its expected output and the
    deliberate mistake really goes wrong at its step."""
    p, steps = "walkthrough", out["steps"]
    last = len(steps)
    for number, step in enumerate(steps, 1):
        if "checkpoint" not in step and number != last:
            continue
        code = _cumulative_code(steps, number)
        if not code:
            raise ContentError(f"{p}.steps[{number - 1}] has no code up to this step")
        _tree, syntax_error, _policy = _VERIFIER.syntax_and_policy(code)
        if syntax_error:
            raise ContentError(f"{p}.steps[{number - 1}]: the code up to this step is not valid Python: "
                               f"{syntax_error.get('message')} (line {syntax_error.get('line')})")
        res = _run_demo(code, f"{p}.steps[{number - 1}]")
        if res.stderr.strip():
            raise ContentError(f"{p}.steps[{number - 1}]: the code up to this step raised {res.stderr.strip().splitlines()[0][:120]}")
        if "checkpoint" in step and _out_norm(res.stdout) != _out_norm(step["checkpoint"]["expected_output"]):
            raise ContentError(f"{p}.steps[{number - 1}].checkpoint.expected_output does not match the sandbox output "
                               f"({_out_norm(res.stdout)[:80]!r})")
    at = out["mistake"]["step"]
    wrong = mistake.get("wrong_code")
    _code(wrong, f"{p}.mistake.wrong_code")
    checkpoint = steps[at - 1].get("checkpoint")
    if checkpoint is None:
        raise ContentError(f"{p}.mistake.step must point at a step that has a checkpoint")
    res = _run_demo(_cumulative_code(steps, at, replace=(at, wrong)), f"{p}.mistake.wrong_code")
    if not res.stderr.strip() and _out_norm(res.stdout) == _out_norm(checkpoint["expected_output"]):
        raise ContentError(f"{p}.mistake.wrong_code neither raises nor prints something different from the checkpoint")
    out["mistake"]["wrong_code"] = wrong


def _ladder(raw, practice: dict, items: list[dict]) -> dict:
    p = "ladder"
    _closed_keys(raw, _LADDER_KEYS, p)
    if raw.get("independent") != "practice":
        raise ContentError(f"{p}.independent must be 'practice' (the independent rung is the lesson practice)")
    python = item_kind(practice) == "python"
    worked = raw.get("worked")
    _closed_keys(worked, {"code_or_steps", "explain_md"}, f"{p}.worked")
    body = worked.get("code_or_steps")
    if isinstance(body, str):
        _text(body, f"{p}.worked.code_or_steps")
        if python:
            _code(body, f"{p}.worked.code_or_steps")
    elif not (isinstance(body, list) and len(body) >= 2 and all(isinstance(s, str) and s.strip() for s in body)):
        raise ContentError(f"{p}.worked.code_or_steps must be code text or a list of at least two steps")
    norm_worked = {"code_or_steps": body, "explain_md": _text(worked.get("explain_md"), f"{p}.worked.explain_md", min_len=10)}
    comp = raw.get("completion")
    _closed_keys(comp, {"template", "blanks", "explain_md"}, f"{p}.completion")
    template = _text(comp.get("template"), f"{p}.completion.template", min_len=10)
    blanks = comp.get("blanks")
    if (not isinstance(blanks, list) or not blanks or not all(isinstance(b, str) and b.strip() and LADDER_BLANK not in b
                                                              for b in blanks)):
        raise ContentError(f"{p}.completion.blanks must list one non-empty answer per blank")
    if template.count(LADDER_BLANK) != len(blanks):
        raise ContentError(f"{p}.completion.template needs exactly {len(blanks)} '{LADDER_BLANK}' blanks, one per answer")
    norm_comp = {"template": template, "blanks": blanks,
                 "explain_md": _text(comp.get("explain_md"), f"{p}.completion.explain_md", min_len=10)}
    out = {"worked": norm_worked, "completion": norm_comp, "independent": "practice"}
    if python:
        if _norm(template) == _norm(practice["reference_solution"]):
            raise ContentError(f"{p}.completion.template must not be the reference solution")
        filled = fill_blanks(template, blanks)
        if not _passes_all(filled, practice["checks"]):
            raise ContentError(f"{p}.completion with its blanks filled does not pass the practice checks")
        if _passes_all(fill_blanks(template, [""] * len(blanks)), practice["checks"]):
            raise ContentError(f"{p}.completion with empty blanks already passes the practice checks; blank the essential parts")
        practice_leak = _first_leak(_ladder_snippets(out, include_completion=False), _worked_blobs(out),
                                    _leak_targets(practice, []))
        if practice_leak:
            raise ContentError(f"{p}.worked {practice_leak[1]} {practice_leak[0]} (solves the practice itself); "
                               "show a different instance")
    leak = _first_leak(_ladder_snippets(out, include_completion=python), [ladder_text(out)], _leak_targets(None, items))
    if leak:
        raise ContentError(f"{p}: a ladder rung {leak[1]} {leak[0]} (answer-leak guard)")
    for item in items:
        _assert_separate(ladder_text(out), set(), item, f"ladder overlaps assessment item {item['item_id']}; use a different example")
    return out


_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.S)
_CODE_LIKE = re.compile(r"\b(?:def|lambda|class)\b")


def _teaching_snippets(lesson: dict) -> tuple[list[str], list[str]]:
    """(code snippets, prose blobs) of every learner-visible lesson text a solution could hide in: fenced code blocks and
    code-like strings of the explanation, why, worked example, alternative example, practice prompt and hints."""
    we, alt, ex, pr = lesson["worked_example"], lesson["alternative_explanation"], lesson["explanation"], lesson["practice"]
    texts = [ex["short"], ex["body"], lesson["why"]["text"], we["problem"], *we["steps"], we["result"], alt["text"],
             alt["example"], pr["prompt_md"], *pr["hints"]]
    snippets = [code for text in texts for code in _FENCE.findall(text)]
    snippets += [t for t in (*we["steps"], alt["example"], *pr["hints"], "\n".join(we["steps"])) if _CODE_LIKE.search(t)]
    return [c for c in dict.fromkeys(snippets) if _CODE_LIKE.search(c)], texts


def _assert_text_no_leak(lesson: dict, targets: list[tuple[str, dict]], where: str) -> None:
    """No learner-visible lesson text may carry code that solves a target: the practice itself (a hint ladder that
    reaches the full solution) or an exam item, as written, renamed, restructured or signature-aliased."""
    snippets, blobs = _teaching_snippets(lesson)
    leak = _first_leak(snippets, blobs, targets) if targets else None
    if leak:
        raise ContentError(f"{where}: lesson text carries code that {leak[1]} {leak[0]} (answer-leak guard); "
                           "show a different instance")


def _assert_behaviour_separate(practice: dict, item: dict, path: str) -> None:
    """An exam item must not be the practised task in disguise: neither side's reference solution may satisfy the other's
    checks, as written or signature-aliased (a renamed, loop-restructured copy passes the lexical separation)."""
    if item_kind(practice) != "python" or item_kind(item) != "python":
        return
    for code, target in ((item["reference_solution"], practice), (practice["reference_solution"], item)):
        if _passes_all(code, target["checks"]) or _behaves_like(code, target):
            raise ContentError(f"{path} behaves like the practice task (its reference solution satisfies the other's checks); "
                               "an exam item must test a new task")


def _assert_no_teaching_leak(lesson: dict, item: dict, path: str) -> None:
    """An exam item must stay new relative to the lesson's walkthrough and ladder (symmetric to the
    lesson-side guard, for items that arrive after the lesson)."""
    wt, ld = lesson.get("walkthrough"), lesson.get("ladder")
    if item_kind(item) != "python":
        if wt:
            _assert_separate(walkthrough_text(wt), set(), item, f"{path} overlaps the walkthrough; an exam item must be new")
        if ld:
            _assert_separate(ladder_text(ld), set(), item, f"{path} overlaps the ladder; an exam item must be new")
        return
    targets = _leak_targets(None, [item])
    _assert_behaviour_separate(lesson["practice"], item, path)
    _assert_text_no_leak(lesson, targets, path)
    if wt:
        _assert_separate(walkthrough_text(wt), set(), item, f"{path} overlaps the walkthrough; an exam item must be new")
        if wt["language"] == "python" and _walkthrough_leak(wt, targets):
            raise ContentError(f"{path}: the lesson walkthrough already solves this item or embeds its reference "
                               "solution (answer-leak guard)")
    if ld:
        _assert_separate(ladder_text(ld), set(), item, f"{path} overlaps the ladder; an exam item must be new")
        python_practice = item_kind(lesson["practice"]) == "python"
        if _first_leak(_ladder_snippets(ld, include_completion=python_practice), [ladder_text(ld)], targets):
            raise ContentError(f"{path}: a ladder rung already solves this item or embeds its reference solution "
                               "(answer-leak guard)")


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


def public_walkthrough(wt: dict) -> dict:
    """The walkthrough as shown to the learner: steps, checkpoint output, predict prompts and the planned mistake.
    It never carries the practice solution or any check (the answer-leak guard keeps it a different instance)."""
    mistake = {k: wt["mistake"][k] for k in ("step", "symptom", "fix_explain")}
    if wt["mistake"].get("wrong_code"):
        mistake["wrong_code"] = wt["mistake"]["wrong_code"]
    return {"title": wt["title"], "scenario_md": wt["scenario_md"], "language": wt["language"],
            "steps": [dict(step) for step in wt["steps"]], "mistake": mistake, "transcript_md": wt["transcript_md"],
            "label": wt["label"]}


def public_ladder_rung(ld: dict, rung: str) -> dict:
    """One ladder rung. The completion rung shows the template and blank count, never the blank answers."""
    if rung == "worked":
        return {"rung": "worked", "title": "Həll olunmuş nümunə", **ld["worked"]}
    return {"rung": "completion", "title": "Boşluqları doldur", "template": ld["completion"]["template"],
            "blank_count": len(ld["completion"]["blanks"]), "explain_md": ld["completion"]["explain_md"]}


LESSON_PARTS = ("explanation", "why", "worked_example", "alternative_explanation")


def lesson_ref_parts(lesson: dict) -> list[tuple[str, list]]:
    """(part name, refs) for every part that carries source refs, including a walkthrough when present."""
    parts = [(part, lesson[part]["refs"]) for part in LESSON_PARTS]
    if lesson.get("walkthrough"):
        parts.append(("walkthrough", lesson["walkthrough"]["refs"]))
    return parts


def cited_sources(lesson: dict) -> set:
    return {r.get("source_id") for _part, refs in lesson_ref_parts(lesson) for r in refs if r.get("grounding") == "source"}


def public_lesson_summary(lesson: dict | None, gate: dict | None = None) -> dict:
    """``gate`` (from the action layer) decides readiness: capability binding + live grounding."""
    if not lesson:
        return {"status": "missing"}
    grounding = [r.get("grounding") for _part, refs in lesson_ref_parts(lesson) for r in refs]
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
    from .utils import private_dirs, write_json
    private_dirs(path.parent)
    write_json(path, obj)

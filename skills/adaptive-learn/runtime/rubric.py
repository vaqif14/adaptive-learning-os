"""Validate and aggregate attributed rubric judgments; never infer semantics.

The assessor supplies scores and reasons. Artifact binding and excerpt checks
make the report auditable but do not authenticate an assessor or prove learning.
"""
import hashlib
import math


def artifact_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number(value, name, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be a finite {'positive' if positive else 'non-negative'} number")
    return float(value)


def assess_rubric(submission: str, rubric: dict, assessment: dict) -> dict:
    if not isinstance(submission, str) or not submission.strip():
        raise ValueError("submission must contain learner work")
    if not isinstance(rubric, dict) or not isinstance(assessment, dict):
        raise ValueError("rubric and assessment must be objects")
    criteria, scores = rubric.get("criteria"), assessment.get("scores")
    if not isinstance(criteria, list) or not criteria or not isinstance(scores, list):
        raise ValueError("rubric needs non-empty criteria and assessment needs scores")
    reviewer = assessment.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("assessment must identify its reviewer")
    digest = artifact_digest(submission)
    if assessment.get("submission_sha256") != digest:
        raise ValueError("assessment does not match this submission")
    threshold = _number(rubric.get("pass_threshold", .8), "pass_threshold", positive=True)
    if threshold > 1:
        raise ValueError("pass_threshold must be <= 1")
    by_id = {}
    for item in scores:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in by_id:
            raise ValueError("score IDs must be unique strings")
        by_id[item["id"]] = item
    seen, results, failures = set(), [], []
    total, maximum = 0., 0.
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise ValueError("criterion must be an object")
        cid = criterion.get("id")
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise ValueError("criterion IDs must be unique non-empty strings")
        seen.add(cid)
        description = criterion.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("each criterion needs a description")
        limit = _number(criterion.get("max_score"), "max_score", positive=True)
        item = by_id.get(cid)
        if item is None:
            raise ValueError(f"missing score for {cid}")
        score = _number(item.get("score"), "score")
        if score > limit:
            raise ValueError(f"score exceeds maximum for {cid}")
        rationale = item.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"missing assessment rationale for {cid}")
        excerpt = item.get("evidence_excerpt", "")
        if not isinstance(excerpt, str) or (score > 0 and not excerpt.strip()) or (excerpt and excerpt not in submission):
            raise ValueError(f"evidence excerpt must occur in submission for {cid}")
        critical = criterion.get("critical", False)
        if type(critical) is not bool:
            raise ValueError("critical must be boolean")
        if critical and score / limit < threshold:
            failures.append(cid)
        total += score
        maximum += limit
        results.append({"id": cid, "score": score, "max_score": limit, "rationale": rationale,
                        "evidence_excerpt": excerpt, "critical": critical})
    if set(by_id) != seen:
        raise ValueError("scores contain unknown criterion IDs")
    ratio = total / maximum
    passed = ratio >= threshold and not failures
    return {"outcome": "correct" if passed else "partial" if ratio > 0 else "incorrect",
            "score_fraction": round(ratio, 4), "criteria": results, "critical_failures": failures,
            "reviewer": reviewer, "submission_sha256": digest,
            "verification": "self_report", "correctness_checked": False,
            "strength": "medium", "note": "Attributed rubric judgment; not an authenticated semantic verifier."}

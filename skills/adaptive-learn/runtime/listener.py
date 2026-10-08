from __future__ import annotations

import re
import math

# The Listener (role 07): the learner explains an idea in their own words; we grade
# lexical overlap against the source and report absent terms. This does not
# grade meaning, paraphrases or contradictions. The host needs a contextual
# rubric and an independent task for any understanding claim.

_STOP = {
    "the","a","an","and","or","but","if","then","is","are","was","were","be","been","being",
    "to","of","in","on","at","for","with","by","from","as","that","this","these","those","it",
    "its","we","you","they","he","she","i","my","your","our","their","not","no","do","does","can",
    "will","would","should","which","what","how","why","when","where","who","there","here","so",
    "və","bu","bir","ki","ilə","üçün","də","da","o","biz","siz","onlar","nə","niyə","necə","amma",
    "yox","var","olur","edir","daha","çox","az","hər","kimi","görə","isə","ancaq",
}
_WORD = re.compile(r"[a-zA-Z0-9əğıöşçü]+", re.UNICODE)


def _terms(text: str, *, min_len: int = 4) -> list[str]:
    out = []
    for w in _WORD.findall((text or "").lower()):
        if len(w) >= min_len and w not in _STOP and not w.isdigit():
            out.append(w)
    return out


def _key_terms(source: str, *, top: int = 40) -> dict[str, int]:
    freq: dict[str, int] = {}
    for w in _terms(source):
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return dict(ranked)


def grade_explanation(explanation: str, source: str, *, threshold: float = 0.6) -> dict:
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("coverage threshold must be in (0, 1]")
    key = _key_terms(source)
    if not key:
        return {"status": "no_source_terms", "coverage": 0.0, "missed": [], "covered": [],
                "unsupported": [], "outcome": "unknown"}
    expl = set(_terms(explanation))
    covered = [t for t in key if t in expl]
    missed = [t for t in key if t not in expl]          # what you missed (ranked by source frequency)
    source_set = set(key)
    unsupported = sorted({t for t in expl if t not in source_set})[:20]  # off-source (possible drift)
    coverage = round(len(covered) / len(key), 3)
    if coverage >= threshold:
        coverage_band = "sufficient"
    elif coverage >= threshold / 2:
        coverage_band = "partial"
    else:
        coverage_band = "low"
    return {
        "status": "needs_semantic_review",
        "coverage": coverage,
        "key_terms": len(key),
        "covered": covered,
        "missed": missed,                 # <- "tell me what I missed"
        "unsupported": unsupported,       # terms you used that aren't in the source
        "outcome": "unknown",
        "coverage_band": coverage_band,
        "correctness_checked": False,
        "mastery_eligible": False,
        "method": "deterministic_term_coverage",
        "note": "Lexical overlap cannot establish correctness, detect negation, or judge paraphrases. Check reasoning against a rubric and use a fresh independent task.",
    }

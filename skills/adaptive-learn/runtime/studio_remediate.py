"""Remediation: classify rules-check findings and decide what happens to a build (course-studio.md section 9).

``content_defect``  the author can fix it (a quote not in the source, an answer leak, a forbidden claim, missing
                    coverage): the affected units are re-opened with the finding text as a fix order, at most
                    ``max_remediation_rounds`` times per unit (the existing budget).
``system_defect``   the ledger, trust key, sandbox, a code invariant or a staged-byte mismatch: the job is ``blocked``
                    and a human acts; it is never auto-fixed and never handed back to the author.
``learner_gated``   intake, roadmap approval or source validation the learner must do: ``needs_learner`` with the
                    exact next step; nothing is fabricated.
Unknown probes classify as ``system_defect`` (fail closed). Pure functions: no I/O, no network, no deletion.
"""
from __future__ import annotations

import re

CONTENT, SYSTEM, LEARNER = "content_defect", "system_defect", "learner_gated"
PRIORITY = (SYSTEM, CONTENT, LEARNER)

PROBE_CLASS = {
    "r03.roadmap_dag": LEARNER, "r03.fresh_item_minimum": CONTENT, "r05.scope_coverage": CONTENT,
    "r07.quotes_grounded": CONTENT, "r07.sources_validated": LEARNER, "r08.public_payload_clean": CONTENT,
    "r08.walkthrough_does_not_solve": CONTENT, "r09.claim_lint": CONTENT, "r10.no_secrets_or_pii": CONTENT,
    "r06.rehearsal_promotion": CONTENT,
}
LEARNER_HINTS = ("approved roadmap version differs", "no installed roadmap", "no studio job")
NEXT_STEP = {
    SYSTEM: ("human action: inspect the failing probes in the receipt; the studio never auto-fixes a ledger, trust, sandbox, "
             "staged-byte or enforcement-code problem"),
    CONTENT: "the author must fix the findings below; the affected units are re-opened with the finding text",
    LEARNER: "the learner must act first: re-validate the sources / propose and approve a corrected roadmap, then resume",
}
_UNIT_ITEMS = ("item", "scope", "distinct", "transfer", "core")
_UNIT_LESSON = ("walkthrough", "ladder", "lesson", "explanation", "quote", "ref", "practice", "worked", "claim", "hint",
                "why", "alternative")


def finding_class(probe: dict, rule: str) -> str:
    if probe["status"] == "error":
        return SYSTEM
    cls = PROBE_CLASS.get(probe["id"], SYSTEM)
    if probe["id"] == "r06.versions_and_digests" and any(h in probe["detail"] for h in LEARNER_HINTS):
        return LEARNER
    if probe["id"] == "r06.rehearsal_promotion" and "promotion_revalidation_failed" not in probe["detail"]:
        return SYSTEM
    return cls


def _units_for(problem: str, node: str) -> list[str]:
    low = problem.lower()
    items = any(w in low for w in _UNIT_ITEMS)
    lesson = any(w in low for w in _UNIT_LESSON)
    if items and not lesson:
        return [f"items:{node}"]
    if lesson and not items:
        return [f"lesson:{node}"]
    return [f"lesson:{node}", f"items:{node}"]


def _nodes_in(problem: str, node_ids: list[str]) -> list[str]:
    head = re.split(r"[:(]", problem, maxsplit=1)[0]
    hits = [n for n in sorted(node_ids, key=len, reverse=True) if re.match(rf"{re.escape(n)}(?![\w-])", head)]
    return hits[:1]


def classify(receipt: dict, node_ids: list[str]) -> dict:
    """{"kind", "findings": [...], "fix_orders": {unit_id: [text]}, "next_step"} for a non-passing receipt."""
    findings, fix_orders = [], {}
    if receipt["status"] == "suite_broken":
        broken = [c["id"] for c in receipt["controls"]["broken"]]
        return {"kind": SYSTEM, "next_step": NEXT_STEP[SYSTEM], "fix_orders": {},
                "findings": [{"rule": "suite", "probe": b, "class": SYSTEM, "detail": "negative control did not fail: the gate "
                              "itself is broken", "units": []} for b in broken]}
    if receipt["status"] == "incomplete":
        return {"kind": SYSTEM, "next_step": NEXT_STEP[SYSTEM], "fix_orders": {},
                "findings": [{"rule": r, "probe": f"{r.lower()}.unverified", "class": SYSTEM, "units": [],
                              "detail": "the full run did not verify this rule (no probe ran, or every probe was not "
                                        "applicable): the receipt cannot claim it"} for r in receipt.get("unverified_rules") or []]}
    for res in receipt["results"]:
        for p in res["probes"]:
            if p["status"] not in {"fail", "error"}:
                continue
            cls = finding_class(p, res["rule"])
            units = []
            if cls == CONTENT:
                for problem in p.get("problems") or [p["detail"]]:
                    for node in _nodes_in(problem, node_ids):
                        for uid in _units_for(problem, node):
                            units.append(uid)
                            fix_orders.setdefault(uid, []).append(f"rules_check:{res['rule']}:{p['id']}: {problem}"[:480])
            findings.append({"rule": res["rule"], "probe": p["id"], "class": cls, "detail": p["detail"][:400],
                             "units": sorted(set(units))})
    kind = next((c for c in PRIORITY if any(f["class"] == c for f in findings)), SYSTEM)
    if kind == CONTENT and not fix_orders:
        # a content finding that names no node cannot be handed to the author precisely: it needs a human
        kind = SYSTEM
    return {"kind": kind, "findings": findings, "fix_orders": fix_orders, "next_step": NEXT_STEP[kind]}

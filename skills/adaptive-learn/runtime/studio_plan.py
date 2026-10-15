"""Studio targets policy and unit planning (pure: no I/O).

Policy ``studio-targets-v2`` (default for new jobs): per roadmap node a ``lesson`` unit and an ``items`` unit.
Items need >= 3 valid items, at least one independent_reproduction (core) item, at least one near/far transfer item, every item fresh against
the learner's current exposure. A lesson needs a valid lesson plus the practice-first parts of course-studio.md
section 7: a walkthrough (python practice lessons: a python walkthrough; concept lessons: language 'none' allowed),
and for python practice lessons also a faded ladder and a hint ceiling. Policy ``studio-targets-v1`` stays readable
and enforceable for jobs created under it (it requires none of those parts). The predicate reads its requirements
from ``targets`` and stays extensible. Units come from the PROPOSED roadmap in prerequisite order.
"""
from __future__ import annotations

from . import course_content as cc
from .studio_store import StudioError

POLICY_ID_V1 = "studio-targets-v1"
POLICY_ID = "studio-targets-v2"
POLICY_IDS = (POLICY_ID_V1, POLICY_ID)
UNIT_KINDS = ("lesson", "items")
TRANSFER_SCOPES = ("near_transfer", "far_transfer")
MIN_ITEMS_FLOOR = 3          # policy overrides may tighten, never loosen
MIN_TRANSFER_FLOOR = 1
MIN_CORE_FLOOR = 1           # independent_reproduction items (course-studio.md R5)
CORE_SCOPE = "independent_reproduction"

TARGETS_V1 = {
    "min_items": 3,
    "min_transfer_items": 1,
    "min_core_items": 1,
    "transfer_scopes": list(TRANSFER_SCOPES),
    "items_must_be_fresh": True,
    "lesson_required": True,
    "walkthrough_required": False,
    "ladder_required": False,
}
# v2: practice-first parts are required (ladder and hint ceiling for python practice lessons).
TARGETS_V2 = {**TARGETS_V1, "walkthrough_required": True, "ladder_required": True, "hint_ceiling_required": True}
DEFAULT_TARGETS = TARGETS_V2
POLICY_TARGETS = {POLICY_ID_V1: TARGETS_V1, POLICY_ID: TARGETS_V2}

LESSON_KEYS = frozenset({"node_id", "objective", "success_criteria", "explanation", "why", "worked_example",
                         "alternative_explanation", "self_explanation_prompt", "self_explanation_key_terms",
                         "practice", "walkthrough", "ladder", "hint_ceiling"})
ITEMS_KEYS = frozenset({"node_id", "items"})

DEFAULT_BUDGETS = {
    "max_attempts_per_unit": 3,
    "max_remediation_rounds": 3,
    "max_runner_seconds": 900,
    "max_output_bytes": 1048576,
}


def unit_id(kind: str, node_id: str) -> str:
    if kind not in UNIT_KINDS:
        raise StudioError("studio_unknown_unit_kind", kind)
    return f"{kind}:{node_id}"


def split_unit_id(uid: str) -> tuple[str, str]:
    kind, _, node = (uid or "").partition(":")
    if kind not in UNIT_KINDS or not node:
        raise StudioError("studio_unknown_unit", str(uid)[:80])
    return kind, node


def resolve_policy(overrides: dict | None = None, policy_id: str = POLICY_ID) -> dict:
    """Targets + budgets + flags. Overrides may tighten targets and set sane budgets."""
    if policy_id not in POLICY_IDS:
        raise StudioError("studio_policy_invalid", f"unknown policy {policy_id}")
    overrides = overrides or {}
    if not isinstance(overrides, dict):
        raise StudioError("studio_policy_invalid", "policy overrides must be an object")
    unknown = set(overrides) - {"targets", "budgets", "allow_private_sources"}
    if unknown:
        raise StudioError("studio_policy_invalid", f"unknown policy keys: {sorted(unknown)}")
    targets, budgets = dict(POLICY_TARGETS[policy_id]), dict(DEFAULT_BUDGETS)
    for key, value in (overrides.get("targets") or {}).items():
        if key not in targets:
            raise StudioError("studio_policy_invalid", f"unknown target {key}")
        targets[key] = value
    if (type(targets["min_items"]) is not int or targets["min_items"] < MIN_ITEMS_FLOOR
            or type(targets["min_transfer_items"]) is not int or targets["min_transfer_items"] < MIN_TRANSFER_FLOOR
            or type(targets["min_core_items"]) is not int or targets["min_core_items"] < MIN_CORE_FLOOR):
        raise StudioError("studio_policy_invalid", "targets may be tightened, never loosened")
    if not targets["items_must_be_fresh"] or not targets["lesson_required"]:
        raise StudioError("studio_policy_invalid", "freshness and lesson requirements cannot be disabled")
    required = [k for k, v in POLICY_TARGETS[policy_id].items() if v is True]
    if any(targets.get(k) is not True for k in required):
        raise StudioError("studio_policy_invalid", "targets may be tightened, never loosened")
    if sorted(targets["transfer_scopes"]) != sorted(TRANSFER_SCOPES):
        raise StudioError("studio_policy_invalid", "transfer scopes are fixed by the policy")
    for key, value in (overrides.get("budgets") or {}).items():
        if key not in budgets:
            raise StudioError("studio_policy_invalid", f"unknown budget {key}")
        if type(value) is not int or not 1 <= value <= (budgets[key] * 4 if key != "max_output_bytes" else 8 * 1024 * 1024):
            raise StudioError("studio_policy_invalid", f"budget {key} out of range")
        budgets[key] = value
    flag = overrides.get("allow_private_sources", False)
    if type(flag) is not bool:
        raise StudioError("studio_policy_invalid", "allow_private_sources must be a boolean")
    return {"id": policy_id, "targets": targets, "budgets": budgets, "allow_private_sources": flag}


def prerequisite_order(nodes: list[dict]) -> list[dict]:
    """Stable topological order over ``requires`` (roadmap order breaks ties)."""
    by_id = {n["id"]: n for n in nodes}
    done, out, visiting = set(), [], set()

    def visit(node):
        if node["id"] in done:
            return
        if node["id"] in visiting:
            raise StudioError("studio_roadmap_cycle", f"prerequisite cycle at {node['id']}")
        visiting.add(node["id"])
        for req in node.get("requires") or []:
            if req in by_id:
                visit(by_id[req])
        visiting.discard(node["id"])
        done.add(node["id"])
        out.append(node)

    for node in nodes:
        visit(node)
    return out


def plan_units(plan: dict) -> list[dict]:
    """Units for the proposed roadmap: per node a lesson, then its items (lesson-first)."""
    units = []
    for node in prerequisite_order(plan.get("nodes") or []):
        for kind in UNIT_KINDS:
            units.append({"unit_id": unit_id(kind, node["id"]), "node_id": node["id"], "kind": kind})
    return units


def unknown_keys(kind: str, payload) -> list[str]:
    """Top-level keys the validators ignore; they are refused so only validated fields get staged."""
    allowed = LESSON_KEYS if kind == "lesson" else ITEMS_KEYS
    return sorted(str(k) for k in (payload or {}) if k not in allowed)


def evaluate_unit(kind: str, payload: dict, validation: dict, targets: dict) -> dict:
    """Completeness of one unit: the dry validation must pass AND the targets must be met.

    Returns ``{"complete": bool, "problems": [str]}``; problems are author-actionable."""
    problems = []
    if not validation.get("valid"):
        return {"complete": False, "problems": list(validation.get("errors") or ["validation_failed"])}
    if kind == "lesson":
        payload = payload or {}
        python = cc.item_kind(payload.get("practice") or {}) == "python"
        walkthrough = payload.get("walkthrough")
        if targets.get("walkthrough_required"):
            if not walkthrough:
                problems.append("a walkthrough is required by this policy version")
            elif python and (walkthrough or {}).get("language") != "python":
                problems.append("a python practice lesson needs a python walkthrough with runnable checkpoints")
        if targets.get("ladder_required") and python and not payload.get("ladder"):
            problems.append("a faded ladder is required by this policy version")
        if targets.get("hint_ceiling_required") and python and "hint_ceiling" not in payload:
            problems.append("a hint_ceiling is required for python practice lessons by this policy version")
    elif kind == "items":
        items = (payload or {}).get("items") or []
        if len(items) < targets["min_items"]:
            problems.append(f"at least {targets['min_items']} items are required, got {len(items)}")
        transfer = [i for i in items if isinstance(i, dict) and i.get("scope") in targets["transfer_scopes"]]
        if len(transfer) < targets["min_transfer_items"]:
            problems.append(f"at least {targets['min_transfer_items']} item with scope near_transfer or far_transfer is required")
        core = [i for i in items if isinstance(i, dict) and i.get("scope") == CORE_SCOPE]
        if len(core) < targets.get("min_core_items", MIN_CORE_FLOOR):
            problems.append(f"at least {targets.get('min_core_items', MIN_CORE_FLOOR)} item with scope {CORE_SCOPE} is required")
        if targets.get("items_must_be_fresh"):
            for item_id, fresh in sorted((validation.get("freshness") or {}).items()):
                if fresh.get("status") != "fresh":
                    problems.append(f"item {item_id} is not fresh ({fresh.get('status')}); write a genuinely new item")
    else:
        raise StudioError("studio_unknown_unit_kind", str(kind))
    return {"complete": not problems, "problems": problems}


def unit_complete(unit: dict) -> bool:
    return unit.get("status") == "staged" and bool(unit.get("staged_digest"))


def build_complete(units: dict) -> bool:
    """Every planned unit is staged (its payload already passed evaluate_unit at staging time)."""
    return bool(units) and all(unit_complete(u) for u in units.values())


def inputs_for(node: dict, kind: str, source_hashes: dict, targets: dict) -> dict:
    """The inputs a unit's authoring depends on; their hash decides reuse across a rebase."""
    return {"kind": kind, "title": node.get("title"),
            "capability": {k: node.get(k) for k in ("task", "outcomes", "skills", "practice", "source_refs", "exam_domain")},
            "sources": {k: source_hashes.get(k) for k in sorted(node.get("source_refs") or [])},
            "targets": targets, "fresh_policy": cc.FRESHNESS_POLICY}

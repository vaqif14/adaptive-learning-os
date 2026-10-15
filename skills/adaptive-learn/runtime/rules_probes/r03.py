"""R3 mastery gating: a valid prerequisite DAG, locked nodes stay closed, retakes of one task never reach mastery."""
from __future__ import annotations

import copy

from .. import course_content as cc
from .. import course_state as cs
from .. import studio_plan as plan_mod
from ..studio_store import StudioError
from .base import Ctx, Result, journey_obs, na, probe, verdict


def dag_problems(nodes: list[dict]) -> list[str]:
    ids = [n.get("id") for n in nodes]
    problems = []
    if len(set(ids)) != len(ids):
        problems.append("duplicate node ids")
    for node in nodes:
        nid = node.get("id")
        if "prerequisites" in node:
            problems.append(f"{nid}: uses a 'prerequisites' key; the gate reads 'requires' only")
        requires = node.get("requires") or []
        if not isinstance(requires, list):
            problems.append(f"{nid}: requires must be a list")
            continue
        for req in requires:
            if req not in ids:
                problems.append(f"{nid}: requires unknown node '{req}'")
            if req == nid:
                problems.append(f"{nid}: requires itself")
    if not problems:
        try:
            plan_mod.prerequisite_order(nodes)
        except StudioError as e:
            problems.append(f"{e.code}: {e.detail}")
    return problems


def _dag(ctx: Ctx) -> Result:
    nodes = ctx.nodes()
    if not nodes:
        return na("no roadmap installed")
    return verdict(dag_problems(nodes), f"{len(nodes)} node(s); every requires id exists and the graph is acyclic")


def _control_dag():
    return Ctx.synthetic(nodes=[{"id": "a", "requires": ["ghost"]}, {"id": "b", "prerequisites": ["a"], "requires": []}])


@probe("r03.roadmap_dag", "R3", control=_control_dag, title="roadmap requires-ids exist, no 'prerequisites' key, no cycle")
def roadmap_dag(ctx: Ctx) -> Result:
    return _dag(ctx)


def _fresh_minimum(ctx: Ctx) -> Result:
    minimum = int(ctx.targets().get("min_items", plan_mod.MIN_ITEMS_FLOOR))
    problems, checked = [], 0
    for subject in ctx.subjects():
        items = subject.get("items")
        if items is None:
            if subject["origin"] == "staged":
                problems.append(f"{subject['node']}: the build has no items unit")
            continue
        checked += 1
        prompts = {cc.canonical_text(str(i.get("prompt_md") or i.get("item_id"))) for i in items if isinstance(i, dict)}
        ids = {i.get("item_id") for i in items if isinstance(i, dict)}
        count = min(len(prompts), len(ids))
        if count < max(minimum, plan_mod.MIN_ITEMS_FLOOR):
            problems.append(f"{subject['node']}: {count} distinct item(s); at least {max(minimum, plan_mod.MIN_ITEMS_FLOOR)} are required")
    if not checked and not problems:
        return na("no items in the build or in the live course")
    return verdict(problems, f"{checked} node(s) have the fresh-v1 minimum of distinct items")


def _control_fresh():
    item = {"item_id": "a", "prompt_md": "same prompt"}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": None,
                                    "items": [item, {**item, "item_id": "b"}, {**item, "item_id": "c"}]}], job=None)


@probe("r03.fresh_item_minimum", "R3", control=_control_fresh, title="every node has the fresh-v1 minimum of distinct items")
def fresh_item_minimum(ctx: Ctx) -> Result:
    return _fresh_minimum(ctx)


def _supersede(ctx: Ctx) -> Result:
    plan = ctx.plan()
    nodes = ctx.nodes()
    if not nodes:
        return na("no roadmap installed")
    impact_fn = ctx.hook("capability_impact", cs.capability_impact)
    approved = cs.capability_snapshot(plan)
    problems, probed = [], 0
    for node in nodes:
        dependents = [n["id"] for n in nodes if node["id"] in (n.get("requires") or [])]
        if not dependents:
            continue
        probed += 1
        changed = copy.deepcopy(plan)
        for n in changed["nodes"]:
            if n["id"] == node["id"]:
                n["task"] = str(n.get("task")) + " (changed capability)"
        impact = impact_fn({k: {**v, "since": "t"} for k, v in approved.items()}, changed)["nodes"]
        if impact[node["id"]]["change"] != "capability_changed":
            problems.append(f"{node['id']}: a changed task is not detected as a capability change")
        for dep in dependents:
            if not impact[dep].get("upstream_changed"):
                problems.append(f"{dep}: not flagged when prerequisite {node['id']} changes capability")
    if not probed:
        return na("the roadmap has no dependent nodes to lock")
    return verdict(problems, f"a capability change supersedes the pass and flags dependents ({probed} prerequisite node(s) probed)")


def _control_supersede():
    plan = {"nodes": [{"id": "a", "task": "t", "requires": []}, {"id": "b", "task": "u", "requires": ["a"]}]}
    return Ctx.synthetic(plan=plan, nodes=plan["nodes"],
                         hooks={"capability_impact": lambda approved, new: {"nodes": {
                             "a": {"change": "unchanged"}, "b": {"change": "unchanged"}}}})


@probe("r03.capability_change_locks_dependents", "R3", control=_control_supersede,
       title="a capability change supersedes passes and flags dependents")
def capability_change_locks_dependents(ctx: Ctx) -> Result:
    return _supersede(ctx)


def _locked(ctx: Ctx) -> Result:
    locked, problems = journey_obs(ctx, "locked"), []
    obs = ctx.scratch.obs
    if locked.get("node") is None:
        return na("the roadmap has no node that starts locked")
    for key in ("start_exam", "start_mission"):
        if not (locked.get(key) or {}).get("refused"):
            problems.append(f"{key} on the locked node '{locked['node']}' was not refused")
    if locked.get("offered_in_mission"):
        problems.append("the next mission offers a locked node")
    retake = obs.get("retake")
    if retake and retake.get("skipped"):
        retake = None                  # recorded explicitly by the journey (no node could be passed), not silently absent
    if retake:
        if len(set(retake["started"])) != len(retake["started"]):
            problems.append(f"the same item was served twice on {retake['node']}: {retake['started']}")
        if retake.get("refused_code") != "no_fresh_item":
            problems.append(f"retakes on {retake['node']} never ran out of fresh items (last code: {retake.get('refused_code')})")
    return verdict(problems, "locked nodes refuse exams and missions; retakes run out of fresh items instead of repeating",
                   retake_checked=bool(retake))


def _control_locked():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"locked": {
        "node": "b", "start_exam": {"refused": False}, "start_mission": {"refused": True}},
        "retake": {"node": "b", "started": ["x", "x"], "refused_code": None}}})())


@probe("r03.locked_node_and_retakes", "R3", kind="dynamic", cost="scratch", control=_control_locked,
       title="locked nodes refuse exams; identical retakes cannot reach mastery")
def locked_node_and_retakes(ctx: Ctx) -> Result:
    return _locked(ctx)


PROBES = [roadmap_dag, fresh_item_minimum, capability_change_locks_dependents, locked_node_and_retakes]

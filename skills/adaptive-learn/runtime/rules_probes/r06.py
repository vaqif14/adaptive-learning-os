"""R6 roadmap authority: exact versions and digests, human-channel approval, staged content unreachable, a promotion
bound to approval + acceptance + receipt."""
from __future__ import annotations

from .. import course_state as cs
from .. import studio_jobs as jobs
from .. import studio_receipt as receipts
from ..studio_store import digest_of
from .base import Ctx, Result, journey_obs, na, probe, verdict

HUMAN_CHANNELS = ("ui", "cli")
PROMOTED_STATES = ("accepted", "awaiting_learner_approval", "promoted")


def version_problems(ctx: Ctx) -> list[str]:
    job, plan, state = ctx.job, ctx.plan(), ctx.state()
    problems = []
    if plan is None or state is None:
        return ["the course has no installed roadmap or state"]
    recomputed = cs.roadmap_version(plan)
    binding = ctx.binding()
    if state.get("roadmap_version") != recomputed:
        problems.append("the course roadmap_version differs from the version recomputed from roadmap.json")
    if binding.get("roadmap_version") != recomputed:
        problems.append(f"the job is bound to {binding.get('roadmap_version')} but the roadmap is {recomputed}")
    approved = state.get("approved_roadmap_version")
    if approved:
        if approved != recomputed:
            problems.append("the approved roadmap version differs from the roadmap the build was made for")
        last = (state.get("approval_history") or [None])[-1] or {}
        if last.get("roadmap_version") != approved:
            problems.append("the last approval_history entry is not for the approved version")
        if last.get("channel") not in HUMAN_CHANNELS or last.get("approved_by") != "learner":
            problems.append(f"the approval channel is '{last.get('channel')}' (only ui or cli by the learner count)")
        if not str(last.get("statement") or "").strip():
            problems.append("the approval has no statement")
    if binding.get("generation_basis") != plan.get("generation_provenance"):
        problems.append("the job's generation basis differs from the roadmap's generation_provenance")
    caps = {nid: v["version"] for nid, v in cs.capability_snapshot(plan).items()}
    if binding.get("capabilities") != caps:
        problems.append("the capability versions the build was authored against differ from the roadmap's")
    live = {r.get("key"): r.get("content_sha256") for r in (ctx.manifest() or {}).get("resources", [])}
    for key, sha in jobs.source_hashes(binding).items():
        if live.get(key) != sha:
            problems.append(f"source {key} changed since the build was authored")
    staged = ctx.staged_units()
    for uid, code in ctx.staged_errors.items():
        problems.append(f"staged unit {uid}: {code}")
    if job.get("build_digest") and not ctx.staged_errors and len(staged) == len(job["unit_order"]):
        recomputed_digest = jobs.build_digest_of({uid: digest_of(p) for uid, p in staged.items()},
                                                 binding.get("roadmap_version"), jobs.source_hashes(binding),
                                                 binding.get("policy_versions") or {})
        if recomputed_digest != job["build_digest"]:
            problems.append("the build digest recomputed from the staged bytes differs from the job's build digest")
    if job.get("status") in PROMOTED_STATES and job.get("build_digest"):
        accept = receipts.find_acceptance(ctx.led(), job["job_id"], job["build_digest"])
        if accept is None:
            problems.append("the job is accepted but no owner acceptance in the ledger is bound to its build digest")
        elif not receipts.acceptance_valid(accept):
            problems.append("the owner acceptance has no owner channel or statement")
        else:
            # Read from the ledger only (the anchored event of the receipt the owner accepted on), never from a stored receipt.
            anchor = next((r["payload"] for r in ctx.records() if r["record_type"] == "event"
                           and r["payload"].get("event_type") == receipts.RECEIPT_EVENT
                           and r["payload"].get("receipt_id") == accept["payload"].get("receipt_id")), None)
            if (anchor is None or anchor.get("build_digest") != job["build_digest"] or anchor.get("scope") != "full"
                    or anchor.get("status") != "pass"):
                problems.append("the owner accepted on a receipt that is not anchored in the ledger as a passing full "
                                "receipt for this build digest")
    return problems


def _versions(ctx: Ctx) -> Result:
    if not ctx.job or not ctx.job.get("unit_order"):
        return na("no build to bind: there is no studio job with staged units")
    return verdict(version_problems(ctx), "versions, digests, approval channel and bindings agree")


def _control_versions():
    job = {"job_id": "job_" + "0" * 16, "unit_order": ["lesson:a"], "status": "ready_for_review", "build_digest": "wrong",
           "binding": {"roadmap_version": "rm-stale", "capabilities": {}, "sources": {}, "policy_versions": {}}}
    plan = {"nodes": [{"id": "a", "task": "t", "requires": []}]}
    state = {"roadmap_version": "rm-other", "approved_roadmap_version": "rm-other",
             "approval_history": [{"roadmap_version": "rm-other", "channel": "agent", "approved_by": "agent", "statement": ""}]}
    return Ctx.synthetic(job=job, plan=plan, state=state, manifest={}, staged_units={"lesson:a": {"x": 1}})


@probe("r06.versions_and_digests", "R6", control=_control_versions,
       title="approved = proposed = bound = recomputed versions; digest and approval channel verified")
def versions_and_digests(ctx: Ctx) -> Result:
    return _versions(ctx)


# ------------------------------------------------------------ scratch observations
def _journey(ctx: Ctx, key: str):
    return journey_obs(ctx, key)


def _rehearsal(ctx: Ctx) -> Result:
    obs = _journey(ctx, "promotion")
    problems = []
    if obs.get("error"):
        problems.append(f"the staged build did not promote into the scratch copy: {obs['error']}")
    elif not (obs.get("promoted") or obs.get("already_promoted")):
        problems.append("the promotion into the scratch copy reported nothing installed")
    elif obs.get("evidence_added", 0) != 0 or obs.get("attempts_added", 0) != 0:
        problems.append("the promotion added evidence or attempts (a build must add none)")
    elif sorted(obs.get("installed_nodes") or []) != sorted(obs.get("plan_nodes") or []):
        problems.append("not every roadmap node was installed")
    return verdict(problems, f"the staged build promoted into the scratch copy ({len(obs.get('installed_nodes') or [])} node(s)); zero evidence added")


def _control_rehearsal():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"promotion": {"error": "promotion_revalidation_failed: n"}}})())


@probe("r06.rehearsal_promotion", "R6", kind="dynamic", cost="scratch", control=_control_rehearsal,
       title="the staged build promotes into the scratch copy through the real promotion function")
def rehearsal_promotion(ctx: Ctx) -> Result:
    return _rehearsal(ctx)


EXPECTED_REFUSALS = {
    "staged_byte_change": ("promotion_digest_mismatch",),
    "source_hash_change": ("promotion_source_changed",),
    "capability_bump": ("promotion_capability_changed",),
    "roadmap_version_mismatch": ("roadmap_version_mismatch",),
    "no_acceptance": ("promotion_requires_owner_acceptance",),
    "wrong_digest_acceptance": ("promotion_acceptance_digest_mismatch",),
    "agent_channel_approval": None,                      # any refusal
}


def _binding(ctx: Ctx) -> Result:
    obs = _journey(ctx, "binding_attacks")
    problems = []
    for name, codes in EXPECTED_REFUSALS.items():
        row = obs.get(name)
        if row is None:
            problems.append(f"attack '{name}' did not run")
        elif not row.get("refused"):
            problems.append(f"attack '{name}' was NOT refused")
        elif codes and row.get("code") not in codes:
            problems.append(f"attack '{name}' was refused for the wrong reason ({row.get('code')})")
    if not obs.get("state_unchanged"):
        problems.append("a refused promotion changed the course state")
    return verdict(problems, "a changed staged byte, source hash, capability version, roadmap version, a missing or "
                             "mismatched owner acceptance and an agent-channel approval are all refused")


def _control_binding():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"binding_attacks": {
        "staged_byte_change": {"refused": False}, "state_unchanged": False}}})())


@probe("r06.binding_adversarial", "R6", kind="dynamic", cost="scratch", control=_control_binding,
       title="promotion refuses every attack on the approval/digest binding")
def binding_adversarial(ctx: Ctx) -> Result:
    return _binding(ctx)


def _unreachable(ctx: Ctx) -> Result:
    rows = _journey(ctx, "http_staged")
    problems = []
    for row in rows:
        if row["leak"]:
            problems.append(f"GET {row['path']} exposed staged content (status {row['status']})")
        elif row.get("must_refuse") and row["status"] not in (400, 401, 403, 404, 409, 423):
            problems.append(f"GET {row['path']} answered {row['status']} instead of a refusal")
    if not rows:
        problems.append("no learner route was probed")
    return verdict(problems, f"{len(rows)} learner/static route(s) probed over loopback: staged content is unreachable")


def _control_unreachable():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"http_staged": [
        {"path": "/studio/staging/x.json", "status": 200, "leak": True}]}})())


@probe("r06.staged_unreachable", "R6", kind="dynamic", cost="scratch", control=_control_unreachable,
       title="staged ids and content are unreachable from learner routes")
def staged_unreachable(ctx: Ctx) -> Result:
    return _unreachable(ctx)


PROBES = [versions_and_digests, rehearsal_promotion, binding_adversarial, staged_unreachable]

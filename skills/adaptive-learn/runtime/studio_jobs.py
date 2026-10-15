"""Course Studio job document, status machine, stage keys, idempotency, drift and leases.

A job binds the exact roadmap version, capability versions, source hashes and policy/validator
versions it was planned against. Drift on any of them supersedes the job (the old document is
archived by rename, its history kept); ``rebase`` builds a new job that reuses units whose
inputs hash is unchanged after re-validating their staged payloads. Status transitions are
table-driven and fail closed. Pure data functions plus load/save helpers; no network, no
deletion.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from . import course_content as cc
from . import studio_plan as plan_mod
from .studio_store import (STUDIO_SCHEMA, StudioError, archive_rename, child, digest_of, read_json, secure_mkdir,
                           write_atomic_json)
from .utils import new_id

JOB_SCHEMA = 1
# Validator/policy versions bound into every job; a change supersedes older jobs.
VALIDATOR_VERSIONS = {
    "lesson": "validate-lesson-v1",
    "items": "validate-items-v1",
    "dry_validator": "dry-v1",
    "order_contract": "order-v1",
}
_JOB_ID = re.compile(r"job_[0-9a-f]{16}")
_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,80}")

STATUSES = ("planning", "authoring", "validating", "checking", "remediation", "ready_for_review", "accepted",
            "awaiting_learner_approval", "promoted", "blocked", "needs_learner", "superseded", "cancelled", "paused")
TERMINAL = frozenset({"promoted", "superseded", "cancelled"})
_LIVE = frozenset(STATUSES) - TERMINAL
_ANY_PARK = {"paused", "cancelled", "superseded"}
TRANSITIONS: dict[str, frozenset] = {
    "planning": frozenset({"authoring", "needs_learner", "blocked"} | _ANY_PARK),
    "needs_learner": frozenset({"planning"} | _ANY_PARK),
    "authoring": frozenset({"validating", "remediation", "needs_learner", "blocked"} | _ANY_PARK),
    "validating": frozenset({"authoring", "checking", "remediation", "needs_learner", "blocked"} | _ANY_PARK),
    "checking": frozenset({"remediation", "ready_for_review", "needs_learner", "blocked"} | _ANY_PARK),
    "remediation": frozenset({"authoring", "validating", "needs_learner", "blocked"} | _ANY_PARK),
    "ready_for_review": frozenset({"accepted", "remediation", "blocked"} | _ANY_PARK),
    "accepted": frozenset({"awaiting_learner_approval", "promoted", "remediation", "blocked", "cancelled", "superseded"}),
    "awaiting_learner_approval": frozenset({"promoted", "needs_learner", "remediation", "blocked", "cancelled", "superseded"}),
    "blocked": frozenset({"authoring", "validating", "remediation", "cancelled", "superseded"}),
    "paused": frozenset({"planning", "authoring", "validating", "checking", "remediation", "ready_for_review",
                         "needs_learner", "blocked", "cancelled", "superseded"}),
    "promoted": frozenset(), "superseded": frozenset(), "cancelled": frozenset(),
}
# Statuses a build can be parked in once every unit is staged (rules gate comes in a later stage).
BUILT_STATUSES = frozenset({"checking", "ready_for_review", "accepted", "awaiting_learner_approval", "promoted"})


def policy_versions(policy_id: str | None = None) -> dict:
    """Versions bound into a job. ``policy_id`` is the job's own policy so a job created under an older policy
    (studio-targets-v1) keeps validating under it instead of drifting."""
    return {"policy": policy_id or plan_mod.POLICY_ID, "studio_schema": STUDIO_SCHEMA, "job_schema": JOB_SCHEMA,
            "freshness": cc.FRESHNESS_POLICY, "validators": dict(VALIDATOR_VERSIONS)}


def valid_job_id(job_id) -> str:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise StudioError("studio_invalid_job_id", "job ids look like job_<16 hex>")
    return job_id


# ------------------------------------------------------------------ binding
def make_binding(snapshot: dict, policy_id: str | None = None) -> dict:
    return {"roadmap_version": snapshot.get("roadmap_version") if snapshot.get("roadmap_proposed") else None,
            "generation_basis": snapshot.get("generation_provenance"),
            "capabilities": {nid: v["version"] for nid, v in sorted((snapshot.get("capabilities") or {}).items())},
            "manifest_version": snapshot.get("manifest_version"),
            "sources": {r["key"]: {"source_id": r.get("source_id"), "content_sha256": r.get("content_sha256"),
                                   "status": r.get("status")}
                        for r in sorted(snapshot.get("resources") or [], key=lambda r: str(r.get("key")))},
            "policy_versions": policy_versions(policy_id)}


def detect_drift(bound: dict, current: dict) -> list[str]:
    """Names of binding parts that differ (empty = no drift)."""
    return [k for k in ("roadmap_version", "generation_basis", "capabilities", "manifest_version", "sources",
                        "policy_versions") if bound.get(k) != current.get(k)]


def source_hashes(binding: dict) -> dict:
    return {k: v.get("content_sha256") for k, v in (binding.get("sources") or {}).items()}


# --------------------------------------------------------------------- jobs
def new_job(session_id: str, now: str, policy: dict, binding: dict) -> dict:
    return {"studio_schema": STUDIO_SCHEMA, "job_schema": JOB_SCHEMA, "job_id": new_id("job"),
            "session_id": session_id, "created_at": now, "updated_at": now, "revision": 0,
            "status": "planning", "status_history": [{"at": now, "from": None, "to": "planning",
                                                      "reason": "job_created", "actor": "studio"}],
            "paused_from": None, "binding": binding, "policy": policy, "units": {}, "unit_order": [],
            "orders": {}, "lease": None, "stage_keys_done": [],
            "needs_learner": None, "blocked": None, "build_digest": None, "rebased_from": None,
            "superseded_by": None, "drift": [], "remediation_rounds": 0}


def populate_units(job: dict, plan: dict) -> None:
    """Plan the units of ``job`` from the proposed roadmap (prerequisite order, lesson then items)."""
    nodes = {n["id"]: n for n in plan.get("nodes") or []}
    hashes = source_hashes(job["binding"])
    job["units"], job["unit_order"] = {}, []
    for unit in plan_mod.plan_units(plan):
        inputs = plan_mod.inputs_for(nodes[unit["node_id"]], unit["kind"], hashes, job["policy"]["targets"])
        job["units"][unit["unit_id"]] = {**unit, "status": "pending", "attempts": 0, "inputs_hash": digest_of(inputs),
                                         "order_id": None, "staged_digest": None, "last_errors": [],
                                         "stage_keys_done": [], "reason": None, "reused_from": None}
        job["unit_order"].append(unit["unit_id"])


def transition(job: dict, to: str, *, at: str, reason: str, actor: str = "studio") -> None:
    """Move to ``to`` if the table allows it; refuse (stable code) otherwise."""
    if to not in STATUSES or job["status"] not in TRANSITIONS:
        raise StudioError("studio_unknown_status", f"{job['status']} -> {to}")
    if to not in TRANSITIONS[job["status"]]:
        raise StudioError("studio_transition_refused", f"{job['status']} -> {to} is not allowed")
    if job["status"] == "paused" and to not in {job.get("paused_from"), "cancelled", "superseded", "blocked"}:
        raise StudioError("studio_transition_refused", f"paused job resumes to {job.get('paused_from')}, not {to}")
    if to == "paused":
        job["paused_from"] = job["status"]
    elif job["status"] == "paused":
        job["paused_from"] = None
    job["status_history"].append({"at": at, "from": job["status"], "to": to, "reason": reason, "actor": actor})
    job["status"] = to


def is_terminal(job: dict) -> bool:
    return job["status"] in TERMINAL


# --------------------------------------------------- stage keys / idempotency
def stage_key(job_id: str, stage: str, inputs_hash: str, versions: dict) -> str:
    return digest_of({"job_id": job_id, "stage": stage, "inputs_hash": inputs_hash, "policy_versions": versions})


def complete_stage(holder: dict, key: str) -> bool:
    """Record a stage key on a job or unit. True when newly completed, False for a no-op repeat."""
    done = holder.setdefault("stage_keys_done", [])
    if key in done:
        return False
    done.append(key)
    return True


def _check_key(key) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise StudioError("studio_invalid_idempotency_key", "idempotency key must be 8-80 safe characters")
    return key


def idem_replay(registry: dict, key, action: str, body) -> dict | None:
    """Replay of a stored result for the same key + same body; refusal for the same key + another body.

    ``registry`` is the session's idempotency document (``studio/idempotency.json``)."""
    if key is None:
        return None
    record = registry.get(_check_key(key))
    if record is None:
        return None
    if record["action"] != action or record["body"] != digest_of(body):
        raise StudioError("studio_conflict", "idempotency key reused with a different action or body")
    return {**record["result"], "idempotent_replay": True}


def idem_remember(registry: dict, key, action: str, body, result: dict, at: str) -> None:
    if key is None:
        return
    registry[_check_key(key)] = {"action": action, "body": digest_of(body), "result": result, "at": at}


# ------------------------------------------------------------ leases/budgets
def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def lease_until(now: datetime, seconds: int) -> str:
    return (now + timedelta(seconds=seconds)).astimezone(UTC).isoformat()


def reclaim_expired(job: dict, now: datetime) -> list[str]:
    """Expire orders whose lease ran out; the unit loses an attempt (bounded retries). Returns order ids."""
    expired = []
    for order_id, entry in sorted(job["orders"].items()):
        if entry["status"] != "issued" or parse_time(entry["lease_until"]) > now:
            continue
        entry["status"] = "expired"
        entry["closed_at"] = now.astimezone(UTC).isoformat()
        unit = job["units"].get(entry["unit_id"])
        if unit and unit["order_id"] == order_id and unit["status"] == "ordered":
            register_failure(job, unit, ["lease_expired: the order was not answered within its lease"])
        expired.append(order_id)
    return expired


def register_failure(job: dict, unit: dict, errors: list[str]) -> bool:
    """Count a failed attempt. True when the unit's budget is exhausted (the unit stays invalid with a reason)."""
    unit["attempts"] += 1
    unit["last_errors"] = [str(e)[:500] for e in errors[:20]]
    unit["status"] = "invalid"
    if unit["attempts"] >= job["policy"]["budgets"]["max_attempts_per_unit"]:
        unit["reason"] = "attempt_budget_exhausted"
        return True
    return False


def block_for_defect(job: dict, unit: dict, *, at: str) -> None:
    job["blocked"] = {"kind": "content_defect", "unit_id": unit["unit_id"], "reason": unit["reason"],
                      "attempts": unit["attempts"], "errors": unit["last_errors"], "at": at,
                      "next_step": "human review: the author could not produce a valid unit within its attempt budget"}
    transition(job, "blocked", at=at, reason=f"content_defect:{unit['unit_id']}")


def compute_build_digest(job: dict) -> str | None:
    """Merkle-style root over the staged units, roadmap version, source hashes and policy versions."""
    units = job["units"]
    if not plan_mod.build_complete(units):
        return None
    binding = job["binding"]
    return build_digest_of({uid: u["staged_digest"] for uid, u in units.items()}, binding["roadmap_version"],
                           source_hashes(binding), binding["policy_versions"])


def build_digest_of(unit_digests: dict, roadmap_version, sources: dict, policy_versions: dict) -> str:
    """The build digest formula, shared with the promotion check so both sides can recompute it."""
    return digest_of({"units": sorted((uid, d) for uid, d in unit_digests.items()),
                      "roadmap_version": roadmap_version, "sources": sources, "policy_versions": policy_versions})


# -------------------------------------------------------------- persistence
def job_path(root, job_id: str):
    return child(root / "jobs", valid_job_id(job_id) + ".json")


def save_job(root, job: dict, now: str) -> None:
    job["revision"] += 1
    job["updated_at"] = now
    secure_mkdir(root / "jobs", root)
    write_atomic_json(job_path(root, job["job_id"]), job, root=root)


def archived_path(root, job_id: str):
    archive = root / "archive"
    if not archive.is_dir():
        return None
    found = list(archive.glob(f"*-{valid_job_id(job_id)}.json"))
    # collision names ("stamp-1-job", "stamp-10-job") do not sort chronologically; the newest write wins
    return max(found, key=lambda p: (p.stat().st_mtime_ns, p.name)) if found else None


def load_job(root, job_id: str) -> dict:
    path = job_path(root, job_id)
    if not path.is_file():
        path = archived_path(root, job_id)
        if path is None:
            raise StudioError("studio_job_not_found", job_id)
    return read_json(path)


def list_jobs(root, *, include_archived: bool = False) -> list[dict]:
    jobs = []
    folders = [root / "jobs"] + ([root / "archive"] if include_archived else [])
    for folder in folders:
        if folder.is_dir():
            for path in sorted(folder.glob("*job_*.json")):
                if path.is_file() and not path.is_symlink():
                    try:
                        doc = read_json(path)
                    except (OSError, ValueError):
                        continue
                    if isinstance(doc, dict) and _JOB_ID.fullmatch(str(doc.get("job_id"))):
                        jobs.append(doc)
    return sorted(jobs, key=lambda j: (j["created_at"], j["job_id"]))


def active_job(root) -> dict | None:
    live = [j for j in list_jobs(root) if j["status"] in _LIVE]
    return live[-1] if live else None


def archive_job(root, job: dict, now: datetime) -> None:
    """Rename a superseded job (and its orders/inbox folders) into archive/; staging stays for rebase."""
    archive_rename(root, job_path(root, job["job_id"]), now=now)
    for folder in ("orders", "inbox"):
        path = root / folder / job["job_id"]
        if path.is_dir():
            archive_rename(root, path, now=now)

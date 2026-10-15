"""Rules receipts and owner acceptance: signing, storage, anchoring, freshness and the promotion conditions.

A receipt only states "these invariants hold under these probes". It is signed with HMAC-SHA256 under the ledger
trust key (the key lives outside the workspace, see ledger_trust), stored under ``studio/receipts/<job>/`` and
anchored by its digest as a ledger ``event`` (never evidence). A receipt is *fresh* while nothing but studio events
was appended to the ledger after the point it describes. Owner acceptance is a ledger ``event`` bound to the exact
build digest. Pure data + small file helpers; no network, no deletion.
"""
from __future__ import annotations

import hmac
import hashlib

from . import ledger_trust
from .course_state import CourseStateError
from .studio_store import canonical, child, digest_of, read_json, write_atomic_json

RECEIPT_SCHEMA = 1
RECEIPT_EVENT = "rules_receipt"
ACCEPT_EVENT = "studio_owner_acceptance"
REJECT_EVENT = "studio_owner_rejection"
OWNER_SOURCE = "studio_owner"
STUDIO_SOURCES = frozenset({"studio", OWNER_SOURCE})
OWNER_CHANNELS = ("owner_cli", "owner_ui")
# Course-state commits that only record the outcome of a gate run (a note in promotion_history): they do not make the
# receipt they describe stale, plus UI engagement telemetry (heartbeats while the learner has the page open): it is not
# evidence and no probe reads it. A roadmap approval is NOT listed: it supersedes capability progress, so it does
# stale the receipt; the refusal names the refresh step (`alearn studio handover`), the owner accepts the dossier they saw.
NOTE_EVENTS = frozenset({"course_promotion_checked", "engagement_interval"})


def payload_digest(payload) -> str:
    return digest_of(payload)


# ------------------------------------------------------------------ signing
def _mac(receipt: dict, key: bytes) -> str:
    body = {k: v for k, v in receipt.items() if k != "signature"}
    return hmac.new(key, canonical(body), hashlib.sha256).hexdigest()


def sign_receipt(receipt: dict) -> dict:
    out = {k: v for k, v in receipt.items() if k != "signature"}
    out["signature"] = {"algo": "hmac-sha256", "value": _mac(out, ledger_trust.ledger_key())}
    return out


def verify_receipt(receipt) -> bool:
    try:
        sig = receipt["signature"]
        return (sig["algo"] == "hmac-sha256"
                and hmac.compare_digest(str(sig["value"]), _mac(receipt, ledger_trust.ledger_key())))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def receipt_digest(receipt: dict) -> str:
    return digest_of(receipt)


# ------------------------------------------------------------------ storage
def persist_receipt(root, job_id: str, receipt: dict):
    path = child(root / "receipts", job_id, receipt["receipt_id"] + ".json")
    write_atomic_json(path, receipt, root=root)
    return path


def load_receipts(root, job_id: str) -> list[dict]:
    folder = child(root / "receipts", job_id)
    out = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            if path.is_file() and not path.is_symlink():
                try:
                    doc = read_json(path)
                except (OSError, ValueError):
                    continue
                if isinstance(doc, dict) and doc.get("receipt_id"):
                    out.append(doc)
    # The ledger position orders receipts even when the clock does not (a later receipt anchors after an earlier one).
    return sorted(out, key=lambda r: (r.get("ledger_seq_after", -1), r.get("created_at", ""), r["receipt_id"]))


def latest_receipt(root, job_id: str, *, scope: str | None = None, build_digest: str | None = None) -> dict | None:
    rows = [r for r in load_receipts(root, job_id) if not r.get("partial")
            and (scope is None or r.get("scope") == scope)
            and (build_digest is None or r.get("build_digest") == build_digest)]
    return rows[-1] if rows else None


def anchor_receipt(led, receipt: dict) -> dict:
    return led.append("event", {"event_type": RECEIPT_EVENT, "receipt_id": receipt["receipt_id"],
                                "receipt_digest": receipt_digest(receipt), "job_id": receipt.get("job_id"),
                                "scope": receipt.get("scope"), "build_digest": receipt.get("build_digest"),
                                "status": receipt.get("status"), "ledger_seq_after": receipt.get("ledger_seq_after")},
                      {"source": "studio"})


# ---------------------------------------------------------------- freshness
def ledger_position(led) -> dict:
    recs = led.verified_records()
    return {"seq": recs[-1]["seq"] if recs else -1, "head": recs[-1]["record_hash"] if recs else None}


def freshness(led, receipt) -> tuple[bool, str | None]:
    """(fresh, reason). Stale when the ledger prefix the receipt described changed or anything other than studio
    events (learner evidence, course state commits, ...) was appended after it."""
    try:
        seq, head = receipt["ledger_seq_after"], receipt["ledger_head_after"]
    except (KeyError, TypeError):
        return False, "receipt_has_no_ledger_position"
    recs = led.verified_records()
    if type(seq) is not int or seq < 0 or seq >= len(recs) or recs[seq]["record_hash"] != head:
        return False, "ledger_prefix_changed"
    for rec in recs[seq + 1:]:
        if rec["record_type"] == "event" and rec["provenance"].get("source") in STUDIO_SOURCES:
            continue
        if rec["record_type"] == "event" and rec["payload"].get("event_type") in NOTE_EVENTS:
            continue
        return False, f"ledger_advanced:{rec['payload'].get('event_type') or rec['record_type']}"
    return True, None


def anchored(led, receipt) -> bool:
    digest = receipt_digest(receipt)
    return any(r["record_type"] == "event" and r["payload"].get("event_type") == RECEIPT_EVENT
               and r["payload"].get("receipt_id") == receipt.get("receipt_id")
               and r["payload"].get("receipt_digest") == digest for r in led.verified_records())


# --------------------------------------------------------------- acceptance
def owner_events(led, job_id: str) -> list[dict]:
    return [r for r in led.verified_records()
            if r["record_type"] == "event" and r["provenance"].get("source") == OWNER_SOURCE
            and r["payload"].get("job_id") == job_id
            and r["payload"].get("event_type") in {ACCEPT_EVENT, REJECT_EVENT}]


def find_acceptance(led, job_id: str, build_digest: str) -> dict | None:
    """The owner acceptance for exactly this digest, unless the owner rejected afterwards."""
    verdict = None
    for rec in owner_events(led, job_id):
        p = rec["payload"]
        if p["event_type"] == ACCEPT_EVENT and p.get("build_digest") == build_digest:
            verdict = rec
        elif p["event_type"] == REJECT_EVENT:
            verdict = None
    return verdict


def acceptance_valid(rec: dict) -> bool:
    p = rec["payload"]
    return p.get("channel") in OWNER_CHANNELS and bool((p.get("statement") or "").strip())


def enforcement_problems(receipt: dict, *, job_id=None, roadmap_version=None, session_id=None) -> list[str]:
    """Why a (signed) full receipt does not describe the gate as it stands NOW: the enforcement code or the probe set
    changed since it was issued, probes it should have run are missing, a rule was left unverified, or it belongs to a
    different job, roadmap or session. Any problem means stale: the gate must be re-run."""
    from . import rules_check as rc          # lazy: rules_check imports this module
    from .rules_probes import all_probes
    from .rules_probes.base import HARD_RULES, RULES
    problems = []
    probes = all_probes()
    if receipt.get("code_tree_hash") != rc.code_tree_hash():
        problems.append("enforcement_code_changed")
    if receipt.get("probe_set_hash") != rc.probe_set_hash(probes):
        problems.append("probe_set_changed")
    missing = sorted({p.id for p in probes} - set(receipt.get("probes_run") or ()))
    if missing:
        problems.append("probes_not_run:" + ",".join(missing[:4]))
    by_rule = {r.get("rule"): r for r in receipt.get("results") or [] if isinstance(r, dict)}
    for rule in RULES:
        row = by_rule.get(rule)
        if row is None or row.get("status") == "skipped":
            problems.append(f"rule_unverified:{rule}")
        elif rule in HARD_RULES and not any(p.get("status") == "pass" for p in row.get("probes") or []):
            problems.append(f"hard_rule_unverified:{rule}")
    if job_id is not None and receipt.get("job_id") != job_id:
        problems.append("receipt_bound_to_another_job")
    if roadmap_version is not None and receipt.get("roadmap_version") != roadmap_version:
        problems.append("receipt_bound_to_another_roadmap")
    if session_id is not None and receipt.get("session_id") != session_id:
        problems.append("receipt_bound_to_another_session")
    return problems


def require_promotion_conditions(led, bundle: dict) -> dict:
    """Owner acceptance + signed fresh passing full receipt, both bound to the bundle's build digest.

    Raises CourseStateError with a stable code; returns a compact reference to the acceptance record."""
    job_id, digest = bundle.get("job_id"), bundle.get("build_digest")
    accept = find_acceptance(led, job_id, digest)
    if accept is None:
        if any(r["payload"]["event_type"] == ACCEPT_EVENT for r in owner_events(led, job_id)):
            raise CourseStateError("promotion_acceptance_digest_mismatch",
                                   "the owner accepted a different build digest than the one being promoted")
        raise CourseStateError("promotion_requires_owner_acceptance", "no owner acceptance is bound to this build digest")
    if not acceptance_valid(accept):
        raise CourseStateError("promotion_acceptance_invalid", "acceptance must come from an owner channel with a statement")
    receipt = bundle.get("receipt")
    if not isinstance(receipt, dict) or not verify_receipt(receipt):
        raise CourseStateError("promotion_requires_fresh_receipt", "the rules receipt is missing or its signature is invalid")
    problems = []
    if receipt.get("scope") != "full" or receipt.get("partial"):
        problems.append("receipt_not_full_scope")
    if receipt.get("build_digest") != digest:
        problems.append("receipt_bound_to_another_build")
    problems += enforcement_problems(receipt, job_id=job_id, roadmap_version=bundle.get("roadmap_version"),
                                     session_id=getattr(led, "session_id", None))
    if receipt.get("status") != "pass" or receipt.get("exit_code") != 0:
        problems.append("receipt_not_passing")
    if receipt.get("ledger_seq_before") != receipt.get("ledger_seq_after") or \
            receipt.get("ledger_head_before") != receipt.get("ledger_head_after"):
        problems.append("receipt_ledger_moved_during_the_run")
    if not anchored(led, receipt):
        problems.append("receipt_not_anchored_in_ledger")
    fresh, reason = freshness(led, receipt)
    if not fresh:
        problems.append(f"receipt_stale:{reason}")
    if problems:
        raise CourseStateError("promotion_requires_fresh_receipt", ", ".join(problems))
    return {"seq": accept["seq"], "record_id": accept["record_id"], "receipt_id": receipt["receipt_id"]}


# ------------------------------------------------------------------- bundles
def bundle_from_job(job: dict, payloads: dict, *, acceptance=None, receipt=None) -> dict:
    """The exact data CourseKernel.promote_build installs: one payload per unit plus everything the build was
    authored against. ``payloads`` is {unit_id: staged payload}."""
    binding = job["binding"]
    return {"job_id": job["job_id"], "build_digest": job.get("build_digest"), "roadmap_version": binding["roadmap_version"],
            "units": [{"unit_id": uid, "kind": job["units"][uid]["kind"], "node_id": job["units"][uid]["node_id"],
                       "payload": payloads[uid]} for uid in job["unit_order"]],
            "sources": {k: v.get("content_sha256") for k, v in (binding.get("sources") or {}).items()},
            "capabilities": dict(binding.get("capabilities") or {}), "policy_versions": dict(binding.get("policy_versions") or {}),
            "targets": job["policy"]["targets"], "acceptance": acceptance, "receipt": receipt}

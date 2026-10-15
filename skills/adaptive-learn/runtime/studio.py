"""Course Studio facade (stage A): durable build jobs for a course session.

The studio is a deterministic orchestrator, never an author: it plans units from the proposed
roadmap, hands the host agent need-to-know work orders, admits only validator-approved
payloads into a non-servable staging area and tracks the build in a hash-chained event log
anchored in the session ledger. It never writes course-state.json and never appends evidence,
observation or attempt records. Every mutation holds the session lock, re-checks drift, passes
the status-machine guard, writes atomically and records a studio event.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import studio_jobs as jobs
from . import studio_orders as orders
from . import studio_plan as plan_mod
from . import studio_staging as staging
from . import studio_worker
from .course import CourseKernel
from .course_state import CourseStateError
from .session_lock import session_lock
from .studio_agents import MAX_RUNNER_FAILURES, PROVENANCE_KEYS, RunnerMixin
from .studio_review import ReviewMixin
from .studio_store import (IDEMPOTENCY_FILE, EventLog, StudioError, anchor, digest_of, ensure_layout, notify,
                           read_json, studio_dir, write_atomic_json)
from .utils import new_id

_DEFAULT_HOLDER = "inline"


class _Txn:
    """One locked operation: the job, its pending events and whether the head must be anchored."""

    def __init__(self, kernel, sid, d, led, root, job):
        self.k, self.sid, self.d, self.led, self.root, self.job = kernel, sid, d, led, root, job
        self.events: list[tuple[str, dict]] = []
        self.anchor = False
        self.history_len = len(job["status_history"])
        self.before = digest_of(job)
        self._snap = None

    def snapshot(self) -> dict:
        if self._snap is None:
            self._snap = self.k.ck.studio_snapshot(self.sid)
        return self._snap

    def event(self, kind: str, data: dict | None = None, *, anchor: bool = False) -> None:
        self.events.append((kind, data or {}))
        self.anchor = self.anchor or anchor


class StudioKernel(ReviewMixin, RunnerMixin):
    def __init__(self, workspace, *, clock=None, gate: bool = True):
        self.ck = CourseKernel(Path(workspace), clock=clock)
        self.gate = bool(gate)         # False only for tests that exercise authoring without the rules gate

    # ----------------------------------------------------------------- plumbing
    def _dt(self) -> datetime:
        return self.ck._dt()

    def _now(self) -> str:
        return self._dt().astimezone(UTC).isoformat()

    @contextmanager
    def _locked(self, sid, *, create: bool):
        d = self.ck.sk.dir(sid)
        with session_lock(d):
            led = self.ck.sk.ledger(sid)
            led.require_integrity()
            root = ensure_layout(d) if create else studio_dir(d)
            yield d, led, root

    def _registry(self, root) -> dict:
        path = root / IDEMPOTENCY_FILE
        return read_json(path) if path.is_file() else {}

    def _save_registry(self, root, registry: dict) -> None:
        write_atomic_json(root / IDEMPOTENCY_FILE, registry, root=root)

    def _load(self, root, job_id):
        if not root.is_dir():
            raise StudioError("studio_no_job", "no studio job exists for this session; run `alearn studio init`")
        if job_id is None:
            live = jobs.active_job(root)
            if live is None:
                every = jobs.list_jobs(root, include_archived=True)
                if not every:
                    raise StudioError("studio_no_job", "no studio job exists for this session; run `alearn studio init`")
                return every[-1]
            return live
        return jobs.load_job(root, job_id)

    def _commit(self, txn: _Txn, *, save: bool | None = None) -> None:
        """Persist a changed job (``save=True`` forces, ``False`` skips), then events, then the anchor."""
        job = txn.job
        if save is None:
            save = digest_of(job) != txn.before
        # Events first: a crash leaves an event without its job change (a retry redoes the work and the
        # log stays an honest intent trail) instead of a staged unit whose audit trail never existed.
        log = EventLog(txn.root)
        for kind, data in txn.events:
            log.append(kind, job["job_id"], data, self._now())
        if save:
            jobs.save_job(txn.root, job, self._now())
        if txn.events or txn.anchor or len(job["status_history"]) != txn.history_len:
            anchor(txn.led, job["job_id"])

    # -------------------------------------------------------------------- drift
    def _supersede(self, txn: _Txn, drift: list[str], current: dict) -> None:
        job = txn.job
        job["drift"] = drift
        for entry in job["orders"].values():
            if entry["status"] == "issued":
                entry["status"] = "superseded"
                entry["closed_at"] = self._now()
        jobs.transition(job, "superseded", at=self._now(), reason="drift:" + ",".join(drift))
        txn.event("drift_superseded", {"drift": drift, "bound_roadmap": job["binding"]["roadmap_version"],
                                       "current_roadmap": current["roadmap_version"]}, anchor=True)
        notify(txn.root, "superseded", job["job_id"], "job superseded by drift: " + ", ".join(drift), self._now())
        jobs.save_job(txn.root, job, self._now())
        self._commit(txn, save=False)
        jobs.archive_job(txn.root, job, self._dt())

    def _refresh(self, txn: _Txn) -> bool:
        """Drift check on open. True when the job was superseded (and archived)."""
        job = txn.job
        if jobs.is_terminal(job):
            return False
        current = jobs.make_binding(txn.snapshot(), job["policy"].get("id"))
        drift = jobs.detect_drift(job["binding"], current)
        if not drift:
            return False
        idle = not job["orders"] and all(u["status"] == "pending" for u in job["units"].values())
        if idle and (txn.snapshot()["roadmap_proposed"] or job["status"] in {"planning", "needs_learner"}):
            job["binding"], job["drift"] = current, []
            job["units"], job["unit_order"] = {}, []
            txn.event("job_rebound", {"drift": drift})
            return False
        self._supersede(txn, drift, current)
        return True

    # --------------------------------------------------------------------- gate
    def _gate(self, txn: _Txn) -> bool:
        """Is the course ready for authoring? Otherwise park the job in needs_learner (never fabricate input)."""
        job, snap = txn.job, txn.snapshot()
        parked = job.get("needs_learner") or {}
        if job["status"] == "needs_learner" and parked.get("phase") == "rules_gate":
            # Parked by the rules gate (a source to re-validate, a roadmap to correct): course readiness does not clear
            # it. A quick recheck of the same probes decides; then the full gate runs again from `checking`.
            if not self._rules_gate_cleared(txn):
                return False
            job["needs_learner"] = None
            jobs.transition(job, "planning", at=self._now(), reason="rules_gate_findings_resolved")
            txn.event("needs_learner_resolved", {"phase": "rules_gate"})
            return True
        if snap["ready"]:
            if job["status"] == "needs_learner":
                job["needs_learner"] = None
                jobs.transition(job, "planning", at=self._now(), reason="learner_prerequisites_resolved")
                txn.event("needs_learner_resolved")
            return True
        step = {"blockers": snap["blockers"], "directive": snap["directive"], "phase": snap["phase"],
                "since": (job.get("needs_learner") or {}).get("since") or self._now()}
        if job["status"] in {"planning", "authoring"}:
            jobs.transition(job, "needs_learner", at=self._now(), reason="course_prerequisites_missing")
            txn.event("needs_learner", {"blockers": snap["blockers"]}, anchor=True)
            notify(txn.root, "needs_learner", job["job_id"], snap["directive"], self._now())
        job["needs_learner"] = step
        return False

    def _ensure_planned(self, txn: _Txn) -> None:
        job, snap = txn.job, txn.snapshot()
        if not job["units"]:
            jobs.populate_units(job, snap["plan"])
            txn.event("job_planned", {"units": job["unit_order"], "roadmap_version": job["binding"]["roadmap_version"]})
            if job.get("rebased_from"):
                self._reuse(txn)
        if job["status"] == "planning":
            jobs.transition(job, "authoring", at=self._now(), reason="units_planned")
        self._maybe_finish(txn)

    # ------------------------------------------------------------------- reuse
    def _reuse(self, txn: _Txn) -> None:
        """Rebase: reuse staged payloads whose inputs are unchanged, after re-validating them."""
        job = txn.job
        old = jobs.load_job(txn.root, job["rebased_from"])
        targets = job["policy"]["targets"]
        for uid in job["unit_order"]:
            unit, prior = job["units"][uid], old["units"].get(uid)
            if not prior or prior["status"] != "staged" or prior["inputs_hash"] != unit["inputs_hash"]:
                continue
            note = None
            try:
                payload = staging.read_staged(txn.root, old["job_id"], uid, prior["staged_digest"])
                validation, other = self._validate(txn, job, unit, payload)
                if other is None and unit["kind"] == "items":
                    note = "its lesson was not reused"
                else:
                    verdict = plan_mod.evaluate_unit(unit["kind"], payload, validation, targets)
                    note = None if verdict["complete"] else "; ".join(verdict["problems"])[:300]
            except (StudioError, CourseStateError) as e:
                payload, note = None, f"{e}"[:300]
            if note:
                unit["reuse_note"] = f"reuse_rejected: {note}"
                txn.event("unit_reuse_rejected", {"unit_id": uid, "note": note})
                continue
            digest = staging.stage_payload(txn.root, job["job_id"], uid, payload)
            unit.update(status="staged", staged_digest=digest, reused_from=old["job_id"])
            jobs.complete_stage(unit, jobs.stage_key(job["job_id"], "stage", unit["inputs_hash"],
                                                     job["binding"]["policy_versions"]))
            txn.event("unit_reused", {"unit_id": uid, "staged_digest": digest, "from_job": old["job_id"]}, anchor=True)

    # -------------------------------------------------------------- validation
    def _validate(self, txn: _Txn, job: dict, unit: dict, payload):
        """Dry-validate ``payload`` for ``unit`` against the node's other staged unit.

        Returns (validation, other_payload_or_None)."""
        node, kind = unit["node_id"], unit["kind"]
        other_kind = "items" if kind == "lesson" else "lesson"
        other = job["units"].get(plan_mod.unit_id(other_kind, node))
        other_payload = None
        if other and other["status"] == "staged" and other["staged_digest"]:
            other_payload = staging.read_staged(txn.root, job["job_id"], other["unit_id"], other["staged_digest"])
        if kind == "lesson":
            return self.ck.validate_lesson_dry(txn.sid, node, payload, staged_items=other_payload), other_payload
        return self.ck.validate_items_dry(txn.sid, node, payload, staged_lesson=other_payload), other_payload

    def _full_check(self, txn: _Txn, job: dict) -> dict:
        units = {}
        for uid in job["unit_order"]:
            unit = job["units"][uid]
            row = {"status": unit["status"], "attempts": unit["attempts"], "staged_digest": unit["staged_digest"],
                   "valid": None, "complete": False, "problems": list(unit["last_errors"])}
            if unit["status"] == "staged":
                try:
                    payload = staging.read_staged(txn.root, job["job_id"], uid, unit["staged_digest"])
                    validation, _ = self._validate(txn, job, unit, payload)
                    verdict = plan_mod.evaluate_unit(unit["kind"], payload, validation, job["policy"]["targets"])
                    row.update(valid=bool(validation["valid"]), complete=verdict["complete"],
                               problems=verdict["problems"], summary=validation.get("summary"))
                    if "freshness" in validation:
                        row["freshness"] = {k: v["status"] for k, v in validation["freshness"].items()}
                except StudioError as e:
                    row.update(valid=False, problems=[f"{e.code}: {e.detail}"])
            units[uid] = row
        complete = bool(units) and all(r["complete"] for r in units.values())
        return {"units": units, "build_complete": complete,
                "build_digest": jobs.compute_build_digest(job) if complete else None}

    def _maybe_finish(self, txn: _Txn) -> None:
        """All units staged: re-validate the whole build against each other, then park it in ``checking``."""
        job = txn.job
        if job["status"] not in {"authoring", "remediation"} or not plan_mod.build_complete(job["units"]):
            return
        jobs.transition(job, "validating", at=self._now(), reason="all_units_staged")
        report = self._full_check(txn, job)
        if report["build_complete"]:
            job["build_digest"] = report["build_digest"]
            jobs.transition(job, "checking", at=self._now(), reason="build_complete")
            txn.event("build_complete", {"build_digest": job["build_digest"]}, anchor=True)
            notify(txn.root, "build_complete", job["job_id"], "all units staged; awaiting the rules check", self._now())
            self._after_checking(txn)
            return
        job["remediation_rounds"] = job.get("remediation_rounds", 0) + 1
        for uid, row in report["units"].items():
            if not row["complete"]:
                unit = job["units"][uid]
                unit.update(status="invalid", staged_digest=None, last_errors=[str(p)[:500] for p in row["problems"][:20]],
                            reason="revalidation_failed")
                txn.event("unit_reopened", {"unit_id": uid, "problems": row["problems"][:5]})
        jobs.transition(job, "remediation", at=self._now(), reason="revalidation_failed")
        if job["remediation_rounds"] > job["policy"]["budgets"]["max_remediation_rounds"]:
            reopened = next((job["units"][u] for u, r in report["units"].items() if not r["complete"]), None)
            if reopened is not None:
                reopened["reason"] = "remediation_rounds_exhausted"
                jobs.block_for_defect(job, reopened, at=self._now())
                txn.event("job_blocked", {"unit_id": reopened["unit_id"], "reason": "remediation_rounds_exhausted"},
                          anchor=True)
                notify(txn.root, "blocked", job["job_id"], "content defect: remediation rounds exhausted", self._now())

    # ---------------------------------------------------------------- summaries
    def _summary(self, job: dict, root) -> dict:
        units = {uid: {k: u[k] for k in ("kind", "node_id", "status", "attempts", "staged_digest", "last_errors",
                                         "order_id", "reason", "reused_from")} for uid, u in job["units"].items()}
        staged = sum(1 for u in job["units"].values() if u["status"] == "staged")
        open_order = next((oid for oid, o in job["orders"].items() if o["status"] == "issued"), None)
        binding = job["binding"]
        return {"job_id": job["job_id"], "session_id": job["session_id"], "status": job["status"],
                "revision": job["revision"], "created_at": job["created_at"], "updated_at": job["updated_at"],
                "binding": {"roadmap_version": binding["roadmap_version"], "manifest_version": binding["manifest_version"],
                            "capabilities": binding["capabilities"], "policy_versions": binding["policy_versions"]},
                "policy": job["policy"],
                "progress": {"units_total": len(units), "units_staged": staged,
                             "units_invalid": sum(1 for u in units.values() if u["status"] == "invalid")},
                "units": units, "current_order": open_order, "needs_learner": job["needs_learner"],
                "blocked": job["blocked"], "build_digest": job["build_digest"], "paused_from": job["paused_from"],
                "rebased_from": job["rebased_from"], "superseded_by": job["superseded_by"], "drift": job["drift"],
                "history": job["status_history"][-10:], "last_receipt": job.get("last_receipt"),
                "handover": job.get("handover"), "acceptance": job.get("acceptance"), "promotion": job.get("promotion")}

    # ---------------------------------------------------------------- operations
    def _operate(self, sid, job_id, action, body, key, fn, *, allow_superseded=False, on_superseded=None):
        with self._locked(sid, create=False) as (d, led, root):
            job = self._load(root, job_id)
            registry = self._registry(root)
            if callable(body):
                body = body(root, job)       # bound to what is on disk, read under the lock
            replay = jobs.idem_replay(registry, key, action, {"job_id": job["job_id"], **body})
            if replay is not None:
                return replay
            txn = _Txn(self, sid, d, led, root, job)
            gone = self._refresh(txn) or job["status"] == "superseded"
            if gone:
                if on_superseded is not None:
                    return on_superseded(txn)
                if not allow_superseded:
                    raise StudioError("studio_job_superseded",
                                      "the course drifted from this job; run `alearn studio rebase`")
                return fn(txn)           # read-only views of a superseded (archived) job never rewrite it
            result = fn(txn)
            self._commit(txn)
            _stamp(result, job)
            if key is not None:
                jobs.idem_remember(registry, key, action, {"job_id": job["job_id"], **body}, result, self._now())
                self._save_registry(root, registry)
            return result

    def init(self, sid, *, policy: dict | None = None) -> dict:
        resolved = plan_mod.resolve_policy(policy)
        with self._locked(sid, create=True) as (d, led, root):
            live = jobs.active_job(root)
            if live is not None:
                txn = _Txn(self, sid, d, led, root, live)
                if not self._refresh(txn):
                    if policy is not None and resolved != live["policy"]:
                        raise StudioError("studio_policy_mismatch",
                                          "a live job exists with a different policy; cancel it before re-initialising")
                    self._commit(txn)
                    return {"result": "already_initialized", **self._summary(live, root)}
            snap = self.ck.studio_snapshot(sid)
            job = jobs.new_job(sid, self._now(), resolved, jobs.make_binding(snap, resolved["id"]))
            txn = _Txn(self, sid, d, led, root, job)
            txn._snap = snap
            txn.history_len = 0
            txn.event("job_created", {"job_id": job["job_id"], "roadmap_version": job["binding"]["roadmap_version"],
                                      "policy": resolved["id"], "allow_private_sources": resolved["allow_private_sources"]},
                      anchor=True)
            self._gate(txn)
            self._commit(txn, save=True)
            return {"result": "created", **self._summary(job, root)}

    def plan(self, sid, job_id=None, *, idempotency_key=None) -> dict:
        def fn(txn):
            if txn.job["status"] in {"planning", "needs_learner", "authoring"} and self._gate(txn):
                if txn.snapshot()["exam_locked"]:
                    raise StudioError("exam_active", "studio planning is locked while an exam is active")
                self._ensure_planned(txn)
            return self._summary(txn.job, txn.root)
        return self._operate(sid, job_id, "plan", {}, idempotency_key, fn)

    def status(self, sid, job_id=None) -> dict:
        with self._locked(sid, create=False) as (d, led, root):
            if not root.is_dir() or (job_id is None and not jobs.list_jobs(root, include_archived=True)):
                return {"status": "no_job", "next_step": "alearn studio init --session " + sid}
        out = self._operate(sid, job_id, "status", {}, None,
                            lambda txn: self._summary(txn.job, txn.root), allow_superseded=True)
        out["worker"] = studio_worker.worker_status(studio_dir(self.ck.sk.dir(sid)), self._dt())
        return out

    def next_task(self, sid, job_id=None, *, holder: str = _DEFAULT_HOLDER) -> dict:
        def blocked(reason, **extra):
            return {"state": "blocked", "reason": reason, **extra}

        def fn(txn):
            job = txn.job
            st = job["status"]
            if st == "cancelled":
                return blocked("cancelled")
            if st == "paused":
                return {"state": "idle", "reason": "paused"}
            if st == "blocked":
                return blocked("blocked", blocked=job["blocked"])
            if st in jobs.BUILT_STATUSES:
                return {"state": "done", "build_digest": job["build_digest"], "status": st}
            if st in {"planning", "needs_learner", "authoring"}:
                if not self._gate(txn):
                    return {"state": "needs_learner", **job["needs_learner"]}
                if txn.snapshot()["exam_locked"]:
                    return {"state": "idle", "reason": "exam_active"}
                self._ensure_planned(txn)
                st = job["status"]
                if st in jobs.BUILT_STATUSES:
                    return {"state": "done", "build_digest": job["build_digest"], "status": st}
            if txn.snapshot()["exam_locked"]:
                return {"state": "idle", "reason": "exam_active"}
            for order_id in jobs.reclaim_expired(job, self._dt()):
                txn.event("lease_expired", {"order_id": order_id}, anchor=True)
            exhausted = next((u for u in job["units"].values()
                              if u["status"] == "invalid" and u["reason"] == "attempt_budget_exhausted"), None)
            if exhausted is not None:
                jobs.block_for_defect(job, exhausted, at=self._now())
                txn.event("job_blocked", {"unit_id": exhausted["unit_id"]}, anchor=True)
                notify(txn.root, "blocked", job["job_id"], "content defect: attempt budget exhausted", self._now())
                return blocked("blocked", blocked=job["blocked"])
            unit = next((job["units"][uid] for uid in job["unit_order"] if job["units"][uid]["status"] != "staged"), None)
            if unit is None:
                self._maybe_finish(txn)
                return {"state": "done", "build_digest": job["build_digest"], "status": job["status"]}
            if unit["status"] == "ordered" and job["orders"][unit["order_id"]]["status"] == "issued":
                return self._order_view(txn, unit["order_id"])
            return self._emit(txn, unit, holder)

        def superseded(txn):
            return blocked("superseded", job_id=txn.job["job_id"], next_step="alearn studio rebase --job " + txn.job["job_id"])
        return self._operate(sid, job_id, "next_task", {}, None, fn, on_superseded=superseded)

    def _order_view(self, txn: _Txn, order_id: str) -> dict:
        order = orders.read_order(txn.root, txn.job["job_id"], order_id)
        entry = txn.job["orders"][order_id]
        return {"state": "order", "order": order, "lease_until": entry["lease_until"], "taken_by": entry["taken_by"],
                "inbox": str(txn.root / "inbox" / txn.job["job_id"] / order_id)}

    def _emit(self, txn: _Txn, unit: dict, holder: str) -> dict:
        job = txn.job
        snap = txn.snapshot()
        node = next(n for n in snap["plan"]["nodes"] if n["id"] == unit["node_id"])
        staged_lesson = None
        if unit["kind"] == "items":
            lesson_unit = job["units"][plan_mod.unit_id("lesson", unit["node_id"])]
            staged_lesson = staging.read_staged(txn.root, job["job_id"], lesson_unit["unit_id"], lesson_unit["staged_digest"])
        order_id = new_id("ord")
        order, texts = orders.build_order(job, unit, node, order_id=order_id, snapshot=snap, staged_lesson=staged_lesson,
                                          source_loader=lambda sid_, sha: self.ck.source_text(txn.sid, sid_, sha),
                                          now=self._now())
        orders.write_order(txn.root, order, texts)
        if job["status"] == "remediation":
            jobs.transition(job, "authoring", at=self._now(), reason="retry_order_issued")
        unit.update(status="ordered", order_id=order_id)
        job["orders"][order_id] = {"unit_id": unit["unit_id"], "status": "issued", "attempt": order["attempt"],
                                   "taken_by": holder, "created_at": order["created_at"],
                                   "lease_until": jobs.lease_until(self._dt(), job["policy"]["budgets"]["max_runner_seconds"]),
                                   "inputs_hash": unit["inputs_hash"], "sources": [s["source_id"] for s in order["sources"]]}
        txn.event("order_issued", {"order_id": order_id, "unit_id": unit["unit_id"], "attempt": order["attempt"],
                                   "sources": job["orders"][order_id]["sources"]})
        return self._order_view(txn, order_id)

    def job_budgets(self, sid, job_id=None) -> dict:
        with self._locked(sid, create=False) as (d, led, root):
            return dict(self._load(root, job_id)["policy"]["budgets"])

    def claim_order(self, sid, job_id, order_id, holder, *, event: bool = True) -> dict:
        """A runner takes (or renews) the lease of an issued order. Refused when the order is not issued, the job does not
        accept work, an exam is active, or ANOTHER holder's lease is still live."""
        def fn(txn):
            job = txn.job
            entry = job["orders"].get(order_id)
            if entry is None:
                raise StudioError("studio_order_not_found", str(order_id)[:80])
            if entry["status"] != "issued":
                raise StudioError(f"studio_order_{entry['status']}", f"order {order_id} is {entry['status']}")
            if job["status"] in {"paused", "blocked", "needs_learner"} or job["status"] in jobs.TERMINAL:
                raise StudioError("studio_job_not_accepting", f"job is {job['status']}")
            if txn.snapshot()["exam_locked"]:
                raise StudioError("exam_active", "studio orders are locked while an exam is active")
            now = self._dt()
            if entry["taken_by"] != holder and jobs.parse_time(entry["lease_until"]) > now:
                raise StudioError("studio_order_held", f"order {order_id} is held by {entry['taken_by']} until {entry['lease_until']}")
            entry["taken_by"] = holder
            entry["lease_until"] = jobs.lease_until(now, job["policy"]["budgets"]["max_runner_seconds"])
            if event:
                txn.event("order_claimed", {"order_id": order_id, "unit_id": entry["unit_id"], "holder": holder,
                                            "lease_until": entry["lease_until"]})
            return {"job_id": job["job_id"], "order_id": order_id, "taken_by": holder, "lease_until": entry["lease_until"]}
        return self._operate(sid, job_id, "claim_order", {}, None, fn)

    def runner_finish(self, sid, job_id, order_id, holder, outcome, provenance, *, reason=None) -> dict:
        """Record one runner run (anchored ``runner_finished`` event + the order entry) and settle the order's lease.

        ``completed``: the output goes to ``submit`` next, the unit's runner failure streak resets. ``failed``: the lease is
        released, the order is re-issued by the next ``next_task`` and the unit counts a runner failure (3 in a row block
        the job as a system defect). ``stopped``: released without counting. ``skipped``: handed back to the inline host."""
        if outcome not in {"completed", "failed", "stopped", "skipped"}:
            raise StudioError("studio_runner_outcome_invalid", str(outcome)[:40])

        def fn(txn):
            job, now = txn.job, self._now()
            entry = job["orders"].get(order_id)
            if entry is None:
                raise StudioError("studio_order_not_found", str(order_id)[:80])
            if entry["status"] != "issued" or entry["taken_by"] != holder:
                raise StudioError("studio_order_not_held", f"order {order_id} is no longer held by {holder}")
            unit = job["units"][entry["unit_id"]]
            record = {k: provenance.get(k) for k in PROVENANCE_KEYS}
            entry["runner"] = {**record, "outcome": outcome, "untrusted_fields": list(provenance.get("untrusted_fields") or [])}
            txn.event("runner_finished", {"order_id": order_id, "unit_id": unit["unit_id"], "outcome": outcome,
                                          "reason": reason, **record}, anchor=True)
            if outcome == "completed":
                unit["runner_failures"] = 0
            elif outcome == "skipped":
                entry["taken_by"] = "inline"
                entry["lease_until"] = jobs.lease_until(self._dt(), job["policy"]["budgets"]["max_runner_seconds"])
                txn.event("runner_skipped", {"order_id": order_id, "unit_id": unit["unit_id"], "reason": reason})
            else:
                entry["status"], entry["closed_at"], entry["release_reason"] = "released", now, reason
                txn.event("order_released", {"order_id": order_id, "unit_id": unit["unit_id"], "reason": reason,
                                             "counted": outcome == "failed"})
                if outcome == "failed":
                    unit["runner_failures"] = unit.get("runner_failures", 0) + 1
                    unit["last_runner_failure"] = reason
                    if unit["runner_failures"] >= MAX_RUNNER_FAILURES and "blocked" in jobs.TRANSITIONS[job["status"]]:
                        job["blocked"] = {"kind": "system_defect", "reason": "runner_unreliable", "unit_id": unit["unit_id"],
                                          "runner_failures": unit["runner_failures"], "last_failure": reason, "at": now,
                                          "next_step": "check `alearn studio doctor`, fix or disable the runner, then rebase or cancel"}
                        jobs.transition(job, "blocked", at=now, reason="system_defect:runner_unreliable")
                        txn.event("job_blocked", {"unit_id": unit["unit_id"], "reason": "runner_unreliable"}, anchor=True)
                        notify(txn.root, "blocked", job["job_id"],
                               f"system defect: the background runner failed {unit['runner_failures']} times in a row "
                               f"on {unit['unit_id']} (last: {reason})", now)
            return {"job_id": job["job_id"], "order_id": order_id, "outcome": outcome,
                    "runner_failures": unit.get("runner_failures", 0), "job_status": job["status"]}
        return self._operate(sid, job_id, "runner_finish", {}, None, fn)

    def finish_checking(self, sid, job_id=None) -> dict:
        """Runtime-owned stage: a build parked in ``checking`` (a crash or a pending gate) gets its full rules run now."""
        def fn(txn):
            before = txn.job["status"]
            if before == "checking":
                if self.gate and txn.snapshot()["exam_locked"]:
                    raise StudioError("exam_active", "the rules gate does not run while the learner has an exam open")
                self._after_checking(txn)
            return {"job_id": txn.job["job_id"], "from": before, "status": txn.job["status"]}
        return self._operate(sid, job_id, "finish_checking", {}, None, fn)

    def order_show(self, sid, job_id, order_id) -> dict:
        with self._locked(sid, create=False) as (d, led, root):
            job = self._load(root, job_id)
            if order_id not in job["orders"]:
                raise StudioError("studio_order_not_found", str(order_id)[:80])
            return {"order": orders.read_order(root, job["job_id"], order_id), "entry": job["orders"][order_id]}

    def submit(self, sid, job_id, order_id, payload=None, *, idempotency_key=None) -> dict:
        def body(root, job):
            return {"order_id": order_id, "payload": self._inbox_digest(root, job, order_id) if payload is None
                    else _body_digest(payload)}

        def fn(txn):
            job = txn.job
            entry = job["orders"].get(order_id)
            if entry is None:
                raise StudioError("studio_order_not_found", str(order_id)[:80])
            unit = job["units"][entry["unit_id"]]
            if unit["status"] == "staged" and entry["status"] == "submitted":
                key = jobs.stage_key(job["job_id"], "stage", unit["inputs_hash"], job["binding"]["policy_versions"])
                if key in unit["stage_keys_done"]:
                    return {"status": "noop", "reason": "stage_already_completed", "unit_id": unit["unit_id"],
                            "staged_digest": unit["staged_digest"]}
            if entry["status"] != "issued":
                raise StudioError(f"studio_order_{entry['status']}", f"order {order_id} is {entry['status']}")
            if job["status"] in {"paused", "blocked", "needs_learner"} or job["status"] in jobs.TERMINAL:
                raise StudioError("studio_job_not_accepting", f"job is {job['status']}")
            if txn.snapshot()["exam_locked"]:
                raise StudioError("exam_active", "studio submissions are locked while an exam is active")
            return self._accept(txn, unit, entry, order_id, payload)
        return self._operate(sid, job_id, "submit", body, idempotency_key, fn)

    @staticmethod
    def _inbox_digest(root, job: dict, order_id: str):
        """Digest of the inbox result file, so an idempotency key cannot replay a verdict for other content."""
        try:
            raw, _ = staging.read_result(root, job["job_id"], order_id, job["policy"]["budgets"]["max_output_bytes"])
        except StudioError:
            return None
        return None if raw is None else hashlib.sha256(raw).hexdigest()

    def _accept(self, txn: _Txn, unit: dict, entry: dict, order_id: str, payload) -> dict:
        job = txn.job
        limit = job["policy"]["budgets"]["max_output_bytes"]
        errors, parsed = [], None
        if payload is None:
            raw, errors = staging.read_result(txn.root, job["job_id"], order_id, limit)
            if errors and errors[0].startswith("result_missing"):
                raise StudioError("studio_result_missing", errors[0])   # a premature submit costs no attempt
            if not errors:
                parsed, errors = staging.parse_strict(raw, limit)
        else:
            parsed, errors = staging.parse_strict(payload, limit)
        validation, verdict = None, None
        if parsed is not None:
            extra = plan_mod.unknown_keys(unit["kind"], parsed)
            if extra:
                errors = [f"unknown top-level fields are not accepted: {extra[:10]}"]
            else:
                try:
                    validation, _ = self._validate(txn, job, unit, parsed)
                    verdict = plan_mod.evaluate_unit(unit["kind"], parsed, validation, job["policy"]["targets"])
                    errors = [] if verdict["complete"] else verdict["problems"]
                except (RecursionError, OverflowError, MemoryError) as e:
                    errors = [f"malformed_payload: {type(e).__name__}"]   # hostile shape: counted, never a crash
        entry["status"], entry["closed_at"] = "submitted", self._now()
        unit["status"] = "submitted"
        if errors:
            exhausted = jobs.register_failure(job, unit, errors)
            txn.event("unit_invalid", {"unit_id": unit["unit_id"], "order_id": order_id, "attempts": unit["attempts"],
                                       "errors": unit["last_errors"][:5]})
            if exhausted:
                jobs.block_for_defect(job, unit, at=self._now())
                txn.event("job_blocked", {"unit_id": unit["unit_id"]}, anchor=True)
                notify(txn.root, "blocked", job["job_id"], "content defect: attempt budget exhausted", self._now())
            elif job["status"] == "authoring":
                jobs.transition(job, "remediation", at=self._now(), reason=f"invalid_submission:{unit['unit_id']}")
            return {"status": "rejected", "unit_id": unit["unit_id"], "attempts": unit["attempts"],
                    "errors": unit["last_errors"], "budget_exhausted": exhausted, "job_status": job["status"]}
        digest = staging.stage_payload(txn.root, job["job_id"], unit["unit_id"], parsed)
        unit.update(status="staged", staged_digest=digest, last_errors=[], reason=None)
        jobs.complete_stage(unit, jobs.stage_key(job["job_id"], "stage", unit["inputs_hash"],
                                                 job["binding"]["policy_versions"]))
        txn.event("unit_staged", {"unit_id": unit["unit_id"], "order_id": order_id, "staged_digest": digest,
                                  "content_version": validation.get("content_version")}, anchor=True)
        self._maybe_finish(txn)
        return {"status": "staged", "unit_id": unit["unit_id"], "staged_digest": digest,
                "summary": validation.get("summary"), "freshness": {k: v["status"] for k, v in
                                                                    (validation.get("freshness") or {}).items()},
                "job_status": job["status"], "build_digest": job["build_digest"]}

    def check(self, sid, job_id=None) -> dict:
        """Read-only: completeness + per-unit validation against the live course. Persists nothing."""
        with self._locked(sid, create=False) as (d, led, root):
            job = self._load(root, job_id)
            txn = _Txn(self, sid, d, led, root, job)
            drift = [] if jobs.is_terminal(job) else jobs.detect_drift(job["binding"], jobs.make_binding(txn.snapshot(), job["policy"].get("id")))
            report = self._full_check(txn, job)
            return {"job_id": job["job_id"], "status": job["status"], "drift": drift, **report,
                    "pending": [u for u, r in report["units"].items() if not r["complete"]],
                    "targets": job["policy"]["targets"]}

    def node_titles(self, sid) -> dict:
        """{node id: title} from the live roadmap plan (read-only; empty when there is no plan)."""
        plan = self.ck.studio_snapshot(sid).get("plan") or {}
        return {n["id"]: n.get("title") for n in plan.get("nodes") or [] if isinstance(n, dict) and "id" in n}

    def staged_view(self, sid, node, job_id=None) -> dict:
        """Read-only: the validated staged payloads of one node, for the owner's preview. Nothing is persisted. The
        payloads still carry the vault (reference solution, hidden checks): callers must reduce them to a public view."""
        with self._locked(sid, create=False) as (d, led, root):
            job = self._load(root, job_id)
            uids = [u for u in (f"lesson:{node}", f"items:{node}") if u in job["units"]]
            if not isinstance(node, str) or not uids:
                raise StudioError("studio_unknown_node", f"no unit of node {str(node)[:60]} in this job")
            payloads = {}
            for uid in uids:
                unit = job["units"][uid]
                if unit["status"] == "staged" and unit.get("staged_digest"):
                    payloads[unit["kind"]] = staging.read_staged(root, job["job_id"], uid, unit["staged_digest"])
            plan = _Txn(self, sid, d, led, root, job).snapshot().get("plan") or {}
            title = next((n.get("title") for n in plan.get("nodes") or [] if n.get("id") == node), None)
            return {"job_id": job["job_id"], "status": job["status"], "build_digest": job.get("build_digest"),
                    "node": node, "title": title, "payloads": payloads,
                    "units": {uid: {"status": job["units"][uid]["status"], "attempts": job["units"][uid]["attempts"]}
                              for uid in uids}}

    def events(self, sid, job_id=None) -> dict:
        with self._locked(sid, create=False) as (d, led, root):
            job = self._load(root, job_id)
            log = EventLog(root)
            return {"job_id": job["job_id"], "events": [e for e in log.read() if e.get("job_id") == job["job_id"]],
                    "verify": log.verify(led)}

    # ------------------------------------------------------------ control plane
    def pause(self, sid, job_id=None, *, idempotency_key=None) -> dict:
        def fn(txn):
            jobs.transition(txn.job, "paused", at=self._now(), reason="owner_pause", actor="owner")
            txn.event("job_paused", anchor=True)
            return self._summary(txn.job, txn.root)
        return self._operate(sid, job_id, "pause", {}, idempotency_key, fn)

    def resume(self, sid, job_id=None, *, idempotency_key=None) -> dict:
        def fn(txn):
            job = txn.job
            if job["status"] != "paused":
                raise StudioError("studio_transition_refused", f"{job['status']} is not paused")
            paused_at = next((h["at"] for h in reversed(job["status_history"]) if h["to"] == "paused"), None)
            jobs.transition(job, job["paused_from"], at=self._now(), reason="owner_resume", actor="owner")
            if paused_at:
                # time spent paused is not the author's: push every live lease out by the paused duration
                held = max(timedelta(0), self._dt() - jobs.parse_time(paused_at))
                for entry in job["orders"].values():
                    if entry["status"] == "issued":
                        entry["lease_until"] = (jobs.parse_time(entry["lease_until"]) + held).astimezone(UTC).isoformat()
            txn.event("job_resumed", anchor=True)
            return self._summary(job, txn.root)
        return self._operate(sid, job_id, "resume", {}, idempotency_key, fn)

    def cancel(self, sid, job_id=None, *, idempotency_key=None) -> dict:
        def fn(txn):
            job = txn.job
            jobs.transition(job, "cancelled", at=self._now(), reason="owner_cancel", actor="owner")
            for entry in job["orders"].values():
                if entry["status"] == "issued":
                    entry["status"], entry["closed_at"] = "cancelled", self._now()
            txn.event("job_cancelled", anchor=True)
            return self._summary(job, txn.root)
        return self._operate(sid, job_id, "cancel", {}, idempotency_key, fn)

    def rebase(self, sid, job_id=None, *, idempotency_key=None) -> dict:
        """New job against the current course, reusing units whose inputs are unchanged (re-validated)."""
        with self._locked(sid, create=False) as (d, led, root):
            old = self._load(root, job_id)
            registry = self._registry(root)
            body = {"job_id": old["job_id"]}
            replay = jobs.idem_replay(registry, idempotency_key, "rebase", body)
            if replay is not None:
                return replay
            if old.get("superseded_by"):
                return {"result": "already_rebased", "job_id": old["superseded_by"]}
            txn = _Txn(self, sid, d, led, root, old)
            if old["status"] != "superseded" and not self._refresh(txn):
                raise StudioError("studio_rebase_not_applicable", "the job has not drifted; nothing to rebase")
            live = jobs.active_job(root)
            if live is not None and live.get("rebased_from") == old["job_id"]:
                self._mark_superseded_by(root, old, live["job_id"])      # a retry after a crash repairs the link
                return {"result": "already_rebased", "job_id": live["job_id"]}
            if live is not None:
                raise StudioError("studio_job_active", "another live job exists for this session")
            old = self._load(root, old["job_id"])
            snap = self.ck.studio_snapshot(sid)
            job = jobs.new_job(sid, self._now(), old["policy"], jobs.make_binding(snap, old["policy"].get("id")))
            job["rebased_from"] = old["job_id"]
            new = _Txn(self, sid, d, led, root, job)
            new._snap = snap
            new.history_len = 0
            new.event("job_rebased", {"job_id": job["job_id"], "from_job": old["job_id"],
                                      "roadmap_version": job["binding"]["roadmap_version"]}, anchor=True)
            if self._gate(new):
                self._ensure_planned(new)
            self._commit(new, save=True)
            self._mark_superseded_by(root, old, job["job_id"])
            result = {"result": "rebased", "from_job": old["job_id"],
                      "reused_units": sorted(u for u, x in job["units"].items() if x["reused_from"]),
                      **self._summary(job, root)}
            if idempotency_key is not None:
                jobs.idem_remember(registry, idempotency_key, "rebase", body, result, self._now())
                self._save_registry(root, registry)
            return result

    def _mark_superseded_by(self, root, old: dict, new_id_: str) -> None:
        """Record the successor on the archived old job (written in place; the document is kept)."""
        archived = jobs.archived_path(root, old["job_id"])
        if archived is not None:
            old["superseded_by"] = new_id_
            write_atomic_json(archived, old, root=root)


def _stamp(result, job: dict) -> None:
    """A summary computed before the save carries the pre-save revision; refresh it."""
    if isinstance(result, dict) and result.get("job_id") == job["job_id"] and "revision" in result:
        result["revision"], result["updated_at"] = job["revision"], job["updated_at"]


def _body_digest(payload):
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        return hashlib.sha256(bytes(payload)).hexdigest()
    return digest_of(payload)

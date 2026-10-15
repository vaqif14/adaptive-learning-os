"""Stage C of the Course Studio: the rules gate, remediation routing, handover, owner acceptance/rejection, promotion.

A mixin of ``StudioKernel`` (every method runs inside a ``_operate`` transaction: session lock, drift check, status
machine, atomic job write, hash-chained events anchored in the ledger). The studio still never writes
``course-state.json`` and never appends evidence: promotion goes through ``CourseKernel.promote_build`` (normal
admission), and acceptance/receipt anchors are ledger ``event`` records.
"""
from __future__ import annotations

from . import rules_check as rc
from . import studio_handover as handover_mod
from . import studio_jobs as jobs
from . import studio_receipt as receipts
from . import studio_remediate as remediate
from . import studio_staging as staging
from .course_state import CourseStateError
from .rules_probes.base import HARD_RULES, Ctx
from .rules_probes.r10 import SECRET_PATTERNS
from .studio_store import StudioError, notify

OWNER_CHANNEL = "owner_cli"
MAX_STATEMENT = 500
MAX_NOTE = 1000
OWNER_TEXT_PATTERNS = [(name, p) for name, p in SECRET_PATTERNS if name != "email address"]


def sensitive_text(text: str):
    """Name of the first secret-like or home-path pattern in an owner-typed text, else None. The ledger is append-only
    (nothing is deleted), so a pasted key would stay for good: such a statement or note is refused instead of stored."""
    return next((name for name, pattern in OWNER_TEXT_PATTERNS if pattern.search(text)), None)


class ReviewMixin:
    gate = True

    # ------------------------------------------------------------------ the gate
    def _node_ids(self, txn) -> list[str]:
        plan = txn.snapshot().get("plan") or {}
        return [n["id"] for n in plan.get("nodes") or []]

    def _receipt_run(self, txn, scope: str) -> dict:
        """Recompute every probe (nothing is read from a previous receipt), sign, store and anchor the receipt."""
        receipt = rc.run_rules(self.ck.sk.workspace, txn.sid, job=txn.job, scope=scope, clock=self.ck.clock,
                               progress=getattr(self, "gate_progress", None))
        info = rc.commit_receipt(self.ck.sk.workspace, txn.sid, receipt)
        job = txn.job
        job["last_receipt"] = {"receipt_id": receipt["receipt_id"], "scope": scope, "status": receipt["status"],
                               "exit_code": receipt["exit_code"], "build_digest": receipt["build_digest"],
                               "failing_rules": receipt["failing_rules"], "at": receipt["created_at"]}
        txn.event("rules_checked", {"receipt_id": receipt["receipt_id"], "scope": scope, "status": receipt["status"],
                                    "failing_rules": receipt["failing_rules"], "build_digest": receipt["build_digest"],
                                    "anchor_seq": info["anchor_seq"]}, anchor=True)
        return receipt

    def _rules_gate_cleared(self, txn) -> bool:
        """Quick recheck for a job parked by the rules gate: True when nothing fails any more (nothing is stored)."""
        quick = rc.run_rules(self.ck.sk.workspace, txn.sid, job=txn.job, scope="quick", clock=self.ck.clock)
        return quick["status"] == "pass"

    def _after_checking(self, txn) -> None:
        """A build just reached ``checking``: run the full gate; pass -> handover + ready_for_review, fail -> route."""
        if not self.gate or txn.job["status"] != "checking" or txn.snapshot()["exam_locked"]:
            return          # during an exam the scratch journey is refused: the build waits in `checking`, never blocks
        self._route_gate(txn, self._receipt_run(txn, "full"))

    def _route_gate(self, txn, receipt: dict) -> None:
        job = txn.job
        if receipt["status"] == "pass" and receipt["scope"] == "full" and not receipt["partial"]:
            self._ready_for_review(txn, receipt)
            return
        verdict = remediate.classify(receipt, self._node_ids(txn))
        if job.get("build_digest"):
            self._write_handover(txn, receipt)            # a failing build still gets its (BLOCKED) dossier
        self._park(txn, verdict, receipt)

    def _ready_for_review(self, txn, receipt: dict) -> None:
        job = txn.job
        written = self._write_handover(txn, receipt)
        quick = self._receipt_run(txn, "quick")           # the handover itself is linted (R9) and re-checked
        if quick["status"] != "pass":
            verdict = remediate.classify(quick, self._node_ids(txn))
            self._park(txn, {**verdict, "kind": remediate.SYSTEM, "next_step": "the handover failed its own checks: "
                                                                                 + remediate.NEXT_STEP[remediate.SYSTEM]}, quick)
            return
        jobs.transition(job, "ready_for_review", at=self._now(), reason="rules_pass_handover_written")
        txn.event("handover_ready", {"build_digest": job["build_digest"], "receipt_id": receipt["receipt_id"],
                                     "handover": written["dir"]}, anchor=True)
        notify(txn.root, "handover_ready", job["job_id"],
               f"handover ready for review: build {job['build_digest'][:12]}, rules receipt {receipt['receipt_id']}", self._now())

    def _park(self, txn, verdict: dict, receipt: dict) -> None:
        """Route a failing gate: content -> remediation (bounded), system -> blocked, learner -> needs_learner."""
        job, kind, now = txn.job, verdict["kind"], self._now()
        allowed = jobs.TRANSITIONS[job["status"]]
        summary = {"kind": kind, "receipt_id": receipt["receipt_id"], "status": receipt["status"],
                   "findings": verdict["findings"][:20], "next_step": verdict["next_step"], "at": now}
        if kind == remediate.CONTENT and "remediation" in allowed:
            budget = job["policy"]["budgets"]["max_remediation_rounds"]
            reopened, exhausted = [], None
            for uid in sorted(verdict["fix_orders"]):
                unit = job["units"][uid]
                unit["rules_rounds"] = unit.get("rules_rounds", 0) + 1
                if unit["rules_rounds"] > budget:
                    exhausted = unit
                    break
            if exhausted is None:
                for uid, orders_ in sorted(verdict["fix_orders"].items()):
                    unit = job["units"][uid]
                    unit.update(status="invalid", staged_digest=None, attempts=0, reason="rules_finding",
                                last_errors=[o[:500] for o in orders_[:20]])
                    reopened.append(uid)
                job["build_digest"] = None
                jobs.transition(job, "remediation", at=now, reason="rules_content_defect")
                txn.event("units_reopened", {"units": reopened, "receipt_id": receipt["receipt_id"],
                                             "round": max(job["units"][u]["rules_rounds"] for u in reopened)}, anchor=True)
                notify(txn.root, "remediation", job["job_id"], "rules findings sent back to the author: " + ", ".join(reopened), now)
                return
            exhausted.update(reason="remediation_rounds_exhausted", attempts=exhausted.get("attempts", 0),
                             last_errors=[o[:500] for o in verdict["fix_orders"].get(exhausted["unit_id"], [])])
            jobs.block_for_defect(job, exhausted, at=now)
            job["blocked"].update(receipt_id=receipt["receipt_id"], findings=summary["findings"])
            txn.event("job_blocked", {"unit_id": exhausted["unit_id"], "reason": "remediation_rounds_exhausted",
                                      "receipt_id": receipt["receipt_id"]}, anchor=True)
            notify(txn.root, "blocked", job["job_id"], "content defect: rules findings not fixed within the remediation budget", now)
            return
        if kind == remediate.LEARNER and "needs_learner" in allowed:
            job["needs_learner"] = {"blockers": [f["probe"] for f in verdict["findings"]], "directive": verdict["next_step"],
                                    "phase": "rules_gate", "since": now, "findings": summary["findings"]}
            jobs.transition(job, "needs_learner", at=now, reason="rules_learner_gated")
            txn.event("needs_learner", {"receipt_id": receipt["receipt_id"], "findings": summary["findings"][:5]}, anchor=True)
            notify(txn.root, "needs_learner", job["job_id"], verdict["next_step"], now)
            return
        job["blocked"] = {**summary, "kind": kind if kind != remediate.LEARNER else remediate.SYSTEM}
        if job["status"] != "blocked":
            jobs.transition(job, "blocked", at=now, reason=f"rules_{kind}")
        txn.event("job_blocked", {"kind": job["blocked"]["kind"], "receipt_id": receipt["receipt_id"]}, anchor=True)
        notify(txn.root, "blocked", job["job_id"], f"{job['blocked']['kind']}: {verdict['next_step']}", now)

    # ------------------------------------------------------------------ handover
    def _write_handover(self, txn, receipt: dict) -> dict:
        job = txn.job
        subjects = Ctx(self.ck.sk.workspace, txn.sid, job=job).subjects()
        titles = {n["id"]: n.get("title") for n in (txn.snapshot().get("plan") or {}).get("nodes") or []}
        doc = handover_mod.build_handover(job, receipt, subjects, titles, now=self._now())
        written = handover_mod.write_handover(txn.root, doc)
        job["handover"] = {"receipt_id": receipt["receipt_id"], "build_digest": job["build_digest"], "blocked": doc["blocked"],
                           "dir": written["dir"], "at": doc["created_at"]}
        txn.event("handover_written", {"receipt_id": receipt["receipt_id"], "blocked": doc["blocked"],
                                       "build_digest": job["build_digest"]})
        return written

    def _latest_full(self, txn):
        """(receipt, fresh, reason) of the newest full receipt bound to the job's current build digest."""
        job = txn.job
        receipt = receipts.latest_receipt(txn.root, job["job_id"], scope="full", build_digest=job.get("build_digest"))
        if receipt is None:
            return None, False, "no full rules receipt exists for this build digest"
        if not receipts.verify_receipt(receipt):
            return receipt, False, "the receipt signature does not verify"
        fresh, reason = receipts.freshness(txn.led, receipt)
        if fresh:        # the receipt must describe the gate as it stands now (code, probes, coverage, identity)
            stale = receipts.enforcement_problems(receipt, job_id=job["job_id"], session_id=txn.sid,
                                                  roadmap_version=job["binding"]["roadmap_version"])
            if stale:
                return receipt, False, "enforcement_changed:" + ",".join(stale[:4])
        return receipt, fresh, reason

    def handover(self, sid, job_id=None, *, idempotency_key=None, refresh: bool = True) -> dict:
        """The owner's dossier for the current build. A missing or stale full receipt is recomputed first, unless
        ``refresh`` is False (the owner HTTP surface: it only reads, the gate never runs inside a threaded server)."""
        def fn(txn):
            job = txn.job
            if not job.get("build_digest"):
                raise StudioError("studio_no_build", "the job has no complete build yet; nothing to hand over")
            receipt, fresh, reason = self._latest_full(txn)
            if refresh and job["status"] == "ready_for_review" and (receipt is None or not fresh) \
                    and txn.snapshot()["exam_locked"]:
                reason = "exam_active: the rules gate does not run while the learner has an exam open"
            elif refresh and job["status"] == "ready_for_review" and (receipt is None or not fresh):
                receipt = self._receipt_run(txn, "full")
                if receipt["status"] == "pass":
                    self._write_handover(txn, receipt)
                else:
                    self._route_gate(txn, receipt)
                fresh, reason = True, None
            loaded = handover_mod.load_latest(txn.root, job["job_id"], job["build_digest"]) if job.get("build_digest") else None
            return {"job_id": job["job_id"], "status": job["status"], "build_digest": job.get("build_digest"),
                    "receipt_id": (receipt or {}).get("receipt_id"), "receipt_fresh": fresh, "stale_reason": reason,
                    "blocked": (loaded or {}).get("json", {}).get("blocked"),
                    "handover": handover_mod.with_probe_summaries((loaded or {}).get("json")), "markdown": (loaded or {}).get("md"),
                    "files": job.get("handover")}
        return self._operate(sid, job_id, "handover", {}, idempotency_key, fn, allow_superseded=True)

    # ------------------------------------------------------------ owner decisions
    def accept(self, sid, job_id, digest, statement, *, idempotency_key=None, interactive=None,
               channel: str = OWNER_CHANNEL, promote: bool = True) -> dict:
        """Owner acceptance bound to the exact build digest; refused when the receipt is stale or failing.

        The acceptance is a CLI statement: nothing cryptographically ties it to a human (an agent in the same account can
        issue the same command). ``interactive`` records whether the CLI had a terminal on stdin/stdout, so the audit
        trail shows how it was given; the handover lists the gap under 'not verified'. ``channel`` is ``owner_cli`` or
        ``owner_ui`` (the owner HTTP surface). ``promote=False`` records the acceptance and stops: the HTTP server is
        threaded and the post-promotion gate swaps process-wide state, so promotion is left to ``alearn studio promote``
        or the learner's next ``course turn`` (both run in their own process)."""
        if channel not in receipts.OWNER_CHANNELS:
            raise StudioError("studio_channel_invalid", f"channel must be one of {list(receipts.OWNER_CHANNELS)}")

        def fn(txn):
            job = txn.job
            text = (statement or "").strip() if isinstance(statement, str) else ""
            if not text or len(text) > MAX_STATEMENT:
                raise StudioError("studio_statement_required", f"an acceptance statement of 1-{MAX_STATEMENT} characters is required")
            if sensitive_text(text):
                raise StudioError("studio_statement_sensitive", f"the statement looks like it contains a {sensitive_text(text)}; "
                                                                 "the ledger is append-only, so it is not stored: rephrase it")
            if job["status"] != "ready_for_review":
                raise StudioError("studio_not_reviewable", f"the job is {job['status']}, not ready_for_review")
            if digest != job.get("build_digest"):
                raise StudioError("studio_digest_mismatch", "the digest to accept is not the job's current build digest")
            receipt, fresh, reason = self._latest_full(txn)
            next_step = f"alearn rules-check --session {sid} --job {job['job_id']} --scope full   (or: alearn studio handover)"
            if receipt is None:
                raise StudioError("studio_receipt_missing", f"{reason}; run: {next_step}")
            if receipt.get("status") != "pass" or receipt.get("exit_code") != 0:
                raise StudioError("studio_receipt_failing", "the latest full rules receipt does not pass; a failing build cannot be accepted")
            if not fresh:
                raise StudioError("studio_receipt_stale", f"the rules receipt is stale ({reason}); run: {next_step}")
            if not receipts.anchored(txn.led, receipt):
                raise StudioError("studio_receipt_unanchored", "the receipt is not anchored in the ledger")
            record = txn.led.append("event", {"event_type": receipts.ACCEPT_EVENT, "job_id": job["job_id"],
                                              "build_digest": digest, "receipt_id": receipt["receipt_id"],
                                              "statement": text, "channel": channel,
                                              "interactive": interactive if isinstance(interactive, bool) else None},
                                  {"source": receipts.OWNER_SOURCE})
            job["acceptance"] = {"at": self._now(), "build_digest": digest, "receipt_id": receipt["receipt_id"],
                                 "statement": text, "channel": channel, "ledger_seq": record["seq"],
                                 "interactive": interactive if isinstance(interactive, bool) else None}
            jobs.transition(job, "accepted", at=self._now(), reason="owner_accepted", actor="owner")
            txn.event("owner_accepted", {"build_digest": digest, "receipt_id": receipt["receipt_id"],
                                         "ledger_seq": record["seq"]}, anchor=True)
            if promote:
                promotion = self._try_promote(txn, refresh=True)
            elif txn.snapshot()["approved_roadmap_version"] != job["binding"]["roadmap_version"]:
                promotion = self._await_approval(txn, job["binding"]["roadmap_version"], transition=False)
            else:
                promotion = {"promoted": False, "state": "deferred",
                             "next_step": f"alearn studio promote --session {sid} --job {job['job_id']} "
                                          "(or the learner's next `course turn`)"}
            return {**self._summary(job, txn.root), "accepted": True, "promotion": promotion}
        return self._operate(sid, job_id, "accept", {"digest": digest, "statement": statement}, idempotency_key, fn)

    def reject(self, sid, job_id, node, note, *, idempotency_key=None, channel: str = OWNER_CHANNEL) -> dict:
        """Re-open one node's units with the owner's note as a fix order. Also withdraws an acceptance that is waiting for
        the learner's roadmap approval (the later rejection event revokes it: ``find_acceptance``)."""
        if channel not in receipts.OWNER_CHANNELS:
            raise StudioError("studio_channel_invalid", f"channel must be one of {list(receipts.OWNER_CHANNELS)}")

        def fn(txn):
            job = txn.job
            text = (note or "").strip() if isinstance(note, str) else ""
            if not text or len(text) > MAX_NOTE:
                raise StudioError("studio_note_required", f"a rejection note of 1-{MAX_NOTE} characters is required")
            if sensitive_text(text):
                raise StudioError("studio_statement_sensitive", f"the note looks like it contains a {sensitive_text(text)}; "
                                                                 "the ledger is append-only, so it is not stored: rephrase it")
            if job["status"] not in {"ready_for_review", "accepted", "awaiting_learner_approval"}:
                raise StudioError("studio_not_reviewable", f"the job is {job['status']}, not ready_for_review or accepted")
            uids = [u for u in (f"lesson:{node}", f"items:{node}") if u in job["units"]]
            if not uids:
                raise StudioError("studio_unknown_node", f"no unit of node {str(node)[:60]} in this job")
            record = txn.led.append("event", {"event_type": receipts.REJECT_EVENT, "job_id": job["job_id"], "node": node,
                                              "build_digest": job.get("build_digest"), "note": text,
                                              "channel": channel}, {"source": receipts.OWNER_SOURCE})
            for uid in uids:
                job["units"][uid].update(status="invalid", staged_digest=None, attempts=0, reason="owner_rejected",
                                         last_errors=[f"owner_note: {text}"[:500]])
            job.setdefault("rejections", []).append({"at": self._now(), "node": node, "note": text,
                                                     "build_digest": job.get("build_digest"), "ledger_seq": record["seq"]})
            job["build_digest"] = None
            jobs.transition(job, "remediation", at=self._now(), reason=f"owner_rejected:{node}", actor="owner")
            txn.event("owner_rejected", {"node": node, "units": uids, "ledger_seq": record["seq"]}, anchor=True)
            return {**self._summary(job, txn.root), "rejected_node": node, "reopened": uids}
        return self._operate(sid, job_id, "reject", {"node": node, "note": note}, idempotency_key, fn)

    # ------------------------------------------------------------------ promotion
    def promote(self, sid, job_id=None, *, refresh=True, idempotency_key=None) -> dict:
        """Promote an accepted build into the live course (all conditions re-checked at promotion time)."""
        def fn(txn):
            result = self._try_promote(txn, refresh=refresh, strict=True)
            return {**self._summary(txn.job, txn.root), "promotion": result}
        return self._operate(sid, job_id, "promote", {"refresh": bool(refresh)}, idempotency_key, fn)

    def _try_promote(self, txn, *, refresh: bool, strict: bool = False) -> dict:
        """Attempt promotion. Refusals are reported (never raised) unless ``strict``; acceptance is never undone."""
        try:
            return self._promote_txn(txn, refresh=refresh)
        except (StudioError, CourseStateError) as e:
            if strict:
                raise
            return {"promoted": False, "refused": getattr(e, "code", type(e).__name__), "detail": str(getattr(e, "detail", e))[:300]}

    def _await_approval(self, txn, version, *, transition: bool = True) -> dict:
        job, now = txn.job, self._now()
        if transition and job["status"] == "accepted":
            jobs.transition(job, "awaiting_learner_approval", at=now, reason="roadmap_not_approved_yet")
            txn.event("awaiting_learner_approval", {"roadmap_version": version}, anchor=True)
            notify(txn.root, "awaiting_learner_approval", job["job_id"],
                   f"accepted build waits for the learner to approve roadmap version {version}", now)
        return {"promoted": False, "state": "awaiting_learner_approval", "roadmap_version": version,
                "next_step": f"the learner approves exactly roadmap version {version}; promotion follows on the next "
                             f"`course turn` or `alearn studio promote --session {txn.sid}`"}

    def _promote_txn(self, txn, *, refresh: bool) -> dict:
        job, now = txn.job, self._now()
        if job["status"] not in {"accepted", "awaiting_learner_approval"}:
            raise StudioError("studio_not_promotable", f"the job is {job['status']}; only an accepted build is promoted")
        version = job["binding"]["roadmap_version"]
        if txn.snapshot()["approved_roadmap_version"] != version:
            return self._await_approval(txn, version)
        digest = job.get("build_digest")
        if not digest or jobs.compute_build_digest(job) != digest:
            raise StudioError("studio_digest_mismatch", "the job's build digest does not match its staged units")
        accept = receipts.find_acceptance(txn.led, job["job_id"], digest)
        if accept is None or not receipts.acceptance_valid(accept):
            raise StudioError("studio_requires_owner_acceptance", "no owner acceptance is bound to this build digest")
        receipt, fresh, reason = self._latest_full(txn)
        if (receipt is None or not fresh) and refresh and txn.snapshot()["exam_locked"]:
            # The gate's scratch journey is refused during an exam and would read as a system defect: defer, touch nothing.
            return {"promoted": False, "state": "deferred_exam_active",
                    "next_step": "promotion is retried after the learner's exam is finished (`alearn studio promote`)"}
        if (receipt is None or not fresh) and refresh:
            receipt = self._receipt_run(txn, "full")      # recomputed from scratch at the current ledger head
            fresh, reason = True, None
            if receipt["status"] != "pass":
                self._route_gate(txn, receipt)
                return {"promoted": False, "state": "rules_failed", "receipt_id": receipt["receipt_id"],
                        "failing_rules": receipt["failing_rules"], "job_status": job["status"]}
        if receipt is None or not fresh:
            raise StudioError("studio_receipt_stale", f"promotion needs a fresh passing full receipt ({reason}); "
                                                      f"run: alearn rules-check --session {txn.sid} --job {job['job_id']} --scope full")
        if receipt.get("status") != "pass":
            raise StudioError("studio_receipt_failing", "the latest full rules receipt does not pass")
        payloads = {uid: staging.read_staged(txn.root, job["job_id"], uid, job["units"][uid]["staged_digest"])
                    for uid in job["unit_order"]}
        bundle = receipts.bundle_from_job(job, payloads, receipt=receipt)
        outcome = self.ck.promote_build(txn.sid, bundle)
        jobs.transition(job, "promoted", at=now, reason="build_promoted", actor="studio")
        job["promotion"] = {"at": now, "build_digest": digest, "receipt_id": receipt["receipt_id"],
                            "nodes": outcome.get("nodes"), "promotion_id": outcome.get("promotion_id"),
                            "evidence_records_added": outcome.get("evidence_records_added", 0)}
        txn.event("build_promoted", {"build_digest": digest, "nodes": outcome.get("nodes"),
                                     "promotion_id": outcome.get("promotion_id")}, anchor=True)
        notify(txn.root, "promoted", job["job_id"], f"build {digest[:12]} promoted into the live course", now)
        try:
            post = self._receipt_run(txn, "full")         # the gate runs again on the promoted course
            self.ck.record_promotion_check(txn.sid, digest, {"receipt_id": post["receipt_id"], "status": post["status"],
                                                             "scope": "full", "failing_rules": post["failing_rules"]})
        except (OSError, ValueError, StudioError, CourseStateError) as e:
            # The course IS promoted and that fact is committed: a failing re-check must not lose the job's state.
            job["promotion"]["post_receipt"] = {"receipt_id": None, "status": "error", "failing_rules": [],
                                                "error": f"{type(e).__name__}: {str(e)[:200]}"}
            txn.event("post_promotion_check_error", {"error": type(e).__name__}, anchor=True)
            notify(txn.root, "post_promotion_rules_failed", job["job_id"], "the post-promotion rules check could not run", self._now())
            return {"promoted": True, "state": "promoted", "nodes": outcome.get("nodes"), "build_digest": digest,
                    "evidence_records_added": outcome.get("evidence_records_added", 0),
                    "post_check": job["promotion"]["post_receipt"], "frontend": self.ck.frontend_status(txn.sid)}
        job["promotion"]["post_receipt"] = {"receipt_id": post["receipt_id"], "status": post["status"],
                                            "failing_rules": post["failing_rules"]}
        if post["status"] != "pass":
            txn.event("post_promotion_rules_failed", {"receipt_id": post["receipt_id"], "failing_rules": post["failing_rules"]},
                      anchor=True)
            notify(txn.root, "post_promotion_rules_failed", job["job_id"],
                   "the rules gate fails on the promoted course: " + ", ".join(post["failing_rules"]), self._now())
        return {"promoted": True, "state": "promoted", "nodes": outcome.get("nodes"), "build_digest": digest,
                "evidence_records_added": outcome.get("evidence_records_added", 0),
                "post_check": job["promotion"]["post_receipt"], "frontend": self.ck.frontend_status(txn.sid)}

    # --------------------------------------------------------------- course turn
    def try_auto_promote(self, sid) -> dict | None:
        """On a course turn: promote an accepted build once the learner approved its exact roadmap version."""
        with self._locked(sid, create=False) as (d, led, root):
            if not root.is_dir():
                return None
            waiting = [j for j in jobs.list_jobs(root) if j["status"] in {"accepted", "awaiting_learner_approval"}]
            if not waiting:
                return None
            snap = self.ck.studio_snapshot(sid)
            job = waiting[-1]
            if snap["approved_roadmap_version"] != job["binding"]["roadmap_version"] or snap["exam_locked"]:
                return None
        try:
            result = self.promote(sid, job["job_id"])
        except (StudioError, CourseStateError) as e:
            return {"attempted": True, "promoted": False, "job_id": job["job_id"], "error": getattr(e, "code", type(e).__name__),
                    "detail": str(getattr(e, "detail", e))[:300]}
        return {"attempted": True, "job_id": job["job_id"], **result["promotion"]}

    def _turn_check(self, sid, led, root, job):
        """(receipt, source, fresh, reason) for one job: the stored receipt while fresh, else a quick recomputation."""
        stored = receipts.latest_receipt(root, job["job_id"])
        fresh, reason = (receipts.freshness(led, stored) if stored is not None and receipts.verify_receipt(stored)
                         else (False, "no stored receipt" if stored is None else "signature does not verify"))
        if stored is not None and fresh:
            return stored, "stored", fresh, reason
        return rc.run_rules(self.ck.sk.workspace, sid, job=job, scope="quick", clock=self.ck.clock), "quick_recomputed", fresh, reason

    def turn_receipt(self, sid) -> dict | None:
        """The rules receipt as the course turn shows it. Teaching is blocked by a failing hard rule of the MOST RECENT
        PROMOTED build whatever the newest job is: a later job in planning must not hide a failure of the live course."""
        with self._locked(sid, create=False) as (d, led, root):
            if not root.is_dir():
                return None
            every = [j for j in jobs.list_jobs(root) if j["status"] not in {"superseded", "cancelled"}]
            if not every:
                return None
            job = every[-1]
            promoted = next((j for j in reversed(every) if j["status"] == "promoted"), None)
            receipt, source, fresh, reason = self._turn_check(sid, led, root, job)
            guard = receipt
            if promoted is not None and promoted["job_id"] != job["job_id"]:
                guard = self._turn_check(sid, led, root, promoted)[0]
            failing = guard["failing_rules"]
            hard = [r for r in failing if r in HARD_RULES]
            blocks = promoted is not None and (bool(hard) or guard["status"] in {"probe_error", "suite_broken"})
            out = {"status": receipt["status"], "scope": receipt["scope"], "source": source, "stale_receipt": not fresh,
                   "stale_reason": reason, "receipt_id": receipt["receipt_id"], "job_id": job["job_id"],
                   "job_status": job["status"], "build_digest": receipt["build_digest"], "failing_rules": receipt["failing_rules"],
                   "errored_rules": receipt["errored_rules"], "claim": receipt["claim"]["text_en"], "blocks_teaching": blocks}
            if blocks:
                out["live_job_id"] = promoted["job_id"]
                out["live_failing_rules"] = failing
                out["agent_directive"] = ("RULES GATE FAILED on the promoted build (" + ", ".join(failing or guard["errored_rules"])
                                          + "): show this to the owner before teaching on it; run `alearn rules-check "
                                          f"--session {sid} --job {promoted['job_id']} --scope full` and follow its findings.")
            return out

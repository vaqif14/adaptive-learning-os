from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .utils import new_id, now_iso, write_json, read_json


def _hours_between(earlier_iso: str, later_iso: str) -> float | None:
    try:
        a = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
        b = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return (b - a).total_seconds() / 3600.0
from .contracts import LearningContract
from .ledger import EventLedger
from .projection import EvidenceProjector
from .adapters import AdapterRegistry
from .anchors import plan_anchor
from .learning_workspace import normalize_plan, create_workspace
from .safety import safe_session_dir, UnsafePathError
from .session_lock import locked_session
from .local_state import commit_state, recover_state, workspace_metadata, sync_tree


class SessionKernel:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = workspace / ".learning" / "sessions"

    def start(self, topic: str, contract: LearningContract, mode: str | None = None, adapter_dirs: list[Path] | None = None, learner_id: str | None = None, roadmap: dict | None = None) -> dict:
        plan = normalize_plan(topic, contract.goal, roadmap) if contract.goal_mode == "learn" and mode != "quick_answer" else None
        sid = new_id("sess")
        d = self.sessions_dir / sid
        d.mkdir(parents=True, exist_ok=False)
        try:
            return self._write_new_session(d, sid, topic, contract, mode, adapter_dirs, learner_id, plan)
        except BaseException:
            # Roll back a half-created session so no 0-byte session.json survives.
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            raise

    def _write_new_session(self, d, sid, topic, contract, mode, adapter_dirs, learner_id=None, plan=None):
        adapter = AdapterRegistry(adapter_dirs).match(topic)
        adapter_dict = asdict(adapter)
        anchor = plan_anchor(topic, mode, contract.to_dict(), adapter_dict)
        session = {
            "session_id": sid,
            "created_at": now_iso(),
            "topic": topic,
            "status": "active",
            "learner_id": learner_id,
            "adapter_id": adapter.adapter_id,
            "interaction_mode": mode,
            "anchor_probe": anchor.to_dict(),
        }
        if plan is not None:
            session["learning_workspace"] = create_workspace(d / "workspace", plan, sid)
        write_json(d / "session.json", session)
        write_json(d / "learning-contract.json", contract.to_dict())
        write_json(d / "source-policy.json", contract.source_policy.to_dict())
        write_json(d / "domain-context.json", adapter_dict)
        write_json(d / "anchor-probe.json", anchor.to_dict())
        ledger = EventLedger(d / "ledger.jsonl", sid)
        ledger.append("event", {"event_type": "session_started", "topic": topic, "mode": mode}, {"source": "session_kernel"})
        EvidenceProjector(ledger).write(d / "learner-evidence.json")
        return session

    @locked_session
    def update_contract(self, session_id: str, **updates) -> dict:
        self.ledger(session_id).require_integrity()
        d = self.dir(session_id)
        current = read_json(d / "learning-contract.json")
        allowed = {
            "goal", "application_context", "constraints", "desired_independence", "time_horizon",
            "deadline", "resources", "source_policy", "stated_background", "target_evidence_scope",
            "goal_mode", "fast_track_requested", "protected_cognition", "delegable_work",
            "accessibility_needs", "authentic_environment_required", "ai_use_policy",
        }
        for k, v in updates.items():
            if k in allowed and v is not None:
                current[k] = v
        contract = LearningContract.from_dict(current)
        session = read_json(d / "session.json")
        domain = read_json(d / "domain-context.json")
        anchor = plan_anchor(session["topic"], session.get("interaction_mode"), contract.to_dict(), domain)
        session["anchor_probe"] = anchor.to_dict()
        commit_state(d, self.ledger(session_id),
            {"event_type": "learning_contract_updated", "fields": sorted(k for k, v in updates.items() if v is not None)},
            {"learning-contract.json": contract.to_dict(), "source-policy.json": contract.source_policy.to_dict(),
             "anchor-probe.json": anchor.to_dict(), "session.json": session})
        return session

    def dir(self, session_id: str) -> Path:
        try:
            d = safe_session_dir(self.sessions_dir, session_id)
        except UnsafePathError:
            # Do not echo the raw id or a full path back to the caller.
            raise FileNotFoundError("unknown session")
        if not d.exists():
            raise FileNotFoundError("unknown session")
        return d

    def _recover_state(self, session_id: str):
        recover_state(self.dir(session_id), self.ledger(session_id))

    def ledger(self, session_id: str) -> EventLedger:
        path = self.dir(session_id) / "ledger.jsonl"
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("session journal missing or empty; restore a verified backup")
        return EventLedger(path, session_id)

    @locked_session
    def rebuild_projection(self, session_id: str) -> dict:
        d = self.dir(session_id)
        return EvidenceProjector(self.ledger(session_id)).write(d / "learner-evidence.json")

    @locked_session
    def verify_ledger(self, session_id: str, *, cadence_hours: float | None = None, force: bool = False) -> dict:
        """Verify the ledger hash chain and persist a dated integrity receipt.

        Operators run this on a cadence (cron/CI) as the periodic chain check the
        design calls for. With ``cadence_hours`` a fresh prior receipt short-circuits
        the work (``status: skipped``) unless ``force`` is set. The receipt is the
        durable record that integrity was checked and what it found.
        """
        d = self.dir(session_id)
        receipt_path = d / "ledger-integrity.json"
        now = now_iso()
        if cadence_hours is not None and cadence_hours > 0 and not force and receipt_path.exists():
            try:
                prev = read_json(receipt_path)
            except (OSError, ValueError):
                prev = None
            last = (prev or {}).get("verified_at")
            elapsed = _hours_between(last, now) if last else None
            if elapsed is not None and elapsed < cadence_hours:
                return {
                    "status": "skipped",
                    "reason": "within_cadence",
                    "cadence_hours": cadence_hours,
                    "hours_since_last": round(elapsed, 3),
                    "last_verified_at": last,
                    "result": (prev or {}).get("result"),
                }
        result = self.ledger(session_id).integrity()
        receipt = {
            "session_id": session_id,
            "verified_at": now,
            "status": "ok" if result["chain_ok"] else "broken",
            "result": result,
        }
        write_json(receipt_path, receipt)
        return receipt

    def record_trace(self, session_id: str, trace: dict) -> dict:
        """Append a SystemTrace to the ledger (observability is now a live record)."""
        payload = {"event_type": "system_trace", **trace}
        return self.ledger(session_id).append("event", payload, {"source": "observability"})

    def observability(self, session_id: str) -> dict:
        from .governance import summarize_traces
        recs = self.ledger(session_id).verified_records()
        traces = [
            r.get("payload", {})
            for r in recs
            if r.get("record_type") == "event"
            and r.get("payload", {}).get("event_type") == "system_trace"
        ]
        return summarize_traces(traces)

    @locked_session
    def verify_python(self, session_id: str, code: str, *,
                      capability: str = "python_artifact_behavior",
                      independence: str = "unknown",
                      scope: str = "supported_completion",
                      evidence_format: str = "artifact_execution",
                      expected_stdout: str | None = None,
                      trusted_test_code: str | None = None,
                      attempt_id: str | None = None,
                      support_provenance: dict | None = None) -> dict:
        """Run the deterministic Python verifier and record evidence.

        Single source of truth for the verify-then-record evidence rule shared by
        the CLI (`alearn verify-python`) and the HTTP API. Only an executed AND
        independently-checked AND passing run becomes STRONG mastery evidence;
        anything else is logged as an observation, never as evidence. Provenance
        source is ``python_verifier`` so `counts_as_strong` accepts it.
        """
        from .verifiers import VerifierRegistry
        from .evidence_semantics import normalize_evidence_semantics
        from .evidence_history import merge_attempt_support, execution_fingerprint
        d = self.dir(session_id)
        fingerprint = execution_fingerprint(capability, code, [expected_stdout, trusted_test_code])
        self.ledger(session_id).require_integrity()
        support = merge_attempt_support(self.ledger(session_id).verified_records(), capability, attempt_id, support_provenance, fingerprint=fingerprint)
        if independence == "assisted":
            support["conceptual_scaffold"] = True
        domain = read_json(d / "domain-context.json")
        result = VerifierRegistry.verify_python(
            domain, code, expected_stdout=expected_stdout, trusted_test_code=trusted_test_code,
        )
        if result.get("available"):
            vr = result["result"]
            executed = bool(vr.get("executed"))
            checked = bool(vr.get("correctness_checked"))
            if not executed or not checked:
                outcome, requested = "unknown", "weak"
            else:
                outcome = "correct" if vr.get("passed") else "incorrect"
                requested = "strong" if vr.get("passed") else "medium"
            semantics = normalize_evidence_semantics(evidence_format, requested, independence)
            mastery_eligible = semantics.mastery_eligible and outcome in {"correct", "incorrect"} and checked
            led = self.ledger(session_id)
            if outcome == "unknown":
                led.append("observation", {
                    "capability_id": capability,
                    "attempt_id": attempt_id, "support_provenance": support,
                    "task_fingerprint": fingerprint,
                    "verifier": {"id": result.get("verifier_id"), "result": vr},
                    "note": "verifier_ran_without_correctness_check" if executed else "verifier_refused_or_failed_to_execute",
                }, {"source": "python_verifier"})
            else:
                led.append("evidence", {
                    "evidence_id": new_id("ev"),
                    "capability_id": capability,
                    "outcome": outcome,
                    "independence": independence,
                    "strength": semantics.normalized_strength,
                    "scope": scope,
                    "evidence_format": semantics.evidence_format,
                    "mastery_eligible": mastery_eligible,
                    "correctness_checked": checked,
                    "format_semantics": {"requested_strength": semantics.requested_strength, "reasons": semantics.reasons},
                    "support_provenance": support,
                    "attempt_id": attempt_id,
                    "task_fingerprint": fingerprint,
                    "note": "deterministic_python_verifier",
                    "source_event_ids": [],
                    "verifier": {"id": result.get("verifier_id"), "result": vr},
                }, {"source": "python_verifier"})
            self.rebuild_projection(session_id)
        else:
            self.ledger(session_id).append("observation", {
                "capability_id": capability, "attempt_id": attempt_id,
                "task_fingerprint": fingerprint,
                "support_provenance": support, "note": "verifier_unavailable",
            }, {"source": "python_verifier"})
        return result

    # --- memory cards (spaced-repetition flashcards) ---------------------
    def _cards_path(self, session_id: str) -> Path:
        return self.dir(session_id) / "cards.json"

    def _load_cards(self, session_id: str) -> list[dict]:
        p = self._cards_path(session_id)
        return read_json(p) if p.exists() else []

    def _roadmap_node(self, session_id: str, node_id: str) -> dict:
        rj = self.dir(session_id) / "workspace" / "roadmap.json"
        if not rj.exists():
            raise FileNotFoundError("session has no roadmap workspace")
        for n in read_json(rj).get("nodes", []):
            if isinstance(n, dict) and n.get("id") == node_id:
                return n
        raise FileNotFoundError("unknown roadmap node")

    @locked_session
    def add_card(self, session_id: str, node_id: str, front: str, back: str | None = None) -> dict:
        self.ledger(session_id).require_integrity()
        from .memory_cards import new_card
        cards = self._load_cards(session_id)
        c = new_card(node_id, front, back).to_dict()
        cards.append(c)
        commit_state(self.dir(session_id), self.ledger(session_id),
            {"event_type": "card_created", "card_id": c["id"], "node_id": node_id}, {"cards.json": cards})
        return c

    @locked_session
    def scaffold_cards(self, session_id: str, node_id: str) -> list[dict]:
        self.ledger(session_id).require_integrity()
        from .memory_cards import scaffold_from_node
        node = self._roadmap_node(session_id, node_id)
        made = [c.to_dict() for c in scaffold_from_node(node)]
        cards = self._load_cards(session_id)
        cards.extend(made)
        commit_state(self.dir(session_id), self.ledger(session_id),
            {"event_type": "cards_scaffolded", "node_id": node_id, "count": len(made)}, {"cards.json": cards})
        return made

    @locked_session
    def review_card(self, session_id: str, card_id: str, correct: bool) -> dict:
        self.ledger(session_id).require_integrity()
        from .memory_cards import record_review, card_state
        cards = self._load_cards(session_id)
        for c in cards:
            if c.get("id") == card_id:
                record_review(c, correct)
                commit_state(self.dir(session_id), self.ledger(session_id),
                    {"event_type": "card_reviewed", "card_id": card_id, "correct": correct}, {"cards.json": cards})
                return card_state(c)
        raise FileNotFoundError("unknown card")

    @locked_session
    def cards_due(self, session_id: str) -> dict:
        from .memory_cards import due_cards
        cards = self._load_cards(session_id)
        return {"total": len(cards), "due": due_cards(cards)}

    @locked_session
    def set_roadmap(self, session_id: str, plan: dict) -> dict:
        """Attach/replace the roadmap on an existing session.

        The agentic flow gathers intake first, so the authoritative roadmap is
        built AFTER `start`. This rebuilds the workspace from a validated plan
        (normalize_plan enforces slugs, acyclic prerequisites, HTTP(S) sources).
        """
        from .learning_workspace import normalize_plan, create_workspace
        d = self.dir(session_id)
        self.ledger(session_id).require_integrity()
        session = read_json(d / "session.json")
        contract = read_json(d / "learning-contract.json")
        norm = normalize_plan(session["topic"], contract.get("goal"), plan)
        # Prepare completely before the journal commit; old learner work stays live.
        stage = d / f"workspace-stage-{new_id('v')}"
        create_workspace(stage, norm, session_id)
        write_json(stage / "frontend/src/data/roadmap.json",
                   {**norm, "workspace_directory": str((d / "workspace").resolve())})
        sync_tree(stage)
        ws = workspace_metadata(d / "workspace")
        session["learning_workspace"] = ws
        commit_state(d, self.ledger(session_id),
            {"event_type": "roadmap_set", "nodes": len(norm["nodes"])},
            {"session.json": session}, workspace_install=stage.name)
        return {"nodes": len(norm["nodes"]), "workspace": ws}

    # --- agentic driver: the OS tells the agent the next step ------------
    @locked_session
    def next_action(self, session_id: str) -> dict:
        integrity = self.ledger(session_id).integrity()
        if not integrity["chain_ok"]:
            return {"action": "repair_ledger", "reason": "evidence_journal_integrity_broken",
                    "integrity": integrity,
                    "instruction": "Stop learning-state writes. Preserve the original journal and investigate or restore a verified backup; never silently discard damaged evidence."}
        from .driver import next_action as _next
        from .intake import INTAKE_QUESTIONS
        from .mastery import observations_from_projection_caps, estimate
        from .scheduling import due_queue
        from .progression import ProgressionContext, choose_next_scope
        from .evidence_bridge import progress_flags
        from .evidence_history import parse_time
        d = self.dir(session_id)
        contract = read_json(d / "learning-contract.json")

        intake_complete = bool(contract.get("goal")) and bool(contract.get("stated_background"))
        intake_next = None
        if not intake_complete:
            for q in INTAKE_QUESTIONS:
                if q.required and not contract.get(q.field):
                    intake_next = q.to_dict()
                    break

        rj = d / "workspace" / "roadmap.json"
        has_roadmap = rj.exists()
        nodes = []
        if has_roadmap:
            roadmap = read_json(rj)
            has_roadmap = roadmap.get("status") != "needs_curriculum_research" and bool(roadmap.get("nodes"))
            for n in roadmap.get("nodes", []):
                if isinstance(n, dict) and n.get("id"):
                    nodes.append({"id": n["id"], "title": n.get("title")})

        records = self.ledger(session_id).verified_records()
        assessment_pending = {}
        for r in records:
            e = r.get("payload", {})
            cap_id = e.get("capability_id")
            if not isinstance(cap_id, str):
                continue
            if r["record_type"] == "observation" and e.get("verification") == "term_coverage":
                assessment_pending[cap_id] = True
            elif r["record_type"] == "evidence" and e.get("outcome") in {"correct", "incorrect", "partial"}:
                assessment_pending[cap_id] = False
        # Cached projections are outputs, never authority for an advance decision.
        proj = EvidenceProjector(self.ledger(session_id)).rebuild()
        caps = proj["demonstrated_capabilities"]
        evidence_caps = {cap: observations_from_projection_caps(records, cap) for cap in caps}
        evidence_caps = {cap: obs for cap, obs in evidence_caps.items() if obs}

        node_status = {}
        for n in nodes:
            nid = n["id"]
            cap = caps.get(nid) or {}
            obs = evidence_caps.get(nid, [])
            mastery = estimate(nid, obs)
            uncertain = nid in proj["uncertainties"]
            flags = progress_flags(proj, nid)
            target = choose_next_scope(ProgressionContext(
                independent_reproduction=flags["independent_reproduction_available"],
                near_transfer=flags["near_transfer_available"],
                far_transfer=flags["far_transfer_available"],
                delayed_independent_performance=flags["delayed_independent_performance_available"],
            )).next_scope
            if target == "maintenance_or_new_frontier" and not mastery.mastered:
                target = "delayed_independent_performance"
            last_time = parse_time(cap.get("last_observed_at"))
            node_status[nid] = {
                "mastered": mastery.mastered and not uncertain,
                "ready_to_advance": int(cap.get("strong_unassisted_successes", 0)) >= 1 and not uncertain,
                "evidence_count": int(cap.get("evidence_count", 0)),
                "strong_unassisted_successes": int(cap.get("strong_unassisted_successes", 0)),
                "consecutive_failures": cap.get("consecutive_failures", 0),
                "mastery": mastery.to_dict(),
                "evidence_target": target,
                "assessment_pending": assessment_pending.get(nid, False),
                "review_not_before": (last_time + timedelta(days=1)).isoformat() if last_time else None,
            }

        due_reviews = [s for s in due_queue(evidence_caps) if s.get("due")]
        reviews_due = len(due_reviews)
        cards = self.cards_due(session_id)

        decision = _next(
            intake_complete=intake_complete, intake_next=intake_next,
            has_roadmap=has_roadmap, nodes=nodes, node_status=node_status,
            cards_due=len(cards["due"]), reviews_due=reviews_due,
        )
        if decision["action"] == "review_capability":
            decision["reviews"] = due_reviews
        return decision

    @locked_session
    def verify_exercise(self, session_id: str, lang: str, code: str, *,
                        capability: str = "exercise_behavior",
                        expected_stdout: str | None = None,
                        independence: str = "unknown",
                        scope: str = "independent_reproduction",
                        evidence_format: str = "artifact_execution",
                        prefer_backend: str | None = None,
                        attempt_id: str | None = None,
                        support_provenance: dict | None = None) -> dict:
        """Run ANY supported language via the execution backend and record evidence.

        The multi-language counterpart of ``verify_python``: only an executed AND
        correctness-checked (expected_stdout) AND passing run becomes STRONG,
        unassisted, mastery-eligible evidence; otherwise it is logged as an
        observation, never as evidence. Provenance source ``exercise_verifier`` is a
        runtime check (not a self-report), so a clean pass counts toward mastery.
        This is what lets a non-Python code node (Kotlin, Go, Java, ...) reach
        mastery wherever a toolchain/container is available.
        """
        from .execution import run_exercise, select_backend
        from .evidence_semantics import normalize_evidence_semantics
        from .evidence_history import merge_attempt_support, execution_fingerprint
        fingerprint = execution_fingerprint(capability, [lang, code], expected_stdout)
        self.ledger(session_id).require_integrity()
        support = merge_attempt_support(self.ledger(session_id).verified_records(), capability, attempt_id, support_provenance, fingerprint=fingerprint)
        if independence == "assisted":
            support["conceptual_scaffold"] = True
        res = run_exercise(lang, code, expect_stdout=expected_stdout, backend=select_backend(prefer_backend))
        available = bool(res.get("available"))
        ran = bool(res.get("ran"))
        checked = expected_stdout is not None
        if not (available and ran and checked):
            outcome, requested = "unknown", "weak"
        else:
            passed = bool(res.get("passed"))
            outcome = "correct" if passed else "incorrect"
            requested = "strong" if passed else "medium"
        semantics = normalize_evidence_semantics(evidence_format, requested, independence)
        mastery_eligible = semantics.mastery_eligible and outcome in {"correct", "incorrect"} and (available and ran and checked)
        led = self.ledger(session_id)
        if outcome == "unknown":
            led.append("observation", {
                "capability_id": capability, "language": lang,
                "attempt_id": attempt_id, "support_provenance": support,
                "task_fingerprint": fingerprint,
                "exercise": {"available": available, "ran": ran, "result": res},
                "note": "exercise_not_verifiable" if (available and ran) else "exercise_toolchain_unavailable_or_failed_to_run",
            }, {"source": "exercise_verifier"})
        else:
            led.append("evidence", {
                "evidence_id": new_id("ev"),
                "capability_id": capability,
                "outcome": outcome,
                "independence": independence,
                "strength": semantics.normalized_strength,
                "scope": scope,
                "evidence_format": semantics.evidence_format,
                "mastery_eligible": mastery_eligible,
                "correctness_checked": checked,
                "format_semantics": {"requested_strength": semantics.requested_strength, "reasons": semantics.reasons},
                "support_provenance": support,
                "attempt_id": attempt_id,
                "task_fingerprint": fingerprint,
                "note": "deterministic_exercise_verifier",
                "source_event_ids": [],
                "verifier": {"language": lang, "result": res},
            }, {"source": "exercise_verifier"})
        self.rebuild_projection(session_id)
        return res

    @locked_session
    def inspect(self, session_id: str) -> dict:
        d = self.dir(session_id)
        return {
            "session": read_json(d / "session.json"),
            "learning_contract": read_json(d / "learning-contract.json"),
            "source_policy": read_json(d / "source-policy.json"),
            "domain_context": read_json(d / "domain-context.json"),
            "anchor_probe": read_json(d / "anchor-probe.json"),
            "learner_evidence": EvidenceProjector(self.ledger(session_id)).rebuild(),
            "ledger_records": len(self.ledger(session_id).records()),
            "ledger_integrity": self.ledger(session_id).integrity(),
        }

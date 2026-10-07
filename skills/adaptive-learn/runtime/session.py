from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from .utils import new_id, now_iso, write_json, read_json
from .contracts import LearningContract
from .ledger import EventLedger
from .projection import EvidenceProjector
from .adapters import AdapterRegistry
from .anchors import plan_anchor
from .learning_workspace import normalize_plan, create_workspace
from .safety import safe_session_dir, UnsafePathError


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

    def update_contract(self, session_id: str, **updates) -> dict:
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
        write_json(d / "learning-contract.json", contract.to_dict())
        write_json(d / "source-policy.json", contract.source_policy.to_dict())

        session = read_json(d / "session.json")
        domain = read_json(d / "domain-context.json")
        anchor = plan_anchor(session["topic"], session.get("interaction_mode"), contract.to_dict(), domain)
        write_json(d / "anchor-probe.json", anchor.to_dict())
        session["anchor_probe"] = anchor.to_dict()
        write_json(d / "session.json", session)
        self.ledger(session_id).append(
            "event",
            {"event_type": "learning_contract_updated", "fields": sorted(k for k, v in updates.items() if v is not None)},
            {"source": "session_kernel"},
        )
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

    def ledger(self, session_id: str) -> EventLedger:
        return EventLedger(self.dir(session_id) / "ledger.jsonl", session_id)

    def rebuild_projection(self, session_id: str) -> dict:
        d = self.dir(session_id)
        return EvidenceProjector(self.ledger(session_id)).write(d / "learner-evidence.json")

    def inspect(self, session_id: str) -> dict:
        d = self.dir(session_id)
        return {
            "session": read_json(d / "session.json"),
            "learning_contract": read_json(d / "learning-contract.json"),
            "source_policy": read_json(d / "source-policy.json"),
            "domain_context": read_json(d / "domain-context.json"),
            "anchor_probe": read_json(d / "anchor-probe.json"),
            "learner_evidence": read_json(d / "learner-evidence.json"),
            "ledger_records": len(self.ledger(session_id).records()),
        }

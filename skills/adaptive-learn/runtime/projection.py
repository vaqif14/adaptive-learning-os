from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from .ledger import EventLedger
from .utils import now_iso, write_json

# A hypothesis is a short-lived diagnostic bet; it should not linger as "active"
# forever (projection stays honest about current uncertainty).
HYPOTHESIS_TTL_DAYS = 30

TRANSFER_SCOPES = {"near_transfer", "far_transfer", "delayed_independent_performance"}
_DELEGATION_SUPPORT = {"pedagogical", "cognitive_delegation"}


def _heavy_support(sp: dict) -> bool:
    """True if the learner was materially carried (revealed answer, worked example,
    or any hints). Such success is not independent-capability evidence."""
    if not isinstance(sp, dict):
        return False
    return (
        bool(sp.get("ai_direct_answer_revealed"))
        or bool(sp.get("worked_example_shown"))
        or int(sp.get("hints_count") or 0) > 0
    )


def counts_as_strong(e: dict) -> bool:
    """One predicate for 'strong unassisted success', honoring provenance and scope.

    Strong requires: correct, unassisted, non-delegation support kind, declared
    strong, mastery-eligible, a scope beyond mere supported completion, and NO
    heavy support. This is what stops a revealed-answer / hinted / AI-generated
    success from counting as mastery.
    """
    return (
        e.get("outcome") == "correct"
        and e.get("independence") == "unassisted"
        and e.get("support_kind", "none") not in _DELEGATION_SUPPORT
        and e.get("strength") == "strong"
        and e.get("mastery_eligible", True)
        and e.get("scope") not in (None, "supported_completion")
        and not _heavy_support(e.get("support_provenance") or {})
    )


def _age_days(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(ts)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0
    except ValueError:
        return None


class EvidenceProjector:
    def __init__(self, ledger: EventLedger):
        self.ledger = ledger

    def rebuild(self) -> dict:
        capabilities: dict[str, dict] = {}
        uncertainties: set[str] = set()
        hypotheses: dict[str, dict] = {}
        seen_evidence: set[str] = set()

        # Trust only the integrity-verified prefix (tamper gate).
        for r in self.ledger.verified_records():
            rtype = r.get("record_type")
            if rtype == "evidence":
                e = r["payload"]
                # Deduplicate: the same evidence_id must not be counted twice.
                eid = e.get("evidence_id")
                if eid is not None:
                    if eid in seen_evidence:
                        continue
                    seen_evidence.add(eid)

                cap = e.get("capability_id", "unknown")
                state = capabilities.setdefault(cap, {
                    "evidence_count": 0,
                    "strong_unassisted_successes": 0,
                    "weak_context_signal_count": 0,
                    "scopes": [],
                    "scope_successes": {},          # per-(scope) strong successes
                    "last_outcome": None,
                    "last_observed_at": None,
                })
                state["evidence_count"] += 1
                state["last_outcome"] = e.get("outcome")
                state["last_observed_at"] = r.get("timestamp")

                scope = e.get("scope")
                if scope and scope not in state["scopes"]:
                    state["scopes"].append(scope)

                context_signals = e.get("context_signals") or {}
                if context_signals and context_signals.get("signal_weight") == "weak":
                    state["weak_context_signal_count"] += 1

                if counts_as_strong(e):
                    state["strong_unassisted_successes"] += 1
                    if scope:
                        state["scope_successes"][scope] = state["scope_successes"].get(scope, 0) + 1
                    # Any success strong enough to pass counts_as_strong (unassisted,
                    # strong, mastery-eligible, beyond supported_completion, no heavy
                    # support) clears the coarse "uncertain" flag. MASTERY stays strict
                    # (BKT threshold + breadth + transfer) and is judged separately.
                    uncertainties.discard(cap)

                if e.get("outcome") in {"incorrect", "partial"}:
                    uncertainties.add(cap)
                # outcome == "unknown" is neutral: neither strong nor a failure.

            elif rtype == "hypothesis":
                h = r["payload"]
                hid = h.get("hypothesis_id")
                if not isinstance(hid, str):
                    continue  # unhashable / malformed id -> ignore, never crash
                age = _age_days(r.get("timestamp"))
                h = dict(h)
                h["_expired"] = (age is not None and age > HYPOTHESIS_TTL_DAYS)
                hypotheses[hid] = h

        active = [h for h in hypotheses.values() if not h.get("_expired")]
        expired = [h for h in hypotheses.values() if h.get("_expired")]
        return {
            "session_id": self.ledger.session_id,
            "demonstrated_capabilities": capabilities,
            "uncertainties": sorted(uncertainties),
            "active_hypotheses": active,
            "expired_hypotheses_count": len(expired),
            "last_rebuilt_at": now_iso(),
        }

    def write(self, path: Path) -> dict:
        obj = self.rebuild()
        write_json(path, obj)
        return obj

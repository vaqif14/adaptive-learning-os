from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from .ledger import EventLedger
from .utils import now_iso, write_json
from .evidence_history import coverage_only, unique_evidence, parse_time, trial_keys
from .evidence_semantics import FORMAT_MAX_STRENGTH, NON_MASTERY_FORMATS

# A hypothesis is a short-lived diagnostic bet; it should not linger as "active"
# forever (projection stays honest about current uncertainty).
HYPOTHESIS_TTL_DAYS = 30

TRANSFER_SCOPES = {"near_transfer", "far_transfer", "delayed_independent_performance"}
_DELEGATION_SUPPORT = {"pedagogical", "cognitive_delegation"}
# Ledger provenance sources where the outcome is a bare declaration (the host agent
# or learner says "correct"), not a runtime check. Such evidence is never strong.
SELF_REPORTED_SOURCES = {"cli_evidence_entry"}


def _self_reported(e: dict, provenance: dict | None) -> bool:
    """True if nothing but a declaration backs this evidence. The payload marker covers
    new records; the record provenance covers ledgers written before the marker existed."""
    if e.get("verification") == "self_report":
        return True
    return isinstance(provenance, dict) and provenance.get("source") in SELF_REPORTED_SOURCES


def _heavy_support(sp: dict) -> bool:
    """True if the learner was materially carried (revealed answer, worked example,
    or any hints). Such success is not independent-capability evidence."""
    if not isinstance(sp, dict):
        return True
    hints = sp.get("hints_count", 0)
    if type(hints) is not int or hints < 0:
        return True  # malformed support must never establish independence
    return (
        bool(sp.get("ai_direct_answer_revealed"))
        or bool(sp.get("worked_example_shown"))
        or hints > 0
        or bool(sp.get("conceptual_scaffold"))
    )


def counts_as_strong(e: dict, provenance: dict | None = None) -> bool:
    """One predicate for 'strong unassisted success', honoring provenance and scope.

    Strong requires: correct, unassisted, non-delegation support kind, declared
    strong, mastery-eligible, a scope beyond mere supported completion, NO
    heavy support, and a runtime check behind it (not a self-report). This is what
    stops a revealed-answer / hinted / AI-generated / merely-asserted success from
    counting as mastery. Pass the ledger record's `provenance` when you have it.
    """
    if not isinstance(e, dict) or not isinstance(e.get("scope"), str):
        return False
    fmt = e.get("evidence_format")
    if "evidence_format" in e and not isinstance(fmt, str):
        return False
    return (
        e.get("outcome") == "correct"
        and e.get("independence") == "unassisted"
        and e.get("support_kind", "none") not in _DELEGATION_SUPPORT
        and e.get("strength") == "strong"
        and e.get("mastery_eligible", True) is True
        and e.get("scope") in {"independent_reproduction", *TRANSFER_SCOPES}
        and ("evidence_format" not in e or
             (FORMAT_MAX_STRENGTH.get(e.get("evidence_format")) == "strong"
              and e.get("evidence_format") not in NON_MASTERY_FORMATS))
        and not _heavy_support(e.get("support_provenance", {}))
        and not _self_reported(e, provenance)
        and not coverage_only(e, provenance)
        and e.get("correctness_checked") is True
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
        successful_attempts = set()

        # Trust only the integrity-verified prefix (tamper gate).
        records = self.ledger.verified_records()
        eligible_records = list(unique_evidence(records))
        evidence_ids = {r["record_id"] for r in eligible_records}
        groups = dict(zip((r["record_id"] for r in eligible_records), trial_keys(eligible_records)))
        for r in records:
            rtype = r.get("record_type")
            if rtype == "evidence":
                if r["record_id"] not in evidence_ids:
                    continue
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
                    "consecutive_failures": 0,
                    "supported_attempts": 0,
                })
                previous_t = parse_time(state["last_observed_at"])
                current_t = parse_time(r.get("timestamp"))
                state["evidence_count"] += 1
                state["last_outcome"] = e.get("outcome")
                state["last_observed_at"] = r.get("timestamp")
                if _heavy_support(e.get("support_provenance") or {}) or e.get("independence") == "assisted":
                    state["supported_attempts"] += 1

                scope = e.get("scope")
                if scope and scope not in state["scopes"]:
                    state["scopes"].append(scope)

                context_signals = e.get("context_signals") or {}
                if isinstance(context_signals, dict) and context_signals.get("signal_weight") == "weak":
                    state["weak_context_signal_count"] += 1

                if counts_as_strong(e, r.get("provenance")):
                    attempt_key = groups[r["record_id"]]
                    fresh_success = attempt_key not in successful_attempts
                    if fresh_success:
                        successful_attempts.add(attempt_key)
                        state["strong_unassisted_successes"] += 1
                    delayed_valid = (scope != "delayed_independent_performance" or
                                     (previous_t and current_t and (current_t - previous_t).total_seconds() >= 86400))
                    if scope and delayed_valid and fresh_success:
                        state["scope_successes"][scope] = state["scope_successes"].get(scope, 0) + 1
                    # Any success strong enough to pass counts_as_strong (unassisted,
                    # strong, mastery-eligible, beyond supported_completion, no heavy
                    # support) clears the coarse "uncertain" flag. MASTERY stays strict
                    # (BKT threshold + breadth + transfer) and is judged separately.
                    uncertainties.discard(cap)
                    state["consecutive_failures"] = 0

                if e.get("outcome") in {"incorrect", "partial"}:
                    uncertainties.add(cap)
                    state["consecutive_failures"] += 1
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

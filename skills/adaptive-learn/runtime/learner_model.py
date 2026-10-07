from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from .utils import read_json
from .ledger import EventLedger
from .projection import counts_as_strong

# Cross-session learner model. Rebuilt FROM each session's integrity-verified
# ledger (never a stale cached projection), with TIME-AWARE uncertainty: a
# capability is uncertain when its most recent graded signal is a failure that is
# newer than its last strong success.


def _parse(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def aggregate_learner_state(sessions_dir: Path, learner_id: str | None = None) -> dict:
    sessions_dir = Path(sessions_dir)
    caps: dict[str, dict] = {}
    sessions_seen = 0
    if sessions_dir.exists():
        for sdir in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
            ledger_path = sdir / "ledger.jsonl"
            if not ledger_path.exists():
                continue
            if learner_id is not None:
                try:
                    if read_json(sdir / "session.json").get("learner_id") != learner_id:
                        continue
                except (FileNotFoundError, ValueError):
                    continue
            sessions_seen += 1
            led = EventLedger(ledger_path, sdir.name)
            for r in led.verified_records():          # trust gate: tampered tail dropped
                if r.get("record_type") != "evidence":
                    continue
                e = r["payload"]
                cap = e.get("capability_id", "unknown")
                ts = _parse(r.get("timestamp"))
                agg = caps.setdefault(cap, {
                    "evidence_count": 0, "strong_unassisted_successes": 0,
                    "scopes": [], "scope_successes": {},
                    "last_observed_at": None, "last_strong_at": None, "last_fail_at": None,
                })
                agg["evidence_count"] += 1
                sc = e.get("scope")
                if sc and sc not in agg["scopes"]:
                    agg["scopes"].append(sc)
                if r.get("timestamp") and (agg["last_observed_at"] is None or r["timestamp"] > agg["last_observed_at"]):
                    agg["last_observed_at"] = r["timestamp"]
                if counts_as_strong(e):
                    agg["strong_unassisted_successes"] += 1
                    if sc:
                        agg["scope_successes"][sc] = agg["scope_successes"].get(sc, 0) + 1
                    if ts and (agg["last_strong_at"] is None or ts > _parse(agg["last_strong_at"] or "") or agg["last_strong_at"] is None):
                        agg["last_strong_at"] = r.get("timestamp")
                elif e.get("outcome") in {"incorrect", "partial"}:
                    if ts and (agg["last_fail_at"] is None or ts > (_parse(agg["last_fail_at"]) or ts.replace(year=1))):
                        agg["last_fail_at"] = r.get("timestamp")

    # Time-aware uncertainty: uncertain iff a failure is the latest word on it
    # (newer than the last strong success, or no strong success at all).
    uncertainties = []
    for cap, agg in caps.items():
        lf, ls = _parse(agg["last_fail_at"]), _parse(agg["last_strong_at"])
        if lf and (ls is None or lf > ls):
            uncertainties.append(cap)

    return {
        "learner_id": learner_id,
        "sessions_aggregated": sessions_seen,
        "capabilities": caps,
        "uncertainties": sorted(uncertainties),
    }

from __future__ import annotations

from pathlib import Path
from .projection import counts_as_strong
from .evidence_history import learner_history, parse_time, trial_keys


def aggregate_learner_state(sessions_dir: Path, learner_id: str | None = None, *, topic: str | None = None) -> dict:
    """Historical evidence for one learner/topic; not a claim of current mastery."""
    sessions_seen, records = learner_history(sessions_dir, learner_id, topic=topic)
    caps = {}
    successful_attempts = set()
    for key, r in zip(trial_keys(records), records):
        e = r["payload"]
        cap, sc = e["capability_id"], e.get("scope")
        agg = caps.setdefault(cap, {
            "evidence_count": 0, "strong_unassisted_successes": 0,
            "scopes": [], "scope_successes": {}, "last_observed_at": None,
            "last_strong_at": None, "last_fail_at": None,
        })
        previous_t = parse_time(agg["last_observed_at"])
        agg["evidence_count"] += 1
        agg["last_observed_at"] = r["timestamp"]  # records already sorted by actual UTC time
        if sc and sc not in agg["scopes"]:
            agg["scopes"].append(sc)
        if counts_as_strong(e, r.get("provenance")):
            if key not in successful_attempts:
                successful_attempts.add(key)
                agg["strong_unassisted_successes"] += 1
                delayed_valid = sc != "delayed_independent_performance" or (
                    previous_t and (parse_time(r["timestamp"]) - previous_t).total_seconds() >= 86400)
                if sc and delayed_valid:
                    agg["scope_successes"][sc] = agg["scope_successes"].get(sc, 0) + 1
            agg["last_strong_at"] = r["timestamp"]
        elif e.get("outcome") in {"incorrect", "partial"}:
            agg["last_fail_at"] = r["timestamp"]

    uncertainties = []
    for cap, agg in caps.items():
        lf, ls = parse_time(agg["last_fail_at"]), parse_time(agg["last_strong_at"])
        if lf and (ls is None or lf >= ls):
            uncertainties.append(cap)
    return {"learner_id": learner_id, "topic": topic, "sessions_aggregated": sessions_seen,
            "capabilities": caps, "uncertainties": sorted(uncertainties)}

from __future__ import annotations

from pathlib import Path
from itertools import combinations
from .projection import counts_as_strong
from .evidence_history import learner_history

MIN_FAILS = 2
MIN_SESSIONS = 2


def analyze(sessions_dir: Path, learner_id: str | None = None, *, topic: str | None = None) -> dict:
    """Find unresolved recurring errors after the latest qualified success.

    A success resolves earlier failures, not later relapses. Only sessions with
    actual unresolved failures count toward persistence. Causes remain hypotheses.
    """
    sessions_seen, records = learner_history(sessions_dir, learner_id, topic=topic)
    caps = {}
    for r in records:
        e = r["payload"]
        st = caps.setdefault(e["capability_id"], {"failures": [], "last_outcome": None})
        if counts_as_strong(e, r.get("provenance")):
            st["failures"] = []
            st["last_outcome"] = "correct"
        elif e.get("outcome") in {"incorrect", "partial"}:
            st["failures"].append(r)
            st["last_outcome"] = e["outcome"]

    persistent, tags, per_session = [], {}, {}
    for capability, st in caps.items():
        failures = st["failures"]
        failed_sessions = {r["session_id"] for r in failures}
        if len(failures) >= MIN_FAILS and len(failed_sessions) >= MIN_SESSIONS:
            persistent.append({"capability": capability, "failures": len(failures),
                "sessions": len(failed_sessions), "last_outcome": st["last_outcome"],
                "note": "repeated unresolved failures after the latest qualified success"})
        for r in failures:
            e = r["payload"]
            support = e.get("support_provenance")
            label = e.get("misconception") or e.get("error_class") or (
                support.get("error_class") if isinstance(support, dict) else None)
            if isinstance(label, str) and label:
                tagged = tags.setdefault(label, {"count": 0, "capabilities": set()})
                tagged["count"] += 1
                tagged["capabilities"].add(capability)
            per_session.setdefault(r["session_id"], set()).add(capability)
    persistent.sort(key=lambda p: (-p["failures"], -p["sessions"], p["capability"]))
    misconceptions = sorted(
        ({"label": label, "count": t["count"], "capabilities": sorted(t["capabilities"])}
         for label, t in tags.items() if t["count"] >= MIN_FAILS),
        key=lambda t: (-t["count"], t["label"]))
    pairs = {}
    for failed in per_session.values():
        for pair in combinations(sorted(failed), 2):
            pairs[pair] = pairs.get(pair, 0) + 1
    clusters = sorted(
        ({"capabilities": list(pair), "co_failed_sessions": n} for pair, n in pairs.items() if n >= MIN_SESSIONS),
        key=lambda p: (-p["co_failed_sessions"], p["capabilities"]))
    suspect = persistent[0]["capability"] if persistent else (misconceptions[0]["label"] if misconceptions else None)
    return {"learner_id": learner_id, "topic": topic, "sessions_analyzed": sessions_seen,
            "persistent_failures": persistent, "recurring_misconceptions": misconceptions,
            "co_failure_clusters": clusters, "primary_suspect": suspect,
            "note": "Evidence-backed patterns to probe, not confirmed causes; verify with a diagnostic task."}

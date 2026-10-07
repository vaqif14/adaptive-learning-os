from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from itertools import combinations

from .utils import read_json
from .ledger import EventLedger
from .projection import counts_as_strong

# The Diagnostician (role 08): scans a learner's sessions and isolates RECURRING
# root problems, not one-off slips. Deterministic over integrity-verified ledgers:
#   - persistent_failures: capabilities failed repeatedly, across sessions, with no
#     qualified strong success (the misconception never got resolved);
#   - recurring_misconceptions: an optional `misconception`/`error_class` tag on
#     evidence, tallied across sessions;
#   - co_failure_clusters: capabilities that fail together in the same sessions —
#     a hint at one shared underlying flaw.
# It never fabricates a cause; it surfaces evidence-backed patterns to probe.

MIN_FAILS = 2          # a pattern needs at least this many failures
MIN_SESSIONS = 2       # ...spread across at least this many sessions to be "persistent"


def _fails(e: dict) -> bool:
    return e.get("outcome") in {"incorrect", "partial"}


def _tag(e: dict):
    return e.get("misconception") or e.get("error_class") or (e.get("support_provenance") or {}).get("error_class")


def analyze(sessions_dir: Path, learner_id: str | None = None) -> dict:
    sessions_dir = Path(sessions_dir)
    cap = {}                       # capability -> {fails, strong, sessions:set, last_outcome, last_at}
    tag_count = {}                 # misconception tag -> {count, capabilities:set}
    per_session_fails = []         # list of sets of failing caps (for co-failure)
    sessions_seen = 0

    if sessions_dir.exists():
        for sdir in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
            lp = sdir / "ledger.jsonl"
            if not lp.exists():
                continue
            if learner_id is not None:
                try:
                    if read_json(sdir / "session.json").get("learner_id") != learner_id:
                        continue
                except (FileNotFoundError, ValueError):
                    continue
            sessions_seen += 1
            failing_here = set()
            for r in EventLedger(lp, sdir.name).verified_records():
                if r.get("record_type") != "evidence":
                    continue
                e = r["payload"]; c = e.get("capability_id", "unknown")
                st = cap.setdefault(c, {"fails": 0, "strong": 0, "sessions": set(), "last_outcome": None, "last_at": None})
                st["sessions"].add(sdir.name)
                st["last_outcome"] = e.get("outcome"); st["last_at"] = r.get("timestamp")
                if counts_as_strong(e):
                    st["strong"] += 1
                if _fails(e):
                    st["fails"] += 1
                    failing_here.add(c)
                    t = _tag(e)
                    if t:
                        tc = tag_count.setdefault(str(t), {"count": 0, "capabilities": set()})
                        tc["count"] += 1; tc["capabilities"].add(c)
            if failing_here:
                per_session_fails.append(failing_here)

    persistent = []
    for c, st in cap.items():
        if st["fails"] >= MIN_FAILS and len(st["sessions"]) >= MIN_SESSIONS and st["strong"] == 0:
            persistent.append({"capability": c, "failures": st["fails"],
                               "sessions": len(st["sessions"]), "last_outcome": st["last_outcome"],
                               "note": "repeated failures across sessions, never resolved by a strong success"})
    persistent.sort(key=lambda x: (-x["failures"], -x["sessions"], x["capability"]))

    misconceptions = sorted(
        ({"label": t, "count": v["count"], "capabilities": sorted(v["capabilities"])} for t, v in tag_count.items()),
        key=lambda x: (-x["count"], x["label"]),
    )

    pair_count = {}
    for fs in per_session_fails:
        for a, b in combinations(sorted(fs), 2):
            pair_count[(a, b)] = pair_count.get((a, b), 0) + 1
    clusters = sorted(
        ({"capabilities": [a, b], "co_failed_sessions": n} for (a, b), n in pair_count.items() if n >= MIN_SESSIONS),
        key=lambda x: -x["co_failed_sessions"],
    )

    top = persistent[0]["capability"] if persistent else (misconceptions[0]["label"] if misconceptions else None)
    return {
        "learner_id": learner_id,
        "sessions_analyzed": sessions_seen,
        "persistent_failures": persistent,
        "recurring_misconceptions": misconceptions,
        "co_failure_clusters": clusters,
        "primary_suspect": top,
        "note": "evidence-backed patterns to probe — not a confirmed cause; verify with a Socratic/diagnostic probe",
    }

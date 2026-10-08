from __future__ import annotations

"""Learning-outcome metrics from the evidence ledger (the measurement instrument).

This does NOT by itself prove the method works. Efficacy is an empirical claim:
it needs a comparison group (this method vs passive study) and a DELAYED post-test,
analyzed for effect size — see ``references/efficacy-study.md``. What this module
does is turn the append-only ledger a session already records into the hard numbers
such a study reports, so a real (even small) pilot produces evidence instead of
opinion. Every figure is computed over first graded trials of verified records, so
edited retries and self-reports can't inflate it.
"""

from .evidence_history import unique_evidence, trial_keys, parse_time
from .projection import counts_as_strong

TRANSFER_SCOPES = {"near_transfer", "far_transfer", "delayed_independent_performance"}


def _first_trials(records: list[dict]) -> list[dict]:
    """First graded trial per task (not the successful retry) — prospective ability."""
    recs = sorted(unique_evidence(records), key=lambda r: (parse_time(r["timestamp"]), r.get("seq", 0)))
    groups, seen, out = trial_keys(recs), set(), []
    for i, r in enumerate(recs):
        if groups[i] in seen:
            continue
        e = r.get("payload", {})
        if e.get("outcome") not in {"correct", "incorrect"}:
            continue
        seen.add(groups[i])
        out.append(r)
    return out


def learning_curve(records: list[dict]) -> list[dict]:
    """Ordered first-trial outcomes — the series a learning curve is drawn from."""
    curve = []
    for idx, r in enumerate(_first_trials(records)):
        e = r["payload"]
        curve.append({
            "order": idx,
            "capability": e.get("capability_id"),
            "correct": e.get("outcome") == "correct",
            "independence": e.get("independence"),
            "scope": e.get("scope"),
            "strong": counts_as_strong(e, r.get("provenance")),
            "timestamp": r.get("timestamp"),
        })
    return curve


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarize(records: list[dict]) -> dict:
    """Outcome metrics for one learner/session (honest, control-group still required)."""
    trials = _first_trials(records)
    n = len(trials)
    correct = sum(1 for r in trials if r["payload"].get("outcome") == "correct")
    unassisted = [r for r in trials if r["payload"].get("independence") == "unassisted"]
    unassisted_correct = sum(1 for r in unassisted if r["payload"].get("outcome") == "correct")

    strong = [r for r in trials if counts_as_strong(r["payload"], r.get("provenance"))]
    strong_caps = {r["payload"].get("capability_id") for r in strong}
    transfer_strong = [r for r in strong if r["payload"].get("scope") in TRANSFER_SCOPES]

    # attempts-to-first-strong per capability (effort to reach independent mastery)
    attempts_to_strong = {}
    seen_strong = set()
    per_cap_count = {}
    for r in trials:
        cap = r["payload"].get("capability_id")
        per_cap_count[cap] = per_cap_count.get(cap, 0) + 1
        if cap not in seen_strong and counts_as_strong(r["payload"], r.get("provenance")):
            attempts_to_strong[cap] = per_cap_count[cap]
            seen_strong.add(cap)

    # assisted -> independent transition: capabilities that started assisted and
    # later produced an unassisted strong success (fading worked).
    first_assisted = {}
    for r in trials:
        cap = r["payload"].get("capability_id")
        if cap not in first_assisted:
            first_assisted[cap] = r["payload"].get("independence") == "assisted"
    faded = sorted(c for c in strong_caps if first_assisted.get(c))

    return {
        "first_trials": n,
        "accuracy_first_trial": _rate(correct, n),
        "independent_success_rate": _rate(unassisted_correct, len(unassisted)),
        "capabilities_reaching_strong": len(strong_caps),
        "attempts_to_first_strong": attempts_to_strong,
        "mean_attempts_to_strong": _rate(sum(attempts_to_strong.values()), len(attempts_to_strong)) if attempts_to_strong else None,
        "transfer_strong_successes": len(transfer_strong),
        "assisted_to_independent": faded,
        "caveat": ("Outcome metrics from real usage. Efficacy requires a control group "
                   "(this method vs passive study) and a DELAYED post-test; analyze effect "
                   "size. See references/efficacy-study.md. Synthetic/agent runs do not count."),
    }


def cohort(sessions: dict[str, list[dict]]) -> dict:
    """Aggregate per-learner summaries into a cohort view for a pilot."""
    per = {sid: summarize(recs) for sid, recs in sessions.items()}
    isr = [s["independent_success_rate"] for s in per.values() if s["independent_success_rate"] is not None]
    mats = [s["mean_attempts_to_strong"] for s in per.values() if s["mean_attempts_to_strong"] is not None]
    return {
        "learners": len(per),
        "mean_independent_success_rate": _rate(round(sum(isr), 6), len(isr)) if isr else None,
        "mean_attempts_to_strong": _rate(round(sum(mats), 6), len(mats)) if mats else None,
        "total_transfer_strong": sum(s["transfer_strong_successes"] for s in per.values()),
        "per_learner": per,
        "caveat": "Descriptive cohort outcomes, not a controlled result. Pair with a control group + delayed post-test.",
    }

"""Offline temporal evaluation of BKT defaults and candidate parameters.

Reports are diagnostics, not permission to lower mastery gates. Holdout labels
never enter parameter selection; after each prediction they update that learner's
state, as in actual sequential use. No external service or synthetic data needed.
"""
from dataclasses import asdict
import hashlib
import itertools
import json
import math

from .evidence_history import unique_evidence, trial_keys, parse_time
from .mastery import BKTParams, bkt_step
from .projection import counts_as_strong


def calibration_rows(records):
    records = sorted(unique_evidence(records), key=lambda r: (parse_time(r["timestamp"]), r.get("seq", 0)))
    # Keep the first graded trial, not the successful retry; this evaluates
    # prospective independent responses rather than final edited artifacts.
    groups, seen, rows = trial_keys(records), set(), []
    for i, record in enumerate(records):
        if groups[i] in seen:
            continue
        seen.add(groups[i])
        e = record["payload"]
        if e.get("outcome") not in {"correct", "incorrect"}:
            continue
        # Failures have medium strength by design. Apply all other quality gates
        # equally so calibration doesn't accidentally select successes only.
        quality = {**e, "outcome": "correct", "strength": "strong"}
        if not counts_as_strong(quality, record.get("provenance")):
            continue
        rows.append({"capability": e["capability_id"], "timestamp": record["timestamp"],
                     "correct": e["outcome"] == "correct"})
    return rows


def _score(rows, params, *, start=0):
    states, losses, squares = {}, [], []
    for i, row in enumerate(rows):
        cap, timestamp = row["capability"], parse_time(row["timestamp"])
        p, last = states.get(cap, (params.p_init, timestamp))
        p *= (1 - params.p_forget_per_day) ** max(0, (timestamp - last).total_seconds() / 86400)
        predicted = min(1 - 1e-12, max(1e-12, p * (1 - params.p_slip) + (1 - p) * params.p_guess))
        y = float(row["correct"])
        if i >= start:
            losses.append(-y * math.log(predicted) - (1 - y) * math.log(1 - predicted))
            squares.append((predicted - y) ** 2)
        states[cap] = bkt_step(p, y, params), timestamp
    return {"count": len(losses), "log_loss": sum(losses) / len(losses), "brier": sum(squares) / len(squares)}


def calibrate(records):
    rows = calibration_rows(records)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    report = {"schema_version": 1, "dataset_sha256": digest, "observations": len(rows),
              "applied": False, "scope": "selected learner and topic only",
              "method": "chronological 80/20 holdout; grid selected on training log loss",
              "limitations": ["Performance prediction is not a validated probability of mastery.",
                              "Candidate fit does not alter mastery thresholds or active runtime defaults."]}
    if len(rows) < 30 or len({r["correct"] for r in rows}) < 2:
        return {**report, "status": "insufficient_data", "required": "at least 30 independent checked trials with successes and failures"}
    split = int(len(rows) * .8)
    # Never split equal timestamps between train and holdout.
    while split > 0 and parse_time(rows[split - 1]["timestamp"]) == parse_time(rows[split]["timestamp"]):
        split -= 1
    if split < 20 or len(rows) - split < 6 or len({r["correct"] for r in rows[:split]}) < 2:
        return {**report, "status": "insufficient_data", "required": "20 training and 6 later holdout trials; both outcomes in training"}
    train = rows[:split]
    candidates = [BKTParams()]
    for initial, learn, slip, guess, forget in itertools.product((.1, .3), (.05, .15, .3), (.05, .15), (.1, .25), (0., .02, .05)):
        candidates.append(BKTParams(p_init=initial, p_learn=learn, p_slip=slip, p_guess=guess, p_forget_per_day=forget))
    selected = min(candidates, key=lambda p: _score(train, p)["log_loss"])
    baseline = _score(rows, BKTParams(), start=split)
    candidate = _score(rows, selected, start=split)
    return {**report, "status": "evaluated", "training_count": split,
            "holdout_count": len(rows) - split, "holdout_from": rows[split]["timestamp"],
            "candidate_params": asdict(selected), "default_holdout": baseline,
            "candidate_holdout": candidate,
            "candidate_improves_holdout": candidate["log_loss"] < baseline["log_loss"] and candidate["brier"] < baseline["brier"]}

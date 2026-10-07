from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

# Bayesian Knowledge Tracing (Corbett & Anderson 1995) with a forgetting term
# (Cepeda et al. 2006): a per-capability probability-of-mastery estimate that
# rises with independent successes, falls with errors, and decays over time.
# Mastery is NOT a single number: it also requires breadth (>=2 scopes) and at
# least one delayed/transfer demonstration (Kulik et al. 1990; Barnett & Ceci 2002).

TRANSFER_SCOPES = {"near_transfer", "far_transfer", "delayed_independent_performance"}

from .projection import counts_as_strong  # one quality predicate across the runtime


@dataclass
class BKTParams:
    p_init: float = 0.2        # prior P(known)
    p_learn: float = 0.15      # P(transition unknown->known) per opportunity
    p_slip: float = 0.1        # P(wrong | known)
    p_guess: float = 0.2       # P(right | unknown)
    p_forget_per_day: float = 0.02   # daily decay of P(known)
    mastery_threshold: float = 0.95
    min_distinct_scopes: int = 2


@dataclass
class MasteryEstimate:
    capability: str
    p_known: float
    observations: int
    distinct_scopes: int
    has_transfer_or_delayed: bool
    mastered: bool
    reasons: list[str]

    def to_dict(self):
        return asdict(self)


def _parse(ts):
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def bkt_step(p: float, correct, params: BKTParams) -> float:
    """``correct`` may be a bool or a fraction in [0,1] (partial credit): the
    posterior blends the correct/incorrect updates by that weight."""
    c = 1.0 if correct is True else 0.0 if correct is False else float(correct)
    c = max(0.0, min(1.0, c))
    slip, guess, learn = params.p_slip, params.p_guess, params.p_learn
    num_c = p * (1 - slip)
    den_c = num_c + (1 - p) * guess
    post_c = (num_c / den_c) if den_c > 0 else p
    num_i = p * slip
    den_i = num_i + (1 - p) * (1 - guess)
    post_i = (num_i / den_i) if den_i > 0 else p
    p_cond = c * post_c + (1 - c) * post_i
    return p_cond + (1 - p_cond) * learn


def estimate(capability: str, observations: list[dict], params: BKTParams | None = None) -> MasteryEstimate:
    """observations: ordered list of {correct: bool, timestamp: iso, scope: str}."""
    params = params or BKTParams()
    p = params.p_init
    scopes: set[str] = set()
    transfer = False
    last_t = None
    for obs in observations:
        t = _parse(obs.get("timestamp"))
        if last_t and t:
            gap_days = max(0.0, (t - last_t).total_seconds() / 86400.0)
            # forgetting decays P(known) toward 0 with elapsed time
            p = p * ((1 - params.p_forget_per_day) ** gap_days)
        if t:
            last_t = t
        p = bkt_step(p, obs.get("correct"), params)
        sc = obs.get("scope")
        # Breadth and transfer count ONLY qualified, correct demonstrations:
        # a failed transfer, or an assisted "correct", does not satisfy mastery.
        w = obs.get("correct")
        if "qualified" in obs:
            is_strong_correct = bool(obs.get("qualified"))
        else:
            # low-level API without quality metadata: a full-credit correct counts
            is_strong_correct = (w is True) or (isinstance(w, (int, float)) and w >= 1.0)
        if sc and is_strong_correct:
            scopes.add(sc)
            if sc in TRANSFER_SCOPES:
                transfer = True
    p = max(0.0, min(1.0, p))
    reasons = []
    threshold_ok = p >= params.mastery_threshold
    breadth_ok = len(scopes) >= params.min_distinct_scopes
    if not threshold_ok:
        reasons.append(f"p_known_below_threshold:{p:.3f}<{params.mastery_threshold}")
    if not breadth_ok:
        reasons.append(f"needs_{params.min_distinct_scopes}_distinct_scopes_have_{len(scopes)}")
    if not transfer:
        reasons.append("needs_transfer_or_delayed_demonstration")
    mastered = threshold_ok and breadth_ok and transfer
    if mastered:
        reasons.append("mastery_criteria_met")
    return MasteryEstimate(capability, round(p, 4), len(observations), len(scopes), transfer, mastered, reasons)


def observations_from_projection_caps(evidence_records: list[dict], capability: str) -> list[dict]:
    """Build ordered BKT observations for one capability from raw evidence records."""
    obs = []
    for r in evidence_records:
        if r.get("record_type") != "evidence":
            continue
        e = r["payload"]
        if e.get("capability_id") != capability:
            continue
        outcome = e.get("outcome")
        if outcome not in {"correct", "incorrect", "partial"}:
            continue  # 'unknown' is not a graded observation
        qualified = counts_as_strong(e)          # correct+unassisted+strong+eligible+no heavy support
        # BKT weight: a strong independent success is full evidence; a correct but
        # ASSISTED/weak/non-eligible answer is downweighted (knowledge shown, with help);
        # partial is half; incorrect is zero.
        if outcome == "correct":
            weight = 1.0 if qualified else 0.6
        elif outcome == "partial":
            weight = 0.5
        else:
            weight = 0.0
        obs.append({
            "correct": weight,
            "correct_outcome": outcome == "correct",
            "qualified": qualified,
            "timestamp": r.get("timestamp"),
            "scope": e.get("scope"),
        })
    return obs

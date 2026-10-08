from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
from .evidence_history import ordered_observations, unique_evidence, score_fraction, trial_keys

# BKT-style estimate with configurable exponential decay. These are uncalibrated
# engineering defaults, not measured probabilities of a particular learner's skill.
# Current mastery also requires qualified breadth and transfer/delayed evidence.

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

    def __post_init__(self):
        for name in ("p_init", "p_learn", "p_slip", "p_guess", "p_forget_per_day", "mastery_threshold"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite probability in [0, 1]")
        if type(self.min_distinct_scopes) is not int or self.min_distinct_scopes < 1:
            raise ValueError("min_distinct_scopes must be a positive integer")


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
    c = score_fraction(correct)
    p = score_fraction(p)
    slip, guess, learn = params.p_slip, params.p_guess, params.p_learn
    num_c = p * (1 - slip)
    den_c = num_c + (1 - p) * guess
    post_c = (num_c / den_c) if den_c > 0 else p
    num_i = p * slip
    den_i = num_i + (1 - p) * (1 - guess)
    post_i = (num_i / den_i) if den_i > 0 else p
    p_cond = c * post_c + (1 - c) * post_i
    return p_cond + (1 - p_cond) * learn


def estimate(capability: str, observations: list[dict], params: BKTParams | None = None, *, now: datetime | None = None) -> MasteryEstimate:
    """observations: ordered list of {correct: bool, timestamp: iso, scope: str}."""
    params = params or BKTParams()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    observations = ordered_observations(observations, now)
    p = params.p_init
    scopes: set[str] = set()
    transfer = False
    last_t = None
    latest_qualified = False
    for obs in observations:
        t = _parse(obs.get("timestamp"))
        gap_days = max(0.0, (t - last_t).total_seconds() / 86400.0) if last_t else 0.0
        if last_t and t:
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
            is_strong_correct = obs.get("qualified") is True
        else:
            # low-level API without quality metadata: a full-credit correct counts
            is_strong_correct = (w is True) or (isinstance(w, (int, float)) and w >= 1.0)
        is_strong_correct = is_strong_correct and float(w or 0) >= 1.0
        latest_qualified = is_strong_correct
        if sc and is_strong_correct:
            # A label alone does not establish a delayed demonstration. Require
            # at least 24 hours without another graded touch of this capability.
            scope_valid = sc != "delayed_independent_performance" or gap_days >= 1.0
            if scope_valid:
                scopes.add(sc)
            if sc in TRANSFER_SCOPES and scope_valid:
                transfer = True
    if last_t:
        p *= (1 - params.p_forget_per_day) ** max(0.0, (now - last_t).total_seconds() / 86400.0)
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
    if not latest_qualified:
        reasons.append("needs_current_qualified_success")
    mastered = threshold_ok and breadth_ok and transfer and latest_qualified
    if mastered:
        reasons.append("mastery_criteria_met")
    return MasteryEstimate(capability, round(p, 4), len(observations), len(scopes), transfer, mastered, reasons)


def observations_from_projection_caps(evidence_records: list[dict], capability: str) -> list[dict]:
    """Build ordered BKT observations for one capability from raw evidence records."""
    obs = []
    records = list(unique_evidence(evidence_records))
    groups = trial_keys(records)
    # Re-running a named task replaces that task's result in the knowledge model;
    # it does not manufacture independent learning opportunities.
    latest_attempt = {}
    for i, r in enumerate(records):
        e = r["payload"]
        if e.get("capability_id") == capability:
            latest_attempt[groups[i]] = i
    for i, r in enumerate(records):
        e = r["payload"]
        if e.get("capability_id") != capability:
            continue
        if latest_attempt[groups[i]] != i:
            continue
        outcome = e.get("outcome")
        if e.get("mastery_eligible") is False:
            continue
        if outcome not in {"correct", "incorrect", "partial"}:
            continue  # 'unknown' is not a graded observation
        qualified = counts_as_strong(e, r.get("provenance"))  # correct+unassisted+strong+eligible+no heavy support+runtime-checked
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

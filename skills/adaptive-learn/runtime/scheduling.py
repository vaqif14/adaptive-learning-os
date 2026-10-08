from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from .evidence_history import ordered_observations, score_fraction

# Conservative, deterministic spacing heuristic; not a fitted HLR or FSRS model.
# Only due, independent recalls extend spacing. Massed practice and assisted
# attempts cannot push a due date back. Parameters require learner-data validation.

MIN_INTERVAL_DAYS = 0.5
MAX_INTERVAL_DAYS = 365.0


@dataclass
class ReviewState:
    capability: str
    next_review_at: str
    interval_days: float
    recalls: int
    lapses: int
    due: bool
    apply_by: str | None = None
    at_risk_48h: bool = False

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


def half_life_days(recalls: int, lapses: int, base: float = 1.0) -> float:
    # 2^(successes - lapses), clamped. Each clean recall ~doubles the interval;
    # each lapse halves it.
    interval = base * (2.0 ** max(-20, min(20, recalls - lapses)))
    return max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, interval))


def schedule(capability: str, observations: list[dict], *, now: datetime | None = None) -> ReviewState:
    """observations: ordered {correct: bool, timestamp: iso}. Returns next due time."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    recalls = 0
    lapses = 0
    interval = 1.0
    nxt = None
    first_t = None
    last_recall = None
    for obs in ordered_observations(observations, now):
        t = _parse(obs.get("timestamp"))
        if first_t is None:
            first_t = t
            nxt = t  # an unproven capability is due now
        c = obs.get("correct")
        val = score_fraction(c)
        clean = val >= 1.0 and obs.get("qualified", True) is True
        if clean and (last_recall is None or t >= nxt):
            recalls += 1
            interval = min(MAX_INTERVAL_DAYS, 2.0 if last_recall is None else interval * 2)
            nxt = t + timedelta(days=interval)
            last_recall = t
        elif val <= 0.0:
            lapses += 1
            interval = max(MIN_INTERVAL_DAYS, interval / 2)
            nxt = min(nxt, t + timedelta(days=interval))
        # Partial, assisted, or early repeated recalls leave the deadline intact.
    nxt = nxt or now
    anchor = last_recall or first_t
    # Product reminder, not a scientifically established 48-hour forgetting law.
    apply_by = (anchor + timedelta(hours=48)) if anchor else None
    at_risk = bool(apply_by and now >= apply_by and recalls <= 1)
    return ReviewState(
        capability=capability,
        next_review_at=nxt.isoformat(),
        interval_days=round(interval, 3),
        recalls=recalls,
        lapses=lapses,
        due=now >= nxt,
        apply_by=apply_by.isoformat() if apply_by else None,
        at_risk_48h=at_risk,
    )


def due_queue(per_capability_obs: dict[str, list[dict]], *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    states = [schedule(cap, obs, now=now) for cap, obs in per_capability_obs.items()]
    states.sort(key=lambda s: s.next_review_at)  # soonest first
    return [s.to_dict() for s in states]

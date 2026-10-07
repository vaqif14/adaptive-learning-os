from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

# Spaced-review scheduling via half-life regression (Settles & Meeder 2016): the
# review interval grows with successful recalls and shrinks after lapses. This is
# the FSRS-family idea in a dependency-free form. Review is due when now >= the
# computed next-review time.

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
    interval = base * (2.0 ** (recalls - lapses))
    return max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, interval))


def schedule(capability: str, observations: list[dict], *, now: datetime | None = None) -> ReviewState:
    """observations: ordered {correct: bool, timestamp: iso}. Returns next due time."""
    now = now or datetime.now(timezone.utc)
    recalls = 0
    lapses = 0
    last_t = None
    for obs in observations:
        c = obs.get("correct")
        val = 1.0 if c is True else 0.0 if c is False else float(c or 0.0)
        if val >= 1.0:
            recalls += 1                      # full, independent recall extends spacing
        elif val <= 0.0:
            lapses += 1
            recalls = max(0, recalls - 1)     # a lapse shortens it
        # a PARTIAL recall (0 < val < 1) neither extends nor shortens: it holds.
        t = _parse(obs.get("timestamp"))
        if t:
            last_t = t
    interval = half_life_days(recalls, lapses)
    anchor = last_t or now
    nxt = anchor + timedelta(days=interval)
    # 48-hour "apply-or-lose" window: a freshly-learned concept (<=1 clean recall)
    # decays fast unless applied/reviewed within 48h of the last touch (Dan Martell).
    apply_by = (anchor + timedelta(hours=48)) if last_t else None
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

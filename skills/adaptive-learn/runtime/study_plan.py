from __future__ import annotations

"""Subject-neutral study-schedule planner.

Spaced review (``scheduling.py``) answers "when should I review a thing I already
learned?". This module answers the different, up-front question the learner asks:
"given my roadmap and how much time I have each week, when do I study what?".

It is deterministic and topic-agnostic: it only sees ordered roadmap node ids and
a weekly time budget, so it works for any subject (a language, history, music —
not just code). Prerequisite order is assumed already encoded in the node order
(the roadmap validator enforces an acyclic, prerequisite-respecting order).
"""

from dataclasses import dataclass, asdict, field
from datetime import date, timedelta


WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class StudyAvailability:
    days_per_week: int = 3
    minutes_per_session: int = 45
    preferred_days: list[int] = field(default_factory=list)  # 0=Mon..6=Sun; empty = auto-spread

    def validate(self) -> list[str]:
        errors = []
        if not 1 <= self.days_per_week <= 7:
            errors.append("days_per_week must be 1..7")
        if self.minutes_per_session < 5:
            errors.append("minutes_per_session must be >= 5")
        if any(d < 0 or d > 6 for d in self.preferred_days):
            errors.append("preferred_days must be 0..6 (Mon..Sun)")
        if self.preferred_days and len(set(self.preferred_days)) < self.days_per_week:
            errors.append("preferred_days has fewer distinct days than days_per_week")
        return errors


def _session_weekdays(avail: StudyAvailability) -> list[int]:
    if avail.preferred_days:
        return sorted(set(avail.preferred_days))[: avail.days_per_week]
    # Auto-spread across the week (floor spacing: 3/wk -> Mon, Wed, Fri).
    n = avail.days_per_week
    return sorted({(i * 7) // n for i in range(n)})[:n] or [0]


def _dates(start: date, weekdays: list[int], count: int) -> list[date]:
    """The next ``count`` calendar dates falling on the allowed weekdays."""
    out: list[date] = []
    d = start
    guard = 0
    while len(out) < count and guard < count * 14 + 14:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
        guard += 1
    return out


@dataclass
class StudySession:
    index: int
    date: str
    weekday: str
    node_ids: list[str]
    minutes: int
    kind: str = "learn"

    def to_dict(self):
        return asdict(self)


def plan_schedule(
    node_ids: list[str],
    availability: StudyAvailability,
    *,
    start_date: str,
    minutes_per_node: int = 30,
    deadline: str | None = None,
) -> dict:
    """Lay roadmap nodes onto dated study sessions within a weekly time budget."""
    errs = availability.validate()
    if errs:
        raise ValueError("; ".join(errs))
    if not node_ids:
        raise ValueError("no roadmap nodes to schedule")
    if minutes_per_node < 5:
        raise ValueError("minutes_per_node must be >= 5")
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        raise ValueError("start_date must be ISO YYYY-MM-DD")

    per_session = max(1, availability.minutes_per_session // minutes_per_node)
    # Pack nodes into sessions, each holding up to `per_session` nodes, in order.
    chunks: list[list[str]] = [
        node_ids[i:i + per_session] for i in range(0, len(node_ids), per_session)
    ]
    weekdays = _session_weekdays(availability)
    dates = _dates(start, weekdays, len(chunks))

    sessions = [
        StudySession(
            index=i,
            date=dates[i].isoformat(),
            weekday=WEEKDAYS[dates[i].weekday()],
            node_ids=chunk,
            minutes=len(chunk) * minutes_per_node,
        ).to_dict()
        for i, chunk in enumerate(chunks)
    ]

    ends_on = dates[-1].isoformat() if dates else start.isoformat()
    total_minutes = len(node_ids) * minutes_per_node
    weeks = (len(chunks) + availability.days_per_week - 1) // availability.days_per_week

    result = {
        "nodes": len(node_ids),
        "sessions": sessions,
        "session_count": len(sessions),
        "nodes_per_session": per_session,
        "minutes_per_node": minutes_per_node,
        "minutes_per_session": availability.minutes_per_session,
        "days_per_week": availability.days_per_week,
        "weekdays": [WEEKDAYS[w] for w in weekdays],
        "total_study_minutes": total_minutes,
        "estimated_weeks": weeks,
        "starts_on": start.isoformat(),
        "ends_on": ends_on,
    }

    if deadline:
        try:
            dl = date.fromisoformat(deadline)
        except ValueError:
            raise ValueError("deadline must be ISO YYYY-MM-DD")
        feasible = dates[-1] <= dl if dates else True
        result["deadline"] = deadline
        result["deadline_feasible"] = feasible
        if not feasible:
            weeks_to_dl = max(1, ((dl - start).days // 7) + 1)
            needed = (len(chunks) + weeks_to_dl - 1) // weeks_to_dl
            result["note"] = (
                f"ends_on {ends_on} is after the deadline; increase days_per_week "
                f"(need about {max(needed, availability.days_per_week + 1)}/week) "
                f"or minutes_per_session, or extend the deadline"
            )
        else:
            result["note"] = "schedule fits before the deadline"
    return result

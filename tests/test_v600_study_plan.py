"""Subject-neutral study-schedule planner."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.study_plan import StudyAvailability, plan_schedule  # noqa: E402

NODES = [f"n{i}" for i in range(1, 11)]  # 10 ordered nodes, any subject


def test_packs_nodes_by_time_budget():
    # 45 min/session, 15 min/node -> 3 nodes per session -> 10 nodes = 4 sessions.
    avail = StudyAvailability(days_per_week=3, minutes_per_session=45)
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=15)
    assert r["nodes_per_session"] == 3
    assert r["session_count"] == 4
    assert sum(len(s["node_ids"]) for s in r["sessions"]) == 10


def test_order_is_preserved():
    avail = StudyAvailability(days_per_week=2, minutes_per_session=60)
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=30)
    flat = [nid for s in r["sessions"] for nid in s["node_ids"]]
    assert flat == NODES  # prerequisite order never reordered


def test_dates_fall_on_spread_weekdays():
    avail = StudyAvailability(days_per_week=3, minutes_per_session=30)
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=30)  # Mon
    assert r["weekdays"] == ["mon", "wed", "fri"]
    for s in r["sessions"]:
        assert s["weekday"] in r["weekdays"]


def test_preferred_days_respected():
    avail = StudyAvailability(days_per_week=2, minutes_per_session=30, preferred_days=[5, 6])  # Sat, Sun
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=30)
    assert set(r["weekdays"]) == {"sat", "sun"}


def test_deadline_feasible_true():
    avail = StudyAvailability(days_per_week=5, minutes_per_session=60)
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=30, deadline="2026-12-31")
    assert r["deadline_feasible"] is True


def test_deadline_infeasible_flags_and_advises():
    avail = StudyAvailability(days_per_week=1, minutes_per_session=30)
    r = plan_schedule(NODES, avail, start_date="2026-10-12", minutes_per_node=30, deadline="2026-10-15")
    assert r["deadline_feasible"] is False
    assert "deadline" in r["note"]


def test_empty_nodes_raises():
    with pytest.raises(ValueError):
        plan_schedule([], StudyAvailability(), start_date="2026-10-12")


def test_bad_availability_raises():
    with pytest.raises(ValueError):
        plan_schedule(NODES, StudyAvailability(days_per_week=9), start_date="2026-10-12")


def test_bad_date_raises():
    with pytest.raises(ValueError):
        plan_schedule(NODES, StudyAvailability(), start_date="12-10-2026")

"""Probe modules of the regenerated 10-rule gate (one module per rule, r01 .. r10)."""
from __future__ import annotations

from . import r01, r02, r03, r04, r05, r06, r07, r08, r09, r10

MODULES = (r01, r02, r03, r04, r05, r06, r07, r08, r09, r10)


def all_probes() -> list:
    """Every registered probe, ordered by rule, then definition order (an explicit ``order`` sorts last)."""
    return [p for _pos, _order, _idx, p in sorted(
        ((position, p.order, index, p) for position, module in enumerate(MODULES) for index, p in enumerate(module.PROBES)),
        key=lambda row: row[:3])]

from __future__ import annotations

from dataclasses import dataclass, asdict, field


@dataclass
class CoverageCell:
    dimension: str
    value: str
    attempts: int = 0
    independent_successes: int = 0


@dataclass
class PracticeCoverage:
    capability_id: str
    cells: list[CoverageCell] = field(default_factory=list)

    def to_dict(self):
        return {"capability_id": self.capability_id, "cells": [asdict(c) for c in self.cells]}

    def least_covered(self) -> CoverageCell | None:
        if not self.cells:
            return None
        return min(self.cells, key=lambda c: (c.independent_successes, c.attempts, c.dimension, c.value))


def choose_practice_variation(coverage: PracticeCoverage) -> dict:
    cell = coverage.least_covered()
    if cell is None:
        return {"strategy": "generate_new_surface_variation", "reason": "coverage_empty"}
    return {
        "strategy": "target_undercovered_cell",
        "dimension": cell.dimension,
        "value": cell.value,
        "reason": "avoid_repetitive_surface_pattern_loop",
    }

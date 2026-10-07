import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.source_scope import (
    SourceScopePolicy, CoverageClaim, analyze_source_gaps, hybrid_source_plan, behavior_for,
)


class StrategyUnificationTests(unittest.TestCase):
    def _report(self, strategy):
        p = SourceScopePolicy.from_strategy(strategy)
        return analyze_source_gaps(["a", "b"], [CoverageClaim("a", "covered"), CoverageClaim("b", "missing")], p), p

    def test_strict_closed_world_never_discovers(self):
        report, p = self._report("strict_closed_world")
        plan = hybrid_source_plan(report, "t", available_source_count=1, strategy=p.strategy)
        self.assertIsNone(plan["discovery_request"])
        self.assertEqual(plan["phase"], "continue_with_covered_scope")
        self.assertFalse(plan["ask_user"])
        self.assertFalse(p.allow_external_discovery)

    def test_curated_acquisition_recommends_not_discovers(self):
        report, p = self._report("curated_acquisition")
        plan = hybrid_source_plan(report, "t", available_source_count=1, strategy=p.strategy)
        self.assertIsNone(plan["discovery_request"])
        self.assertEqual(plan["phase"], "acquire_if_critical")
        self.assertTrue(plan["ask_user"])

    def test_adaptive_hybrid_still_discovers(self):
        report, p = self._report("adaptive_hybrid")
        plan = hybrid_source_plan(report, "t", available_source_count=1, strategy=p.strategy)
        self.assertIsNotNone(plan["discovery_request"])
        self.assertEqual(plan["phase"], "discover")

    def test_behavior_mapping_consistent(self):
        self.assertFalse(behavior_for("strict_closed_world")["discover"])
        self.assertTrue(behavior_for("adaptive_hybrid")["discover"])
        self.assertTrue(behavior_for("unknown_strategy")["discover"])  # safe default


if __name__ == "__main__":
    unittest.main()

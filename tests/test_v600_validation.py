import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.contracts import LearningContract
from runtime.adapters.registry import AdapterRegistry
from runtime.source_scope import analyze_source_gaps, hybrid_source_plan, SourceScopePolicy, CoverageClaim


class ContractValidationTests(unittest.TestCase):
    def test_string_constraint_not_char_split(self):
        c = LearningContract.from_dict({"goal": "x", "constraints": "abc"})
        self.assertEqual(c.constraints, ["abc"])  # not ['a','b','c']

    def test_bogus_goal_mode_defaults(self):
        c = LearningContract.from_dict({"goal": "x", "goal_mode": "bogus"})
        self.assertEqual(c.goal_mode, "learn")

    def test_valid_goal_mode_kept(self):
        c = LearningContract.from_dict({"goal": "x", "goal_mode": "deliver_artifact"})
        self.assertEqual(c.goal_mode, "deliver_artifact")


class AdapterMatchTests(unittest.TestCase):
    def setUp(self):
        self.reg = AdapterRegistry()

    def test_pipeline_does_not_match_pip(self):
        dc = self.reg.match("data pipeline design")
        self.assertNotEqual(dc.adapter_id, "python-reference")

    def test_python_topic_still_matches(self):
        dc = self.reg.match("python programming")
        self.assertEqual(dc.adapter_id, "python-reference")


class DiscoveryTypeTests(unittest.TestCase):
    def _report(self):
        p = SourceScopePolicy.from_strategy("adaptive_hybrid")
        return analyze_source_gaps(["c"], [CoverageClaim("c", "missing")], p)

    def test_string_usable_does_not_close_gap(self):
        plan = hybrid_source_plan(self._report(), "t", available_source_count=0,
                                  discovery_results=[{"closes_gaps": ["c"], "usable": "false"}], strategy="adaptive_hybrid")
        self.assertEqual(plan["material_classes"]["missing"]["count"], 1)  # string "false" != True

    def test_string_closes_gaps_ignored(self):
        plan = hybrid_source_plan(self._report(), "t", available_source_count=0,
                                  discovery_results=[{"closes_gaps": "c", "usable": True}], strategy="adaptive_hybrid")
        self.assertEqual(plan["material_classes"]["missing"]["count"], 1)  # bare string ignored


if __name__ == "__main__":
    unittest.main()

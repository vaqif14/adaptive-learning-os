import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.contracts import LearningContract, LearningResources, SourcePolicy
from runtime.session import SessionKernel
from runtime.source_scope import (
    SourceScopePolicy, CoverageClaim, analyze_source_gaps,
    source_boundary_gate, hybrid_source_plan,
)
from runtime.utils import read_json


class V550Tests(unittest.TestCase):
    def test_default_contract_does_not_require_user_material(self):
        c = LearningContract(goal="learn distributed systems")
        self.assertEqual(c.resources.learner_supplied, [])
        self.assertEqual(c.source_policy.strategy, "adaptive_hybrid")
        self.assertFalse(c.source_policy.require_user_material_each_session)
        self.assertTrue(c.source_policy.discover_missing_materials)

    def test_no_material_still_resolves_source_gate(self):
        d = source_boundary_gate(SourceScopePolicy(), mode="learn", has_supplied_resources=False)
        self.assertEqual(d.status, "resolved")
        self.assertEqual(d.strategy, "adaptive_hybrid")
        self.assertIn("no_learner_materials_present_runtime_will_bootstrap_sources", d.reasons)

    def test_available_materials_are_used_and_gaps_discovered(self):
        p = SourceScopePolicy()
        report = analyze_source_gaps(
            ["replication", "consensus"],
            [CoverageClaim("replication", "covered"), CoverageClaim("consensus", "missing")],
            p,
        )
        self.assertEqual(report.next_action, "discover_then_acquire_if_needed")
        plan = hybrid_source_plan(report, "distributed systems", available_source_count=2)
        self.assertEqual(plan["material_classes"]["available"]["learner_supplied_or_existing"], 2)
        self.assertEqual(plan["material_classes"]["missing"]["capabilities"], ["consensus"])
        self.assertIn("discover_authoritative_open_sources_for_partial_or_missing_capabilities", plan["automatic_actions"])
        self.assertEqual(plan["phase"], "discover")
        self.assertFalse(plan["ask_user"])

    def test_discovery_can_resolve_gap_without_user_upload(self):
        p = SourceScopePolicy()
        report = analyze_source_gaps(["consensus"], [CoverageClaim("consensus", "missing")], p)
        plan = hybrid_source_plan(
            report,
            "distributed systems",
            available_source_count=0,
            discovery_results=[{"title":"Raft paper", "closes_gaps":["consensus"], "usable":True}],
        )
        self.assertEqual(plan["material_classes"]["missing"]["count"], 0)
        self.assertFalse(plan["ask_user"])
        self.assertEqual(plan["material_classes"]["available"]["runtime_discovered"], 1)

    def test_unresolved_closed_gap_requests_user_only_as_fallback(self):
        p = SourceScopePolicy()
        report = analyze_source_gaps(["specialized_topic"], [], p)
        plan = hybrid_source_plan(report, "specialized domain", available_source_count=0, discovery_results=[])
        self.assertTrue(plan["ask_user"])
        self.assertEqual(plan["acquisition_required_for"], ["specialized_topic"])
        self.assertIn("critical_unresolved_sources", plan["ask_user_only_for"])

    def test_session_persists_source_policy_without_resources(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            sess = k.start("distributed systems", LearningContract(goal="learn storage internals"))
            sp = read_json(k.dir(sess["session_id"]) / "source-policy.json")
            self.assertEqual(sp["strategy"], "adaptive_hybrid")
            self.assertFalse(sp["require_user_material_each_session"])

    def test_strict_mode_remains_available_when_explicitly_requested(self):
        c = LearningContract(
            goal="study only my internal handbook",
            resources=LearningResources(learner_supplied=["handbook.pdf"]),
            source_policy=SourcePolicy(
                strategy="strict_closed_world",
                discover_missing_materials=False,
                recommend_acquisition_for_unresolved=False,
            ),
        )
        self.assertEqual(c.source_policy.strategy, "strict_closed_world")

    def test_enterprise_source_provider_is_not_part_of_consumer_release(self):
        self.assertIn("invalid_source_provider", SourceScopePolicy(provider="notebook_enterprise").validate())


if __name__ == "__main__":
    unittest.main()

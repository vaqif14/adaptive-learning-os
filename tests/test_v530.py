import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.cognitive_delegation import CognitiveWorkContract, DelegationContext, evaluate_delegation
from runtime.challenge import ChallengeContext, choose_challenge_gate
from runtime.coverage import CoverageCell, PracticeCoverage, choose_practice_variation
from runtime.curriculum import CurriculumContext, curriculum_authority_gate
from runtime.source_governance import SourceRecord, SourceRights, evaluate_source_use
from runtime.accessibility import support_effect_on_independence
from runtime.simulation import SimulationFidelity, cap_simulation_scope
from runtime.governance import ToolRequest, authorize_tool
from runtime.capability_registry import get_capability
from runtime.policy import TeachingContext, choose_move
from runtime.router import RouteContext, route_message
from runtime.intents import INTENT_CATALOG
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.evidence_semantics import normalize_evidence_semantics
from runtime.primitives import PrimitiveRegistry
from runtime.utils import read_json


class V530Tests(unittest.TestCase):
    def test_delivery_intent_exists(self):
        self.assertIn("deliver_artifact", INTENT_CATALOG)

    def test_delivery_request_not_misreported_as_mastery(self):
        r = route_message("Create the final report for me", RouteContext())
        self.assertEqual(r.study_intent, "deliver_artifact")
        d = choose_move(TeachingContext(mode=r.mode, route=r.route, study_intent=r.study_intent, goal_mode="deliver_artifact"))
        self.assertEqual(d.move, "deliver_with_governance")
        self.assertFalse(d.mastery_evidence_eligible)

    def test_cognitive_replacement_blocked_in_learning_mode(self):
        c = CognitiveWorkContract(
            target_capability="architecture_reasoning",
            protected_cognition=["compare_tradeoffs", "justify_decision"],
            delegable_work=["formatting"],
            independence_required=True,
        )
        d = evaluate_delegation(DelegationContext(
            goal_mode="learn",
            ai_role="replace",
            requested_work=["compare_tradeoffs"],
            support_kind="cognitive_delegation",
            contract=c,
        ))
        self.assertFalse(d.allowed)
        self.assertIn("compare_tradeoffs", d.protected_overlap)

    def test_delivery_mode_can_delegate_more_work(self):
        c = CognitiveWorkContract("x", ["reason"], [], True)
        d = evaluate_delegation(DelegationContext("deliver_artifact", "replace", ["reason"], "cognitive_delegation", c))
        self.assertTrue(d.allowed)

    def test_accessibility_support_does_not_lower_independence_by_itself(self):
        d = support_effect_on_independence("accessibility")
        self.assertFalse(d["changes_independence"])

    def test_pedagogical_support_changes_independence_semantics(self):
        d = support_effect_on_independence("pedagogical")
        self.assertTrue(d["changes_independence"])

    def test_policy_protects_cognition(self):
        d = choose_move(TeachingContext(
            mode="learn", route="teach", goal_mode="learn",
            ai_role="replace", requested_ai_work=["choose_hypothesis"],
            protected_cognition=["choose_hypothesis"], support_kind="cognitive_delegation",
        ))
        self.assertEqual(d.move, "preserve_learner_cognition")
        self.assertFalse(d.mastery_evidence_eligible)

    def test_challenge_gate_is_capability_specific_and_requires_task(self):
        d = choose_challenge_gate(ChallengeContext(
            capability_id="decorators",
            fast_track_requested=True,
            authentic_task_available=True,
            deterministic_or_rubric_verifier_available=True,
        ))
        self.assertTrue(d.use_challenge_gate)
        self.assertEqual(d.challenge_type, "verified_authentic_task")

    def test_challenge_gate_does_not_fire_without_authentic_task(self):
        d = choose_challenge_gate(ChallengeContext(capability_id="x", fast_track_requested=True))
        self.assertFalse(d.use_challenge_gate)

    def test_policy_can_select_challenge_gate(self):
        d = choose_move(TeachingContext(
            mode="learn", route="teach", fast_track_requested=True,
            authentic_challenge_available=True, challenge_verifier_available=True,
        ))
        self.assertEqual(d.move, "challenge_gate")
        self.assertEqual(d.evidence_target, "near_transfer")

    def test_curriculum_prefers_authoritative_sequence(self):
        d = curriculum_authority_gate(CurriculumContext(
            authoritative_curriculum_available=True,
            authoritative_source_ids=["official_spec"],
        ))
        self.assertEqual(d.strategy, "adapt_authoritative_sequence")
        self.assertFalse(d.require_research)

    def test_curriculum_researches_when_authority_missing(self):
        d = curriculum_authority_gate(CurriculumContext())
        self.assertEqual(d.strategy, "research_triangulate_then_propose")
        self.assertTrue(d.require_research)

    def test_practice_coverage_targets_undercovered_cell(self):
        cov = PracticeCoverage("cap", [
            CoverageCell("context", "A", attempts=5, independent_successes=3),
            CoverageCell("context", "B", attempts=1, independent_successes=0),
        ])
        d = choose_practice_variation(cov)
        self.assertEqual(d["value"], "B")

    def test_public_access_does_not_imply_training_permission(self):
        src = SourceRecord(
            source_id="s1", uri="https://example.com", source_type="web",
            rights=SourceRights(access_status="public", rights_status="unknown")
        )
        d = evaluate_source_use(src, "train_model")
        self.assertEqual(d.status, "review_required")

    def test_explicit_source_denial_is_enforced(self):
        src = SourceRecord(
            source_id="s1", uri=None, source_type="book",
            rights=SourceRights(permitted_operations={"redistribute": False})
        )
        self.assertEqual(evaluate_source_use(src, "redistribute").status, "deny")

    def test_ai_derived_source_requires_underlying_provenance(self):
        src = SourceRecord(source_id="derived", uri=None, source_type="ai_derived")
        self.assertEqual(evaluate_source_use(src, "summarize").status, "deny")

    def test_ai_derived_source_with_provenance_can_be_reviewed(self):
        src = SourceRecord(source_id="derived", uri=None, source_type="ai_derived", derived_from=["s1"])
        self.assertEqual(evaluate_source_use(src, "summarize").status, "review_required")

    def test_simulation_without_authentic_environment_caps_transfer(self):
        d = cap_simulation_scope("far_transfer", SimulationFidelity(cognitive="high", social="medium"))
        self.assertEqual(d.normalized_scope, "near_transfer")

    def test_authentic_environment_still_does_not_auto_prove_durability(self):
        d = cap_simulation_scope("delayed_independent_performance", SimulationFidelity(
            cognitive="high", social="high", physical="high", authentic_environment=True
        ))
        self.assertEqual(d.normalized_scope, "far_transfer")

    def test_high_risk_tool_requires_authorization(self):
        d = authorize_tool(ToolRequest("executor", "run", high_risk=True, authorized=False))
        self.assertFalse(d.allowed)

    def test_side_effecting_tool_requires_authorization(self):
        d = authorize_tool(ToolRequest("writer", "write", side_effecting=True, authorized=False))
        self.assertFalse(d.allowed)

    def test_tool_capability_registry_marks_writing_as_possible_cognitive_replacement(self):
        self.assertTrue(get_capability("write").can_replace_target_cognition)

    def test_ai_generated_output_is_not_mastery_evidence(self):
        s = normalize_evidence_semantics("ai_generated_output", "strong", "unassisted")
        self.assertFalse(s.mastery_eligible)
        self.assertEqual(s.normalized_strength, "weak")

    def test_language_adapter_is_domain_sensitive(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("English speaking and listening", LearningContract(goal="travel conversation"))
            self.assertEqual(s["adapter_id"], "language-learning")
            domain = read_json(k.dir(s["session_id"]) / "domain-context.json")
            self.assertTrue(domain["authentic_environment"]["required_for_far_transfer"])
            node_ids = {n["id"] for n in domain["competence_graph"]["nodes"]}
            self.assertIn("spontaneous_speaking", node_ids)

    def test_learning_contract_persists_protected_cognition_and_accessibility(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            c = LearningContract(
                goal="learn architecture",
                goal_mode="learn",
                protected_cognition=["compare_tradeoffs"],
                delegable_work=["formatting"],
                accessibility_needs=["text_to_speech"],
                fast_track_requested=True,
            )
            s = k.start("system design", c)
            saved = read_json(k.dir(s["session_id"]) / "learning-contract.json")
            self.assertEqual(saved["protected_cognition"], ["compare_tradeoffs"])
            self.assertEqual(saved["accessibility_needs"], ["text_to_speech"])
            self.assertTrue(saved["fast_track_requested"])

    def test_m4_multimodal_primitives_are_contract_only(self):
        reg = PrimitiveRegistry()
        for pid in ["scenario_simulator", "artifact_workspace", "audio_dialogue", "source_comparison"]:
            p = reg.get(pid)
            self.assertFalse(p.renderer_available)
            self.assertIn("contract_only", p.lifecycle_status)

    def test_no_html_or_js_exists_in_package(self):
        _skip = {".learning", "decks", "build", "node_modules"}  # content/build output, not runtime renderer code
        suffixes = {p.suffix for p in ROOT.rglob("*") if p.is_file() and not (_skip & set(p.relative_to(ROOT).parts))}
        self.assertNotIn(".html", suffixes)
        self.assertNotIn(".js", suffixes)


if __name__ == "__main__":
    unittest.main()

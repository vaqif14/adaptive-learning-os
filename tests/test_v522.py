import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.router import RouteContext, route_message
from runtime.intents import infer_study_intent, INTENT_CATALOG
from runtime.policy import TeachingContext, choose_move
from runtime.contracts import LearningContract, LearningResources
from runtime.session import SessionKernel
from runtime.evidence_semantics import normalize_evidence_semantics
from runtime.review import ReviewContext, choose_review_item
from runtime.primitives import PrimitiveRegistry
from runtime.utils import read_json


class V522Tests(unittest.TestCase):
    def test_study_intent_catalog_contains_real_world_intents(self):
        for intent in [
            "compress_source", "build_plan", "learn_concept", "decompose_task",
            "build_with_help", "critique_artifact", "interpret_question",
            "simulate_performance", "practice_retrieval",
        ]:
            self.assertIn(intent, INTENT_CATALOG)

    def test_compress_intent_routes_direct_by_default(self):
        d = route_message("Summarize this document in five bullets", RouteContext())
        self.assertEqual(d.study_intent, "compress_source")
        self.assertEqual(d.route, "direct")
        self.assertEqual(d.mode, "quick_answer")

    def test_compress_for_learning_routes_teach(self):
        d = route_message("Summarize this so I can learn it", RouteContext())
        self.assertEqual(d.study_intent, "compress_source")
        self.assertEqual(d.route, "teach")
        self.assertEqual(d.mode, "learn")

    def test_compression_never_counts_as_mastery_by_itself(self):
        d = choose_move(TeachingContext(
            mode="quick_answer", route="direct", study_intent="compress_source"
        ))
        self.assertEqual(d.move, "compress_source")
        self.assertFalse(d.mastery_evidence_eligible)
        self.assertIsNone(d.evidence_target)

    def test_learning_compression_requires_reconstruction_followup(self):
        d1 = choose_move(TeachingContext(
            mode="learn", route="teach", study_intent="compress_source", compressed_content_delivered=False
        ))
        self.assertEqual(d1.move, "compress_for_learning")
        self.assertFalse(d1.mastery_evidence_eligible)
        self.assertEqual(d1.follow_up_move, "generative_reconstruction")
        d2 = choose_move(TeachingContext(
            mode="learn", route="teach", study_intent="compress_source", compressed_content_delivered=True
        ))
        self.assertEqual(d2.move, "generative_reconstruction")
        self.assertTrue(d2.mastery_evidence_eligible)

    def test_learning_contract_persists_resources_deadline_and_background_separately(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            c = LearningContract(
                goal="AWS Lambda data processing",
                deadline="2026-11-01T00:00:00Z",
                resources=LearningResources(
                    learner_supplied=["internal_api_docs.md"],
                    canonical=["https://docs.python.org/3/"],
                ),
                stated_background="I am intermediate",
                target_evidence_scope="independent_reproduction",
            )
            s = k.start("Python", c)
            saved = read_json(k.dir(s["session_id"]) / "learning-contract.json")
            self.assertEqual(saved["deadline"], "2026-11-01T00:00:00Z")
            self.assertEqual(saved["resources"]["learner_supplied"], ["internal_api_docs.md"])
            self.assertEqual(saved["stated_background"], "I am intermediate")
            projection = read_json(k.dir(s["session_id"]) / "learner-evidence.json")
            self.assertNotIn("stated_background", projection)

    def test_mcq_format_caps_evidence_strength_at_weak(self):
        s = normalize_evidence_semantics("recognition_mcq", "strong", "unassisted")
        self.assertEqual(s.normalized_strength, "weak")
        self.assertTrue(s.mastery_eligible)

    def test_free_recall_caps_evidence_strength_at_medium(self):
        s = normalize_evidence_semantics("free_recall", "strong", "unassisted")
        self.assertEqual(s.normalized_strength, "medium")

    def test_compression_output_is_not_mastery_eligible(self):
        s = normalize_evidence_semantics("compression_output", "strong", "unassisted")
        self.assertFalse(s.mastery_eligible)
        self.assertEqual(s.normalized_strength, "weak")

    def test_question_interpretation_gate_precedes_diagnosis(self):
        d = choose_move(TeachingContext(
            mode="learn",
            route="deep",
            study_intent="learn_concept",
            repeated_observable_error=True,
            root_cause_changes_intervention=True,
            candidate_causes=3,
            task_ambiguity_signal=True,
        ))
        self.assertEqual(d.move, "clarify_task_intent")
        self.assertEqual(d.diagnostic_level, 0)

    def test_explicit_interpret_question_intent_clarifies_without_diagnosis(self):
        d = choose_move(TeachingContext(
            mode="learn", route="teach", study_intent="interpret_question"
        ))
        self.assertEqual(d.move, "clarify_task_intent")
        self.assertFalse(d.mastery_evidence_eligible)

    def test_review_representation_is_capability_sensitive(self):
        code = choose_review_item(ReviewContext("c1", "code", known_failure_mode="aliasing"))
        theory = choose_review_item(ReviewContext("c2", "theory"))
        terminology = choose_review_item(ReviewContext("c3", "terminology"))
        self.assertEqual(code.representation, "debug_task")
        self.assertEqual(theory.representation, "teach_back")
        self.assertEqual(terminology.representation, "qa_card")

    def test_scenario_simulator_is_registered_but_not_rendered_in_m0_m3(self):
        p = PrimitiveRegistry().get("scenario_simulator")
        self.assertFalse(p.renderer_available)
        self.assertEqual(p.lifecycle_status, "contract_only_m4")
        self.assertIn("learner_response_committed", p.supported_events)

    def test_simulation_intent_routes_deep_and_selects_simulate_move(self):
        r = route_message("Run a mock security interview with me", RouteContext())
        self.assertEqual(r.study_intent, "simulate_performance")
        self.assertEqual(r.route, "deep")
        d = choose_move(TeachingContext(
            mode=r.mode, route=r.route, study_intent=r.study_intent
        ))
        self.assertEqual(d.move, "simulate")
        self.assertTrue(d.mastery_evidence_eligible)

    def test_critique_is_not_auto_mastery_evidence(self):
        d = choose_move(TeachingContext(
            mode="build_with_help", route="teach", study_intent="critique_artifact"
        ))
        self.assertEqual(d.move, "critique")
        self.assertFalse(d.mastery_evidence_eligible)
        self.assertEqual(d.follow_up_move, "learner_revision")


if __name__ == "__main__":
    unittest.main()

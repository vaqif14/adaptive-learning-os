import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.router import RouteContext, route_message
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.policy import TeachingContext, choose_move
from runtime.diagnosis import DiagnosisContext, diagnostic_level, validate_hypotheses
from runtime.progression import ProgressionContext, choose_next_scope
from runtime.verifiers import PythonVerifier, VerifierRegistry
from runtime.utils import read_json


class RuntimeTests(unittest.TestCase):
    def test_quick_answer_routes_direct(self):
        d = route_message("Python dict.get default value nədir?", RouteContext(mode="quick_answer"))
        self.assertEqual(d.route, "direct")

    def test_repetition_alone_does_not_force_deep_route(self):
        d = route_message("still wrong", RouteContext(
            mode="learn", conceptual_error=True, repeated_conceptual_failures=4
        ))
        self.assertEqual(d.route, "teach")
        self.assertIn("repetition_recorded_but_not_sufficient_for_deep_diagnosis", d.reasons)

    def test_decision_sensitive_ambiguity_routes_deep(self):
        d = route_message("why", RouteContext(mode="learn", decision_sensitive_ambiguity=True))
        self.assertEqual(d.route, "deep")

    def test_single_error_not_level2(self):
        self.assertEqual(diagnostic_level(DiagnosisContext(
            repeated_observable_error=True,
            root_cause_changes_intervention=False,
            candidate_causes=3,
        )), 1)

    def test_level2_requires_decision_impact_and_competing_causes(self):
        self.assertEqual(diagnostic_level(DiagnosisContext(True, True, 2)), 2)
        self.assertNotEqual(diagnostic_level(DiagnosisContext(True, True, 1)), 2)

    def test_hypothesis_validation(self):
        good = [{
            "hypothesis_id": "H1",
            "explanation": "narrow",
            "status": "tentative",
            "predicted_observation": "will fail flat copy distinction",
            "probe_action": "ask_flat_copy_prediction",
            "decision_impact": "changes explanation target",
        }]
        self.assertEqual(validate_hypotheses(good), [])
        bad = [{
            "hypothesis_id": "H1", "explanation": "x", "status": "confirmed",
            "predicted_observation": "", "probe_action": "tbd", "decision_impact": "x",
        }]
        self.assertTrue(validate_hypotheses(bad))

    def test_projection_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("unknown subject", LearningContract(goal="learn it"))
            led = k.ledger(s["session_id"])
            led.append("evidence", {
                "evidence_id": "e1",
                "capability_id": "c1",
                "outcome": "correct",
                "independence": "unassisted",
                "strength": "strong",
                "scope": "near_transfer",
                "support_provenance": {},
                "source_event_ids": [],
            })
            d = k.dir(s["session_id"])
            (d / "learner-evidence.json").unlink()
            p = k.rebuild_projection(s["session_id"])
            self.assertIn("c1", p["demonstrated_capabilities"])
            self.assertEqual(p["demonstrated_capabilities"]["c1"]["strong_unassisted_successes"], 1)

    def test_unknown_domain_uses_generic_adapter_without_kernel_change(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("quantum basket weaving", LearningContract(goal="understand"))
            self.assertEqual(s["adapter_id"], "generic-research")
            self.assertEqual(s["anchor_probe"]["status"], "generate")

    def test_python_reference_adapter_and_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("Python references and shallow copy", LearningContract(goal="debug aliasing"))
            self.assertEqual(s["adapter_id"], "python-reference")
            self.assertEqual(s["anchor_probe"]["status"], "ready")
            self.assertEqual(s["anchor_probe"]["probe_id"], "python-reference-aliasing-anchor")

    def test_anchor_deferred_until_goal_known(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("Python references", LearningContract(goal=None))
            self.assertEqual(s["anchor_probe"]["status"], "deferred")
            updated = k.update_contract(s["session_id"], goal="debug aliasing")
            self.assertEqual(updated["anchor_probe"]["status"], "ready")

    def test_quick_answer_skips_anchor(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("Python references", LearningContract(goal="lookup"), mode="quick_answer")
            self.assertEqual(s["anchor_probe"]["status"], "skipped")

    def test_support_heavy_success_requests_independent_reproduction(self):
        d = choose_move(TeachingContext(
            mode="learn", route="teach", prior_support_heavy=True,
            independent_reproduction_available=False,
        ))
        self.assertEqual(d.move, "fade")
        self.assertEqual(d.evidence_target, "independent_reproduction")

    def test_strong_independent_reasoning_can_skip_mechanical_reproduction(self):
        d = choose_move(TeachingContext(
            mode="learn",
            route="teach",
            strong_independent_reasoning_available=True,
            recent_full_solution_seen=False,
        ))
        self.assertEqual(d.move, "transfer")
        self.assertEqual(d.evidence_target, "near_transfer")
        self.assertIn("independent_reproduction", d.skipped_scopes)

    def test_full_solution_blocks_skip(self):
        p = choose_next_scope(ProgressionContext(
            strong_independent_reasoning=True,
            recent_full_solution_seen=True,
        ))
        self.assertEqual(p.next_scope, "independent_reproduction")

    def test_high_stakes_overrides_struggle(self):
        d = choose_move(TeachingContext(mode="learn", route="deep", high_stakes=True, learner_stuck=True))
        self.assertEqual(d.move, "verify_then_explain")

    def test_support_monotonicity_projection(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("unknown", LearningContract(goal="learn"))
            led = k.ledger(s["session_id"])
            led.append("evidence", {
                "evidence_id": "assisted", "capability_id": "cap", "outcome": "correct",
                "independence": "assisted", "strength": "strong", "scope": "supported_completion",
                "support_provenance": {"hints_count": 3}, "source_event_ids": [],
            })
            p1 = k.rebuild_projection(s["session_id"])
            self.assertEqual(p1["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 0)
            led.append("evidence", {
                "evidence_id": "unassisted", "capability_id": "cap", "outcome": "correct",
                "independence": "unassisted", "strength": "strong", "scope": "independent_reproduction",
                "support_provenance": {}, "source_event_ids": [],
            })
            p2 = k.rebuild_projection(s["session_id"])
            self.assertEqual(p2["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 1)

    def test_latency_is_context_only_not_mastery_authority(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("unknown", LearningContract(goal="learn"))
            led = k.ledger(s["session_id"])
            base = {
                "capability_id": "cap", "outcome": "correct", "independence": "unassisted",
                "strength": "strong", "scope": "independent_reproduction", "support_provenance": {},
                "source_event_ids": [],
            }
            led.append("evidence", {"evidence_id": "fast", **base, "context_signals": {
                "response_latency_ms": 1000, "signal_weight": "weak"
            }})
            led.append("evidence", {"evidence_id": "slow", **base, "context_signals": {
                "response_latency_ms": 45000, "signal_weight": "weak"
            }})
            p = k.rebuild_projection(s["session_id"])
            cap = p["demonstrated_capabilities"]["cap"]
            self.assertEqual(cap["strong_unassisted_successes"], 2)
            self.assertEqual(cap["weak_context_signal_count"], 2)

    def test_python_verifier_executes_and_compares_output(self):
        v = PythonVerifier(timeout_sec=1.5)
        result = v.verify("print(2 + 3)", expected_stdout="5")
        self.assertTrue(result.executed)
        self.assertTrue(result.passed)

    def test_python_verifier_rejects_forbidden_import(self):
        v = PythonVerifier(timeout_sec=1.0)
        result = v.verify("import os\nprint(os.getcwd())")
        self.assertFalse(result.policy_valid)
        self.assertFalse(result.executed)

    def test_python_adapter_exposes_real_verifier(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("Python references", LearningContract(goal="learn"))
            domain = read_json(k.dir(s["session_id"]) / "domain-context.json")
            out = VerifierRegistry.verify_python(domain, "print([1, 2])", expected_stdout="[1, 2]")
            self.assertTrue(out["available"])
            self.assertTrue(out["result"]["passed"])

    def test_learning_start_generates_offline_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("Python references", LearningContract(goal="learn"))
            files = [p.suffix for p in k.dir(s["session_id"]).rglob("*") if p.is_file()]
            self.assertIn(".html", files)
            self.assertNotIn(".js", files)


if __name__ == "__main__":
    unittest.main()

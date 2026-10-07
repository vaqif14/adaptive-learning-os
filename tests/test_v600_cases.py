import sys, unittest, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.adapters.registry import AdapterRegistry
from runtime.mastery import estimate, bkt_step, BKTParams
from runtime.session import SessionKernel
from runtime.contracts import LearningContract


class Case5MLAdapterTests(unittest.TestCase):
    def setUp(self):
        self.reg = AdapterRegistry()

    def test_llm_topic_matches_ml_adapter(self):
        dc = self.reg.match("Large Language Models: attention and transformers")
        self.assertEqual(dc.adapter_id, "ai-ml-engineering")

    def test_ml_adapter_has_verifier(self):
        dc = self.reg.match("deep learning neural network training")
        self.assertEqual(dc.verifier.get("id"), "python-low-risk")

    def test_generic_still_default_for_unrelated(self):
        dc = self.reg.match("medieval european history")
        self.assertEqual(dc.adapter_id, "generic-research")


class Case3PartialCreditTests(unittest.TestCase):
    def test_partial_between_incorrect_and_correct(self):
        p = BKTParams().p_init
        self.assertLess(bkt_step(p, False, BKTParams()),
                        bkt_step(p, 0.5, BKTParams()))
        self.assertLess(bkt_step(p, 0.5, BKTParams()),
                        bkt_step(p, True, BKTParams()))

    def test_partial_observation_weight(self):
        def obs(correct, scope="independent_reproduction"):
            return {"correct": correct, "timestamp": "2026-01-01T00:00:00+00:00", "scope": scope}
        all_partial = estimate("c", [obs(0.5) for _ in range(4)]).p_known
        all_correct = estimate("c", [obs(1.0) for _ in range(4)]).p_known
        self.assertLess(all_partial, all_correct)


class Case2UncertaintyTests(unittest.TestCase):
    def test_strong_independent_success_clears_uncertainty(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("t", LearningContract(goal="x"))
            led = k.ledger(s["session_id"])
            # a partial flags uncertainty
            led.append("evidence", {"evidence_id": "p", "capability_id": "cap", "outcome": "partial",
                                    "independence": "assisted", "strength": "medium", "scope": "supported_completion",
                                    "support_provenance": {"hints_count": 2}})
            p1 = k.rebuild_projection(s["session_id"])
            self.assertIn("cap", p1["uncertainties"])
            # a clean unassisted independent success clears it (no transfer required)
            led.append("evidence", {"evidence_id": "s", "capability_id": "cap", "outcome": "correct",
                                    "independence": "unassisted", "strength": "strong", "scope": "independent_reproduction",
                                    "mastery_eligible": True, "support_provenance": {}})
            p2 = k.rebuild_projection(s["session_id"])
            self.assertNotIn("cap", p2["uncertainties"])


if __name__ == "__main__":
    unittest.main()

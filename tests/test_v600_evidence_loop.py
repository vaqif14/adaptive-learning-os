import sys, unittest, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.projection import counts_as_strong
from runtime.evidence_bridge import progress_flags, apply_evidence_to_context
from runtime.learner_model import aggregate_learner_state
from runtime.policy import TeachingContext, choose_move


def ev(**kw):
    base = dict(evidence_id="e", capability_id="cap", outcome="correct", independence="unassisted",
                strength="strong", scope="near_transfer", mastery_eligible=True, support_provenance={})
    base.update(kw)
    return base


class StrongPredicateTests(unittest.TestCase):
    def test_revealed_answer_not_strong(self):
        self.assertFalse(counts_as_strong(ev(support_provenance={"ai_direct_answer_revealed": True})))

    def test_hinted_not_strong(self):
        self.assertFalse(counts_as_strong(ev(support_provenance={"hints_count": 2})))

    def test_supported_completion_scope_not_strong(self):
        self.assertFalse(counts_as_strong(ev(scope="supported_completion")))

    def test_not_mastery_eligible_not_strong(self):
        self.assertFalse(counts_as_strong(ev(mastery_eligible=False)))

    def test_clean_unassisted_is_strong(self):
        self.assertTrue(counts_as_strong(ev()))


class ProjectionDedupeTests(unittest.TestCase):
    def test_duplicate_evidence_id_counted_once(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s = k.start("t", LearningContract(goal="x"))
            led = k.ledger(s["session_id"])
            led.append("evidence", ev(evidence_id="dup", scope="independent_reproduction"))
            led.append("evidence", ev(evidence_id="dup", scope="independent_reproduction"))
            p = k.rebuild_projection(s["session_id"])
            self.assertEqual(p["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 1)


class EvidenceBridgeTests(unittest.TestCase):
    def test_flags_derived_from_projection(self):
        proj = {"demonstrated_capabilities": {"cap": {
            "evidence_count": 3, "strong_unassisted_successes": 2,
            "scope_successes": {"near_transfer": 1, "independent_reproduction": 1}}},
            "uncertainties": []}
        f = progress_flags(proj, "cap")
        self.assertTrue(f["near_transfer_available"])
        self.assertTrue(f["independent_reproduction_available"])
        self.assertTrue(f["strong_independent_reasoning_available"])
        self.assertFalse(f["far_transfer_available"])

    def test_heavy_support_flagged(self):
        proj = {"demonstrated_capabilities": {"cap": {"evidence_count": 5, "strong_unassisted_successes": 0, "scope_successes": {}}}, "uncertainties": ["cap"]}
        f = progress_flags(proj, "cap")
        self.assertTrue(f["prior_support_heavy"])
        self.assertTrue(f["capability_uncertain"])

    def test_apply_to_context_overrides_flags(self):
        proj = {"demonstrated_capabilities": {"cap": {"evidence_count": 1, "strong_unassisted_successes": 1, "scope_successes": {"independent_reproduction": 1}}}, "uncertainties": []}
        ctx = TeachingContext(mode="learn", route="teach", independent_reproduction_available=False)
        ctx, flags = apply_evidence_to_context(ctx, proj, "cap")
        self.assertTrue(ctx.independent_reproduction_available)  # overridden from evidence


class CrossSessionTests(unittest.TestCase):
    def test_capability_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            for _ in range(2):
                s = k.start("t", LearningContract(goal="x"), learner_id="L1")
                led = k.ledger(s["session_id"])
                led.append("evidence", ev(evidence_id="x", scope="independent_reproduction"))
                k.rebuild_projection(s["session_id"])
            agg = aggregate_learner_state(Path(td) / ".learning" / "sessions", learner_id="L1")
            self.assertEqual(agg["sessions_aggregated"], 2)
            self.assertEqual(agg["capabilities"]["cap"]["strong_unassisted_successes"], 2)

    def test_learner_filter(self):
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td))
            s1 = k.start("t", LearningContract(goal="x"), learner_id="A")
            k.ledger(s1["session_id"]).append("evidence", ev(evidence_id="a", scope="independent_reproduction"))
            k.rebuild_projection(s1["session_id"])
            s2 = k.start("t", LearningContract(goal="x"), learner_id="B")
            k.ledger(s2["session_id"]).append("evidence", ev(evidence_id="b", scope="independent_reproduction"))
            k.rebuild_projection(s2["session_id"])
            agg = aggregate_learner_state(Path(td) / ".learning" / "sessions", learner_id="A")
            self.assertEqual(agg["sessions_aggregated"], 1)


if __name__ == "__main__":
    unittest.main()

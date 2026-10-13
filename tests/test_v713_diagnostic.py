"""Diagnostic integrity tests. Fixtures demonstrate policy, not learning efficacy."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_journey as journey
from runtime.course_state import CourseStateError
from test_v712_journey import observation
from test_v700_course_flow import CourseCase


class DiagnosticIntegrityTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.k.init(self.sid)

    def record(self, **changes):
        return self.k.record_diagnostic(self.sid, {"entries": [observation(**changes)]})

    def test_renamed_same_prompt_cannot_complete_a_fresh_two_task_diagnostic(self):
        self.record()
        self.record(id="dx-renamed", prompt="  İki vektorun istiqamətcə yaxınlığını necə müqayisə edərdin?  ")
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["observations"], 1)
        self.assertEqual(result["action"], "fresh_transfer_probe")
        self.assertEqual(result["repeated_task_entry_ids"], ["dx-renamed"])

    def test_repeated_task_help_cannot_restore_claimed_cold_independence(self):
        self.record(assistance=["Worked example given"])
        self.record(id="dx-retry01", response="İndi cavabı bilirəm.")
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["action"], "smaller_independent_probe")
        self.approve()
        evidence = self.k.state(self.sid)["roadmap"]["nodes"][0]["skill_evidence"]
        self.assertEqual(evidence["status"], "assisted_or_uncertain")

    def test_failed_or_uncertain_diagnostic_is_not_a_positive_skill_observation(self):
        for changes, expected in [({"outcome": "incorrect"}, "needs_more_evidence"),
                                  ({"outcome": "partial"}, "needs_more_evidence"),
                                  ({"confidence": "low"}, "assisted_or_uncertain"),
                                  ({"outcome": "unknown"}, "assisted_or_uncertain")]:
            entry = observation(**changes)
            entry.update(journey.diagnostic_attribution(entry))
            state = {"learner_profile": {"diagnostic": [entry]}, "attempts": {}}
            with self.subTest(changes=changes):
                result = journey.skill_evidence(state, "vec-sim", "c1")
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["mastery_claim"])

    def test_blank_capability_observation_is_not_scope_evidence(self):
        self.record(node_id=None)
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["action"], "performance_probe")
        self.assertEqual(result["observations"], 0)
        self.assertEqual(result["unscoped_entry_ids"], ["dx-first"])

    def test_plan_carries_bounded_task_evidence_and_clear_local_limit(self):
        self.record()
        self.record(id="dx-second", prompt="Yeni mətn axtarışı ssenarisində uzunluğu fərqli vektorları müqayisə et.")
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["action"], "ready_to_plan")
        self.assertEqual(result["evidence_entry_ids"], ["dx-first", "dx-second"])
        self.assertEqual(result["calibrated_probability"], None)
        self.assertIn("scope_limit_az", result)
        self.assertNotIn("overall_level", result)

    def test_non_boolean_or_deceptive_attribution_values_are_rejected(self):
        for changes in ({"response": None}, {"rationale": []}, {"confidence": []},
                        {"independence": []}, {"assessor": []}, {"task_kind": []}):
            with self.subTest(changes=changes), self.assertRaises(CourseStateError):
                journey.diagnostic_attribution(observation(**changes))

    def test_diagnostic_success_followed_by_fresh_failure_exposes_mixed_evidence(self):
        self.record()
        self.record(id="dx-second", prompt="Yeni ssenaridə vektor uzunluqlarının müqayisəyə təsirini izah et.",
                    outcome="incorrect", response="Yalnız uzun vektoru seçmək kifayətdir.")
        self.approve()
        evidence = self.k.state(self.sid)["roadmap"]["nodes"][0]["skill_evidence"]
        self.assertEqual(evidence["status"], "mixed_evidence")
        self.assertFalse(evidence["mastery_claim"])

    def test_diagnostic_same_prompt_help_remains_visible_in_success_filter(self):
        first = observation()
        first.update(journey.diagnostic_attribution(first))
        repeated = observation(id="dx-again1", assistance=["Answer shown"])
        repeated.update(journey.diagnostic_attribution(repeated))
        self.assertEqual(journey.independent_diagnostic_successes([first, repeated]), [])

    def test_brief_plan_remains_bounded_when_later_observations_are_recorded(self):
        self.record()
        self.record(id="dx-second", prompt="Bir yeni vektor cütünü izah edərək müqayisə et.")
        self.record(id="dx-third1", prompt="İndi üçüncü müstəqil ssenaridə oxşarlığı tətbiq et.")
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["observations"], 2)
        self.assertEqual(result["total_distinct_observations"], 3)
        self.assertEqual(len(result["evidence_entry_ids"]), 2)


class SkillEvidenceIntegrityTests(unittest.TestCase):
    def state(self, attempts):
        return {"learner_profile": {"diagnostic": []}, "attempts": attempts}

    def attempt(self, **changes):
        return {"attempt_id": "a1", "node_id": "vec-sim", "capability_version": "c1", "status": "finalized",
                "kind": "python", "result": {"outcome": "correct"}, "finalized_at": "2026-10-01T09:00:00Z",
                "evidence_strength": "strong", "support": {"hints_count": 0, "worked_example_shown": False,
                "ai_direct_answer_revealed": False, "conceptual_scaffold": False}, **changes}

    def test_assisted_python_pass_and_missing_provenance_do_not_claim_independence(self):
        helped = self.attempt(support={"hints_count": 1})
        unknown = self.attempt()
        unknown.pop("support")
        for attempt in (helped, unknown):
            with self.subTest(attempt=attempt):
                result = journey.skill_evidence(self.state({"a1": attempt}), "vec-sim", "c1")
                self.assertEqual(result["status"], "assisted_or_uncertain")

    def test_latest_outcome_uses_finalization_time_not_dictionary_order(self):
        latest = self.attempt(attempt_id="a2", result={"outcome": "incorrect"},
                              finalized_at="2026-10-02T09:00:00Z")
        older = self.attempt()
        result = journey.skill_evidence(self.state({"a2": latest, "a1": older}), "vec-sim", "c1")
        self.assertEqual(result["status"], "mixed_evidence")
        self.assertEqual(result["attempt_ids"], ["a1", "a2"])

    def test_weak_python_success_retains_freshness_qualification(self):
        result = journey.skill_evidence(self.state({"a1": self.attempt(evidence_strength="medium",
                    freshness_basis="author_review")}), "vec-sim", "c1")
        self.assertEqual(result["status"], "provisional_observation")
        self.assertFalse(result["mastery_claim"])

    def test_current_scope_only_and_explicit_evidence_counts(self):
        attempts = {"old": self.attempt(attempt_id="old", capability_version="c0"), "a1": self.attempt()}
        result = journey.skill_evidence(self.state(attempts), "vec-sim", "c1")
        self.assertEqual(result["status"], "independent_performance")
        self.assertEqual(result["attempt_ids"], ["a1"])
        self.assertEqual(result["evidence_counts"]["independent_executable_successes"], 1)


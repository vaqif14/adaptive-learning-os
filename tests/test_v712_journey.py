"""Functional journey safeguards; synthetic data cannot establish learning efficacy."""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.course import CourseKernel
from runtime.course_state import CourseStateError
from runtime import course_journey as journey
from course_fixture import items, roadmap
from test_v700_course_flow import CourseCase


def observation(**changes):
    entry = {"id": "dx-first", "node_id": "vec-sim", "prompt": "İki vektorun istiqamətcə yaxınlığını necə müqayisə edərdin?",
             "basis": "observed", "task_kind": "explanation", "response": "Dot product-u uzunluqlara bölüb müqayisə edərdim.",
             "outcome": "correct", "independence": "unassisted", "assistance": [], "assessor": "host_agent",
             "confidence": "medium", "rationale": "Cavab uzunluqların təsirini aradan qaldırmaq lazım olduğunu göstərir."}
    return {**entry, **changes}


class DiagnosticJourneyTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.k.init(self.sid)

    def record(self, **changes):
        return self.k.record_diagnostic(self.sid, {"entries": [observation(**changes)]})

    def test_intake_requests_performance_after_background_not_a_self_rating(self):
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "performance_probe")
        self.k.record_diagnostic(self.sid, {"entries": [{"prompt": "Python bilirsən?", "basis": "self_report"}]})
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "performance_probe")

    def test_adaptive_second_task_then_stops_at_a_bounded_provisional_plan(self):
        self.record()
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "fresh_transfer_probe")
        self.record(id="dx-second", prompt="İki sənədin vektoru müxtəlif uzunluqdadır. İstiqamət oxşarlığını necə müqayisə edərdin?", outcome="incorrect", response="Vektorun böyük uzunluğu kifayətdir.")
        result = self.k.diagnostic_next(self.sid)
        self.assertEqual(result["action"], "ready_to_plan")
        self.assertTrue(result["provisional"])
        self.assertEqual(result["observations"], 2)
        self.assertNotIn("overall_level", result)
        restarted = CourseKernel(self.ws)
        self.assertEqual(restarted.diagnostic_next(self.sid), result)

    def test_failure_discriminates_prerequisite_or_task_interpretation(self):
        self.record(outcome="incorrect")
        step = self.k.diagnostic_next(self.sid)
        self.assertEqual(step["action"], "prerequisite_or_interpretation_probe")
        self.assertEqual(step["node_id"], "vec-sim")

    def test_assistance_overrides_claimed_independence_and_is_persisted(self):
        result = self.record(assistance=["worked example"])
        entry = result["learner_profile"]["diagnostic"][0]
        self.assertEqual(entry["independence"], "assisted")
        self.assertEqual(entry["response"], observation()["response"])
        self.assertFalse(entry["independent_observation"])
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "smaller_independent_probe")
        self.approve()
        self.assertFalse(self.k.state(self.sid)["next_mission"]["suggest_fast_track"])

    def test_legacy_summary_without_provenance_does_not_suggest_fast_track(self):
        self.k.record_diagnostic(self.sid, {"entries": [{"prompt": "Test", "basis": "observed", "outcome": "correct",
                                                        "node_id": "vec-sim", "response_summary": "Correct"}]})
        self.approve()
        self.assertFalse(self.k.state(self.sid)["next_mission"]["suggest_fast_track"])
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "performance_probe")

    def test_diagnostic_prompt_is_not_automatically_fresh_as_an_exam(self):
        self.record(prompt=items()["items"][0]["prompt_md"])
        self.approve()
        self.author()
        state = self.k.state(self.sid)
        node = next(n for n in state["roadmap"]["nodes"] if n["id"] == "vec-sim")
        self.assertLess(node["fresh_items"], 2)

    def test_no_skill_probability_is_fabricated(self):
        self.record()
        self.approve()
        node = self.k.state(self.sid)["roadmap"]["nodes"][0]
        self.assertEqual(node["skill_evidence"]["calibrated_probability"], None)
        self.assertEqual(node["skill_evidence"]["status"], "provisional_observation")
        self.assertFalse(node["skill_evidence"]["mastery_claim"])

    def test_malformed_new_attribution_is_refused_without_writing(self):
        revision = self.k.state(self.sid)["revision"]
        for change in ({"independence": "expert"}, {"assistance": "some help"}, {"assessor": "auto_verified"}):
            with self.subTest(change=change), self.assertRaises(CourseStateError):
                self.record(**change)
        self.assertEqual(self.k.state(self.sid)["revision"], revision)

    def test_diagnostic_identity_replay_is_deduplicated_and_changed_response_refused(self):
        self.record()
        self.record()
        self.assertEqual(self.k.diagnostic_next(self.sid)["observations"], 1)
        with self.assertRaises(CourseStateError):
            self.record(response="Bu, fərqli cavabdır.")

    def test_changed_capability_does_not_reuse_old_diagnostic_for_fast_track(self):
        self.record()
        self.approve()
        self.assertTrue(self.k.state(self.sid)["next_mission"]["suggest_fast_track"])
        changed = roadmap()
        changed["nodes"][0]["outcomes"] = ["Evklid və kosinus məsafəsini uyğun ssenari üçün müqayisə edir."]
        version = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        state = self.k.state(self.sid)
        self.assertFalse(state["next_mission"]["suggest_fast_track"])
        self.assertEqual(state["roadmap"]["nodes"][0]["skill_evidence"]["status"], "not_yet_observed")


class GraderReliabilityTests(CourseCase):
    def attempts(self, first_grader="host_agent", current_grader="host_agent"):
        first = {"node_id": "vec-sim", "status": "finalized", "kind": "free_text", "capability_version": "c1",
                 "content_fingerprint": "one", "result": {"outcome": "correct", "confidence": "medium", "grader": first_grader}}
        current = {**copy.deepcopy(first), "status": "scored", "content_fingerprint": "two"}
        current["result"]["grader"] = current_grader
        return {"attempts": {"one": first, "two": current}}, current

    def test_two_uncalibrated_ai_judgments_are_not_independent_corroboration(self):
        state, att = self.attempts()
        self.assertFalse(self.k._free_text_corroborated(state, att))

    def test_attributed_human_or_verified_python_corroboration_remains_possible(self):
        state, att = self.attempts(first_grader="human")
        self.assertTrue(self.k._free_text_corroborated(state, att))
        state, att = self.attempts(current_grader="human")
        self.assertTrue(self.k._free_text_corroborated(state, att))
        state["attempts"]["one"]["kind"] = "python"
        self.assertTrue(self.k._free_text_corroborated(state, att))

    def test_recognition_only_cannot_corroborate_open_response(self):
        state, att = self.attempts()
        state["attempts"]["one"].update(kind="concept", evidence_strength="weak")
        self.assertFalse(self.k._free_text_corroborated(state, att))

    def test_missing_confidence_is_uncertain_even_with_a_previous_human_judgment(self):
        state, att = self.attempts(first_grader="human")
        att["result"].pop("confidence")
        self.assertFalse(self.k._free_text_corroborated(state, att))

    def test_uncalibrated_ai_review_does_not_extend_a_retention_interval(self):
        state = {"node_progress": {"vec-sim": {"passed": True, "capability_version": "c1"}},
                 "capabilities": {"vec-sim": {"version": "c1"}},
                 "review_queue": [{"review_id": "r1", "node_id": "vec-sim", "status": "in_progress",
                                   "capability_version": "c1", "recalls": 0, "lapses": 0}]}
        att = {"attempt_id": "second", "review_id": "r1", "node_id": "vec-sim", "kind": "free_text",
               "result": {"confidence": "high", "grader": "host_agent"}}
        self.k._complete_review(state, att, "correct")
        self.assertEqual(state["node_progress"]["vec-sim"]["retention"]["status"], "uncertain")
        scheduled = [q for q in state["review_queue"] if q["status"] == "scheduled"]
        self.assertEqual((scheduled[0]["interval_days"], scheduled[0]["recalls"]), (1, 0))

    def test_later_failure_is_exposed_as_mixed_skill_evidence(self):
        state, att = self.attempts()
        state["learner_profile"] = {"diagnostic": []}
        state["attempts"]["one"].update(kind="python", attempt_id="one")
        att.update(status="finalized", attempt_id="two")
        att["result"]["outcome"] = "incorrect"
        result = journey.skill_evidence(state, "vec-sim", "c1")
        self.assertEqual(result["status"], "mixed_evidence")
        self.assertFalse(result["mastery_claim"])


class RecoveryJourneyTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.author()

    def fail(self, index):
        payload = items()
        item = copy.deepcopy(payload["items"][0])
        item["item_id"] = f"failure-{index}"
        item["prompt_md"] = f"Yeni, fərqli test konteksti {index}: `cosine(a, b)` yaz."
        for i, check in enumerate(item["checks"]):
            check["code"] = f"assert cosine([{index + i + 1},0], [1,0]) == 1.0"
        installed = self.k.install_items(self.sid, "vec-sim", {"node_id": "vec-sim", "items": [item]})
        # Explicit host review of fresh authored values; this is a synthetic fixture.
        if installed["freshness"][item["item_id"]]["status"] == "needs_review":
            self.k.review_freshness(self.sid, "vec-sim", item["item_id"], "distinct",
                                    "Bu fixture-da qiymətlər və tapşırıq konteksti fərqlidir.")
        att = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        Path(att["file"]).write_text("def cosine(a,b): return -999\n")
        out = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.k.advance(self.sid)
        return out

    def test_repeated_failed_exams_require_an_agreed_changed_recovery_plan(self):
        for i in range(3):
            last = self.fail(i + 1)
        self.assertEqual(last["next_mission"]["type"], "recovery_check")
        self.assertEqual(last["next_mission"]["blocked_on"], "recovery_required")
        self.k = CourseKernel(self.ws)
        for action in (lambda: self.k.start_mission(self.sid, "vec-sim"),
                       lambda: self.k.start_exam(self.sid, "vec-sim", fast_track=True)):
            with self.assertRaises(CourseStateError) as ctx:
                action()
            self.assertEqual(ctx.exception.code, "recovery_required")
        plan = {"blocker": "task_interpretation", "plan": "Əvvəl şərti öz sözlərinlə izah et, sonra kiçik nümunədə nəticəni təxmin et.",
                "learner_agreed": True}
        self.k.record_recovery(self.sid, "vec-sim", plan)
        self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(self.k.state(self.sid)["next_mission"]["type"], "targeted_recovery")

    def test_recovery_needs_explicit_agreement_and_a_concrete_plan(self):
        for i in range(3):
            self.fail(i + 1)
        for plan in ({"blocker": "tooling", "plan": "Try again", "learner_agreed": True},
                     {"blocker": "tooling", "plan": "Əvvəl traceback-i oxuyub ilk səhvin yerini tap.", "learner_agreed": False}):
            with self.subTest(plan=plan), self.assertRaises(CourseStateError):
                self.k.record_recovery(self.sid, "vec-sim", plan)

    def test_material_change_releases_old_recovery_for_a_different_capability(self):
        for i in range(3):
            self.fail(i + 1)
        changed = roadmap()
        changed["nodes"][0]["skills"] = ["Yeni ölçü üzrə bacarıq"]
        version = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(self.k.state(self.sid)["phase"], "teaching")


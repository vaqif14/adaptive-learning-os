"""Release assessment gates; fixtures prove mechanisms, never learning efficacy."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_content as cc
from runtime.course import CourseKernel
from runtime.course_state import CourseStateError
from runtime.utils import read_json, write_json
from course_fixture import CONCEPT_ITEMS, EXAM_CORRECT, NEAREST_REF, concept_roadmap, items, manifest_for, roadmap
from test_v700_course_flow import CourseCase


class NumericIntegrityTests(unittest.TestCase):
    def question(self, **changes):
        return {"id": "number-one", "type": "numeric", "category": "core", "prompt": "Hesablamanın nəticəsi neçədir?",
                "answer": 1.0, "tolerance": 0, "fail_feedback": "Hesablamanı yenidən yoxla.", **changes}

    def test_nonfinite_decimal_strings_are_refused_in_author_and_learner_data(self):
        for huge in ("9" * 400, "-" + "9" * 400):
            with self.subTest(value=huge[:8]):
                self.assertIsNone(cc._number(huge))
                with self.assertRaises(cc.ContentError):
                    cc._questions([self.question(tolerance=huge)], "questions", set())
                with self.assertRaises(cc.ContentError):
                    cc.validate_answers({"kind": "concept", "questions": [self.question()]}, {"number-one": huge})

    def test_correct_wrong_blank_and_malformed_values_never_confuse_truth(self):
        q = self.question(tolerance=0.01)
        for value, expected in ((1, True), ("1,005", True), (2, False), (None, False), ("", False),
                                (True, False), ({"value": 1}, False), (float("nan"), False)):
            with self.subTest(value=value):
                rows, _ = cc.grade_questions([q], {q["id"]: value}, [])
                self.assertIs(rows[0]["passed"], expected)

    def test_zero_tolerance_does_not_silently_accept_a_different_number(self):
        q = self.question(answer=0)
        rows, _ = cc.grade_questions([q], {q["id"]: 0.0000000000001}, [])
        self.assertFalse(rows[0]["passed"])

    def test_answer_type_errors_are_content_refusals_instead_of_python_errors(self):
        q = copy.deepcopy(CONCEPT_ITEMS["items"][0]["questions"][1])
        for malformed in ([{"a": 1}], [["a"]], [True]):
            with self.subTest(answer=malformed), self.assertRaises(cc.ContentError):
                cc._questions([{**q, "answer": malformed}], "questions", set())

    def test_numeric_response_is_preserved_instead_of_silently_truncated(self):
        answer = "123456789012345678901234567890123456789"
        spec = {"kind": "concept", "questions": [self.question()]}
        self.assertEqual(cc.validate_answers(spec, {"number-one": answer})["number-one"], answer)
        with self.assertRaises(cc.ContentError):
            cc.validate_answers(spec, {"number-one": "0." + "0" * 150 + "1"})


class GraderProtocolTests(unittest.TestCase):
    def item(self):
        return {"kind": "python", "checks": [{"id": "core-check", "category": "core"},
                                               {"id": "transfer-check", "category": "transfer"}], "misconceptions": []}

    def rows(self):
        return [{"id": c["id"], "category": c["category"], "name": "Frozen check", "passed": True,
                 "feedback": None, "error": None} for c in self.item()["checks"]]

    def test_complete_boolean_rows_and_explicit_build_failure_are_accepted(self):
        cc.validate_grading_result(self.rows(), [], self.item())
        cc.validate_grading_result([{"id": "build", "category": "build", "name": "Syntax check", "passed": False,
                                     "feedback": "Syntax error", "error": None}], [], self.item())

    def test_empty_incomplete_duplicate_truthy_or_contradictory_result_is_refused(self):
        rows = self.rows()
        for malformed in ([], None, rows[:1], [rows[0], rows[0]],
                          [{**rows[0], "passed": "false"}, rows[1]],
                          [{**rows[0], "error": "failed"}, rows[1]],
                          [{**rows[0], "category": "unknown"}, rows[1]],
                          [{"id": "build", "category": "build", "passed": True, "name": "Build",
                            "feedback": None, "error": None}]):
            with self.subTest(rows=malformed), self.assertRaises(cc.ContentError):
                cc.validate_grading_result(malformed, [], self.item())

    def test_unknown_or_malformed_misconception_is_refused(self):
        for fired in (None, [{"id": "unregistered", "name": "Guess", "feedback": "Guess"}], ["guess"]):
            with self.subTest(fired=fired), self.assertRaises(cc.ContentError):
                cc.validate_grading_result(self.rows(), fired, self.item())


class AssessmentExecutionGates(CourseCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.author()
        self.att = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        Path(self.att["file"]).write_text(EXAM_CORRECT, encoding="utf-8")

    def assert_ungraded(self):
        state = CourseKernel(self.ws).state(self.sid)
        self.assertTrue(state["teaching_locked"])
        self.assertEqual(state["attempt_status"], "scoring_failed")
        self.assertEqual(self.strong(), 0)
        self.assertFalse(self.evidence())

    def test_empty_verifier_response_is_not_vacuous_mastery(self):
        with patch.object(cc, "run_checks", return_value=[]):
            result = self.k.submit_and_finalize(self.sid, self.att["attempt_id"])
        self.assertEqual(result["status"], "scoring_failed")
        self.assert_ungraded()

    def test_malformed_verifier_response_stays_ungraded_and_resumable(self):
        self.k.submit_exam(self.sid, self.att["attempt_id"])
        for rows in ([{"passed": True}], None, [{"id": "same", "name": "fake", "category": "core",
                                                "passed": "false", "feedback": None, "error": None}]):
            with self.subTest(rows=rows), patch.object(cc, "run_checks", return_value=rows):
                result = self.k.score_exam(self.sid, self.att["attempt_id"])
                self.assertEqual(result["status"], "scoring_failed")
                self.assert_ungraded()

    def test_grader_value_error_is_an_explicit_failure_without_unlocking(self):
        with patch.object(cc, "run_checks", side_effect=ValueError("unparseable grader response")):
            result = self.k.submit_and_finalize(self.sid, self.att["attempt_id"])
        self.assertEqual(result["status"], "scoring_failed")
        self.assert_ungraded()

    def test_post_scoring_item_change_cannot_finalize_old_score(self):
        self.k.submit_exam(self.sid, self.att["attempt_id"])
        self.k.score_exam(self.sid, self.att["attempt_id"])
        vault = self.k._vault(self.d, "vec-sim") / f"{self.att['item']['item_id']}.json"
        item = read_json(vault)
        item["scope"] = "far_transfer"
        write_json(vault, item)
        with self.assertRaises(CourseStateError):
            self.k.finalize_exam(self.sid, self.att["attempt_id"])
        self.assertEqual(self.strong(), 0)
        self.assertFalse(self.evidence())
        self.assertTrue(CourseKernel(self.ws).state(self.sid)["teaching_locked"])

    def test_post_start_item_change_is_not_presented_as_the_frozen_prompt(self):
        vault = self.k._vault(self.d, "vec-sim") / f"{self.att['item']['item_id']}.json"
        item = read_json(vault)
        item["prompt_md"] = "CHANGED PRIVATE AUTHOR PROMPT THAT IS NOT THIS ATTEMPT"
        write_json(vault, item)
        current = CourseKernel(self.ws).state(self.sid)["active_attempt"]
        self.assertNotIn(item["prompt_md"], json.dumps(current))

    def test_corrupted_vault_contents_are_an_ungraded_failure_not_a_request_crash(self):
        self.k.submit_exam(self.sid, self.att["attempt_id"])
        vault = self.k._vault(self.d, "vec-sim") / f"{self.att['item']['item_id']}.json"
        for corrupted in (b"{invalid-json", b"[]", b"\xff\xfe"):
            with self.subTest(corrupted=corrupted):
                vault.write_bytes(corrupted)
                result = self.k.score_exam(self.sid, self.att["attempt_id"])
                self.assertEqual(result["status"], "scoring_failed")
                self.assert_ungraded()

    def test_corrupted_submission_encoding_is_an_ungraded_failure(self):
        self.k.submit_exam(self.sid, self.att["attempt_id"])
        snapshot = self.k._attempt_dir(self.d, self.att["attempt_id"]) / "submitted" / "solution.py"
        snapshot.write_bytes(b"\xff\xfe")
        result = self.k.score_exam(self.sid, self.att["attempt_id"])
        self.assertEqual(result["status"], "scoring_failed")
        self.assert_ungraded()

    def test_finalization_replay_is_one_evidence_record_after_restart(self):
        result = self.k.submit_and_finalize(self.sid, self.att["attempt_id"])
        self.assertEqual(result["assessment_result"]["outcome"], "correct")
        restarted = CourseKernel(self.ws)
        for _ in range(3):
            replay = restarted.submit_and_finalize(self.sid, self.att["attempt_id"])
            self.assertEqual(replay["status"], "finalized")
        self.assertEqual(len(self.evidence()), 1)


class PublicAnswerSeparation(CourseCase):
    def test_open_response_key_rubric_and_exemplar_never_leave_the_pre_submission_view(self):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        version = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][1]]
        self.k.install_items(self.sid, "vec-ideas", payload)
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        for public in (att, self.k.state(self.sid), self.k.export(self.sid)):
            encoded = json.dumps(public, ensure_ascii=False)
            self.assertNotIn(payload["items"][0]["exemplar"], encoded)
            for criterion in payload["items"][0]["rubric"]:
                self.assertNotIn(criterion["criterion"], encoded)
        self.assertFalse(self.evidence())


class OpenGradingGates(CourseCase):
    RESPONSE = "Dot product uzun vektorlarda böyüyür; uzunluqlara böləndə yalnız istiqamət qalır."

    def setUp(self):
        super().setUp()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        version = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][1]]
        self.k.install_items(self.sid, "vec-ideas", payload)
        self.att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.k.save_answers(self.sid, self.att["attempt_id"], {"response": self.RESPONSE})
        result = self.k.submit_and_finalize(self.sid, self.att["attempt_id"])
        self.assertEqual(result["status"], "awaiting_rubric")

    def judgment(self, **changes):
        return {"grader": "host_agent", "confidence": "medium", "rationale": "Cavab normallaşdırmanın təsirini izah edir.",
                "criteria": {"r-normalize": {"met": True, "evidence_quote": "uzunluqlara böləndə"},
                             "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}}, **changes}

    def test_invalid_ambiguous_or_fabricated_judgment_stays_ungraded_and_locked(self):
        revision = self.k.state(self.sid)["revision"]
        for judgment in (None, {"grader": "host_agent"}, self.judgment(criteria={}),
                         self.judgment(criteria={1: {"met": True}, "r-direction": {"met": True}}),
                         self.judgment(criteria={"r-normalize": {"met": "true"}, "r-direction": {"met": True}}),
                         self.judgment(criteria={"r-normalize": {"met": True, "evidence_quote": "a fabricated answer"},
                                                 "r-direction": {"met": False}}),
                         self.judgment(confidence="uncertain")):
            with self.subTest(judgment=judgment), self.assertRaises(CourseStateError):
                self.k.record_rubric(self.sid, self.att["attempt_id"], judgment)
            state = CourseKernel(self.ws).state(self.sid)
            self.assertEqual(state["attempt_status"], "awaiting_rubric")
            self.assertTrue(state["teaching_locked"])
            self.assertEqual(state["revision"], revision)
            self.assertFalse(self.evidence())

    def test_low_confidence_correct_response_is_not_mastery(self):
        result = self.k.judge_and_finalize(self.sid, self.att["attempt_id"], self.judgment(confidence="low"))
        self.assertTrue(result["assessment_result"]["provisional"])
        self.assertEqual(result["assessment_result"]["grader_reliability"], "uncalibrated_ai")
        node = next(n for n in self.k.state(self.sid)["roadmap"]["nodes"] if n["id"] == "vec-ideas")
        self.assertNotEqual(node["status"], "passed")
        self.assertEqual(self.strong("vec-ideas"), 0)

    def test_wrong_rubric_result_produces_remediation_without_progress(self):
        result = self.k.judge_and_finalize(self.sid, self.att["attempt_id"], self.judgment(
            criteria={"r-normalize": {"met": False}, "r-direction": {"met": False}}))
        self.assertEqual(result["assessment_result"]["outcome"], "incorrect")
        self.assertEqual(self.strong("vec-ideas"), 0)
        self.assertEqual(result["next_mission"]["node_id"], "vec-ideas")


class SourceAndPrerequisiteReleaseGates(CourseCase):
    def test_absent_roadmap_source_key_is_reported_and_blocks_independent_assessment(self):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        plan = roadmap()
        plan["nodes"][0]["source_refs"] = ["not-imported"]
        version = self.k.propose_roadmap(self.sid, plan)["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        self.k.install_items(self.sid, "vec-sim", items())
        state = read_json(self.d / "course-state.json")
        self.assertEqual(self.k._node_source_problems(state, plan["nodes"][0]), ["not-imported"])
        with self.assertRaises(CourseStateError) as caught:
            self.k.start_exam(self.sid, "vec-sim", fast_track=True)
        self.assertEqual(caught.exception.code, "grounding_invalid")
        self.assertFalse(self.evidence())

    def test_three_node_chain_withdraws_dependent_access_but_preserves_historical_results(self):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        plan = roadmap()
        third = copy.deepcopy(plan["nodes"][1])
        third.update(id="vec-release", title="Axtarış nəticələrini yoxlamaq", requires=["vec-search"],
                     task="Axtarış nəticələrinin uyğunluğunu müstəqil yoxla.")
        plan["nodes"].append(third)
        version = self.k.propose_roadmap(self.sid, plan)["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        for node, selected, code in (("vec-sim", 0, EXAM_CORRECT), ("vec-search", 1, NEAREST_REF)):
            payload = items()
            payload["node_id"] = node
            payload["items"] = [payload["items"][selected]]
            self.k.install_items(self.sid, node, payload)
            attempt = self.k.start_exam(self.sid, node, fast_track=True)["attempt"]
            Path(attempt["file"]).write_text(code, encoding="utf-8")
            self.k.submit_and_finalize(self.sid, attempt["attempt_id"])
            self.k.advance(self.sid)
        before = read_json(self.d / "course-state.json")
        self.assertTrue(self.k._available(before, third))
        own_second_result = copy.deepcopy(before["node_progress"]["vec-search"])
        evidence_ids = {r["payload"]["evidence_id"] for r in self.evidence()}
        changed = copy.deepcopy(plan)
        changed["nodes"][0]["task"] = "Kosinusdan əlavə sıfır vektor üçün qəbul edilən davranışı əsaslandır."
        version = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli")
        restarted = CourseKernel(self.ws)
        state = read_json(self.d / "course-state.json")
        self.assertFalse(restarted._passed(state, "vec-sim"))
        self.assertTrue(restarted._passed(state, "vec-search"))  # own historical result remains intact
        # New prerequisite-warning metadata may be added; the earned result and
        # original attempt identities must remain preserved for the learner's audit.
        preserved = ("passed", "passed_at", "capability_version", "last_outcome", "exam_attempts")
        self.assertEqual({k: state["node_progress"]["vec-search"][k] for k in preserved},
                         {k: own_second_result[k] for k in preserved})
        self.assertFalse(restarted._available(state, changed["nodes"][1]))
        self.assertFalse(restarted._available(state, third))  # transitive prerequisite validity
        with self.assertRaises(CourseStateError):
            restarted.start_exam(self.sid, "vec-release", fast_track=True)
        self.assertEqual({r["payload"]["evidence_id"] for r in self.evidence()}, evidence_ids)
        self.assertTrue(all(a["status"] == "finalized" and a["result"]["outcome"] == "correct"
                            for a in restarted.export(self.sid)["attempts"]))


if __name__ == "__main__":
    unittest.main()

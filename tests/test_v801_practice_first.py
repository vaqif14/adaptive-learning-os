"""Stage B: practice-first teaching (course-studio.md sections 2 and 7).

Walkthrough + faded ladder + hint ceiling validation (incl. the answer-leak guard), the learner flows
(help walkthrough/ladder, hint ceiling, practice reveal, practice reset) through the guarded course path,
and the studio-targets-v2 policy. All content is the synthetic TEST FIXTURE course.
"""
import contextlib
import io
import json
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
import runtime.course_content as cc  # noqa: E402
import runtime.studio_jobs as jobs  # noqa: E402
import runtime.studio_plan as plan_mod  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from course_fixture import (CONCEPT_ITEMS, CONCEPT_LESSON, COSINE_REF, IsolatedState, PRACTICE_CORRECT,  # noqa: E402
                            concept_roadmap, manifest_for, roadmap)
from studio_fixture import (LIBRARY, FakeAuthor, StudioCase, search_items, search_lesson, search_lesson_v1,  # noqa: E402
                            sim_items, sim_lesson, sim_lesson_v1)
from test_v700_course_flow import CourseCase  # noqa: E402

SIM_LABEL = cc.WALKTHROUGH_LABEL_AZ


def leaky_walkthrough(solution_code, *prints):
    """A python walkthrough whose steps define ``solution_code`` and print ``prints`` (code, expected cumulative output)."""
    wt = sim_lesson()["walkthrough"]
    steps = [{"explain_md": "Əvvəl köməkçi funksiyaları təyin edirik.", "code": solution_code}]
    for i, (code, expected) in enumerate(prints, 1):
        steps.append({"explain_md": f"Nəticəni {i}-ci dəfə çap edirik.", "code": code,
                      "checkpoint": {"expected_output": expected}})
    wt["steps"] = steps
    wt["mistake"] = {"step": 2, "wrong_code": "print(undefined_name)", "symptom": "Kod xəta verir: ad təyin olunmayıb.",
                     "fix_explain": "Çap etməzdən əvvəl dəyişəni təyin et."}
    return wt


class WalkthroughValidationTests(StudioCase):
    def validate(self, mutate=None, *, staged_items=None):
        lesson = sim_lesson()
        if mutate:
            mutate(lesson)
        return self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson, staged_items=staged_items)

    def assertRejected(self, mutate, needle, **kw):
        out = self.validate(mutate, **kw)
        self.assertFalse(out["valid"], out)
        self.assertTrue(any(needle in e for e in out["errors"]), out["errors"])

    def test_the_fixture_lessons_are_valid_with_items_staged(self):
        out = self.validate(staged_items=sim_items())
        self.assertTrue(out["valid"], out["errors"])
        self.assertEqual((out["summary"]["walkthrough"], out["summary"]["ladder"], out["summary"]["hint_ceiling"]),
                         ("python", True, 1))
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-search", search_lesson(), staged_items=search_items())["valid"])

    def test_legacy_lessons_stay_valid_without_the_new_parts(self):
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", sim_lesson_v1())
        self.assertTrue(out["valid"], out["errors"])
        self.assertEqual((out["summary"]["walkthrough"], out["summary"]["ladder"]), (None, False))

    def test_checkpoint_mismatch_is_rejected(self):
        self.assertRejected(lambda l: l["walkthrough"]["steps"][1]["checkpoint"].update(expected_output="19"),
                            "does not match the sandbox output")

    def test_trailing_whitespace_in_the_checkpoint_is_normalised(self):
        def pad(lesson):
            lesson["walkthrough"]["steps"][1]["checkpoint"]["expected_output"] = "18   \n\n"
        self.assertTrue(self.validate(pad)["valid"])

    def test_step_code_that_raises_is_rejected(self):
        self.assertRejected(lambda l: l["walkthrough"]["steps"][1].update(code="total = sum(nothing)\nprint(total)"),
                            "raised")

    def test_walkthrough_that_solves_the_practice_is_rejected(self):
        wt = leaky_walkthrough(PRACTICE_CORRECT, ("print(dot([1, 2], [3, 4]))", "11"), ("print(norm([3, 4]))", "11\n5.0"))
        self.assertRejected(lambda l: l.update(walkthrough=wt), "passes the practice checks")

    def test_walkthrough_that_solves_an_exam_item_is_rejected(self):
        wt = leaky_walkthrough(COSINE_REF, ("print(cosine([1, 0], [1, 0]))", "1.0"), ("print(cosine([1, 0], [0, 1]))", "1.0\n0.0"))
        self.assertRejected(lambda l: l.update(walkthrough=wt), "passes exam item cosine-a", staged_items=sim_items())

    def test_items_arriving_after_a_leaky_lesson_are_rejected_too(self):
        leaky = sim_lesson()
        leaky["walkthrough"] = leaky_walkthrough(COSINE_REF, ("print(cosine([1, 0], [1, 0]))", "1.0"),
                                                 ("print(cosine([1, 0], [0, 1]))", "1.0\n0.0"))
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=leaky)
        self.assertFalse(out["valid"])
        self.assertTrue(any("walkthrough already solves" in e for e in out["errors"]), out["errors"])

    def test_fewer_than_three_steps_is_rejected(self):
        def trim(lesson):
            lesson["walkthrough"]["steps"] = lesson["walkthrough"]["steps"][:2]
            lesson["walkthrough"]["mistake"]["step"] = 2
        self.assertRejected(trim, "at least 3 steps")

    def test_fewer_than_two_checkpoints_is_rejected(self):
        def one(lesson):
            for step in lesson["walkthrough"]["steps"][2:]:
                step.pop("checkpoint", None)
            lesson["walkthrough"]["mistake"]["step"] = 2
        self.assertRejected(one, "at least 2 checkpoints")

    def test_expert_name_fields_and_attributions_are_rejected(self):
        self.assertRejected(lambda l: l["walkthrough"].update(expert="Jane Roe"), "no field may name a real person")
        self.assertRejected(lambda l: l["walkthrough"]["steps"][0].update(author="Jane Roe"), "no field may name a real person")
        self.assertRejected(lambda l: l["walkthrough"].update(transcript_md="Bu nümayişi expert Jane Roe danışır və izah edir."),
                            "names a real person")
        self.assertRejected(lambda l: l["walkthrough"].update(unexpected_field="x"), "unknown fields")

    def test_the_label_is_enforced_exactly(self):
        self.assertEqual(SIM_LABEL, "Agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş")
        self.assertRejected(lambda l: l["walkthrough"].update(label="Ekspert nümayişi"), "label must be exactly")
        self.assertRejected(lambda l: l["walkthrough"].pop("label"), "label must be exactly")

    def test_walkthrough_refs_are_grounded_like_other_parts(self):
        self.assertRejected(lambda l: l["walkthrough"].update(refs=[{"source_id": "src-fixture-001", "quote": "no such sentence anywhere"}]),
                            "not grounded")
        ok = self.validate(lambda l: l["walkthrough"].update(refs=[{"supplementary": True, "note": "Fixture analogy."}]))
        self.assertTrue(ok["valid"], ok["errors"])

    def test_wrong_code_must_fail_or_differ_from_the_checkpoint(self):
        self.assertRejected(lambda l: l["walkthrough"]["mistake"].update(wrong_code="average = total / len(scores)\nprint(average)"),
                            "neither raises nor prints something different")
        differs = self.validate(lambda l: l["walkthrough"]["mistake"].update(wrong_code="average = total / 2\nprint(average)"))
        self.assertTrue(differs["valid"], differs["errors"])
        self.assertRejected(lambda l: l["walkthrough"]["mistake"].pop("wrong_code"), "wrong_code")
        self.assertRejected(lambda l: l["walkthrough"]["mistake"].update(step=1), "checkpoint")

    def test_a_walkthrough_that_overlaps_an_exam_item_lexically_is_rejected(self):
        item = sim_items()["items"][0]
        self.assertRejected(lambda l: l["walkthrough"].update(scenario_md=item["prompt_md"]), "overlaps assessment item",
                            staged_items=sim_items())

    def test_concept_walkthrough_needs_no_code_but_the_same_rules(self):
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-search", search_lesson())["valid"])
        for mutate, needle in ((lambda l: l["walkthrough"]["steps"][0].update(code="print(1)"), "language 'none'"),
                               (lambda l: l["walkthrough"].update(steps=l["walkthrough"]["steps"][:2]), "at least 3 steps"),
                               (lambda l: l["walkthrough"].update(label="x"), "label must be exactly"),
                               (lambda l: l["walkthrough"].update(speaker="Jane Roe"), "no field may name")):
            lesson = search_lesson()
            mutate(lesson)
            out = self.ck.validate_lesson_dry(self.sid, "vec-search", lesson)
            self.assertFalse(out["valid"])
            self.assertTrue(any(needle in e for e in out["errors"]), out["errors"])

    def test_hint_ceiling_bounds(self):
        for bad in (0, 3, True, "1", 1.0):
            lesson = sim_lesson()
            lesson["hint_ceiling"] = bad
            out = self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson)
            self.assertFalse(out["valid"], bad)
            self.assertTrue(any("hint_ceiling" in e for e in out["errors"]))
        lesson = sim_lesson()
        lesson["hint_ceiling"] = 2
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson)["valid"])


class LadderValidationTests(StudioCase):
    def assertRejected(self, mutate, needle, **kw):
        lesson = sim_lesson()
        mutate(lesson)
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson, **kw)
        self.assertFalse(out["valid"], out)
        self.assertTrue(any(needle in e for e in out["errors"]), out["errors"])

    def test_completion_filled_passes_and_empty_fails(self):
        ladder = sim_lesson()["ladder"]["completion"]
        filled = cc.fill_blanks(ladder["template"], ladder["blanks"])
        self.assertEqual(filled, ladder["template"].replace("___", "{}").format(*ladder["blanks"]))
        self.assertTrue(cc._passes_all(filled, sim_lesson()["practice"]["checks"]))
        self.assertFalse(cc._passes_all(cc.fill_blanks(ladder["template"], ["", ""]), sim_lesson()["practice"]["checks"]))

    def test_wrong_blank_answers_are_rejected(self):
        self.assertRejected(lambda l: l["ladder"]["completion"].update(blanks=["x + y", "dot(a, a)"]),
                            "does not pass the practice checks")

    def test_a_template_that_already_passes_with_empty_blanks_is_rejected(self):
        solved = ("import math\n\ndef dot(a, b):\n    return sum(x * y for x, y in zip(a, b))  # ___\n\n"
                  "def norm(a):\n    return math.sqrt(dot(a, a))\n")
        self.assertRejected(lambda l: l["ladder"].update(completion={**l["ladder"]["completion"], "template": solved, "blanks": ["x"]}),
                            "empty blanks already passes")

    def test_blank_count_must_match(self):
        self.assertRejected(lambda l: l["ladder"]["completion"].update(blanks=["x * y"]), "exactly 1")
        self.assertRejected(lambda l: l["ladder"]["completion"].update(blanks=["x * y", ""]), "non-empty answer")

    def test_independent_rung_is_the_practice(self):
        self.assertRejected(lambda l: l["ladder"].update(independent="another"), "independent must be 'practice'")
        self.assertRejected(lambda l: l["ladder"].pop("completion"), "must be an object")

    def test_a_worked_rung_that_solves_the_practice_is_rejected(self):
        self.assertRejected(lambda l: l["ladder"]["worked"].update(code_or_steps=PRACTICE_CORRECT), "solves the practice")

    def test_a_ladder_that_solves_an_exam_item_is_rejected(self):
        def cosine_ladder(lesson):
            lesson["ladder"]["worked"]["code_or_steps"] = COSINE_REF
        self.assertRejected(cosine_ladder, "passes exam item cosine-a", staged_items=sim_items())

    def test_ladder_text_overlapping_an_exam_item_is_rejected(self):
        item = sim_items()["items"][0]
        self.assertRejected(lambda l: l["ladder"]["worked"].update(explain_md=item["prompt_md"]), "overlaps assessment item",
                            staged_items=sim_items())

    def test_concept_ladder_is_text_only(self):
        lesson = search_lesson()
        lesson["ladder"]["completion"]["blanks"] = ["azalan"]
        out = self.ck.validate_lesson_dry(self.sid, "vec-search", lesson)
        self.assertFalse(out["valid"])
        self.assertTrue(any("exactly 1" in e for e in out["errors"]))


class AnswerLeakGuardTests(StudioCase):
    validate = WalkthroughValidationTests.validate
    assertRejected = WalkthroughValidationTests.assertRejected

    """The guard covers every visible state of the teaching code, not only the final cumulative code, and it is
    not bound to the identifiers the checks call."""

    DOT = ("print(dot([1, 2], [3, 4]))", "11")
    NORM = ("print(norm([3, 4]))", "11\n5.0")
    COS = [("print(cosine([1, 0], [1, 0]))", "1.0"), ("print(cosine([1, 0], [0, 1]))", "1.0\n0.0")]

    def test_a_mid_walkthrough_solution_deleted_in_a_last_step_is_rejected(self):
        wt = leaky_walkthrough(PRACTICE_CORRECT, self.DOT, self.NORM)
        wt["steps"].append({"explain_md": "Köməkçi adları təmizləyirik.", "code": "del dot, norm",
                            "checkpoint": {"expected_output": "11\n5.0"}})
        self.assertRejected(lambda l: l.update(walkthrough=wt), "passes the practice checks")

    def test_a_mid_walkthrough_exam_solution_is_rejected_for_lessons_and_for_later_items(self):
        wt = leaky_walkthrough(COSINE_REF, *self.COS)
        wt["steps"].append({"explain_md": "Köməkçi adları təmizləyirik.", "code": "del cosine",
                            "checkpoint": {"expected_output": "1.0\n0.0"}})
        self.assertRejected(lambda l: l.update(walkthrough=wt), "passes exam item cosine-a", staged_items=sim_items())
        leaky = sim_lesson()
        leaky["walkthrough"] = wt
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=leaky)
        self.assertFalse(out["valid"])
        self.assertTrue(any("walkthrough already solves" in e for e in out["errors"]), out["errors"])

    def test_the_mistake_wrong_code_may_not_carry_a_solution(self):
        for solution, needle, kw in ((PRACTICE_CORRECT, "passes the practice checks", {}),
                                     (COSINE_REF, "passes exam item cosine-a", {"staged_items": sim_items()})):
            def mutate(lesson, code=solution):
                lesson["walkthrough"]["mistake"]["wrong_code"] = code + "\nprint('x')"
            self.assertRejected(mutate, needle, **kw)

    def test_a_renamed_copy_of_the_exam_or_practice_solution_is_rejected(self):
        renamed = COSINE_REF.replace("cosine", "cos_sim")
        wt = leaky_walkthrough(renamed, ("print(cos_sim([1, 0], [1, 0]))", "1.0"), ("print(cos_sim([1, 0], [0, 1]))", "1.0\n0.0"))
        self.assertRejected(lambda l: l.update(walkthrough=wt), "exam item cosine-a", staged_items=sim_items())
        leaky = sim_lesson()
        leaky["walkthrough"] = wt
        self.assertFalse(self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=leaky)["valid"])
        practice = PRACTICE_CORRECT.replace("dot", "dot2").replace("norm", "norm2")
        wt = leaky_walkthrough(practice, ("print(dot2([1, 2], [3, 4]))", "11"), ("print(norm2([3, 4]))", "11\n5.0"))
        self.assertRejected(lambda l: l.update(walkthrough=wt), "practice checks")

    def test_a_different_instance_with_the_same_shape_is_still_accepted(self):
        different = "def dot(a, b):\n    return sum(x * y + 1 for x, y in zip(a, b))\n"
        wt = leaky_walkthrough(different, ("print(dot([1, 2], [3, 4]))", "13"))
        wt["steps"].append({"explain_md": "Yenə çap edirik.", "code": "print(dot([0], [5]))",
                            "checkpoint": {"expected_output": "13\n1"}})
        wt["steps"][1]["checkpoint"]["expected_output"] = "13"
        self.assertTrue(self.validate(lambda l: l.update(walkthrough=wt))["valid"])

    def test_an_inconclusive_sandbox_fails_closed(self):
        real = cc._VERIFIER.verify

        class Timeout:
            timed_out, executed, passed, correctness_checked, stdout, stderr = True, True, False, False, "", ""

        def verify(code, **kw):               # only the leak guard's runs of the walkthrough code flake
            return Timeout() if kw.get("trusted_test_code") and "scores" in code else real(code, **kw)
        with self.assertRaises(cc.ContentError) as ctx:
            cc._VERIFIER.verify = verify
            try:
                cc._passes_all("scores = [1]", sim_lesson()["practice"]["checks"])
            finally:
                del cc._VERIFIER.verify
        self.assertIn("sandbox_inconclusive", str(ctx.exception))
        cc._VERIFIER.verify = verify
        try:
            out = self.validate()
        finally:
            del cc._VERIFIER.verify
        self.assertFalse(out["valid"])
        self.assertTrue(any("sandbox_inconclusive" in e for e in out["errors"]), out["errors"])


class LadderLeakGuardTests(StudioCase):
    assertRejected = LadderValidationTests.assertRejected

    def test_a_worked_step_list_may_not_carry_the_practice_or_exam_solution(self):
        self.assertRejected(lambda l: l["ladder"]["worked"].update(code_or_steps=[PRACTICE_CORRECT, "Sonra yeni nümunədə işlədirik."]),
                            "solves the practice")
        self.assertRejected(lambda l: l["ladder"]["worked"].update(code_or_steps=[COSINE_REF, "Sonra yeni nümunədə işlədirik."]),
                            "exam item cosine-a", staged_items=sim_items())
        embedded = ["Addım: " + PRACTICE_CORRECT.replace("\n", " ; "), "Sonra yeni nümunədə işlədirik."]
        self.assertRejected(lambda l: l["ladder"]["worked"].update(code_or_steps=embedded), "solves the practice")

    def test_a_renamed_worked_solution_is_rejected(self):
        renamed = PRACTICE_CORRECT.replace("dot", "scalar").replace("norm", "length")
        self.assertRejected(lambda l: l["ladder"]["worked"].update(code_or_steps=renamed), "solves the practice")

    def test_items_arriving_after_a_leaky_ladder_list_are_rejected(self):
        leaky = sim_lesson()
        leaky["ladder"]["worked"]["code_or_steps"] = [COSINE_REF.replace("cosine", "cos_sim"), "Sonra yeni nümunədə işlədirik."]
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=leaky)
        self.assertFalse(out["valid"])
        self.assertTrue(any("ladder rung already solves" in e for e in out["errors"]), out["errors"])


class NamedPersonTests(StudioCase):
    validate = WalkthroughValidationTests.validate
    assertRejected = WalkthroughValidationTests.assertRejected

    def test_bare_attributed_names_are_rejected(self):
        for field, text in (("scenario_md", "Bu nümayişi Andrew Ng hazırlayıb və izah edir, ballar siyahısı üzərində."),
                            ("transcript_md", "Andrew Ng deyir ki, ballar siyahısı yaradılır və cəm hesablanır, sonra bölünür."),
                            ("transcript_md", "Cəm hesablanır və sonra Jane Roe bunu ballar siyahısı üzərində göstərir yenidən.")):
            self.assertRejected(lambda l, f=field, t=text: l["walkthrough"].update({f: t}), "names a real person")

    def test_technical_terms_are_not_names(self):
        def fine(lesson):
            lesson["walkthrough"]["transcript_md"] = ("Bu nümayişdə Python dilində ballar siyahısı yaradılır, cəm hesablanır "
                                                       "və Machine Learning kontekstində ortaya bölünür.")
        self.assertTrue(self.validate(fine)["valid"])


class PracticeFirstFlowCase(CourseCase):
    """An approved course with the v2 fixture lesson (walkthrough, ladder, hint ceiling) installed."""

    def setUp(self):
        super().setUp()
        self.approve()
        self.k.install_items(self.sid, "vec-sim", sim_items())
        self.k.install_lesson(self.sid, "vec-sim", sim_lesson())
        self.k.start_mission(self.sid, "vec-sim")

    def records(self, record_type):
        return [r for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == record_type]

    def progress(self):
        return read_json(self.d / "course-state.json")["node_progress"]["vec-sim"]

    def submission(self):
        return self.d / "workspace" / "modules" / "vec-sim" / "submission" / "practice.py"

    def manifest_with(self, status):
        stage = self.d / "sources-staging" / "stg_fx"
        manifest = manifest_for(stage)
        manifest["resources"][0]["status"] = status
        manifest["manifest_version"] = "sm-changed-" + status
        self.k.record_sources(self.sid, manifest, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}],
                              staged_cache=stage)


class WatchingTests(PracticeFirstFlowCase):
    def test_walkthrough_records_provenance_and_creates_no_evidence(self):
        evidence_before, observations_before = len(self.records("evidence")), len(self.records("observation"))
        out = self.k.help(self.sid, "vec-sim", "walkthrough")
        self.assertEqual(out["kind"], "walkthrough")
        content = out["content"]
        self.assertEqual(content["label"], SIM_LABEL)
        self.assertEqual(len(content["steps"]), 4)
        self.assertTrue(out["support_provenance"]["worked_example_shown"])
        self.assertEqual(self.k.state(self.sid)["support_provenance"]["worked_example_shown"], True)
        progress = self.progress()["help"]
        self.assertEqual(progress["walkthrough"], 1)
        self.k.help(self.sid, "vec-sim", "walkthrough")
        self.assertEqual(self.progress()["help"]["walkthrough"], 2)
        self.assertEqual((len(self.records("evidence")), len(self.records("observation"))),
                         (evidence_before, observations_before))
        text = json.dumps(content)
        for secret in (PRACTICE_CORRECT.strip(), "reference_solution", "fail_feedback"):
            self.assertNotIn(secret, text)

    def test_ladder_rungs_record_provenance_and_hide_the_blank_answers(self):
        evidence_before = len(self.records("evidence"))
        worked = self.k.help(self.sid, "vec-sim", "ladder")                     # default rung
        self.assertEqual(worked["content"]["rung"], "worked")
        completion = self.k.help(self.sid, "vec-sim", "ladder", rung="completion")
        content = completion["content"]
        self.assertEqual((content["rung"], content["blank_count"]), ("completion", 2))
        self.assertIn("___", content["template"])
        self.assertNotIn("x * y", json.dumps(content))
        self.assertNotIn("blanks", content)
        self.assertTrue(completion["support_provenance"]["worked_example_shown"])
        self.assertEqual(self.progress()["help"]["ladder"], 2)
        self.assertEqual(len(self.records("evidence")), evidence_before)
        for bad in ("independent", "solution"):
            with self.assertRaises(CourseStateError):
                self.k.help(self.sid, "vec-sim", "ladder", rung=bad)
        with self.assertRaises(CourseStateError):
            self.k.help(self.sid, "vec-sim", "explain", rung="worked")

    def test_a_legacy_lesson_has_nothing_to_show(self):
        self.k.install_lesson(self.sid, "vec-sim", sim_lesson_v1())
        for kind, code in (("walkthrough", "walkthrough_missing"), ("ladder", "ladder_missing")):
            with self.assertRaises(CourseStateError) as ctx:
                self.k.help(self.sid, "vec-sim", kind)
            self.assertEqual(ctx.exception.code, code)

    def test_idempotent_replay_keeps_one_effect(self):
        first = self.k.help(self.sid, "vec-sim", "walkthrough", idempotency_key="walk-key-0001")
        again = self.k.help(self.sid, "vec-sim", "walkthrough", idempotency_key="walk-key-0001")
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(self.progress()["help"]["walkthrough"], 1)
        self.assertEqual(first["kind"], "walkthrough")


class HintCeilingTests(PracticeFirstFlowCase):
    def test_hints_stop_at_the_ceiling_with_a_reveal_offer(self):
        self.k.begin_practice(self.sid, "vec-sim")
        first = self.k.help(self.sid, "vec-sim", "hint")
        self.assertEqual((first["content"]["level"], first["content"]["of"]), (1, 2))
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-sim", "hint")
        self.assertEqual(ctx.exception.code, "hint_ceiling_reached")
        message = ctx.exception.to_dict()["message"]
        self.assertIn("həll", message)
        self.assertIn("kredit", message)
        self.assertEqual(self.progress()["help"]["hints"], 1)
        self.assertEqual(self.k.state(self.sid)["support_provenance"]["hints_count"], 1)

    def test_no_ceiling_keeps_the_legacy_ladder(self):
        self.k.install_lesson(self.sid, "vec-sim", sim_lesson_v1())
        self.k.begin_practice(self.sid, "vec-sim")
        self.k.help(self.sid, "vec-sim", "hint")
        self.k.help(self.sid, "vec-sim", "hint")
        self.assertTrue(self.k.help(self.sid, "vec-sim", "hint")["content"]["exhausted"])


class RevealTests(PracticeFirstFlowCase):
    def test_reveal_requires_explicit_confirmation(self):
        self.k.begin_practice(self.sid, "vec-sim")
        for confirm in (False, None, "yes", 1):
            with self.assertRaises(CourseStateError) as ctx:
                self.k.practice_reveal(self.sid, "vec-sim", confirm=confirm)
            self.assertEqual(ctx.exception.code, "reveal_confirmation_required")
        with self.assertRaises(CourseStateError):
            self.k.practice_reveal(self.sid, "vec-sim")
        self.assertFalse([r for r in self.records("observation") if r["payload"].get("verification") == "reveal"])
        self.assertFalse(self.k.state(self.sid)["support_provenance"]["ai_direct_answer_revealed"])

    def test_reveal_needs_a_started_practice(self):
        with self.assertRaises(CourseStateError):
            self.k.practice_reveal(self.sid, "vec-sim", confirm=True)             # teaching phase, no practice yet

    def test_reveal_records_an_observation_and_returns_the_solution(self):
        begun = self.k.begin_practice(self.sid, "vec-sim")["practice"]["practice_attempt_id"]
        evidence_before = len(self.records("evidence"))
        out = self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
        self.assertEqual(out["reference_solution"], sim_lesson()["practice"]["reference_solution"])
        self.assertEqual((out["revealed"], out["credit"]), (True, 0))
        reveals = [r for r in self.records("observation") if r["payload"].get("verification") == "reveal"]
        self.assertEqual(len(reveals), 1)
        payload = reveals[0]["payload"]
        self.assertIs(payload["correctness_checked"], False)
        self.assertIs(payload["mastery_eligible"], False)
        self.assertEqual((payload["capability_id"], payload["attempt_id"]), ("vec-sim", begun))
        self.assertEqual(len(self.records("evidence")), evidence_before)         # an observation, never evidence
        self.assertTrue(self.k.state(self.sid)["support_provenance"]["ai_direct_answer_revealed"])
        again = self.k.practice_reveal(self.sid, "vec-sim", confirm=True)        # re-delivery, not a second reveal
        self.assertEqual(again["reference_solution"], out["reference_solution"])
        self.assertEqual(len([r for r in self.records("observation") if r["payload"].get("verification") == "reveal"]), 1)
        self.assertEqual(self.progress()["help"]["reveal"], 1)

    def test_a_reveal_replay_with_the_same_idempotency_key_still_delivers_the_solution(self):
        self.k.begin_practice(self.sid, "vec-sim")
        first = self.k.practice_reveal(self.sid, "vec-sim", confirm=True, idempotency_key="reveal-key-01")
        replay = self.k.practice_reveal(self.sid, "vec-sim", confirm=True, idempotency_key="reveal-key-01")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["result"]["reference_solution"], first["reference_solution"])
        self.assertEqual(len([r for r in self.records("observation") if r["payload"].get("verification") == "reveal"]), 1)

    def test_a_failed_commit_does_not_duplicate_the_reveal_observation(self):
        self.k.begin_practice(self.sid, "vec-sim")
        real, calls = self.k._commit, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk full")
            return real(*args, **kwargs)
        self.k._commit = flaky
        try:
            with self.assertRaises(OSError):
                self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
            out = self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
        finally:
            del self.k._commit
        self.assertTrue(out["revealed"])
        self.assertEqual(len([r for r in self.records("observation") if r["payload"].get("verification") == "reveal"]), 1)

    def test_after_a_reveal_a_new_attempt_is_required_and_evidence_is_assisted(self):
        old = self.k.begin_practice(self.sid, "vec-sim")["practice"]["practice_attempt_id"]
        self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
        self.submission().write_text(PRACTICE_CORRECT, encoding="utf-8")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.practice_check(self.sid, "vec-sim")                             # the revealed attempt earns nothing
        self.assertEqual(ctx.exception.code, "practice_revealed")
        self.assertFalse([r for r in self.records("evidence") if r["provenance"]["source"] == "course_practice"])
        fresh = self.k.begin_practice(self.sid, "vec-sim")["practice"]["practice_attempt_id"]
        self.assertNotEqual(fresh, old)
        self.assertEqual(self.k.begin_practice(self.sid, "vec-sim")["practice"]["practice_attempt_id"], fresh)   # stable again
        self.assertEqual([h["practice_attempt_id"] for h in self.progress()["practice_history"]],
                         [old])
        result = self.k.practice_check(self.sid, "vec-sim")
        self.assertTrue(result["passed"])
        evidence = [r["payload"] for r in self.records("evidence") if r["provenance"]["source"] == "course_practice"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual((evidence[0]["independence"], evidence[0]["scope"], evidence[0]["mastery_eligible"]),
                         ("assisted", "supported_completion", False))
        self.assertIs(evidence[0]["support_provenance"]["ai_direct_answer_revealed"], True)
        self.assertEqual(evidence[0]["attempt_id"], fresh)
        self.assertEqual(self.strong(), 0)



class ConceptPracticeTests(CourseCase):
    """A concept practice has no submission file: reveal returns the answer key, reset is refused."""

    def setUp(self):
        super().setUp()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        version = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli", commitments={"weekly_active_minutes": 90})
        self.k.install_items(self.sid, "vec-ideas", CONCEPT_ITEMS)
        self.k.install_lesson(self.sid, "vec-ideas", CONCEPT_LESSON)
        self.k.start_mission(self.sid, "vec-ideas")
        self.k.begin_practice(self.sid, "vec-ideas")

    def test_reveal_returns_the_answer_key_and_blocks_the_attempt(self):
        out = self.k.practice_reveal(self.sid, "vec-ideas", confirm=True)
        self.assertEqual(out["answer_key"], {"p1": "b"})
        with self.assertRaises(CourseStateError) as ctx:
            self.k.practice_answer(self.sid, "vec-ideas", {"p1": "b"})
        self.assertEqual(ctx.exception.code, "practice_revealed")
        self.k.begin_practice(self.sid, "vec-ideas")
        result = self.k.practice_answer(self.sid, "vec-ideas", {"p1": "b"})
        self.assertTrue(result["passed"])
        evidence = [r["payload"] for r in self.sk.ledger(self.sid).verified_records()
                    if r["record_type"] == "evidence" and r["provenance"]["source"] == "course_practice"]
        self.assertEqual((len(evidence), evidence[0]["independence"]), (1, "assisted"))
        self.assertIs(evidence[0]["support_provenance"]["ai_direct_answer_revealed"], True)

    def test_reset_is_refused_without_a_submission_file(self):
        with self.assertRaises(CourseStateError):
            self.k.practice_reset(self.sid, "vec-ideas")


class ResetTests(PracticeFirstFlowCase):
    def test_reset_archives_the_learner_file_then_restores_the_starter(self):
        self.k.begin_practice(self.sid, "vec-sim")
        starter = self.submission().read_text(encoding="utf-8")
        mine = "def dot(a, b):\n    return 42  # my work in progress\n"
        self.submission().write_text(mine, encoding="utf-8")
        out = self.k.practice_reset(self.sid, "vec-sim")
        archive = Path(out["archived"])
        self.assertEqual(archive.parent, self.d / "workspace" / "modules" / "vec-sim" / "archive")
        self.assertRegex(archive.name, r"^\d{8}T\d{12}Z-practice\.py$")
        self.assertEqual(archive.read_text(encoding="utf-8"), mine)
        self.assertEqual(self.submission().read_text(encoding="utf-8"), starter)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(archive.parent.stat().st_mode), 0o700)
        self.submission().write_text("# second try\n", encoding="utf-8")
        again = Path(self.k.practice_reset(self.sid, "vec-sim")["archived"])
        self.assertNotEqual(again, archive)
        self.assertEqual((archive.read_text(encoding="utf-8"), again.read_text(encoding="utf-8")), (mine, "# second try\n"))

    def test_a_reset_retry_does_not_archive_the_starter_again(self):
        self.k.begin_practice(self.sid, "vec-sim")
        self.submission().write_text("# mine\n", encoding="utf-8")
        real, calls = self.k._commit, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk full")
            return real(*args, **kwargs)
        self.k._commit = flaky
        try:
            with self.assertRaises(OSError):
                self.k.practice_reset(self.sid, "vec-sim")                 # file already restored, no event journaled
            out = self.k.practice_reset(self.sid, "vec-sim")
        finally:
            del self.k._commit
        archive = self.d / "workspace" / "modules" / "vec-sim" / "archive"
        self.assertIsNone(out["archived"])
        self.assertEqual([p.read_text(encoding="utf-8") for p in archive.iterdir()], ["# mine\n"])

    def test_reset_never_touches_the_ledger(self):
        self.k.begin_practice(self.sid, "vec-sim")
        before = len(self.sk.ledger(self.sid).verified_records())
        self.k.practice_reset(self.sid, "vec-sim")
        after = [r for r in self.sk.ledger(self.sid).verified_records()][before:]
        self.assertTrue(all(r["record_type"] == "event" for r in after))


class StickyRevealTests(CourseCase):
    """A node switch resets the mission-level provenance; the reveal must still taint later practice evidence."""

    def setUp(self):
        super().setUp()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        version = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, version, channel="cli", commitments={"weekly_active_minutes": 90})
        self.k.install_items(self.sid, "vec-sim", sim_items())
        self.k.install_lesson(self.sid, "vec-sim", sim_lesson())
        self.k.install_items(self.sid, "vec-ideas", CONCEPT_ITEMS)
        self.k.install_lesson(self.sid, "vec-ideas", CONCEPT_LESSON)
        self.k.start_mission(self.sid, "vec-sim")

    def test_switching_nodes_and_back_keeps_the_reveal_on_later_evidence(self):
        self.k.begin_practice(self.sid, "vec-sim")
        self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
        self.k.start_mission(self.sid, "vec-ideas")
        self.assertFalse(self.k.state(self.sid)["support_provenance"]["ai_direct_answer_revealed"])
        self.k.start_mission(self.sid, "vec-sim")
        self.k.begin_practice(self.sid, "vec-sim")
        path = self.d / "workspace" / "modules" / "vec-sim" / "submission" / "practice.py"
        path.write_text(PRACTICE_CORRECT, encoding="utf-8")
        self.assertTrue(self.k.practice_check(self.sid, "vec-sim")["passed"])
        evidence = [r["payload"] for r in self.sk.ledger(self.sid).verified_records()
                    if r["record_type"] == "evidence" and r["provenance"]["source"] == "course_practice"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["independence"], "assisted")
        self.assertIs(evidence[0]["support_provenance"]["ai_direct_answer_revealed"], True)


class RefusalTests(PracticeFirstFlowCase):
    def calls(self):
        return {"walkthrough": lambda: self.k.help(self.sid, "vec-sim", "walkthrough"),
                "ladder": lambda: self.k.help(self.sid, "vec-sim", "ladder", rung="completion"),
                "reveal": lambda: self.k.practice_reveal(self.sid, "vec-sim", confirm=True),
                "reset": lambda: self.k.practice_reset(self.sid, "vec-sim")}

    def test_everything_is_refused_during_an_exam(self):
        self.k.begin_practice(self.sid, "vec-sim")
        self.k.start_exam(self.sid, "vec-sim")
        before = len(self.sk.ledger(self.sid).verified_records())
        for name, call in self.calls().items():
            with self.assertRaises(CourseStateError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "exam_active", name)
        self.assertEqual(len(self.sk.ledger(self.sid).verified_records()), before)

    def test_everything_is_refused_when_grounding_is_invalid(self):
        self.k.begin_practice(self.sid, "vec-sim")
        self.manifest_with("unreadable")
        for name, call in self.calls().items():
            with self.assertRaises(CourseStateError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "grounding_invalid", name)

    def test_reveal_and_reset_belong_to_practice_phases(self):
        self.assertNotIn("practice_reveal", self.k.state(self.sid)["permitted_actions"])
        self.k.begin_practice(self.sid, "vec-sim")
        permitted = self.k.state(self.sid)["permitted_actions"]
        self.assertIn("practice_reveal", permitted)
        self.assertIn("practice_reset", permitted)

    def test_walkthrough_grounding_is_part_of_the_live_gate(self):
        lesson = sim_lesson()                                                   # only the walkthrough cites the source
        for part in ("explanation", "worked_example", "alternative_explanation"):
            lesson[part]["refs"] = [{"supplementary": True, "note": "Fixture framing."}]
        self.k.install_lesson(self.sid, "vec-sim", lesson)
        self.assertEqual(list(read_json(self.d / "course-state.json")["lesson_bindings"]["vec-sim"]["sources"]),
                         ["src-fixture-001"])
        self.assertEqual(self.k.help(self.sid, "vec-sim", "explain")["kind"], "explain")
        self.manifest_with("unreadable")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-sim", "explain")
        self.assertEqual(ctx.exception.code, "grounding_invalid")


class StudioV2Tests(StudioCase):
    def test_v2_is_the_default_policy_and_drains_to_checking(self):
        policy = plan_mod.resolve_policy()
        self.assertEqual(policy["id"], "studio-targets-v2")
        self.assertTrue(all(policy["targets"][k] for k in ("walkthrough_required", "ladder_required", "hint_ceiling_required")))
        job_id = self.st.init(self.sid)["job_id"]
        last = FakeAuthor().run(self.st, self.sid, job_id)
        self.assertEqual(last["state"], "done")
        self.assertEqual(jobs.load_job(self.studio_root(), job_id)["status"], "checking")
        self.assertEqual(jobs.load_job(self.studio_root(), job_id)["binding"]["policy_versions"]["policy"], "studio-targets-v2")
        self.assertTrue(self.st.check(self.sid, job_id)["build_complete"])

    def test_v2_refuses_lessons_without_the_practice_first_parts(self):
        job_id = self.st.init(self.sid)["job_id"]
        author = FakeAuthor(defects={("lesson", "vec-sim"): [sim_lesson_v1()]})
        task = self.st.next_task(self.sid, job_id)
        self.assertEqual(task["order"]["unit_id"], "lesson:vec-sim")
        contract = task["order"]["payload_contract"]
        self.assertTrue(contract["practice_first_fields"]["required_by_policy"]["walkthrough"])
        self.assertEqual(contract["practice_first_fields"]["walkthrough_label"], SIM_LABEL)
        self.assertEqual(task["order"]["labels"]["walkthrough_label_az"], SIM_LABEL)
        out = author.answer(self.st, self.sid, task)
        self.assertEqual(out["status"], "rejected")
        text = json.dumps(out)
        self.assertIn("a walkthrough is required", text)
        self.assertIn("a faded ladder is required", text)
        self.assertIn("hint_ceiling is required", text)

    def test_python_practice_lessons_need_a_python_walkthrough(self):
        lesson = sim_lesson()
        lesson["walkthrough"] = search_lesson()["walkthrough"]                  # language 'none'
        verdict = plan_mod.evaluate_unit("lesson", lesson, {"valid": True}, plan_mod.TARGETS_V2)
        self.assertFalse(verdict["complete"])
        self.assertIn("python walkthrough", verdict["problems"][0])
        concept = plan_mod.evaluate_unit("lesson", search_lesson_v1() | {"walkthrough": search_lesson()["walkthrough"]},
                                         {"valid": True}, plan_mod.TARGETS_V2)
        self.assertTrue(concept["complete"], concept)                            # concept lessons: language 'none' allowed

    def test_unknown_keys_accept_the_new_lesson_keys(self):
        self.assertEqual(plan_mod.unknown_keys("lesson", sim_lesson()), [])
        self.assertEqual(plan_mod.unknown_keys("lesson", {**sim_lesson(), "extra": 1}), ["extra"])

    def test_v2_targets_cannot_be_loosened(self):
        for key in ("walkthrough_required", "ladder_required", "hint_ceiling_required"):
            with self.assertRaises(Exception) as ctx:
                plan_mod.resolve_policy({"targets": {key: False}})
            self.assertEqual(ctx.exception.code, "studio_policy_invalid")

    def test_v1_jobs_stay_readable_and_enforceable(self):
        job_id = self.st.init(self.sid)["job_id"]
        job = jobs.load_job(self.studio_root(), job_id)
        job["policy"] = plan_mod.resolve_policy(None, "studio-targets-v1")
        job["binding"]["policy_versions"] = jobs.policy_versions("studio-targets-v1")
        jobs.save_job(self.studio_root(), job, self.clock().isoformat())
        status = self.st.status(self.sid, job_id)
        self.assertNotEqual(status["status"], "superseded")
        self.assertEqual(status.get("drift", []), [])
        library = dict(LIBRARY)
        library[("lesson", "vec-sim")] = sim_lesson_v1
        library[("lesson", "vec-search")] = search_lesson_v1
        last = FakeAuthor(library).run(self.st, self.sid, job_id)
        self.assertEqual(last["state"], "done")
        self.assertEqual(jobs.load_job(self.studio_root(), job_id)["status"], "checking")
        self.assertEqual(jobs.load_job(self.studio_root(), job_id)["policy"]["id"], "studio-targets-v1")


class InterfaceTests(PracticeFirstFlowCase):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = alearn.main(["--workspace", str(self.ws), "course", *argv, "--session", self.sid])
            except SystemExit as e:
                code = e.code
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None), err.getvalue()

    def test_cli_commands(self):
        self.k.begin_practice(self.sid, "vec-sim")
        code, out, _ = self.cli("help", "--node", "vec-sim", "--kind", "walkthrough")
        self.assertEqual((code, out["content"]["label"]), (0, SIM_LABEL))
        code, out, _ = self.cli("help", "--node", "vec-sim", "--kind", "ladder", "--rung", "completion")
        self.assertEqual((code, out["content"]["rung"]), (0, "completion"))
        self.submission().write_text("# mine\n", encoding="utf-8")
        code, out, _ = self.cli("practice-reset", "--node", "vec-sim")
        self.assertEqual((code, out["reset"]), (0, True))
        code, out, err = self.cli("practice-reveal", "--node", "vec-sim")
        self.assertNotEqual(code, 0)                                              # no --confirm
        code, out, _ = self.cli("practice-reveal", "--node", "vec-sim", "--confirm")
        self.assertEqual((code, out["revealed"]), (0, True))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iso = IsolatedState().__enter__()
        cls.tmp = tempfile.TemporaryDirectory()
        ws = Path(cls.tmp.name)
        sk = SessionKernel(ws)
        cls.sid = sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığı", stated_background="Python"))["session_id"]
        k = CourseKernel(ws)
        d = sk.dir(cls.sid)
        k.init(cls.sid)
        k.record_sources(cls.sid, manifest_for(d / "sources"), [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        version = k.propose_roadmap(cls.sid, roadmap())["roadmap_version"]
        k.approve_roadmap(cls.sid, version, channel="cli", commitments={"weekly_active_minutes": 60})
        k.install_items(cls.sid, "vec-sim", sim_items())
        k.install_lesson(cls.sid, "vec-sim", sim_lesson())
        k.start_mission(cls.sid, "vec-sim")
        cls.k, cls.d = k, d
        ui = ws / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
        cls.srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=ws, ui_dir=ui,
                                           course_session=cls.sid))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()
        cls.iso.__exit__()

    def post(self, path, body):
        rev = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/course/state", timeout=60).read())["revision"]
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST",
                                     data=json.dumps({"ui_schema": 2, "expected_revision": rev, **body}).encode())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_routes(self):
        code, body = self.post("/api/course/help", {"node": "vec-sim", "kind": "ladder", "rung": "completion"})
        self.assertEqual((code, body["content"]["rung"]), (200, "completion"))
        code, body = self.post("/api/course/help", {"node": "vec-sim", "kind": "walkthrough"})
        self.assertEqual((code, body["content"]["label"]), (200, SIM_LABEL))
        self.assertEqual(self.post("/api/course/practice/begin", {"node": "vec-sim"})[0], 200)
        code, body = self.post("/api/course/practice/reveal", {"node": "vec-sim"})
        self.assertEqual((code, body["error"]), (409, "reveal_confirmation_required"))
        code, body = self.post("/api/course/practice/reveal", {"node": "vec-sim", "confirm": "true"})
        self.assertEqual(code, 409)                                                # only a real boolean confirms
        code, body = self.post("/api/course/practice/reset", {"node": "vec-sim"})
        self.assertEqual((code, body["reset"]), (200, True))
        code, body = self.post("/api/course/practice/reveal", {"node": "vec-sim", "confirm": True})
        self.assertEqual((code, body["revealed"]), (200, True))
        self.assertIn("reference_solution", body)


if __name__ == "__main__":
    unittest.main()

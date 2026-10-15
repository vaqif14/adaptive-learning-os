"""Regressions for the adversarial review of the v7.1 remediation (one test per confirmed finding).

All data is the synthetic TEST FIXTURE course.
"""
import copy
import hashlib
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_content as cc  # noqa: E402
from runtime import course_state as cs  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.local_state import commit_state  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from course_fixture import (CONCEPT_ITEMS, CONCEPT_LESSON, NEAREST_REF, QUOTE, SOURCE_TEXT,  # noqa: E402
                            concept_roadmap, items, lesson, manifest_for, roadmap)
from test_v700_course_flow import CourseCase  # noqa: E402
from test_v710_audit_regressions import Clock, pass_exam  # noqa: E402
from test_v710_hardening import search_content  # noqa: E402


def nodes_of(st):
    return {n["id"]: n for n in st["roadmap"]["nodes"]}


def journal_as_v1(case, extra=None, keep_archive=False):
    v1 = cs.down_v2_to_v1(cs.down_v3_to_v2(read_json(case.d / "course-state.json")))
    if not keep_archive:
        v1.pop("v2_archive")
    v1.update(extra or {})
    commit_state(case.d, case.sk.ledger(case.sid), {"event_type": "test_simulated_v1_state"}, {"course-state.json": v1})


class StateMigrationReviewTests(CourseCase):
    def test_due_review_of_a_dependent_waits_for_its_superseded_prerequisite(self):
        clock = Clock(datetime.now(UTC) - timedelta(days=5))
        self.k = CourseKernel(self.ws, clock=clock)
        self.approve()
        self.author()
        pass_exam(self)
        its, les = search_content()
        self.k.install_items(self.sid, "vec-search", its)
        self.k.install_lesson(self.sid, "vec-search", les)
        pass_exam(self, node="vec-search", code=NEAREST_REF)
        changed = roadmap()
        changed["nodes"][0]["skills"] = ["dot product", "norm", "miqyas"]
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        clock.advance(days=3)
        st = self.k.state(self.sid)
        self.assertEqual(st["due_reviews"], [])                       # not surfaced while vec-sim is not re-shown
        self.assertNotEqual((st["next_mission"] or {}).get("type"), "due_review")
        self.assertEqual([q["status"] for q in st["review_queue"] if q["node_id"] == "vec-search"], ["scheduled"])

    def test_legacy_pending_removal_supersedes_the_removed_node(self):
        clock = Clock(datetime.now(UTC) - timedelta(days=5))
        self.k = CourseKernel(self.ws, clock=clock)
        self.approve()
        self.author()
        pass_exam(self)
        its, les = search_content()
        self.k.install_items(self.sid, "vec-search", its)
        self.k.install_lesson(self.sid, "vec-search", les)
        pass_exam(self, node="vec-search", code=NEAREST_REF)
        changed = roadmap()
        changed["nodes"] = changed["nodes"][:1]
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        journal_as_v1(self)
        self.k.state(self.sid)
        self.assertEqual(read_json(self.d / "course-state.json")["capabilities"]["vec-search"]["basis"], "legacy_removed")
        out = self.k.approve_roadmap(self.sid, v, channel="cli")
        self.assertIn("vec-search", out["superseded"]["passes"])
        st = self.k.state(self.sid)
        self.assertEqual([q["status"] for q in st["review_queue"] if q["node_id"] == "vec-search"], ["invalidated"])

    def test_legacy_binding_never_trusts_a_drifted_roadmap(self):
        self.approve()
        self.author()
        pass_exam(self)
        drifted = roadmap()
        drifted["nodes"][0]["outcomes"] = ["Kənardan dəyişdirilmiş nəticə"]
        self.sk.set_roadmap(self.sid, drifted, _course_internal=True)     # out-of-band install
        journal_as_v1(self)
        st = self.k.state(self.sid)
        raw = read_json(self.d / "course-state.json")
        self.assertEqual(raw["capabilities"]["vec-sim"]["basis"], "legacy_unverified")
        self.assertEqual(st["phase"], "roadmap_awaiting_approval")
        impact = cs.capability_impact(raw["capabilities"], read_json(self.d / "workspace" / "roadmap.json"))
        self.assertIn("vec-sim", impact["summary"]["unverified"])

    def test_down_then_up_restores_v2_bindings_instead_of_rebinding(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"][0]["task"] = "Dəyişmiş tapşırıq mətni."
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        before = nodes_of(self.k.state(self.sid))["vec-sim"]
        self.assertEqual(before["lesson"]["status"], "capability_changed")
        journal_as_v1(self, keep_archive=True)
        after = nodes_of(self.k.state(self.sid))["vec-sim"]
        self.assertEqual(after["lesson"]["status"], "capability_changed")
        self.assertEqual(after["fresh_items"], 0)
        self.assertEqual(len(self.k.state(self.sid)["capability_history"]), 1)

    def test_reverting_a_pending_change_restores_the_approved_course(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        changed = roadmap()
        changed["nodes"][0]["skills"] = ["başqa"]
        self.k.propose_roadmap(self.sid, changed)
        self.assertEqual(self.k.state(self.sid)["phase"], "roadmap_awaiting_approval")
        self.k.propose_roadmap(self.sid, roadmap())
        st = self.k.state(self.sid)
        self.assertEqual(st["phase"], "teaching")
        self.assertIsNone(st["roadmap"]["review_required"])
        self.assertIn("help", st["permitted_actions"])

    def test_idempotency_key_is_bound_to_its_arguments(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim", idempotency_key="start-mission-key-1")
        again = self.k.start_mission(self.sid, "vec-sim", idempotency_key="start-mission-key-1")
        self.assertTrue(again["idempotent_replay"])
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_mission(self.sid, "vec-search", idempotency_key="start-mission-key-1")
        self.assertIn("different arguments", str(ctx.exception))


class AssessmentReviewTests(CourseCase):
    def test_unverifiable_exposure_blocks_automatic_freshness(self):
        fp = cc.fingerprint(items()["items"][1])
        out = cc.classify_freshness(fp, [{"item_id": "lost-item", "unknown": True}])
        self.assertEqual(out["status"], "needs_review")
        self.assertEqual(out["unverifiable_exposures"], ["lost-item"])

    def test_practice_question_identical_to_an_exam_question_is_rejected(self):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        v = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][0]]
        self.k.install_items(self.sid, "vec-ideas", payload)
        leaky = copy.deepcopy(CONCEPT_LESSON)
        q = copy.deepcopy(CONCEPT_ITEMS["items"][0]["questions"][0])
        q["misconceptions"] = {}
        leaky["practice"]["questions"] = [q]
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_lesson(self.sid, "vec-ideas", leaky)
        self.assertIn("overlaps", str(ctx.exception))

    def test_reworded_concept_item_with_the_same_options_needs_review(self):
        base = copy.deepcopy(CONCEPT_ITEMS["items"][0])
        base["item_version"] = "it-x"
        exposed = [cc.exposure_record(base, "att_0000000000000001", "2026-10-12T00:00:00+00:00")]
        sibling = copy.deepcopy(base)
        sibling["item_id"] = "ideas-check-reworded"
        sibling["prompt_md"] = "Yeni tapşırıq: kosinus oxşarlığına dair bu suallara cavab yaz."
        for q in sibling["questions"]:
            q["prompt"] = q["prompt"].replace("?", " (izah et)?")
        out = cc.classify_freshness(cc.fingerprint(sibling), exposed)
        self.assertEqual(out["status"], "needs_review")

    def test_ordinary_exam_on_a_passed_node_keeps_the_review_schedule(self):
        clock = Clock(datetime.now(UTC) - timedelta(days=5))
        self.k = CourseKernel(self.ws, clock=clock)
        self.approve()
        self.author()
        pass_exam(self)
        first = [q for q in self.k.state(self.sid)["review_queue"] if q["status"] == "scheduled"]
        pass_exam(self, code=NEAREST_REF)                       # not via start_review
        now = [q for q in self.k.state(self.sid)["review_queue"] if q["status"] == "scheduled"]
        self.assertEqual([q["review_id"] for q in now], [q["review_id"] for q in first])

    def test_review_spacing_is_measured_from_the_ledger_start_event(self):
        clock = Clock(datetime.now(UTC) - timedelta(days=5))
        self.k = CourseKernel(self.ws, clock=clock)
        self.approve()
        self.author()
        pass_exam(self)
        clock.advance(days=2)
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        Path(att["file"]).write_text(NEAREST_REF, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        ev = next(r["payload"] for r in self.sk.ledger(self.sid).verified_records()
                  if r["payload"].get("evidence_id") == f"ev_exam_{att['attempt_id']}")
        self.assertIsNotNone(ev["review"]["observed_spacing_hours"])
        self.assertGreaterEqual(ev["review"]["observed_spacing_hours"], 0)
        self.assertFalse(ev["review"]["counts_as_delayed"])


class RubricReviewTests(CourseCase):
    RESPONSE = ("Dot product uzun vektorlarda böyüyür; uzunluqlara böləndə yalnız istiqamət qalır "
                "və nəticə müqayisə olunur.")

    def setUp(self):
        super().setUp()
        self.clock = Clock(datetime.now(UTC) - timedelta(days=5))
        self.k = CourseKernel(self.ws, clock=self.clock)
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        v = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")

    def judgment(self, confidence="medium", **over):
        j = {"grader": "host_agent", "confidence": confidence,
             "rationale": "Cavab normallaşdırmanın uzunluğu aradan qaldırdığını və istiqaməti saxladığını deyir.",
             "criteria": {"r-normalize": {"met": True, "evidence_quote": "uzunluqlara böləndə"},
                          "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}}}
        j.update(over)
        return j

    def test_malformed_misconception_list_is_a_rubric_refusal(self):
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][1]]
        self.k.install_items(self.sid, "vec-ideas", payload)
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.k.save_answers(self.sid, att["attempt_id"], {"response": self.RESPONSE})
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_rubric(self.sid, att["attempt_id"], self.judgment(misconceptions=[["a"]]))
        self.assertEqual(ctx.exception.code, "rubric_required")

    def test_low_confidence_review_is_not_retention(self):
        payload = copy.deepcopy(CONCEPT_ITEMS)
        self.k.install_items(self.sid, "vec-ideas", payload)
        exam = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]    # concept item (easier)
        self.k.save_answers(self.sid, exam["attempt_id"], {"q1": "c", "q2": ["a", "c"], "q3": ["s2", "s1", "s3"]})
        self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        self.k.advance(self.sid)
        self.clock.advance(days=2)
        att = self.k.start_review(self.sid, "vec-ideas")["attempt"]
        self.assertEqual(att["kind"], "free_text")
        self.k.save_answers(self.sid, att["attempt_id"], {"response": self.RESPONSE})
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.k.judge_and_finalize(self.sid, att["attempt_id"], self.judgment(confidence="low"))
        st = self.k.state(self.sid)
        nxt = [q for q in st["review_queue"] if q["status"] == "scheduled"]
        self.assertEqual((nxt[0]["recalls"], nxt[0]["basis"]), (0, "retry_uncertain"))
        self.assertEqual(nodes_of(st)["vec-ideas"]["progress"]["retention"]["status"], "uncertain")


class ReliabilityReviewTests(CourseCase):
    def test_crlf_source_text_grounds(self):
        text = SOURCE_TEXT.replace(". ", ".\r\n")
        stage = self.d / "sources-staging" / "stg_crlf"
        m = manifest_for(stage)
        (stage / "src-fixture-001.txt").write_bytes(text.encode("utf-8"))
        m["resources"][0]["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, m, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}],
                              staged_cache=stage)
        res = self.k._ground(self.d)("src-fixture-001", QUOTE)
        self.assertTrue(res["grounded"], res)

    def test_failed_install_commit_keeps_committed_content_ready(self):
        self.approve()
        self.author()
        changed = lesson()
        changed["objective"] = "Tamamilə yeni məqsəd mətni, commit alınmayan quraşdırma üçün."
        more = items()
        more["items"][1]["title"] = "Ən oxşar sənəd (yenilənmiş)"
        with patch("runtime.course.commit_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.k.install_lesson(self.sid, "vec-sim", changed)
            with self.assertRaises(OSError):
                self.k.install_items(self.sid, "vec-sim", more)
        node = nodes_of(CourseKernel(self.ws).state(self.sid))["vec-sim"]
        self.assertEqual(node["lesson"]["status"], "ready")
        self.assertEqual(node["fresh_items"], 2)
        self.k.start_mission(self.sid, "vec-sim")

    def test_huge_numeric_answer_is_refused_cleanly(self):
        self.assertIsNone(cc._number(10 ** 400))
        q = {"id": "n", "type": "numeric", "category": "core", "prompt": "Neçədir bu?", "answer": 1.0, "tolerance": 0.0,
             "fail_feedback": "Yenidən hesabla."}
        rows, _ = cc.grade_questions([q], {"n": 10 ** 400}, [])
        self.assertFalse(rows[0]["passed"])

    def test_practice_evidence_is_written_once_even_if_the_commit_fails(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self.k.begin_practice(self.sid, "vec-sim")
        path = self.d / "workspace/modules/vec-sim/submission/practice.py"
        path.write_text(lesson()["practice"]["reference_solution"], encoding="utf-8")
        real = CourseKernel._commit

        def fail_once(kernel, d, led, state, event, files=None):
            if event.get("event_type") == "course_practice_checked" and not getattr(fail_once, "done", False):
                fail_once.done = True
                raise OSError("crash before commit")
            return real(kernel, d, led, state, event, files)
        with patch.object(CourseKernel, "_commit", fail_once), self.assertRaises(OSError):
            self.k.practice_check(self.sid, "vec-sim")
        self.k.practice_check(self.sid, "vec-sim")                            # retry after restart
        ev = [r for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == "evidence"
              and r["provenance"].get("source") == "course_practice"]
        self.assertEqual(len(ev), 1)

    def test_export_is_create_only_and_leaves_parent_permissions_alone(self):
        import alearn
        self.approve()
        out_dir = self.ws / "exports"
        out_dir.mkdir()
        os.chmod(out_dir, 0o755)
        target = out_dir / "state-v1.json"
        rc = alearn.main(["--workspace", str(self.ws), "course", "schema-down", "--session", self.sid,
                          "--output", str(target)])
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(out_dir.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(alearn.main(["--workspace", str(self.ws), "course", "export", "--session", self.sid,
                                      "--output", str(target)]), 1)

    def test_dist_carry_forward_keeps_links_as_links(self):
        self.approve()
        dist = self.d / "workspace" / "frontend" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
        secret = self.ws / "outside-secret.txt"
        secret.write_text("TOP SECRET", encoding="utf-8")
        (dist / "leak.txt").symlink_to(secret)
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın k sənədi tap."
        self.k.propose_roadmap(self.sid, changed)
        carried = self.d / "workspace" / "frontend" / "dist" / "leak.txt"
        self.assertTrue(carried.is_symlink())                                # never materialized as a copy


if __name__ == "__main__":
    import unittest
    unittest.main()

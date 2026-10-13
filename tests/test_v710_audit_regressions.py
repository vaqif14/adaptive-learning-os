"""Regressions for the learning-integrity audit at df88a0c (written red-first).

F1 renamed/cosmetic duplicate items must not yield fresh independent evidence.
F2 material capability changes invalidate current progress/readiness (history kept).
F3 stale or invalid grounding fails closed for help, readiness and labels.
F4 non-code (concept / free-text) assessments run through the same guards.
F5 delayed review is a durable, dispatchable queue driven by an injectable clock.
All data is the synthetic TEST FIXTURE course (no NotebookLM, no private content).
"""
import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.projection import EvidenceProjector  # noqa: E402
from course_fixture import (CONCEPT_ITEMS, CONCEPT_LESSON, EXAM_CORRECT, NEAREST_REF, SOURCE_TEXT,  # noqa: E402
                            concept_roadmap, items, manifest_for, roadmap)
from test_v700_course_flow import CourseCase  # noqa: E402


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + timedelta(**kw)


def strong(case, node="vec-sim"):
    proj = EvidenceProjector(case.sk.ledger(case.sid)).rebuild()
    return proj["demonstrated_capabilities"].get(node, {}).get("strong_unassisted_successes", 0)


def pass_exam(case, node="vec-sim", code=EXAM_CORRECT, kernel=None):
    k = kernel or case.k
    att = k.start_exam(case.sid, node, fast_track=True)["attempt"]
    Path(att["file"]).write_text(code, encoding="utf-8")
    out = k.submit_and_finalize(case.sid, att["attempt_id"])
    k.advance(case.sid)
    return att, out


class F1ItemIdentityTests(CourseCase):
    def _renamed_copy(self, item_id="cosine-a-renamed", cosmetic=False):
        payload = items()
        copy_item = copy.deepcopy(payload["items"][0])
        copy_item["item_id"] = item_id
        copy_item["checks"] = list(reversed(copy_item["checks"]))            # reordered
        for c in copy_item["checks"]:
            c["id"] = c["id"] + "-x"                                          # renamed check ids
            c["name"] = c["name"] + " "                                       # cosmetic
        if cosmetic:
            copy_item["prompt_md"] = copy_item["prompt_md"].replace("Yeni tapşırıq:", "Yeni  tapşırıq :")
        return {"node_id": "vec-sim", "items": [copy_item]}

    def test_renamed_duplicate_is_not_offered_as_a_fresh_item(self):
        self.approve()
        self.author()
        pass_exam(self)
        self.assertEqual(strong(self), 1)
        self.k.install_items(self.sid, "vec-sim", self._renamed_copy())
        att = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertNotEqual(att["item"]["item_id"], "cosine-a-renamed")   # the genuinely fresh item is chosen

    def test_cosmetic_duplicate_cannot_add_strong_evidence(self):
        self.approve()
        self.author()
        pass_exam(self)
        pass_exam(self, code=NEAREST_REF)                                  # exhaust the genuinely fresh item
        self.k.install_items(self.sid, "vec-sim", self._renamed_copy("cosine-a-cosmetic", cosmetic=True))
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "no_fresh_item")
        self.assertEqual(strong(self), 2)

    def test_reworded_prompt_with_identical_hidden_checks_is_a_duplicate(self):
        self.approve()
        self.author()
        pass_exam(self)
        pass_exam(self, code=NEAREST_REF)
        reworded = self._renamed_copy("cosine-a-reworded")
        reworded["items"][0]["checks"][0]["code"] += "  # comment only"
        reworded["items"][0]["prompt_md"] = ("Yeni tapşırıq: iki vektor arasında kosinus oxşarlığını qaytaran "
                                             "`cosine(a, b)` yaz; uzunluğu sıfır olan vektorda 0.0 olsun.")
        out = self.k.install_items(self.sid, "vec-sim", reworded)
        self.assertEqual(out["freshness"]["cosine-a-reworded"]["status"], "duplicate")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "no_fresh_item")

    def test_near_duplicate_needs_review_and_is_not_auto_fresh(self):
        self.approve()
        self.author()
        pass_exam(self)
        pass_exam(self, code=NEAREST_REF)
        near = self._renamed_copy("cosine-a-near")
        near["items"][0]["prompt_md"] = ("Yeni tapşırıq: kosinus funksiyasını `cosine(a, b)` adı ilə yaz; "
                                         "sıfır uzunluqlu vektorda nəticə 0.0 olsun.")
        # 3 of 4 hidden checks identical, one with new values: similar, not provably the same task.
        near["items"][0]["checks"][0]["code"] = ("assert abs(cosine([2,0],[7,0]) - 1) < 1e-9 "
                                                 "and abs(cosine([0,1],[0,-3]) + 1) < 1e-9")
        out = self.k.install_items(self.sid, "vec-sim", near)
        self.assertEqual(out["freshness"]["cosine-a-near"]["status"], "needs_review")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "no_fresh_item")


class F2CapabilityVersionTests(CourseCase):
    def test_material_change_invalidates_current_progress_but_keeps_history(self):
        self.approve()
        self.author()
        pass_exam(self)
        before = [r["record_id"] for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == "evidence"]
        changed = roadmap()
        changed["nodes"][0]["outcomes"] = ["Kosinus və Evklid məsafəsini müqayisə edə bilir"]
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        st = self.k.state(self.sid)
        node = {n["id"]: n for n in st["roadmap"]["nodes"]}
        self.assertNotEqual(node["vec-sim"]["status"], "passed")
        self.assertNotEqual(node["vec-sim"]["lesson"]["status"], "ready")
        self.assertEqual(node["vec-search"]["status"], "locked")            # dependency impact
        after = [r["record_id"] for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == "evidence"]
        self.assertEqual(before, after[:len(before)])                       # history untouched
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_mission(self.sid, "vec-sim")                       # lesson needs reauthoring/rebinding
        self.assertEqual(ctx.exception.code, "capability_changed")

    def test_cosmetic_and_unrelated_changes_keep_progress(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın sənədləri tap (yeni ifadə)."   # only the other node
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        node = {n["id"]: n for n in self.k.state(self.sid)["roadmap"]["nodes"]}
        self.assertEqual(node["vec-sim"]["status"], "passed")
        self.assertEqual(node["vec-sim"]["lesson"]["status"], "ready")


class F3GroundingTests(CourseCase):
    def _manifest_with(self, status=None, text=None):
        stage = self.d / "sources-staging" / "stg_fx"
        m = manifest_for(stage)
        if text is not None:
            import hashlib
            (stage / "src-fixture-001.txt").write_text(text, encoding="utf-8")
            m["resources"][0]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            m["resources"][0]["text_sha256"] = m["resources"][0]["content_sha256"]
        if status is not None:
            m["resources"][0]["status"] = status
        m["manifest_version"] = "sm-changed-" + (status or "text")
        return m

    def _record(self, m, kernel=None):
        (kernel or self.k).record_sources(self.sid, m, [{"key": "r1", "kind": "url",
                                                         "locator": "https://example.org/vectors"}],
                                          staged_cache=self.d / "sources-staging" / "stg_fx")

    def test_unreadable_source_blocks_help_readiness_and_label(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self._record(self._manifest_with(status="unreadable"))
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-sim", "explain")
        self.assertEqual(ctx.exception.code, "grounding_invalid")
        node = {n["id"]: n for n in self.k.state(self.sid)["roadmap"]["nodes"]}["vec-sim"]
        self.assertNotEqual(node["lesson"]["status"], "ready")
        self.assertEqual(node["lesson"]["grounding"], "invalid")

    def test_changed_but_valid_source_requires_explicit_regrounding(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self._record(self._manifest_with(text=SOURCE_TEXT + " New appended paragraph."))
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-sim", "explain")
        self.assertEqual(ctx.exception.code, "grounding_invalid")
        out = self.k.reground(self.sid, "vec-sim")
        self.assertEqual(out["status"], "rebound")
        self.assertEqual(self.k.help(self.sid, "vec-sim", "explain")["content"]["refs"][0]["grounding"], "source")

    def test_interrupted_source_commit_keeps_committed_grounding(self):
        self.approve()
        self.author()
        with patch("runtime.course.commit_state", side_effect=OSError("crash during commit")), \
                self.assertRaises(OSError):
            self._record(self._manifest_with(text=SOURCE_TEXT + " Totally different."))
        # The committed grounding text is untouched by an uncommitted refresh.
        self.assertEqual((self.d / "sources" / "src-fixture-001.txt").read_text(encoding="utf-8"), SOURCE_TEXT)
        restarted = CourseKernel(self.ws)
        restarted.start_mission(self.sid, "vec-sim")
        self.assertEqual(restarted.help(self.sid, "vec-sim", "explain")["content"]["refs"][0]["grounding"], "source")


class F4NonCodeTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        v = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")

    def test_concept_lesson_practice_and_fresh_exam_are_graded_deterministically(self):
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][0]]                         # objective concept item only
        self.k.install_items(self.sid, "vec-ideas", payload)
        self.k.install_lesson(self.sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        self.k.start_mission(self.sid, "vec-ideas")
        self.k.begin_practice(self.sid, "vec-ideas")
        wrong = self.k.practice_answer(self.sid, "vec-ideas", {"p1": "a"})
        self.assertFalse(wrong["passed"])
        self.assertTrue(wrong["misconceptions"])
        right = self.k.practice_answer(self.sid, "vec-ideas", {"p1": "b"})
        self.assertTrue(right["passed"])
        att = self.k.start_exam(self.sid, "vec-ideas")["attempt"]
        self.assertNotIn('"answer"', json.dumps(att["item"]))
        self.k.save_answers(self.sid, att["attempt_id"], {"q1": "c", "q2": ["a", "c"], "q3": ["s2", "s1", "s3"]})
        done = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.assertEqual(done["assessment_result"]["outcome"], "correct")
        ev = [r["payload"] for r in self.sk.ledger(self.sid).verified_records()
              if r["payload"].get("evidence_id") == f"ev_exam_{att['attempt_id']}"][0]
        self.assertNotEqual(ev["strength"], "strong")                    # selected response is never strong

    def test_free_text_needs_rubric_judgment_before_progress(self):
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][1]]                         # the free-text item only
        self.k.install_items(self.sid, "vec-ideas", payload)
        self.k.install_lesson(self.sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.k.save_answers(self.sid, att["attempt_id"], {"response": "Uzunluğa bölürük ki, yalnız istiqamət qalsın."})
        out = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.assertEqual(out["status"], "awaiting_rubric")
        self.assertTrue(self.k.state(self.sid)["teaching_locked"])
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-ideas", "explain")
        self.assertEqual(ctx.exception.code, "exam_active")


class F5ReviewQueueTests(CourseCase):
    def test_due_review_is_durable_dispatched_and_completed(self):
        clock = Clock(datetime(2026, 10, 13, 9, 0, tzinfo=UTC))
        k = CourseKernel(self.ws, clock=clock)
        self.k = k
        self.approve()
        self.author()
        pass_exam(self, kernel=k)
        queue = k.state(self.sid)["review_queue"]
        self.assertEqual([(q["node_id"], q["status"]) for q in queue], [("vec-sim", "scheduled")])
        self.assertEqual(k.state(self.sid)["due_reviews"], [])
        clock.advance(hours=25)
        restarted = CourseKernel(self.ws, clock=clock)
        turn = restarted.turn(self.sid)
        self.assertEqual([r["node_id"] for r in turn["due_reviews"]], ["vec-sim"])
        self.assertEqual(turn["next_mission"]["type"], "due_review")
        att = restarted.start_review(self.sid, "vec-sim")["attempt"]
        again = restarted.start_review(self.sid, "vec-sim")
        self.assertEqual(again["attempt"]["attempt_id"], att["attempt_id"])   # duplicate start is idempotent
        Path(att["file"]).write_text(NEAREST_REF, encoding="utf-8")
        restarted.submit_and_finalize(self.sid, att["attempt_id"])
        q = restarted.state(self.sid)["review_queue"]
        self.assertEqual(len([x for x in q if x["status"] == "completed"]), 1)
        nxt = [x for x in q if x["status"] == "scheduled"]
        self.assertEqual(len(nxt), 1)
        self.assertGreater(nxt[0]["due_at"], (clock.t + timedelta(days=1)).isoformat())

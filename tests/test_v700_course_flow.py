"""Course action layer end-to-end on real persisted state (ledger + journaled files).

Uses the synthetic TEST FIXTURE course in course_fixture.py. No NotebookLM calls:
the source manifest here is a labeled fixture (see test_v700_course_sources.py for
the mocked-CLI validation tests).
"""
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel, FORBIDDEN_TONE  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.projection import EvidenceProjector  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.utils import read_json, write_json  # noqa: E402
import runtime.course_content as cc  # noqa: E402
from course_fixture import (IsolatedState, roadmap, lesson, items, manifest_for, PRACTICE_WRONG_ADD,  # noqa: E402
                            PRACTICE_CORRECT, EXAM_CORRECT, EXAM_NO_NORMALIZE, NEAREST_REF)


class CourseCase(unittest.TestCase):
    def setUp(self):
        self.iso = IsolatedState().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.sk = SessionKernel(self.ws)
        s = self.sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığını hesablamaq",
                                                        stated_background="Python əsasları"))
        self.sid = s["session_id"]
        self.k = CourseKernel(self.ws)
        self.d = self.sk.dir(self.sid)

    def tearDown(self):
        self.tmp.cleanup()
        self.iso.__exit__()

    def approve(self, **commit):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        r = self.k.propose_roadmap(self.sid, roadmap())
        return self.k.approve_roadmap(self.sid, r["roadmap_version"], channel="cli",
                                      commitments=commit or {"weekly_active_minutes": 90})

    def author(self):
        self.k.install_items(self.sid, "vec-sim", items())
        self.k.install_lesson(self.sid, "vec-sim", lesson())

    def write(self, rel, code):
        p = self.d / "workspace" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")

    def exam_file(self, attempt):
        return Path(attempt["file"])

    def evidence(self):
        return [r for r in self.sk.ledger(self.sid).verified_records() if r["record_type"] == "evidence"]

    def strong(self, node="vec-sim"):
        proj = EvidenceProjector(self.sk.ledger(self.sid)).rebuild()
        return proj["demonstrated_capabilities"].get(node, {}).get("strong_unassisted_successes", 0)


class ApprovalGateTests(CourseCase):
    def test_generation_blocked_until_explicit_current_approval(self):
        self.k.init(self.sid)
        r = self.k.propose_roadmap(self.sid, roadmap())
        self.assertEqual(self.k.state(self.sid)["phase"], "roadmap_awaiting_approval")
        self.assertTrue((self.d / "workspace" / "roadmap.html").is_file())
        for call in (lambda: self.k.install_lesson(self.sid, "vec-sim", lesson()),
                     lambda: self.k.install_items(self.sid, "vec-sim", items()),
                     lambda: self.k.start_mission(self.sid, "vec-sim"),
                     lambda: self.k.start_exam(self.sid, "vec-sim", fast_track=True)):
            with self.assertRaises(CourseStateError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "roadmap_not_approved")
        self.assertEqual(self.sk.next_action(self.sid)["action"], "await_roadmap_approval")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.approve_roadmap(self.sid, "rm-0000000000000000", channel="ui")
        self.assertEqual(ctx.exception.code, "roadmap_version_mismatch")
        ok = self.k.approve_roadmap(self.sid, r["roadmap_version"], channel="ui",
                                    commitments={"weekly_active_minutes": 90,
                                                 "checkpoints": [{"node_id": "vec-sim", "due": "2026-12-01"}]})
        state = self.k.state(self.sid)
        self.assertEqual(state["phase"], "course_generation")
        self.assertEqual(state["roadmap"]["approval_event"]["roadmap_version"], r["roadmap_version"])
        self.assertEqual(state["roadmap"]["approval_event"]["approved_by"], "learner")
        self.assertEqual(ok["next_mission"]["node_id"], "vec-sim")
        self.assertEqual(ok["next_mission"]["blocked_on"], "lesson_content_missing")
        approvals = [r for r in self.sk.ledger(self.sid).verified_records()
                     if r["payload"].get("event_type") == "course_roadmap_approved"]
        self.assertEqual(len(approvals), 1)

    def test_material_change_invalidates_approval_cosmetic_does_not(self):
        self.approve()
        self.author()
        v1 = self.k.state(self.sid)["roadmap"]["version"]
        cosmetic = roadmap()
        cosmetic["nodes"][0]["pitfalls"] = ["Fərqli ifadə"]
        out = self.k.propose_roadmap(self.sid, cosmetic)
        self.assertFalse(out["changed"])
        self.assertTrue(self.k.state(self.sid)["roadmap"]["approved"])
        changed = roadmap()
        changed["nodes"][1]["requires"] = []
        out = self.k.propose_roadmap(self.sid, changed)
        self.assertTrue(out["approval_invalidated"])
        st = self.k.state(self.sid)
        self.assertEqual(st["phase"], "roadmap_awaiting_approval")
        self.assertFalse(st["roadmap"]["approved"])
        self.assertEqual(st["roadmap"]["review_required"]["previous_version"], v1)
        self.assertIn({"change": "node_changed", "node": "vec-search", "fields": ["requires"]},
                      st["roadmap"]["review_required"]["changes"])
        with self.assertRaises(CourseStateError):
            self.k.start_mission(self.sid, "vec-sim")
        # Lessons and their learner files survive the roadmap revision.
        self.assertTrue((self.d / "workspace/modules/vec-sim/submission/practice.py").is_file())
        self.assertEqual(st["content_versions"]["vec-sim"][:3], "lc-")

    def test_legacy_roadmap_session_requires_approval_on_init(self):
        s = self.sk.start("Legacy", LearningContract(goal="g"), roadmap=roadmap())
        init = self.k.init(s["session_id"])
        self.assertEqual(init["phase"], "roadmap_awaiting_approval")
        self.assertEqual(init["roadmap"]["review_required"]["reason"], "legacy_roadmap_requires_approval")

    def test_roadmap_html_is_escaped_and_csp_pinned(self):
        self.k.init(self.sid)
        hostile = roadmap()
        hostile["nodes"][0]["title"] = "</script><script>alert(1)</script>"
        self.k.propose_roadmap(self.sid, hostile)
        import base64
        import hashlib
        import html as htmllib
        import re
        html = (self.d / "workspace" / "roadmap.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)", html)
        csp = htmllib.unescape(re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1))
        pinned = re.search(r"script-src 'sha256-([^']+)'", csp).group(1)
        inline = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
        self.assertEqual(pinned, base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode())
        self.assertIn("default-src 'none'", csp)


class ContentTests(CourseCase):
    def test_nonempty_grounded_lesson_required_and_examples_kept_apart_from_items(self):
        self.approve()
        empty = lesson()
        empty["explanation"]["body"] = ""
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_lesson(self.sid, "vec-sim", empty)
        self.assertIn("explanation.body", ctx.exception.detail)
        ungrounded = lesson()
        ungrounded["explanation"]["refs"] = [{"source_id": "src-fixture-001", "quote": "a sentence that is not in the source text"}]
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_lesson(self.sid, "vec-sim", ungrounded)
        self.assertIn("quote_not_found_in_source", ctx.exception.detail)
        self.k.install_items(self.sid, "vec-sim", items())
        leaky = lesson()
        leaky["worked_example"]["problem"] = items()["items"][0]["prompt_md"]
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_lesson(self.sid, "vec-sim", leaky)
        self.assertIn("overlaps assessment item", ctx.exception.detail)
        bad_items = items()
        bad_items["items"][0]["checks"] = [c for c in bad_items["items"][0]["checks"] if c["category"] != "transfer"]
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_items(self.sid, "vec-sim", bad_items)
        self.assertIn("transfer", ctx.exception.detail)
        broken_ref = items()
        broken_ref["items"][0]["reference_solution"] = "def cosine(a, b):\n    return 1\n"
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_items(self.sid, "vec-sim", broken_ref)
        self.assertIn("reference solution fails", ctx.exception.detail)
        self.k.install_lesson(self.sid, "vec-sim", lesson())
        node = next(n for n in self.k.state(self.sid)["roadmap"]["nodes"] if n["id"] == "vec-sim")
        self.assertEqual(node["lesson"]["status"], "ready")
        self.assertEqual(node["lesson"]["grounding"], "mixed")
        other = next(n for n in self.k.state(self.sid)["roadmap"]["nodes"] if n["id"] == "vec-search")
        self.assertEqual(other["lesson"]["status"], "missing")   # never labelled ready without content

    def test_hidden_checks_and_keys_never_reach_learner_workspace(self):
        self.approve()
        self.author()
        started = self.k.start_exam(self.sid, "vec-sim", fast_track=True)
        public = json.dumps(started)
        self.assertNotIn("reference_solution", public)
        self.assertNotIn("cosine([3,4],[6,8])", public)        # hidden transfer check input
        workspace_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                                   for p in (self.d / "workspace").rglob("*") if p.is_file())
        self.assertNotIn("cosine([3,4],[6,8])", workspace_text)
        self.assertNotIn("na == 0 or nb == 0", workspace_text)  # reference solution body
        self.assertTrue((self.d / "assessment-vault/vec-sim/cosine-a.json").is_file())


class LearnerPathTests(CourseCase):
    def test_beginner_path_teach_practice_then_fresh_unassisted_exam(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        explain = self.k.help(self.sid, "vec-sim", "explain")          # "Bilmirəm" / "İzah et"
        self.assertTrue(explain["content"]["short"])
        self.assertEqual(explain["content"]["refs"][0]["grounding"], "source")
        example = self.k.help(self.sid, "vec-sim", "worked_example")
        self.assertNotEqual(example["content"]["problem"], explain["content"]["body"])
        self.assertIn("imtahan tapşırığı fərqli", example["content"]["note"])
        with self.assertRaises(CourseStateError):
            self.k.help(self.sid, "vec-sim", "hint")                   # hints belong to practice
        self.k.begin_practice(self.sid, "vec-sim")
        hint = self.k.help(self.sid, "vec-sim", "hint")
        self.assertEqual(hint["content"]["level"], 1)
        self.write("modules/vec-sim/submission/practice.py", PRACTICE_WRONG_ADD)
        first = self.k.practice_check(self.sid, "vec-sim")
        self.assertFalse(first["passed"])
        self.assertEqual([m["id"] for m in first["misconceptions"]], ["add-not-multiply"])
        dot_row = next(c for c in first["checks"] if c["id"] == "dot-basic")
        self.assertEqual(dot_row["observed"], "10")                     # feedback tied to the actual answer
        self.assertTrue(any("Sənin nəticən: 10" in line for line in first["feedback_az"]))
        self.assertFalse(first["method_change"])
        second = self.k.practice_check(self.sid, "vec-sim")
        self.assertTrue(second["method_change"])                       # recurring error -> change method
        alt = self.k.help(self.sid, "vec-sim", "alternative")
        self.assertIn("ox", alt["content"]["body"])
        self.write("modules/vec-sim/submission/practice.py", PRACTICE_CORRECT)
        ok = self.k.practice_check(self.sid, "vec-sim")
        self.assertTrue(ok["passed"])
        practice_ev = [e["payload"] for e in self.evidence() if e["provenance"]["source"] == "course_practice"]
        self.assertEqual(len(practice_ev), 3)
        self.assertTrue(all(e["scope"] == "supported_completion" and e["independence"] == "assisted" for e in practice_ev))
        self.assertEqual(self.strong(), 0)                             # guided success is not mastery
        exam = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertEqual(exam["item"]["item_id"], "cosine-a")
        self.assertNotIn(exam["item"]["prompt_md"], json.dumps(example))
        self.exam_file(exam).write_text(EXAM_CORRECT, encoding="utf-8")
        done = self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        self.assertEqual(done["status"], "finalized")
        self.assertEqual(done["assessment_result"]["outcome"], "correct")
        ev = [e["payload"] for e in self.evidence() if e["provenance"]["source"] == "course_exam_verifier"]
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["independence"], ev[0]["strength"], ev[0]["verification"]),
                         ("unassisted", "strong", "hidden_checks"))
        self.assertEqual(ev[0]["support_provenance"]["hints_count"], 0)
        self.assertEqual(self.strong(), 1)
        mission = done["next_mission"]
        self.assertEqual((mission["type"], mission["node_id"]), ("next_node", "vec-search"))
        self.assertIn("ustalıq hələ təsdiqlənməyib", mission["uncertainty_az"])
        self.assertEqual(mission["blocked_on"], "lesson_content_missing")
        st = self.k.state(self.sid)
        self.assertEqual(st["phase"], "feedback")
        self.assertEqual(st["learner_profile"]["gaps"], [])
        self.k.advance(self.sid)
        self.assertEqual(self.k.state(self.sid)["phase"], "next_mission")

    def test_prepared_learner_demonstrates_first_on_fresh_task(self):
        self.approve()
        self.author()
        exam = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        self.assertTrue(exam["fast_track"])
        self.exam_file(exam).write_text(EXAM_CORRECT, encoding="utf-8")
        done = self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        self.assertEqual(done["assessment_result"]["outcome"], "correct")
        self.assertEqual(self.strong(), 1)
        self.assertEqual(self.k.state(self.sid)["roadmap"]["nodes"][1]["status"], "available")

    def test_failed_exam_gives_misconception_feedback_and_a_new_item(self):
        self.approve()
        self.author()
        exam = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        self.exam_file(exam).write_text(EXAM_NO_NORMALIZE, encoding="utf-8")
        done = self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        res = done["assessment_result"]
        self.assertEqual(res["outcome"], "partial")                   # core passes on unit vectors; transfer fails
        self.assertIn("no-normalize", [m["id"] for m in res["misconception_feedback"]])
        self.assertFalse(any("cosine(" in (c.get("feedback") or "") for c in res["checks"]))  # hidden inputs stay hidden
        self.assertEqual(done["next_mission"]["type"], "targeted_practice")
        self.assertEqual(self.strong(), 0)
        self.k.advance(self.sid)
        retry = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertEqual(retry["item"]["item_id"], "nearest-b")        # exposed item never reused
        self.exam_file(retry).write_text(NEAREST_REF, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, retry["attempt_id"])
        self.k.advance(self.sid)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "no_fresh_item")


class ExamIntegrityTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self.exam = self.k.start_exam(self.sid, "vec-sim")["attempt"]

    def test_active_exam_locks_teaching_everywhere_and_resumes_same_attempt(self):
        for call in (lambda: self.k.help(self.sid, "vec-sim", "explain"),
                     lambda: self.k.lesson_view(self.sid, "vec-sim"),
                     lambda: self.k.start_mission(self.sid, "vec-sim"),
                     lambda: self.k.begin_practice(self.sid, "vec-sim"),
                     lambda: self.k.self_explanation(self.sid, "vec-sim", "dot bölmək uzunluq"),
                     lambda: self.sk.verify_python(self.sid, "print(1)", capability="vec-sim"),
                     lambda: self.sk.attest(self.sid, "vec-sim", assessor="Dr. External")):
            with self.assertRaises(CourseStateError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "exam_active")
        self.assertEqual(self.sk.next_action(self.sid)["action"], "exam_in_progress")
        again = self.k.start_exam(self.sid, "vec-sim")            # refresh / second tab
        self.assertTrue(again["resumed"])
        self.assertEqual(again["attempt"]["attempt_id"], self.exam["attempt_id"])
        restarted = CourseKernel(self.ws)                         # process restart
        self.assertEqual(restarted.state(self.sid)["active_attempt"]["attempt_id"], self.exam["attempt_id"])
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-search")
        self.assertEqual(ctx.exception.code, "exam_active")
        turn = self.k.turn(self.sid)
        self.assertFalse(turn["teaching_allowed"])
        self.assertIn("EXAM LOCK", turn["agent_directive"])
        self.assertEqual(set(turn["permitted_actions"]), {"submit_exam", "abandon_exam"})

    def test_duplicate_submit_and_finalize_count_once(self):
        self.exam_file(self.exam).write_text(EXAM_CORRECT, encoding="utf-8")
        a = self.k.submit_exam(self.sid, self.exam["attempt_id"], idempotency_key="submit-click-0001")
        b = self.k.submit_exam(self.sid, self.exam["attempt_id"], idempotency_key="submit-click-0001")
        self.assertTrue(b["idempotent_replay"])
        c = self.k.submit_exam(self.sid, self.exam["attempt_id"])
        self.assertTrue(c["duplicate"])
        self.assertEqual(a["status"], "submitted")
        self.k.score_exam(self.sid, self.exam["attempt_id"])
        self.k.score_exam(self.sid, self.exam["attempt_id"])
        self.k.finalize_exam(self.sid, self.exam["attempt_id"])
        again = self.k.finalize_exam(self.sid, self.exam["attempt_id"])
        self.assertTrue(again["duplicate"])
        exam_ev = [e for e in self.evidence() if e["payload"]["evidence_id"] == f"ev_exam_{self.exam['attempt_id']}"]
        self.assertEqual(len(exam_ev), 1)
        self.assertEqual(self.strong(), 1)

    def test_revision_conflict_rejects_stale_writer_without_side_effects(self):
        rev = self.k.state(self.sid)["revision"]
        self.exam_file(self.exam).write_text(EXAM_CORRECT, encoding="utf-8")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.submit_exam(self.sid, self.exam["attempt_id"], expected_revision=rev - 1)
        self.assertEqual(ctx.exception.code, "revision_conflict")
        self.assertEqual(self.k.state(self.sid)["attempt_status"], "active")
        self.k.submit_exam(self.sid, self.exam["attempt_id"], expected_revision=rev)
        self.assertEqual(self.k.state(self.sid)["revision"], rev + 1)

    def test_crash_between_evidence_and_state_commit_is_reconciled(self):
        self.exam_file(self.exam).write_text(EXAM_CORRECT, encoding="utf-8")
        self.k.submit_exam(self.sid, self.exam["attempt_id"])
        self.k.score_exam(self.sid, self.exam["attempt_id"])
        with patch.object(CourseKernel, "_finalize_state", side_effect=OSError("simulated crash")), \
                self.assertRaises(OSError):
            self.k.finalize_exam(self.sid, self.exam["attempt_id"])
        self.assertEqual(read_json(self.d / "course-state.json")["attempt_status"], "scored")
        state = CourseKernel(self.ws).state(self.sid)                   # next open reconciles
        self.assertEqual(state["phase"], "feedback")
        self.assertEqual(state["assessment_result"]["outcome"], "correct")
        self.assertEqual(len([e for e in self.evidence() if e["provenance"]["source"] == "course_exam_verifier"]), 1)

    def test_torn_materialized_state_is_replayed_from_the_journal(self):
        committed = read_json(self.d / "course-state.json")
        stale = {**committed, "revision": committed["revision"] - 3, "phase": "teaching", "active_attempt_id": None}
        write_json(self.d / "course-state.json", stale)                 # simulate a write lost after journaling
        state = self.k.state(self.sid)
        self.assertEqual(state["revision"], committed["revision"])
        self.assertEqual(state["phase"], "exam_active")

    def test_unavailable_scoring_is_resumable_and_keeps_the_lock(self):
        self.exam_file(self.exam).write_text(EXAM_CORRECT, encoding="utf-8")
        with patch.object(cc, "run_checks", side_effect=OSError("verifier down")):
            out = self.k.submit_and_finalize(self.sid, self.exam["attempt_id"])
        self.assertEqual(out["status"], "scoring_failed")
        self.assertTrue(out["resumable"])
        st = self.k.state(self.sid)
        self.assertEqual((st["phase"], st["attempt_status"]), ("assessment_pending", "scoring_failed"))
        with self.assertRaises(CourseStateError):
            self.k.help(self.sid, "vec-sim", "explain")
        self.assertEqual(self.strong(), 0)
        done = self.k.submit_and_finalize(self.sid, self.exam["attempt_id"])
        self.assertEqual(done["status"], "finalized")
        self.assertEqual(self.strong(), 1)

    def test_changed_item_version_blocks_scoring_and_abandon_is_never_success(self):
        self.exam_file(self.exam).write_text(EXAM_CORRECT, encoding="utf-8")
        vault = self.d / "assessment-vault/vec-sim/cosine-a.json"
        item = read_json(vault)
        item["checks"] = item["checks"][:1]                            # tampered / edited after start
        write_json(vault, item)
        out = self.k.submit_and_finalize(self.sid, self.exam["attempt_id"])
        self.assertEqual(out["reason"], "item_version_changed")
        aband = self.k.abandon_exam(self.sid, self.exam["attempt_id"], reason="item changed")
        self.assertFalse(aband["counted_as_success"])
        self.assertEqual(aband["next_mission"]["type"], "retry_later")
        st = self.k.state(self.sid)
        self.assertEqual(st["phase"], "teaching")
        self.assertEqual(st["roadmap"]["nodes"][0]["status"], "active")
        self.assertEqual(self.strong(), 0)
        obs = [r for r in self.sk.ledger(self.sid).verified_records()
               if r["record_type"] == "observation" and r["payload"].get("note") == "exam_abandoned"]
        self.assertEqual(len(obs), 1)
        nxt = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertNotEqual(nxt["item"]["item_id"], "cosine-a")


class PersistenceTests(CourseCase):
    def test_ui_regeneration_preserves_ids_evidence_and_work(self):
        self.approve()
        self.author()
        exam = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        self.exam_file(exam).write_text(EXAM_CORRECT, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        before = self.k.export(self.sid)
        out = self.k.regenerate_ui(self.sid)
        self.assertEqual(out["status"], "regenerated")
        after = self.k.export(self.sid)
        self.assertEqual([e["evidence_id"] for e in before["evidence"]], [e["evidence_id"] for e in after["evidence"]])
        self.assertEqual(before["course"]["revision"], after["course"]["revision"])
        self.assertEqual([a["attempt_id"] for a in before["attempts"]], [a["attempt_id"] for a in after["attempts"]])
        self.assertTrue(self.exam_file(exam).is_file())
        self.assertTrue(after["ledger_integrity"]["chain_ok"])

    def test_newer_schema_on_disk_is_refused_not_overwritten(self):
        self.approve()
        newer = {**read_json(self.d / "course-state.json"), "schema_version": 99}
        from runtime.local_state import commit_state
        commit_state(self.d, self.sk.ledger(self.sid), {"event_type": "test_newer_schema"},
                     {"course-state.json": newer})
        with self.assertRaises(CourseStateError) as ctx:
            self.k.state(self.sid)
        self.assertEqual(ctx.exception.code, "newer_state_schema")
        self.assertEqual(read_json(self.d / "course-state.json")["schema_version"], 99)

    def test_engagement_intervals_are_idempotent_and_not_double_counted(self):
        self.approve()
        now = datetime.now(UTC)
        start = (now - timedelta(minutes=10)).isoformat()

        def iv(seq, active, idle=0, ivid="iv_00000000000000aa"):
            return {"interval_id": ivid, "kind": "practice", "node_id": "vec-sim", "started_at": start,
                    "last_at": now.isoformat(), "active_seconds": active, "idle_seconds": idle,
                    "paused_seconds": 0, "seq": seq, "ended": False}
        self.assertEqual(self.k.record_activity(self.sid, iv(1, 60))["status"], "recorded")
        self.assertEqual(self.k.record_activity(self.sid, iv(1, 60))["status"], "duplicate_ignored")
        self.k.record_activity(self.sid, iv(2, 120, idle=30))
        with self.assertRaises(CourseStateError):
            self.k.record_activity(self.sid, iv(3, 90))                   # counters never decrease
        with self.assertRaises(CourseStateError):
            self.k.record_activity(self.sid, iv(4, 5000))                 # more than elapsed time
        self.k.record_activity(self.sid, iv(1, 30, ivid="iv_00000000000000bb"))   # refresh = new interval
        eng = CourseKernel(self.ws).state(self.sid)["engagement"]           # restart
        self.assertEqual(eng["by_kind"]["practice"]["active_seconds"], 150)
        self.assertEqual(eng["by_kind"]["practice"]["idle_seconds"], 30)
        self.assertEqual(eng["interval_count"], 2)

    def test_missed_commitment_is_stated_factually_once_with_one_recovery_task(self):
        self.approve(weekly_active_minutes=120)
        self.author()
        later = datetime.now(UTC) + timedelta(days=8)
        view = self.k.resume(self.sid, now=later)
        acc = view["accountability"]["open"]
        self.assertIsNotNone(acc)
        self.assertIn("Razılaşma: həftədə 120 dəqiqə", acc["message_az"])
        self.assertIn("Nə mane oldu?", acc["question_az"])
        self.assertIn("15 dəqiqə", acc["recovery_task"]["text_az"])
        text = json.dumps(acc, ensure_ascii=False).casefold()
        for word in FORBIDDEN_TONE:
            self.assertNotIn(word.casefold(), text)
        rev = self.k.state(self.sid)["revision"]
        self.k.resume(self.sid, now=later)                                # no duplicate opening
        self.assertEqual(self.k.state(self.sid)["revision"], rev)
        out = self.k.respond_accountability(self.sid, "time", note="iş həftəsi")
        self.assertEqual(out["recovery_checkpoint"]["kind"], "recovery_checkpoint")
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        self.assertIsNone(self.k.resume(self.sid, now=tomorrow)["accountability"]["open"])
        # Missing the agreed recovery checkpoint is a new, factual discrepancy.
        reopened = self.k.resume(self.sid, now=later)["accountability"]["open"]
        self.assertIn("recovery_checkpoint", [f["kind"] for f in reopened["findings"]])
        self.assertNotEqual(reopened["signature"], acc["signature"])

    def test_self_report_cannot_be_recorded_as_competence(self):
        self.k.init(self.sid)
        with self.assertRaises(CourseStateError):
            self.k.record_diagnostic(self.sid, {"entries": [{"prompt": "Python bilirsən?", "basis": "self_report",
                                                             "outcome": "correct"}]})
        out = self.k.record_diagnostic(self.sid, {
            "stated_background": "Python əsasları", "explanation_preferences": ["əvvəl konkret nümunə"],
            "entries": [{"prompt": "dot([1,2],[3,4])?", "basis": "observed", "outcome": "incorrect", "node_id": "vec-sim"}]})
        self.assertEqual(out["learner_profile"]["gaps"], ["vec-sim"])
        self.assertTrue(out["learner_profile"]["provisional"])
        self.assertEqual(self.k.state(self.sid)["phase"], "source_preparation")


if __name__ == "__main__":
    unittest.main()

"""Goal/diagnostic/source generation gates using clearly synthetic local data."""
import sys
import hashlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract
from runtime.course import CourseKernel
from runtime.course_state import CourseStateError
from runtime.learning_workspace import create_workspace
from runtime.utils import read_json
from course_fixture import items, lesson, manifest_for, roadmap
from test_v700_course_flow import CourseCase
from test_v713_intake_flow import probe, judgment


def confirmation(**changes):
    return {"kind": "learning", "statement": "Məqsədim vektor oxşarlığını müstəqil müqayisə etməkdir; bu məqsədi təsdiqləyirəm.", **changes}


def target(**changes):
    return {"kind": "certification", "official_name": "Synthetic certification target (TEST FIXTURE)",
            "code": "TEST-FIXTURE-001", "version": None, "version_note": "TEST FIXTURE: no published version.",
            "verified_at": "2026-10-13T10:00:00+00:00", "verification_url": "https://example.org/specification",
            "objectives": [{"domain": "TEST FIXTURE: compare vector directions."}], **changes}


class GenerationReadinessTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.sid = self.sk.start("Generation gates (TEST FIXTURE)", LearningContract(), defer_workspace=True)["session_id"]
        self.d = self.sk.dir(self.sid)
        self.k.init(self.sid, require_intake=True)

    def background(self, goal="Vektor oxşarlığını müstəqil müqayisə etmək"):
        self.k.record_intake(self.sid, {"goal": goal})
        self.k.record_intake(self.sid, {"stated_background": "Cədvəl hazırlamışam; vektorları hələ müqayisə etməmişəm."})

    def diagnostic(self):
        for payload in (probe(), probe(id="probe-two2", kind="scenario",
                 prompt="Yeni sənəd axtarışında uzun mətnlərin üstünlük qazanmaması üçün müqayisəni necə qurardın?")):
            self.k.install_diagnostic_probe(self.sid, payload)
            self.k.answer_diagnostic(self.sid, "İstiqamətləri müqayisə etmək üçün uzunluqları ayırardım.")
            self.k.judge_diagnostic(self.sid, judgment())

    def sources(self, **changes):
        manifest = manifest_for(self.d / "sources")
        manifest.update(changes)
        self.k.record_sources(self.sid, manifest,
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        return manifest

    def ready(self):
        self.background()
        self.diagnostic()
        self.k.confirm_goal(self.sid, confirmation())
        self.sources()

    def change_valid_source(self):
        """A real new readable fixture snapshot; the original lesson quote remains."""
        manifest = manifest_for(self.d / "sources")
        cached = self.d / "sources" / "src-fixture-001.txt"
        text = cached.read_text() + "\nNew TEST FIXTURE source revision: inspect changed assumptions before using this version.\n"
        cached.write_text(text)
        sha = hashlib.sha256(cached.read_bytes()).hexdigest()
        manifest["manifest_version"] = "sm-fixture-revised"
        manifest["resources"][0].update(content_sha256=sha, char_count=len(text.strip()))
        self.k.record_sources(self.sid, manifest,
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        return sha

    def assert_proposal_blocked_without_effects(self, plan=None):
        revision = self.k.state(self.sid)["revision"]
        html = self.d / "workspace" / "roadmap.html"
        before = html.read_bytes() if html.exists() else None
        with self.assertRaises(CourseStateError):
            self.k.propose_roadmap(self.sid, roadmap() if plan is None else plan)
        self.assertEqual(self.k.state(self.sid)["revision"], revision)
        self.assertEqual(html.read_bytes() if html.exists() else None, before)
        self.assertEqual(self.strong(), 0)

    def test_no_proposal_or_html_before_goal_background_diagnostic_confirmation_sources(self):
        self.assert_proposal_blocked_without_effects()
        self.background()
        self.assert_proposal_blocked_without_effects()
        self.diagnostic()
        self.assert_proposal_blocked_without_effects()
        self.k.confirm_goal(self.sid, confirmation())
        self.assert_proposal_blocked_without_effects()

    def test_sources_and_confirmation_cannot_replace_actual_performance_diagnostic(self):
        self.background()
        self.k.confirm_goal(self.sid, confirmation())
        self.sources()
        self.assert_proposal_blocked_without_effects()

    def test_happy_flow_persists_and_preserves_explicit_roadmap_approval_gate(self):
        self.ready()
        self.k = CourseKernel(self.ws)
        proposed = self.k.propose_roadmap(self.sid, roadmap())
        html = self.d / "workspace" / "roadmap.html"
        self.assertTrue(html.is_file())
        self.assertIn("roadmap-data", html.read_text())
        for operation in (lambda: self.k.install_lesson(self.sid, "vec-sim", lesson()),
                          lambda: self.k.install_items(self.sid, "vec-sim", items())):
            with self.assertRaises(CourseStateError) as ctx:
                operation()
            self.assertEqual(ctx.exception.code, "roadmap_not_approved")
        self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")
        self.k.install_items(self.sid, "vec-sim", items())
        self.k.install_lesson(self.sid, "vec-sim", lesson())
        state = self.k.state(self.sid)
        self.assertTrue(state["next_mission"]["lesson_ready"])
        self.assertEqual(self.strong(), 0)
        self.assertFalse(state["roadmap"]["nodes"][0]["skill_evidence"]["mastery_claim"])

    def test_certification_goal_cannot_be_relabelled_learning_or_confirmed_without_exact_target(self):
        self.background("TEST FIXTURE: NVIDIA sertifikasiya imtahanına hazırlaşmaq")
        self.diagnostic()
        self.sources()
        for payload in (confirmation(kind="learning"), confirmation(kind="certification")):
            with self.subTest(payload=payload), self.assertRaises(CourseStateError):
                self.k.confirm_goal(self.sid, payload)
        self.assert_proposal_blocked_without_effects()
        self.k.record_target(self.sid, target())
        self.k.confirm_goal(self.sid, confirmation(kind="certification",
            statement="TEST FIXTURE certification adını və TEST-FIXTURE-001 kodunu bu hazırlığın məqsədi kimi təsdiqləyirəm."))
        self.k.propose_roadmap(self.sid, roadmap())

    def test_certification_target_requires_code_name_https_and_objectives(self):
        self.background("TEST FIXTURE certification exam hazırlığı")
        for changes in ({"code": None}, {"official_name": ""}, {"verification_url": "file:///spec"}, {"objectives": []}):
            with self.subTest(changes=changes), self.assertRaises(CourseStateError):
                # Either target validation or confirmation must reject this.
                self.k.record_target(self.sid, target(**changes))
                self.k.confirm_goal(self.sid, confirmation(kind="certification"))

    def test_legacy_skip_is_unqualified_until_explicit_provisional_consent(self):
        self.background()
        self.k.skip_diagnostic(self.sid, "İndi qısa diagnostic-i keçmək istəyirəm; planın ilkin olduğunu bilirəm.")
        self.sources()
        with self.assertRaises(CourseStateError) as ctx:
            self.k.confirm_goal(self.sid, confirmation())
        self.assertEqual(ctx.exception.code, "provisional_skip_confirmation_required")
        self.assert_proposal_blocked_without_effects()
        self.k.confirm_goal(self.sid, confirmation(accept_provisional_skip=True,
            statement="Diagnostic-i keçdiyim üçün bacarığımın təsdiqlənmədiyini bilirəm; ilkin planla başlamağa açıq razıyam."))
        self.k.propose_roadmap(self.sid, roadmap())
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])
        self.assertEqual(self.strong(), 0)

    def test_malformed_confirmation_and_consent_are_not_accepted(self):
        self.background()
        for payload in ({}, confirmation(kind=[]), confirmation(statement="yes"),
                        confirmation(accept_provisional_skip="true"), confirmation(outcome="expert")):
            revision = self.k.state(self.sid)["revision"]
            with self.subTest(payload=payload), self.assertRaises(CourseStateError):
                self.k.confirm_goal(self.sid, payload)
            self.assertEqual(self.k.state(self.sid)["revision"], revision)

    def test_revision_conflict_and_exact_confirmation_replay_survive_restart(self):
        self.background()
        rev = self.k.state(self.sid)["revision"]
        with self.assertRaises(CourseStateError) as ctx:
            self.k.confirm_goal(self.sid, confirmation(), expected_revision=rev - 1)
        self.assertEqual(ctx.exception.code, "revision_conflict")
        self.k.confirm_goal(self.sid, confirmation(), expected_revision=rev, idempotency_key="confirm-goal1")
        self.k = CourseKernel(self.ws)
        replay = self.k.confirm_goal(self.sid, confirmation(), idempotency_key="confirm-goal1")
        self.assertTrue(replay["idempotent_replay"])
        with self.assertRaises(CourseStateError):
            self.k.confirm_goal(self.sid, confirmation(statement="A different learner confirmation statement."),
                                idempotency_key="confirm-goal1")

    def test_changed_goal_cannot_reuse_previous_confirmation_or_skip_consent(self):
        self.background()
        self.k.skip_diagnostic(self.sid, "Bu mövzu üzrə diagnostic-i keçərək ilkin planla başlamağı seçirəm.")
        self.k.confirm_goal(self.sid, confirmation(accept_provisional_skip=True))
        self.sources()
        self.k.record_intake(self.sid, {"goal": "Yeni mövzuda fərqli praktik qabiliyyət yaratmaq"})
        self.assert_proposal_blocked_without_effects()

    def test_target_revision_invalidates_confirmation_and_approval(self):
        self.background("TEST FIXTURE certification imtahanına hazırlaşmaq")
        self.diagnostic()
        self.sources()
        self.k.record_target(self.sid, target())
        self.k.confirm_goal(self.sid, confirmation(kind="certification"))
        proposed = self.k.propose_roadmap(self.sid, roadmap())
        self.k.record_target(self.sid, target(code="TEST-FIXTURE-002"))
        with self.assertRaises(CourseStateError):
            self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")

    def test_unreadable_tampered_source_or_incomplete_manifest_blocks_generation(self):
        self.ready()
        cache = self.d / "sources" / "src-fixture-001.txt"
        original = cache.read_text()
        cache.write_text("Tampered source snapshot. " * 50)
        self.assert_proposal_blocked_without_effects()
        cache.write_text(original)
        incomplete = manifest_for(self.d / "sources")
        incomplete["complete"] = False
        incomplete["resources"][0]["status"] = "processing"
        self.k.record_sources(self.sid, incomplete,
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        self.assert_proposal_blocked_without_effects()

    def test_all_roadmap_node_source_refs_must_exist_and_be_readable(self):
        self.ready()
        missing = roadmap()
        missing["nodes"][1]["source_refs"] = ["missing-source"]
        self.assert_proposal_blocked_without_effects(missing)
        cache = self.d / "sources" / "src-fixture-001.txt"
        cache.unlink()
        self.assert_proposal_blocked_without_effects()

    def test_source_revalidation_cannot_leave_approval_or_lesson_generation_open(self):
        self.ready()
        proposed = self.k.propose_roadmap(self.sid, roadmap())
        invalid = manifest_for(self.d / "sources")
        invalid["resources"][0]["status"] = "unreadable"
        invalid["complete"] = False
        self.k.record_sources(self.sid, invalid,
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        with self.assertRaises(CourseStateError):
            self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")

    def test_source_loss_after_approval_blocks_items_and_lessons_before_side_effects(self):
        self.ready()
        proposed = self.k.propose_roadmap(self.sid, roadmap())
        self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")
        (self.d / "sources" / "src-fixture-001.txt").unlink()
        revision = self.k.state(self.sid)["revision"]
        for operation in (lambda: self.k.install_items(self.sid, "vec-sim", items()),
                          lambda: self.k.install_lesson(self.sid, "vec-sim", lesson())):
            with self.assertRaises(CourseStateError):
                operation()
            self.assertEqual(self.k.state(self.sid)["revision"], revision)

    def test_failed_approval_preparation_preserves_pending_state_and_live_workspace(self):
        self.ready()
        proposed = self.k.propose_roadmap(self.sid, roadmap())
        self.assertFalse((self.d / "workspace" / "frontend").exists())
        before = self.k.state(self.sid)
        html = (self.d / "workspace" / "roadmap.html").read_bytes()

        def prepare_then_fail(*args, **kwargs):
            create_workspace(*args, **kwargs)
            raise OSError("Synthetic interrupted frontend preparation")

        with patch("runtime.learning_workspace.create_workspace", side_effect=prepare_then_fail) as intercepted:
            with self.assertRaises(OSError):
                self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")
            self.assertTrue(intercepted.called)
        self.k = CourseKernel(self.ws)
        after = self.k.state(self.sid)
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["phase"], "roadmap_awaiting_approval")
        self.assertIsNone(after["roadmap"]["approved_version"])
        self.assertFalse((self.d / "workspace" / "frontend").exists())
        self.assertEqual((self.d / "workspace" / "roadmap.html").read_bytes(), html)

    def test_valid_source_revision_before_approval_requires_new_bound_roadmap_version(self):
        self.ready()
        first = self.k.propose_roadmap(self.sid, roadmap())
        changed_sha = self.change_valid_source()
        self.k = CourseKernel(self.ws)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.approve_roadmap(self.sid, first["roadmap_version"], channel="cli")
        self.assertEqual(ctx.exception.code, "roadmap_generation_stale")
        revised = self.k.propose_roadmap(self.sid, roadmap())
        self.assertNotEqual(first["roadmap_version"], revised["roadmap_version"])
        plan = read_json(self.d / "workspace" / "roadmap.json")
        self.assertEqual(plan["generation_provenance"]["sources"]["r1"]["text_sha256"], changed_sha)
        self.k.approve_roadmap(self.sid, revised["roadmap_version"], channel="cli")
        self.assertTrue(self.k.state(self.sid)["roadmap"]["approved"])
        self.assertEqual(self.strong(), 0)

    def test_valid_source_revision_after_approval_locks_mission_and_lesson_until_reapproval(self):
        self.ready()
        first = self.k.propose_roadmap(self.sid, roadmap())
        self.k.approve_roadmap(self.sid, first["roadmap_version"], channel="cli")
        self.k.install_items(self.sid, "vec-sim", items())
        self.k.install_lesson(self.sid, "vec-sim", lesson())
        self.change_valid_source()
        # Quote rebinding alone does not authorize a new roadmap source basis.
        self.k.reground(self.sid, "vec-sim")
        self.k = CourseKernel(self.ws)
        for operation in (lambda: self.k.start_mission(self.sid, "vec-sim"),
                          lambda: self.k.lesson_view(self.sid, "vec-sim")):
            with self.assertRaises(CourseStateError) as ctx:
                operation()
            self.assertEqual(ctx.exception.code, "roadmap_generation_stale")
        revised = self.k.propose_roadmap(self.sid, roadmap())
        self.k.approve_roadmap(self.sid, revised["roadmap_version"], channel="cli")
        self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(self.k.lesson_view(self.sid, "vec-sim")["lesson"]["status"], "ready")

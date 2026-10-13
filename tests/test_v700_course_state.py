"""Course state machine: phases, guards, approval versioning, migration (pure, no I/O)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_state as cs  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.learning_workspace import normalize_plan  # noqa: E402
from course_fixture import roadmap  # noqa: E402

SID = "sess_0123456789abcdef"


def approved_state(phase="teaching"):
    st = cs.new_state(SID, "goal", "2026-10-12T00:00:00+00:00")
    st["roadmap_version"] = st["approved_roadmap_version"] = "rm-aaaa"
    st["roadmap_approval_event"] = {"event_id": "apv_1", "roadmap_version": "rm-aaaa"}
    st["phase"] = phase
    return st


class StateMachineTests(unittest.TestCase):
    def test_every_phase_listed_and_new_state_valid(self):
        st = cs.new_state(SID, "goal", "now")
        self.assertEqual(cs.validate(st), [])
        self.assertEqual(st["phase"], "onboarding")
        for key in ("schema_version", "revision", "session_id", "goal", "source_refs", "course_version",
                    "roadmap_version", "approved_roadmap_version", "roadmap_approval_event", "phase",
                    "active_node_id", "active_attempt_id", "attempt_status", "assessment_item_version",
                    "support_provenance", "evidence_refs", "assessment_result", "next_mission",
                    "resume_checkpoint", "learning_commitments", "engagement_intervals", "created_at", "updated_at"):
            self.assertIn(key, st)

    def test_generation_and_teaching_blocked_before_approval(self):
        st = cs.new_state(SID, "goal", "now")
        st["phase"] = "roadmap_awaiting_approval"
        st["roadmap_version"] = "rm-bbbb"
        for action in ("install_content", "start_mission", "help", "begin_practice", "start_exam"):
            with self.assertRaises(CourseStateError) as ctx:
                cs.guard(st, action)
            self.assertEqual(ctx.exception.code, "roadmap_not_approved", action)
        cs.guard(st, "approve_roadmap")

    def test_post_approval_phase_with_stale_approval_is_invalid_state(self):
        st = approved_state()
        st["roadmap_version"] = "rm-changed"
        self.assertIn("phase_requires_current_approval", cs.validate(st))
        with self.assertRaises(CourseStateError) as ctx:
            cs.guard(st, "help")
        self.assertEqual(ctx.exception.code, "invalid_state")

    def test_exam_phases_lock_teaching(self):
        st = approved_state("exam_active")
        st["active_attempt_id"] = "att_0123456789abcdef"
        st["attempt_status"] = "active"
        st["assessment_item_version"] = "it-1"
        st["attempts"] = {"att_0123456789abcdef": {"attempt_id": "att_0123456789abcdef", "status": "active",
                                                   "item_version": "it-1"}}
        self.assertEqual(cs.validate(st), [])
        for action in ("help", "self_explanation", "begin_practice", "practice_check", "start_mission",
                       "propose_roadmap", "record_sources", "install_content"):
            with self.assertRaises(CourseStateError) as ctx:
                cs.guard(st, action)
            self.assertEqual(ctx.exception.code, "exam_active", action)
        cs.guard(st, "submit_exam")
        cs.guard(st, "abandon_exam")
        # save_answers persists the learner's own non-code answers; it carries no teaching.
        self.assertEqual(set(cs.permitted_actions(st)), {"submit_exam", "save_answers", "abandon_exam"})

    def test_exam_phase_without_attempt_is_inconsistent(self):
        st = approved_state("exam_active")
        self.assertIn("exam_active_without_active_attempt", cs.validate(st))

    def test_revision_and_unknown_action(self):
        st = approved_state()
        cs.check_revision(st, None)
        cs.check_revision(st, 0)
        with self.assertRaises(CourseStateError) as ctx:
            cs.check_revision(st, 5)
        self.assertEqual(ctx.exception.code, "revision_conflict")
        with self.assertRaises(CourseStateError):
            cs.guard(st, "teleport")

    def test_material_versus_cosmetic_roadmap_change(self):
        base = normalize_plan("T", "goal", roadmap())
        v1 = cs.roadmap_version(base)
        cosmetic = roadmap()
        cosmetic["nodes"][0]["pitfalls"] = ["Başqa ifadə"]
        cosmetic["nodes"][0]["stage"] = "Yeni bölmə adı"
        self.assertEqual(cs.roadmap_version(normalize_plan("T", "goal", cosmetic)), v1)
        material = roadmap()
        material["nodes"][0]["outcomes"] = ["Fərqli nəticə"]
        changed = normalize_plan("T", "goal", material)
        self.assertNotEqual(cs.roadmap_version(changed), v1)
        diff = cs.diff_roadmaps(base, changed)
        self.assertEqual(diff, [{"change": "node_changed", "node": "vec-sim", "fields": ["outcomes"]}])
        added = roadmap()
        added["nodes"].append({"id": "extra", "title": "Əlavə", "task": "x", "requires": ["vec-search"]})
        self.assertIn({"change": "node_added", "node": "extra"}, cs.diff_roadmaps(base, normalize_plan("T", "goal", added)))

    def test_migration_from_v0_and_refusal_of_newer_schema(self):
        v0 = {"session_id": SID, "goal": "eski məqsəd", "phase": "onboarding", "created_at": "2026-01-01T00:00:00+00:00",
              "learning_commitments": [{"id": "cm_1", "kind": "weekly_active_minutes", "target": 60, "status": "active"}]}
        migrated, applied = cs.migrate(v0)
        self.assertEqual(applied, [0, 1])          # v0 -> v1 -> v2
        self.assertEqual(migrated["schema_version"], cs.SCHEMA_VERSION)
        self.assertEqual(migrated["goal"]["text"], "eski məqsəd")
        self.assertEqual(migrated["learning_commitments"][0]["id"], "cm_1")   # IDs preserved
        self.assertEqual(cs.validate(migrated), [])
        _, none = cs.migrate(migrated)
        self.assertEqual(none, [])
        with self.assertRaises(CourseStateError) as ctx:
            cs.migrate({**migrated, "schema_version": 99})
        self.assertEqual(ctx.exception.code, "newer_state_schema")

    def test_idempotency_store_is_bounded(self):
        st = approved_state()
        for i in range(cs.IDEMPOTENCY_LIMIT + 10):
            cs.remember(st, f"key-{i:06d}", "help", {"n": i})
        self.assertEqual(len(st["idempotency"]), cs.IDEMPOTENCY_LIMIT)
        self.assertIsNone(cs.remembered(st, "key-000000"))
        self.assertEqual(cs.remembered(st, f"key-{cs.IDEMPOTENCY_LIMIT + 9:06d}")["action"], "help")


if __name__ == "__main__":
    unittest.main()

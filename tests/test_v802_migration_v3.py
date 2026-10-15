"""Stage C: course state schema 3 (additive Course Studio keys): pure, idempotent migration; down migration that
archives; first-open snapshot; CLI export."""
import copy
import io
import contextlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
import runtime.course_state as cs  # noqa: E402
from runtime.local_state import STATE_FILES, commit_state  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from test_v700_course_flow import CourseCase  # noqa: E402

SID = "sess_0123456789abcdef"


def v2_state():
    """A schema 2 state exactly as the previous release wrote it (no studio keys)."""
    state = cs.down_v3_to_v2(cs.new_state(SID, "goal", "2026-10-01T00:00:00+00:00"))
    state.pop("v3_archive")
    state["custom_extension"] = {"keep": True}
    return state


class PureMigrationTests(unittest.TestCase):
    def test_schema_is_three_and_new_states_carry_the_studio_keys(self):
        self.assertEqual(cs.SCHEMA_VERSION, 3)
        self.assertEqual(cs.UI_SCHEMA_VERSION, 2)            # the bridge contract did not change
        state = cs.new_state(SID, "goal", "now")
        self.assertEqual(state["studio"], {"active_job": None, "promoted_builds": []})
        self.assertEqual(state["promotion_history"], [])
        self.assertEqual(cs.validate(state), [])

    def test_v2_to_v3_is_pure_keeps_every_key_and_adds_only_the_v3_keys(self):
        old = v2_state()
        frozen = copy.deepcopy(old)
        new, applied = cs.migrate(old)
        self.assertEqual(old, frozen)                         # the input is never mutated
        self.assertEqual(applied, [2])
        self.assertEqual(new["schema_version"], 3)
        self.assertEqual(new["custom_extension"], {"keep": True})
        self.assertEqual(set(new) - set(old), {"studio", "promotion_history"})
        for key in old:
            if key not in {"schema_version", "migration_log"}:
                self.assertEqual(new[key], old[key], key)
        self.assertEqual(new["migration_log"][-1]["from"], 2)
        self.assertEqual(cs.validate(new), [])

    def test_the_migration_is_idempotent(self):
        once, _ = cs.migrate(v2_state())
        twice, applied = cs.migrate(once)
        self.assertEqual((twice, applied), (once, []))
        self.assertEqual(cs._v2_to_v3(once), once)            # even called directly on a v3 state
        self.assertEqual(cs._v2_to_v3(cs._v2_to_v3(v2_state())), cs._v2_to_v3(v2_state()))

    def test_migration_chain_from_v0_reaches_v3_and_validates(self):
        v0 = {"session_id": SID, "goal": "eski", "phase": "onboarding", "created_at": "2026-01-01T00:00:00+00:00"}
        migrated, applied = cs.migrate(v0)
        self.assertEqual((applied, migrated["schema_version"]), ([0, 1, 2], 3))
        self.assertEqual(cs.validate(migrated), [])

    def test_down_v3_to_v2_archives_the_v3_only_keys_and_never_drops_them(self):
        state, _ = cs.migrate(v2_state())
        state["studio"]["promoted_builds"].append({"job_id": "job_x", "build_digest": "d" * 64})
        state["promotion_history"].append({"kind": "promotion", "build_digest": "d" * 64})
        frozen = copy.deepcopy(state)
        down = cs.down_v3_to_v2(state)
        self.assertEqual(state, frozen)
        self.assertEqual(down["schema_version"], 2)
        self.assertFalse(set(cs.V3_ONLY_KEYS) & set(down))
        self.assertEqual(down["v3_archive"], {"studio": frozen["studio"], "promotion_history": frozen["promotion_history"]})
        with self.assertRaises(cs.StateVersionError):
            cs.down_v3_to_v2(down)                            # export only: a v2 state cannot go down again

    def test_round_trip_down_then_up_restores_the_archived_data(self):
        state, _ = cs.migrate(v2_state())
        state["studio"]["promoted_builds"].append({"job_id": "job_x"})
        state["promotion_history"].append({"kind": "promotion"})
        back, applied = cs.migrate(cs.down_v3_to_v2(state))
        self.assertEqual(applied, [2])
        self.assertEqual(back["studio"], state["studio"])
        self.assertEqual(back["promotion_history"], state["promotion_history"])
        self.assertNotIn("v3_archive", back)
        self.assertEqual({k: v for k, v in back.items() if k != "migration_log"},
                         {k: v for k, v in state.items() if k != "migration_log"})

    def test_a_chain_down_to_v1_and_back_up_keeps_both_archives(self):
        state, _ = cs.migrate(v2_state())
        state["promotion_history"].append({"kind": "promotion", "note": "kept"})
        v1 = cs.down_v2_to_v1(cs.down_v3_to_v2(state))
        self.assertEqual(v1["schema_version"], 1)
        self.assertIn("v3_archive", v1)
        again, applied = cs.migrate(v1)
        self.assertEqual(applied, [1, 2])
        self.assertEqual(again["promotion_history"], [{"kind": "promotion", "note": "kept"}])

    def test_validation_rejects_a_malformed_studio_block(self):
        state, _ = cs.migrate(v2_state())
        for bad in ({"studio": []}, {"studio": {"active_job": 5, "promoted_builds": []}},
                    {"studio": {"active_job": None, "promoted_builds": "x"}}):
            self.assertIn("bad_studio_block", cs.validate({**state, **bad}))
        self.assertIn("bad_promotion_history", cs.validate({**state, "promotion_history": {}}))

    def test_the_pre_v3_snapshot_file_is_an_allowed_state_file(self):
        self.assertIn("course-state.pre-v3.json", STATE_FILES)
        self.assertIn("course-state.pre-v2.json", STATE_FILES)


class LiveMigrationTests(CourseCase):
    def journal_state(self, state):
        commit_state(self.d, self.sk.ledger(self.sid), {"event_type": "test_simulated_older_state"}, {"course-state.json": state})

    def test_first_open_of_a_v2_state_migrates_snapshots_and_logs(self):
        self.approve()
        live = read_json(self.d / "course-state.json")
        self.assertEqual(live["schema_version"], 3)
        v2 = cs.down_v3_to_v2(live)
        v2.pop("v3_archive")
        self.journal_state(v2)
        self.assertFalse((self.d / "course-state.pre-v3.json").exists())
        view = self.k.state(self.sid)
        raw = read_json(self.d / "course-state.json")
        self.assertEqual((raw["schema_version"], view["state_schema_version"]), (3, 3))
        self.assertEqual(raw["studio"], {"active_job": None, "promoted_builds": []})
        self.assertEqual(read_json(self.d / "course-state.pre-v3.json"), v2)      # the exact pre-migration state is kept
        self.assertFalse((self.d / "course-state.pre-v2.json").exists())
        events = [r["payload"] for r in self.sk.ledger(self.sid).verified_records()
                  if r["payload"].get("event_type") == "course_state_migrated"]
        self.assertEqual(events[-1]["from_versions"], [2])
        again = self.k.state(self.sid)
        self.assertEqual(again["revision"], view["revision"])                      # idempotent: no second migration

    def test_cli_schema_down_exports_v2_or_v1_and_leaves_the_live_state_alone(self):
        self.approve()
        for target, name in ((2, "down2.json"), (1, "down1.json")):
            out = self.ws / name
            args = ["--workspace", str(self.ws), "course", "schema-down", "--session", self.sid, "--output", str(out),
                    "--target-version", str(target)]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(alearn.main(args), 0)
            exported = read_json(out)
            self.assertEqual(exported["schema_version"], target)
            self.assertIn("v3_archive", exported)
            self.assertEqual(("v2_archive" in exported), target == 1)
            self.assertEqual(read_json(self.d / "course-state.json")["schema_version"], 3)

    def test_the_course_view_reports_schema_three_and_an_unchanged_ui_schema(self):
        self.approve()
        view = self.k.state(self.sid)
        self.assertEqual((view["state_schema_version"], view["ui_schema_version"]), (3, 2))
        self.assertNotIn("rules_receipt", view)               # the turn view carries the receipt, the UI bridge does not

    def test_a_course_turn_without_a_studio_has_no_rules_receipt_and_no_promotion(self):
        self.approve()
        turn = self.k.turn(self.sid)
        self.assertNotIn("rules_receipt", turn)
        self.assertNotIn("studio_promotion", turn)


if __name__ == "__main__":
    unittest.main()

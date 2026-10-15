"""Stage A: dry-run validators (course validate-lesson / validate-items). They run the install
validators but must leave the session byte-identical: no state, file, vault or ledger writes."""
import contextlib
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from course_fixture import items, lesson  # noqa: E402
from studio_fixture import StudioCase, search_items, search_lesson, sim_items  # noqa: E402


def tree_hash(*roots):
    """{relative path: sha256} of every file under the roots (byte-level identity)."""
    out = {}
    for base in roots:
        for path in sorted(Path(base).rglob("*")):
            if path.is_file():
                out[str(path.relative_to(base.parent))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class DryRunCase(StudioCase):
    def snapshot(self):
        return tree_hash(self.d, Path(self.iso._tmp.name))      # session dir + out-of-workspace trust heads

    def approve(self):
        version = self.ck.state(self.sid)["roadmap"]["version"]
        self.ck.approve_roadmap(self.sid, version, channel="cli", commitments={"weekly_active_minutes": 90})


class DryRunPurityTests(DryRunCase):
    def exercise(self):
        bad = lesson()
        bad["explanation"]["refs"] = [{"source_id": "src-fixture-001", "quote": "this sentence is not in the source text"}]
        calls = [
            lambda: self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson()),
            lambda: self.ck.validate_lesson_dry(self.sid, "vec-sim", bad),
            lambda: self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson(), staged_items=sim_items()),
            lambda: self.ck.validate_items_dry(self.sid, "vec-sim", sim_items()),
            lambda: self.ck.validate_items_dry(self.sid, "vec-sim", {"node_id": "vec-sim", "items": []}),
            lambda: self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=lesson()),
            lambda: self.ck.validate_lesson_dry(self.sid, "vec-search", search_lesson(), staged_items=search_items()),
        ]
        return [call() for call in calls]

    def test_session_is_byte_identical_before_and_after_dry_runs(self):
        before, ledger_before = self.snapshot(), len(self.records())
        results = self.exercise()
        self.assertEqual([r["valid"] for r in results], [True, False, True, True, False, True, True])
        self.assertEqual(self.snapshot(), before)           # course-state, ledger, manifest, trust heads, everything
        self.assertEqual(len(self.records()), ledger_before)
        self.assertFalse((self.d / "studio").exists())
        self.assertFalse((self.d / "assessment-vault").exists())
        self.assertFalse((self.d / "course" / "lessons").exists())

    def test_dry_runs_stay_pure_on_an_approved_course_with_installed_content(self):
        self.approve()
        self.ck.install_items(self.sid, "vec-sim", items())
        self.ck.install_lesson(self.sid, "vec-sim", lesson())
        before = self.snapshot()
        state_before = (self.d / "course-state.json").read_bytes()
        self.exercise()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((self.d / "course-state.json").read_bytes(), state_before)


class DryRunResultTests(DryRunCase):
    def test_valid_lesson_summary_and_content_version(self):
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson())
        self.assertEqual((out["valid"], out["errors"]), (True, []))
        self.assertTrue(out["content_version"].startswith("lc-"))
        self.assertEqual(out["summary"]["practice_task_id"], "dot-norm")
        self.assertEqual(out["summary"]["cited_sources"], ["src-fixture-001"])
        self.assertNotIn("reference_solution", json.dumps(out))     # nothing private is echoed back

    def test_structured_errors_for_ungrounded_and_malformed_payloads(self):
        bad = lesson()
        bad["explanation"]["refs"] = [{"source_id": "src-fixture-001", "quote": "not present in the cached source text"}]
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", bad)
        self.assertFalse(out["valid"])
        self.assertTrue(all(isinstance(e, str) for e in out["errors"]))
        self.assertIn("not grounded", out["errors"][0])
        self.assertIsNone(out["content_version"])
        wrong_node = self.ck.validate_lesson_dry(self.sid, "vec-sim", {**lesson(), "node_id": "vec-search"})
        self.assertIn("node_id", wrong_node["errors"][0])
        mangled = lesson()
        mangled["explanation"] = ["not", "an", "object"]
        malformed = self.ck.validate_lesson_dry(self.sid, "vec-sim", mangled)
        self.assertFalse(malformed["valid"])
        self.assertTrue(malformed["errors"][0].startswith("malformed_payload"))
        self.assertFalse(self.ck.validate_items_dry(self.sid, "vec-sim", [1, 2])["valid"])

    def test_sibling_duplicates_and_failing_self_tests_are_rejected(self):
        payload = items()
        twin = json.loads(json.dumps(payload["items"][0]))
        twin["item_id"] = "cosine-renamed"
        payload["items"].append(twin)
        out = self.ck.validate_items_dry(self.sid, "vec-sim", payload)
        self.assertFalse(out["valid"])
        self.assertIn("assesses the same thing", out["errors"][0])
        broken = items()
        broken["items"][0]["reference_solution"] = "def cosine(a, b):\n    return 0\n"
        out = self.ck.validate_items_dry(self.sid, "vec-sim", broken)
        self.assertFalse(out["valid"])
        self.assertIn("reference solution fails checks", out["errors"][0])

    def test_separation_against_staged_counterpart(self):
        leaky = lesson()
        leaky["worked_example"]["problem"] = sim_items()["items"][0]["prompt_md"]
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", leaky, staged_items=sim_items())
        self.assertFalse(out["valid"])
        self.assertIn("overlaps assessment item", out["errors"][0])
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-sim", leaky)["valid"])    # no counterpart, no overlap known
        copied = sim_items()
        copied["items"][0]["prompt_md"] = lesson()["practice"]["prompt_md"]
        out = self.ck.validate_items_dry(self.sid, "vec-sim", copied, staged_lesson=lesson())
        self.assertFalse(out["valid"])
        self.assertIn("overlaps the worked example/practice", out["errors"][0])
        bad_staged = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson={"node_id": "vec-sim"})
        self.assertFalse(bad_staged["valid"])
        self.assertTrue(bad_staged["errors"][0].startswith("staged_lesson:"))

    def test_freshness_is_reported_per_item_against_exposure(self):
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items())
        self.assertEqual({k: v["status"] for k, v in out["freshness"].items()},
                         {"cosine-a": "fresh", "nearest-b": "fresh", "sim-concept-c": "fresh"})
        self.assertEqual(out["summary"]["freshness"], {"fresh": 3})
        self.assertEqual(out["summary"]["scopes"]["far_transfer"], 1)
        self.assertEqual(set(out["item_versions"]), {"cosine-a", "nearest-b", "sim-concept-c"})
        # The learner is examined on cosine-a; a renamed copy is a duplicate, not fresh.
        self.approve()
        self.ck.install_items(self.sid, "vec-sim", items())
        self.ck.install_lesson(self.sid, "vec-sim", lesson())
        self.ck.start_mission(self.sid, "vec-sim")
        attempt = self.ck.start_exam(self.sid, "vec-sim", fast_track=True)
        with self.assertRaises(CourseStateError) as ctx:
            self.ck.validate_items_dry(self.sid, "vec-sim", sim_items())
        self.assertEqual(ctx.exception.code, "exam_active")
        with self.assertRaises(CourseStateError):
            self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson())
        self.ck.abandon_exam(self.sid, attempt["attempt_id"])
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items())
        self.assertEqual(out["freshness"]["cosine-a"]["status"], "duplicate")
        self.assertEqual(out["freshness"]["nearest-b"]["status"], "fresh")
        self.assertEqual(out["freshness"]["cosine-a"]["matches"][0]["relation"], "duplicate")

    def test_exposed_id_republished_with_other_content_is_an_error(self):
        self.approve()
        self.ck.install_items(self.sid, "vec-sim", items())
        self.ck.install_lesson(self.sid, "vec-sim", lesson())
        self.ck.start_mission(self.sid, "vec-sim")
        self.ck.abandon_exam(self.sid, self.ck.start_exam(self.sid, "vec-sim", fast_track=True)["attempt_id"])
        changed = sim_items()
        changed["items"][0]["checks"][0]["code"] = "assert abs(cosine([1,1],[1,1]) - 1) < 1e-9"
        out = self.ck.validate_items_dry(self.sid, "vec-sim", changed)
        self.assertFalse(out["valid"])
        self.assertIn("was already shown; publish changes under a new item_id", out["errors"][0])


class DryRunGuardTests(DryRunCase):
    def test_unknown_node_is_refused(self):
        with self.assertRaises(CourseStateError) as ctx:
            self.ck.validate_lesson_dry(self.sid, "no-such-node", lesson())
        self.assertEqual(ctx.exception.code, "phase_forbids_action")

    def test_before_the_roadmap_is_proposed(self):
        from runtime.contracts import LearningContract
        sid = self.sk.start("Boş", LearningContract(goal="g", stated_background="b"))["session_id"]
        self.ck.init(sid)
        with self.assertRaises(CourseStateError) as ctx:
            self.ck.validate_items_dry(sid, "vec-sim", items())
        self.assertEqual(ctx.exception.code, "roadmap_not_approved")

    def test_any_pre_exam_phase_is_fine(self):
        self.assertEqual(self.ck.state(self.sid)["phase"], "roadmap_awaiting_approval")
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson())["valid"])
        self.approve()
        self.assertTrue(self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson())["valid"])


class DryRunCliTests(DryRunCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = alearn.main(["--workspace", str(self.ws), "course", *argv])
            except SystemExit as e:
                code = e.code
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def write(self, name, obj):
        path = self.ws / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return str(path)

    def test_exit_zero_when_valid_and_one_with_the_error_list(self):
        before = self.snapshot()
        code, out = self.run_cli("validate-lesson", "--session", self.sid, "--node", "vec-sim",
                                 "--file", self.write("lesson.json", lesson()))
        self.assertEqual((code, out["valid"]), (0, True))
        code, out = self.run_cli("validate-items", "--session", self.sid, "--node", "vec-sim",
                                 "--file", self.write("items.json", sim_items()),
                                 "--staged-lesson", self.write("staged.json", lesson()))
        self.assertEqual((code, out["valid"]), (0, True))
        bad = lesson()
        bad["objective"] = "short"
        code, out = self.run_cli("validate-lesson", "--session", self.sid, "--node", "vec-sim",
                                 "--file", self.write("bad.json", bad))
        self.assertEqual(code, 1)
        self.assertFalse(out["valid"])
        self.assertIn("objective", out["errors"][0])
        code, _ = self.run_cli("validate-items", "--session", self.sid, "--node", "vec-sim",
                               "--file", self.write("none.json", {"node_id": "vec-sim", "items": []}))
        self.assertEqual(code, 1)
        after = self.snapshot()
        after.pop("lesson.json", None)
        self.assertEqual({k: v for k, v in after.items() if k.startswith(self.sid)},
                         {k: v for k, v in before.items() if k.startswith(self.sid)})


if __name__ == "__main__":
    unittest.main()

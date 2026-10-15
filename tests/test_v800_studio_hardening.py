"""Stage A hardening regressions: hostile inbox files and payloads, crash ordering, torn event tails,
tail-truncation anchoring, lease accounting across pauses, remediation budget, rebase retries,
idempotency binding and read-only guarantees."""
import copy
import hashlib
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import studio as studio_mod  # noqa: E402
from runtime import studio_jobs as jobs  # noqa: E402
from runtime import studio_plan as plan_mod  # noqa: E402
from runtime.studio_store import EventLog, StudioError, verify_events  # noqa: E402
from course_fixture import roadmap  # noqa: E402
from studio_fixture import FakeAuthor, StudioCase  # noqa: E402


def tree_hash(base):
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(base).rglob("*")) if p.is_file()}


class OrderCase(StudioCase):
    def setUp(self):
        super().setUp()
        self.job_id = self.st.init(self.sid)["job_id"]
        self.task = self.st.next_task(self.sid, self.job_id)
        self.order = self.task["order"]
        self.inbox = Path(self.task["inbox"])
        self.good = FakeAuthor().payload_for(self.order)

    def submit(self, payload=None, key=None):
        return self.st.submit(self.sid, self.job_id, self.order["order_id"], payload, idempotency_key=key)

    def unit(self, uid="lesson:vec-sim"):
        return jobs.load_job(self.studio_root(), self.job_id)["units"][uid]

    def result_path(self):
        return self.inbox / "out" / "result.json"


class CoreCoverageTests(unittest.TestCase):
    def test_items_need_an_independent_reproduction_item(self):
        ok = {"valid": True, "freshness": {}}
        items = {"items": [{"scope": "far_transfer"}] * 3}
        verdict = plan_mod.evaluate_unit("items", items, ok, plan_mod.DEFAULT_TARGETS)
        self.assertFalse(verdict["complete"])
        self.assertTrue(any("independent_reproduction" in p for p in verdict["problems"]))
        with self.assertRaises(StudioError):
            plan_mod.resolve_policy({"targets": {"min_core_items": 0}})

    def test_unknown_top_level_keys_are_listed(self):
        self.assertEqual(plan_mod.unknown_keys("items", {"node_id": "n", "items": [], "x": 1}), ["x"])
        self.assertEqual(plan_mod.unknown_keys("lesson", {"node_id": "n", "objective": "o"}), [])


class HostileInboxTests(OrderCase):
    def run_bounded(self, fn):
        box = {}
        thread = threading.Thread(target=lambda: box.setdefault("out", fn()), daemon=True)
        thread.start()
        thread.join(8)
        self.assertFalse(thread.is_alive(), "submit blocked on a hostile file")
        return box["out"]

    def assert_unsafe_and_counted(self, out):
        self.assertEqual(out["status"], "rejected")
        self.assertTrue(out["errors"][0].startswith("result_unsafe"), out["errors"])
        self.assertEqual(self.unit()["attempts"], 1)

    def test_fifo_result_is_refused_not_blocked_on(self):
        os.mkfifo(self.result_path())
        self.assert_unsafe_and_counted(self.run_bounded(self.submit))

    def test_directory_result_is_a_counted_rejection(self):
        self.result_path().mkdir()
        self.assert_unsafe_and_counted(self.submit())

    def test_hardlinked_result_is_refused(self):
        other = self.ws / "other.json"
        other.write_text(json.dumps(self.good))
        os.link(other, self.result_path())
        self.assert_unsafe_and_counted(self.submit())

    def test_symlinked_order_folder_is_refused(self):
        elsewhere = self.ws / "elsewhere"
        (elsewhere / "out").mkdir(parents=True)
        (elsewhere / "out" / "result.json").write_text(json.dumps(self.good))
        moved = self.inbox.with_name(self.inbox.name + ".moved")
        self.inbox.rename(moved)
        self.inbox.symlink_to(elsewhere)
        out = self.submit()
        self.assertEqual(out["status"], "rejected")
        self.assertTrue(out["errors"][0].startswith("result_"), out["errors"])
        self.assertIsNone(self.unit()["staged_digest"])

    def test_deeply_nested_payload_is_a_counted_rejection_not_a_crash(self):
        for depth in (200, 800):
            nested = []
            for _ in range(depth):
                nested = [nested]
            payload = {**copy.deepcopy(self.good), "extra": nested}
            order = self.st.next_task(self.sid, self.job_id)["order"]
            out = self.st.submit(self.sid, self.job_id, order["order_id"], payload)
            self.assertEqual(out["status"], "rejected", depth)
            self.assertIn("nesting", out["errors"][0])
        self.assertEqual(self.unit()["attempts"], 2)

    def test_dry_validators_survive_a_recursion_bomb(self):
        nested = []
        for _ in range(3000):
            nested = [nested]
        out = self.ck.validate_lesson_dry(self.sid, "vec-sim", {"node_id": "vec-sim", "x": nested})
        self.assertFalse(out["valid"])

    def test_unknown_top_level_fields_are_refused_and_never_staged(self):
        out = self.submit({**self.good, "planted": "note"})
        self.assertEqual(out["status"], "rejected")
        self.assertIn("unknown top-level", out["errors"][0])
        self.assertFalse(list((self.studio_root() / "staging").rglob("*.json")))

    def test_inbox_idempotency_key_is_bound_to_the_file_content(self):
        self.result_path().write_text(json.dumps({"node_id": "vec-sim"}))
        first = self.submit(key="inbox-key-0001")
        self.assertEqual(first["status"], "rejected")
        self.result_path().write_text(json.dumps(self.good))
        with self.assertRaises(StudioError) as ctx:
            self.submit(key="inbox-key-0001")
        self.assertEqual(ctx.exception.code, "studio_conflict")
        self.result_path().write_text(json.dumps({"node_id": "vec-sim"}))
        self.assertTrue(self.submit(key="inbox-key-0001")["idempotent_replay"])


class CrashOrderingTests(OrderCase):
    def test_events_are_written_before_the_job_so_a_crash_cannot_hide_a_staging(self):
        with patch.object(studio_mod.jobs, "save_job", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.submit(self.good)
        types = [e["type"] for e in self.st.events(self.sid, self.job_id)["events"]]
        self.assertIn("unit_staged", types)                    # the audit trail exists...
        self.assertEqual(self.unit()["status"], "ordered")     # ...and the job can simply be retried
        self.assertEqual(self.submit(self.good)["status"], "staged")
        self.assertTrue(self.st.events(self.sid, self.job_id)["verify"]["ok"])

    def test_every_event_batch_is_anchored_so_a_dropped_tail_is_detected(self):
        log = EventLog(self.studio_root())
        lines = [x for x in log.path.read_text().split("\n") if x]
        self.assertEqual(json.loads(lines[-1])["type"], "order_issued")
        previous = json.loads(lines[-2])
        log.path.write_text("\n".join(lines[:-1]) + "\n")
        log.head_path.write_text(json.dumps({"seq": previous["seq"], "hash": previous["hash"]}))
        report = verify_events(self.d, self.sk.ledger(self.sid))
        self.assertFalse(report["ok"])
        self.assertIn("ledger anchor", report["error"])

    def test_a_torn_event_tail_is_archived_and_the_log_keeps_working(self):
        path = self.studio_root() / "events.jsonl"
        before = path.read_bytes()
        with open(path, "ab") as stream:
            stream.write(b'{"seq": 3, "type": "half')
        self.assertTrue(self.st.events(self.sid, self.job_id)["verify"]["ok"])
        self.assertEqual(self.st.pause(self.sid, self.job_id)["status"], "paused")
        torn = list((self.studio_root() / "archive").glob("events-torn-*.jsonl"))
        self.assertEqual([p.read_bytes() for p in torn], [b'{"seq": 3, "type": "half'])
        self.assertTrue(path.read_bytes().startswith(before))
        self.assertTrue(self.st.events(self.sid, self.job_id)["verify"]["ok"])


class PauseLeaseTests(OrderCase):
    def test_time_spent_paused_is_not_charged_to_the_author(self):
        self.st.pause(self.sid, self.job_id)
        self.clock.advance(5000)
        self.st.resume(self.sid, self.job_id)
        again = self.st.next_task(self.sid, self.job_id)
        self.assertEqual(again["order"]["order_id"], self.order["order_id"])
        self.assertEqual(self.unit()["attempts"], 0)
        self.clock.advance(901)                          # a lease still runs out normally afterwards
        self.assertEqual(self.st.next_task(self.sid, self.job_id)["order"]["attempt"], 2)


class RemediationBudgetTests(StudioCase):
    def test_revalidation_loop_is_bounded_by_max_remediation_rounds(self):
        job_id = self.st.init(self.sid)["job_id"]

        def failing(self_, txn, job):
            first = job["unit_order"][0]
            return {"units": {uid: {"complete": uid != first, "problems": ["drifted"]} for uid in job["unit_order"]},
                    "build_complete": False, "build_digest": None}
        with patch.object(studio_mod.StudioKernel, "_full_check", failing):
            task = FakeAuthor().run(self.st, self.sid, job_id, limit=60)
        out = self.st.status(self.sid, job_id)
        self.assertEqual((task["state"], out["status"]), ("blocked", "blocked"))
        self.assertEqual(jobs.load_job(self.studio_root(), job_id)["remediation_rounds"], 4)
        self.assertEqual(out["blocked"]["kind"], "content_defect")
        self.assertEqual(out["blocked"]["reason"], "remediation_rounds_exhausted")


class RebaseAndInitTests(StudioCase):
    def test_init_with_a_different_policy_is_refused_not_silently_ignored(self):
        self.st.init(self.sid)
        self.assertEqual(self.st.init(self.sid)["result"], "already_initialized")
        with self.assertRaises(StudioError) as ctx:
            self.st.init(self.sid, policy={"budgets": {"max_attempts_per_unit": 2}})
        self.assertEqual(ctx.exception.code, "studio_policy_mismatch")

    def test_archived_job_lookup_prefers_the_newest_copy(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.st.cancel(self.sid, job_id)
        archive = self.studio_root() / "archive"
        base = jobs.archived_path(self.studio_root(), job_id)
        if base is None:
            jobs.archive_job(self.studio_root(), jobs.load_job(self.studio_root(), job_id), self.clock())
            base = jobs.archived_path(self.studio_root(), job_id)
        stamp = base.name[:-len(f"-{job_id}.json")]
        for n, age in ((10, 1), (2, 2), (1, 3)):         # "-10-" sorts before "-2-" but is the oldest write here
            copy_ = archive / f"{stamp}-{n}-{job_id}.json"
            copy_.write_text(base.read_text())
            os.utime(copy_, (1_000_000 + age * 100, 1_000_000 + age * 100))
        os.utime(base, (1_000_000, 1_000_000))
        self.assertEqual(jobs.archived_path(self.studio_root(), job_id).name, f"{stamp}-1-{job_id}.json")


class ReadOnlyTests(OrderCase):
    def test_snapshot_check_events_and_order_show_write_nothing(self):
        before = tree_hash(self.d)
        self.ck.studio_snapshot(self.sid)
        self.st.check(self.sid, self.job_id)
        self.st.events(self.sid, self.job_id)
        self.st.order_show(self.sid, self.job_id, self.order["order_id"])
        self.assertEqual(tree_hash(self.d), before)


class RebaseRetryTests(StudioCase):
    def test_a_retried_rebase_repairs_the_missing_successor_link(self):
        job_id = self.st.init(self.sid)["job_id"]
        FakeAuthor().answer(self.st, self.sid, self.st.next_task(self.sid, job_id))
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın sənədləri tap və uyğunluq xalını izah et."
        self.ck.propose_roadmap(self.sid, changed)
        out = self.st.rebase(self.sid, job_id)
        self.assertEqual(out["result"], "rebased")
        new_id = out["job_id"]
        archived = jobs.archived_path(self.studio_root(), job_id)
        doc = json.loads(archived.read_text())
        doc["superseded_by"] = None                       # the crash window: successor saved, link not written
        archived.write_text(json.dumps(doc))
        again = self.st.rebase(self.sid, job_id)
        self.assertEqual((again["result"], again["job_id"]), ("already_rebased", new_id))
        self.assertEqual(json.loads(jobs.archived_path(self.studio_root(), job_id).read_text())["superseded_by"], new_id)


if __name__ == "__main__":
    unittest.main()

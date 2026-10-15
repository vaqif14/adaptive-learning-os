"""Stage A: studio storage - layout and modes, atomic writes, hash-chained events, archive-not-delete,
and the ledger anchor (an `event` record, never evidence)."""
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import studio_store as store  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.projection import EvidenceProjector  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from course_fixture import IsolatedState  # noqa: E402

AT = "2026-10-13T09:00:00+00:00"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.iso = IsolatedState().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.sk = SessionKernel(self.ws)
        self.sid = self.sk.start("Store", LearningContract(goal="g", stated_background="b"))["session_id"]
        self.d = self.sk.dir(self.sid)
        self.root = store.ensure_layout(self.d)
        self.log = store.EventLog(self.root)

    def tearDown(self):
        self.tmp.cleanup()
        self.iso.__exit__()


class LayoutAndModesTests(StoreCase):
    def test_layout_matches_the_design_with_private_modes(self):
        self.assertEqual(self.root, self.d / "studio")
        for name in store.SUBDIRS:
            self.assertTrue((self.root / name).is_dir(), name)
            self.assertEqual(mode(self.root / name), 0o700)
        self.assertEqual(set(store.SUBDIRS), {"jobs", "orders", "inbox", "staging", "receipts", "handover", "scratch", "archive"})
        self.assertEqual(mode(self.root), 0o700)

    def test_secure_mkdir_sets_every_new_level_to_0700(self):
        deep = store.secure_mkdir(self.root / "scratch" / "run_a" / "inner")
        for path in (deep, deep.parent, deep.parent.parent):
            self.assertEqual(mode(path), 0o700)

    def test_atomic_json_is_0600_and_replaces_whole(self):
        path = self.root / "jobs" / "x.json"
        store.write_atomic_json(path, {"a": 1})
        self.assertEqual(mode(path), 0o600)
        store.write_atomic_json(path, {"a": 2})
        self.assertEqual(store.read_json(path), {"a": 2})
        self.assertEqual([p.name for p in path.parent.iterdir()], ["x.json"])     # no temp files left behind

    def test_failed_write_keeps_the_previous_document_and_leaves_no_temp(self):
        path = self.root / "jobs" / "x.json"
        store.write_atomic_json(path, {"a": 1})
        with patch("runtime.utils.json.dumps", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                store.write_atomic_json(path, {"a": 2})
        self.assertEqual(store.read_json(path), {"a": 1})
        self.assertEqual([p.name for p in path.parent.iterdir()], ["x.json"])

    def test_atomic_bytes_and_event_files_are_0600(self):
        path = self.root / "inbox" / "j" / "o" / "sources" / "s.txt"
        store.write_atomic_bytes(path, b"text")
        self.assertEqual((mode(path), path.read_bytes()), (0o600, b"text"))
        self.log.append("t", "job_x", {}, AT)
        self.assertEqual(mode(self.log.path), 0o600)
        self.assertEqual(mode(self.log.head_path), 0o600)

    def test_path_components_must_be_plain_names(self):
        for bad in ("../x", "a/b", "", ".", "..", "a b", "x" * 200):
            with self.assertRaises(store.StudioError) as ctx:
                store.safe_name(bad)
            self.assertEqual(ctx.exception.code, "studio_invalid_name")

    def test_symlinked_directories_are_refused(self):
        target = Path(self.tmp.name) / "elsewhere"
        target.mkdir()
        link = self.root / "scratch" / "link"
        link.symlink_to(target)
        with self.assertRaises(store.StudioError):
            store.secure_mkdir(link)


class EventChainTests(StoreCase):
    def fill(self, n=4):
        return [self.log.append("evt", "job_a", {"n": i}, AT) for i in range(n)]

    def test_chain_links_and_verifies(self):
        events = self.fill()
        self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
        self.assertEqual(events[0]["prev_hash"], store.GENESIS)
        self.assertEqual(events[2]["prev_hash"], events[1]["hash"])
        self.assertEqual(set(events[0]), {"seq", "at", "type", "job_id", "data", "prev_hash", "hash"})
        body = {k: v for k, v in events[1].items() if k != "hash"}
        self.assertEqual(events[1]["hash"], store.digest_of(body))
        report = store.verify_events(self.d)
        self.assertEqual((report["ok"], report["count"], report["head_seq"]), (True, 4, 3))

    def test_an_edited_event_is_detected(self):
        self.fill()
        lines = self.log.path.read_text().split("\n")
        lines[1] = lines[1].replace('"n": 1', '"n": 99')
        self.log.path.write_text("\n".join(lines))
        report = store.verify_events(self.d)
        self.assertFalse(report["ok"])
        self.assertIn("edited", report["error"])
        with self.assertRaises(store.StudioError) as ctx:
            self.log.append("evt", "job_a", {}, AT)           # no appending onto a corrupt chain
        self.assertEqual(ctx.exception.code, "studio_events_corrupt")

    def test_a_removed_middle_event_breaks_the_chain(self):
        self.fill()
        lines = [line for line in self.log.path.read_text().split("\n") if line]
        self.log.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
        self.assertFalse(store.verify_events(self.d)["ok"])

    def test_truncation_is_detected_by_the_head_file_and_by_the_ledger_anchor(self):
        self.fill()
        led = self.sk.ledger(self.sid)
        store.anchor(led, "job_a")
        lines = [line for line in self.log.path.read_text().split("\n") if line]
        self.log.path.write_text("\n".join(lines[:2]) + "\n")                 # drop the tail
        report = store.verify_events(self.d, led)
        self.assertFalse(report["ok"])
        self.assertIn("truncated", report["error"])
        # An attacker who also rewrites the head file still hits the anchor held in the ledger.
        self.log.head_path.write_text(json.dumps({"seq": 1, "hash": json.loads(lines[1])["hash"]}))
        self.assertTrue(store.verify_events(self.d)["ok"])                    # chain alone cannot tell
        report = store.verify_events(self.d, led)
        self.assertFalse(report["ok"])
        self.assertIn("ledger anchor 3", report["error"])

    def test_an_interrupted_append_where_the_head_file_lags_is_still_valid(self):
        events = self.fill(3)
        self.log.head_path.write_text(json.dumps({"seq": 1, "hash": events[1]["hash"]}))
        self.assertTrue(store.verify_events(self.d)["ok"])

    def test_empty_log_verifies(self):
        self.assertEqual(store.verify_events(self.d), {"ok": True, "count": 0, "head_seq": -1,
                                                       "head_hash": store.GENESIS, "error": None})


class ArchiveTests(StoreCase):
    def test_archive_renames_into_a_stamped_name_and_keeps_the_data(self):
        path = self.root / "jobs" / "job_x.json"
        store.write_atomic_json(path, {"keep": "me"})
        moved = store.archive_rename(self.root, path, now=datetime(2026, 10, 13, 9, 0, tzinfo=UTC))
        self.assertFalse(path.exists())
        self.assertEqual(moved.parent, self.root / "archive")
        self.assertEqual(moved.name, "20261013T090000000000Z-job_x.json")
        self.assertEqual(store.read_json(moved), {"keep": "me"})

    def test_same_name_same_instant_never_overwrites(self):
        when = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
        first = self.root / "jobs" / "a.json"
        store.write_atomic_json(first, {"v": 1})
        one = store.archive_rename(self.root, first, now=when)
        store.write_atomic_json(first, {"v": 2})
        two = store.archive_rename(self.root, first, now=when)
        self.assertNotEqual(one, two)
        self.assertEqual((store.read_json(one), store.read_json(two)), ({"v": 1}, {"v": 2}))

    def test_directories_can_be_archived_and_missing_paths_are_refused(self):
        folder = store.secure_mkdir(self.root / "scratch" / "run_1")
        (folder / "f.txt").write_text("x")
        moved = store.archive_rename(self.root, folder)
        self.assertEqual((moved / "f.txt").read_text(), "x")
        with self.assertRaises(store.StudioError):
            store.archive_rename(self.root, self.root / "scratch" / "nothing")


class AnchorTests(StoreCase):
    def test_anchor_is_an_event_record_with_studio_provenance(self):
        self.log.append("job_created", "job_a", {}, AT)
        before = self.sk.ledger(self.sid).verified_records()
        record = store.anchor(self.sk.ledger(self.sid), "job_a")
        self.assertEqual(record["record_type"], "event")
        self.assertEqual(record["provenance"], {"source": "studio"})
        self.assertEqual(record["payload"], {"event_type": "studio_events_anchor", "job_id": "job_a",
                                             "events_seq": 0, "events_head": self.log.head()["hash"]})
        after = self.sk.ledger(self.sid).verified_records()
        self.assertEqual(len(after), len(before) + 1)
        self.assertTrue(self.sk.ledger(self.sid).integrity()["chain_ok"])

    def test_anchor_never_changes_evidence_or_the_projection(self):
        led = self.sk.ledger(self.sid)
        projector = EvidenceProjector(led)
        before = {k: v for k, v in projector.rebuild().items() if k != "last_rebuilt_at"}
        evidence = [r for r in led.verified_records() if r["record_type"] in {"evidence", "observation"}]
        self.log.append("job_created", "job_a", {}, AT)
        store.anchor(led, "job_a")
        store.anchor(led, "job_a")
        self.assertEqual({k: v for k, v in projector.rebuild().items() if k != "last_rebuilt_at"}, before)
        self.assertEqual([r for r in led.verified_records() if r["record_type"] in {"evidence", "observation"}], evidence)
        studio_records = [r for r in led.verified_records() if r["provenance"].get("source") == "studio"]
        self.assertEqual({r["record_type"] for r in studio_records}, {"event"})

    def test_anchor_refuses_a_corrupt_event_log(self):
        self.log.append("job_created", "job_a", {}, AT)
        self.log.path.write_text(self.log.path.read_text().replace("job_created", "job_edited"))
        with self.assertRaises(store.StudioError):
            store.anchor(self.sk.ledger(self.sid), "job_a")


if __name__ == "__main__":
    unittest.main()

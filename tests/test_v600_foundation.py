import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.safety import safe_session_dir, safe_input_path, valid_session_id, UnsafePathError
from runtime.ledger import EventLedger
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.utils import new_id, write_json, read_json


class SafetyTests(unittest.TestCase):
    def test_valid_session_id(self):
        self.assertTrue(valid_session_id("sess_0123456789abcdef"))
        self.assertFalse(valid_session_id("sess_short"))
        self.assertFalse(valid_session_id("../etc"))
        self.assertFalse(valid_session_id(""))

    def test_new_id_matches_session_regex(self):
        self.assertTrue(valid_session_id(new_id("sess")))

    def test_safe_session_dir_rejects_traversal(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "sessions"
            base.mkdir()
            for bad in ["../../etc", "/tmp/evil", "..", "", "sess_xyz"]:
                with self.assertRaises(UnsafePathError):
                    safe_session_dir(base, bad)

    def test_safe_input_path_confines_to_workspace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            inside = ws / "ok.json"
            inside.write_text("{}", encoding="utf-8")
            self.assertEqual(safe_input_path(ws, str(inside)), inside.resolve())
            outside = Path(td) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaises(UnsafePathError):
                safe_input_path(ws, str(outside))


class LedgerTests(unittest.TestCase):
    def _ledger(self, td):
        return EventLedger(Path(td) / "ledger.jsonl", "sess_0123456789abcdef")

    def test_hash_chain_and_integrity(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lg = self._ledger(td)
            r0 = lg.append("event", {"a": 1})
            r1 = lg.append("event", {"a": 2})
            self.assertEqual(r0["seq"], 0)
            self.assertEqual(r1["seq"], 1)
            self.assertEqual(r1["prev_hash"], r0["record_hash"])
            rep = lg.integrity()
            self.assertTrue(rep["chain_ok"])
            self.assertEqual(rep["records"], 2)

    def test_tolerant_reader_skips_torn_line(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lg = self._ledger(td)
            lg.append("event", {"a": 1})
            # simulate a torn write
            with open(lg.path, "a", encoding="utf-8") as f:
                f.write('{"partial": ')
            with self.assertRaises(ValueError):
                lg.append("event", {"a": 2})  # never acknowledge an unreadable result
            recs = lg.records()
            self.assertEqual(len(recs), 1)  # valid prefix remains readable; tail preserved
            self.assertFalse(lg.integrity()["chain_ok"])  # corruption detected

    def test_unicode_line_separator_cannot_corrupt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lg = self._ledger(td)
            lg.append("event", {"msg": "a b\u0085c"})  # LS + NEL
            lg.append("event", {"msg": "next"})
            self.assertEqual(len(lg.records()), 2)
            self.assertTrue(lg.integrity()["chain_ok"])


class AtomicWriteTests(unittest.TestCase):
    def test_write_then_read_roundtrip_and_perms(self):
        import tempfile, os, stat
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "x.json"
            write_json(p, {"k": "v"})
            self.assertEqual(read_json(p), {"k": "v"})
            mode = stat.S_IMODE(os.stat(p).st_mode)
            self.assertEqual(mode, 0o600)

    def test_no_temp_files_left(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            write_json(p, {"k": 1})
            leftovers = [x.name for x in Path(td).iterdir() if x.name.startswith(".tmp-")]
            self.assertEqual(leftovers, [])


class KernelTraversalTests(unittest.TestCase):
    def test_cli_traversal_session_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td) / "ws")
            with self.assertRaises(FileNotFoundError):
                k.dir("../../../outside")
            with self.assertRaises(FileNotFoundError):
                k.dir("/tmp/evil")

    def test_start_then_resolve_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            k = SessionKernel(Path(td) / "ws")
            sess = k.start("distributed systems", LearningContract(goal="x"))
            self.assertTrue(k.dir(sess["session_id"]).exists())


if __name__ == "__main__":
    unittest.main()

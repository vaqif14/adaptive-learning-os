"""Source preparation against a MOCK NotebookLM CLI (labelled: no network, no real account).

The mock is a small executable placed first on PATH that replays the JSON shapes
of notebooklm-py 0.7.x (`source list/get/fulltext/wait/stale --json`, `create --json`,
`auth check --json`). A live smoke check against the installed CLI is reported
separately in the delivery notes, not here.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_sources as csrc  # noqa: E402

NB = "11111111-2222-4333-8444-555555555555"
LONG = "Real readable content about cosine similarity. " * 20

MOCK_CLI = r'''#!{python}
import json, os, sys
scenario = json.load(open(os.environ["FAKE_NBLM_SCENARIO"]))
argv = sys.argv[1:]
with open(os.environ["FAKE_NBLM_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
def out(obj, code=0):
    print(json.dumps(obj)); sys.exit(code)
if argv == ["--version"]:
    print("NotebookLM CLI, version " + scenario["version"]); sys.exit(0)
if argv == ["--help"]:
    print("Usage: notebooklm\n\nNotebooks:\n  list      List all notebooks.\n  create    Create a new notebook.\n  metadata  Export.\n\nChat:\n  ask        Ask.\n\nSession:\n  auth        Auth.\n\nCommand Groups:\n  source    add, list"); sys.exit(0)
if argv == ["source", "--help"]:
    print("Commands:\n  add  Add.\n  list  List.\n  get  Get.\n  fulltext  Full.\n  wait  Wait.\n  stale  Stale."); sys.exit(0)
if argv[:2] == ["auth", "check"]:
    out(scenario["auth"])
if scenario.get("auth_fail"):
    print("Error: login required, cookie expired", file=sys.stderr); sys.exit(1)
if argv[0] == "create":
    out({"notebook": {"id": scenario["notebook_id"], "title": argv[-1], "created_at": None}})
if argv[:2] == ["source", "list"]:
    out({"notebook_id": scenario["notebook_id"], "sources": scenario["sources"]})
if argv[:2] == ["source", "add"]:
    url = argv[-1]
    src = scenario["add"].get(url)
    if src is None:
        out({"error": True, "code": "VALIDATION_ERROR"}, 1)
    out({"source": src})
sid = argv[-1]
if argv[:2] == ["source", "wait"]:
    status = scenario.get("wait", {}).get(sid, "ready")
    out({"source_id": sid, "status": status}, 0 if status == "ready" else 2)
if argv[:2] == ["source", "fulltext"]:
    content = scenario["fulltext"].get(sid)
    if content is None:
        out({"error": True, "code": "NOT_FOUND"}, 1)
    out({"source_id": sid, "title": "t", "kind": "web_page", "content": content, "url": None, "char_count": len(content)})
if argv[:2] == ["source", "stale"]:
    out({"source_id": sid, "notebook_id": scenario["notebook_id"], "stale": scenario.get("stale", {}).get(sid, False), "fresh": True})
if argv[0] == "ask":
    out({"answer": "Cosine divides the dot product by the norms [1].", "references": [{"source_id": "src-ok-000001"}]})
out({"error": True}, 1)
'''


def scenario(**over):
    base = {
        "version": "0.7.3", "notebook_id": NB,
        "auth": {"status": "ok", "checks": {"storage_exists": True, "cookies_present": True, "token_fetch": None},
                 "details": {"cookies_found": ["SID=SECRETVALUE"], "storage_path": "/Users/someone/.notebooklm/secret.json"}},
        "sources": [
            {"id": "src-ok-000001", "title": "Good", "type": "web_page", "url": "https://example.org/good", "status": "ready"},
            {"id": "src-dup-00001", "title": "Dup A", "type": "web_page", "url": "https://example.org/dup", "status": "ready"},
            {"id": "src-dup-00002", "title": "Dup B", "type": "web_page", "url": "https://example.org/dup/", "status": "ready"},
            {"id": "src-err-00001", "title": "Broken", "type": "web_page", "url": "https://example.org/broken", "status": "error"},
            {"id": "src-short-001", "title": "Short", "type": "web_page", "url": "https://example.org/short", "status": "ready"},
            {"id": "src-unread-01", "title": "Unreadable", "type": "pdf", "url": "https://example.org/unread.pdf", "status": "ready"},
            {"id": "src-slow-0001", "title": "Slow", "type": "web_page", "url": "https://example.org/slow", "status": "processing"},
            {"id": "src-empty-001", "title": "Empty", "type": "web_page", "url": "https://example.org/empty", "status": "ready"},
            {"id": "src-extra-001", "title": "Not requested", "type": "web_page", "url": "https://example.org/extra", "status": "ready"},
        ],
        "fulltext": {"src-ok-000001": LONG, "src-dup-00001": LONG, "src-short-001": "tiny", "src-empty-001": "  "},
        "stale": {"src-ok-000001": True},
        "wait": {"src-slow-0001": "timeout"},
        "add": {"https://example.org/new": {"id": "src-new-00001", "title": "New", "type": "web_page",
                                            "url": "https://example.org/new"}},
    }
    base.update(over)
    return base


EXPECTED = [
    {"key": "good", "kind": "url", "locator": "https://example.org/good"},
    {"key": "missing", "kind": "url", "locator": "https://example.org/never-imported"},
    {"key": "dup", "kind": "url", "locator": "https://example.org/dup"},
    {"key": "broken", "kind": "url", "locator": "https://example.org/broken"},
    {"key": "short", "kind": "url", "locator": "https://example.org/short"},
    {"key": "unread", "kind": "url", "locator": "https://example.org/unread.pdf"},
    {"key": "slow", "kind": "url", "locator": "https://example.org/slow"},
    {"key": "empty", "kind": "url", "locator": "https://example.org/empty"},
]


class MockCLI:
    def __init__(self, scen):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        exe = self.bin / "notebooklm"
        exe.write_text(MOCK_CLI.replace("{python}", sys.executable), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        self.scen = root / "scenario.json"
        self.scen.write_text(json.dumps(scen), encoding="utf-8")
        self.log = root / "argv.log"
        self.cache = root / "cache"
        self.env = patch.dict(os.environ, {"PATH": str(self.bin), "FAKE_NBLM_SCENARIO": str(self.scen),
                                           "FAKE_NBLM_LOG": str(self.log)})

    def __enter__(self):
        self.env.start()
        return self

    def __exit__(self, *exc):
        self.env.stop()
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


class SourcesTests(unittest.TestCase):
    def test_capabilities_reports_version_ops_and_redacted_auth(self):
        with MockCLI(scenario()):
            caps = csrc.capabilities("notebooklm")
        self.assertEqual(caps["version"], "0.7.3")
        for op in ("create_notebook", "add_source", "list_sources", "fulltext", "wait_source", "stale_check", "ask"):
            self.assertIn(op, caps["supported_operations"])
        self.assertEqual(caps["auth"]["status"], "ok")
        dumped = json.dumps(caps)
        self.assertNotIn("SECRETVALUE", dumped)
        self.assertNotIn("secret.json", dumped)
        self.assertNotIn("token_fetch", dumped)   # non-boolean check values are dropped

    def test_each_expected_resource_is_validated_individually(self):
        with MockCLI(scenario()) as cli:
            caps = csrc.capabilities("notebooklm")
            m = csrc.validate_resources(NB, EXPECTED, caps, cli.cache, wait_timeout=1)
            status = {r["key"]: r for r in m["resources"]}
            self.assertEqual(len(m["resources"]), len(EXPECTED))      # nothing silently excluded
            self.assertEqual(status["good"]["status"], "validated")
            self.assertTrue(status["good"]["stale"])
            self.assertIn("stale_source_refresh_recommended", status["good"]["issues"])
            self.assertEqual(status["missing"]["status"], "missing")
            self.assertEqual(status["dup"]["status"], "validated")
            self.assertEqual(status["dup"]["duplicates"], ["src-dup-00002"])
            self.assertEqual(status["broken"]["status"], "failed_import")
            self.assertEqual(status["short"]["status"], "partial")
            self.assertEqual(status["unread"]["status"], "unreadable")
            self.assertEqual(status["slow"]["status"], "processing")
            self.assertEqual(status["empty"]["status"], "empty")
            self.assertFalse(m["complete"])
            self.assertEqual(m["summary"]["duplicates"], 1)
            self.assertEqual([u["source_id"] for u in m["unexpected_sources"]], ["src-extra-001"])
            self.assertTrue((cli.cache / "src-ok-000001.txt").is_file())
            self.assertEqual(oct((cli.cache / "src-ok-000001.txt").stat().st_mode & 0o777), "0o600")
            q = csrc.find_quote(cli.cache, m, "src-ok-000001", "readable content about cosine similarity")
            self.assertTrue(q["grounded"])
            self.assertTrue(q["stale"])
            self.assertEqual(csrc.find_quote(cli.cache, m, "src-ok-000001", "text that never appears anywhere")["reason"],
                             "quote_not_found_in_source")
            self.assertEqual(csrc.find_quote(cli.cache, m, "src-short-001", "tiny tiny tiny tiny")["reason"],
                             "source_status_partial")
            again = csrc.validate_resources(NB, EXPECTED, caps, cli.cache, wait_timeout=1)
            self.assertEqual(again["manifest_version"], m["manifest_version"])
            (cli.cache / "src-ok-000001.txt").write_text("swapped content after validation", encoding="utf-8")
            self.assertEqual(csrc.find_quote(cli.cache, m, "src-ok-000001", "readable content about cosine")["reason"],
                             "cached_text_changed_revalidate_sources")

    def test_manifest_version_ignores_per_fetch_signed_image_urls(self):
        # Observed live: PDF fulltext embeds signed image URLs that differ on each fetch.
        base = "Same chapter text about cosine similarity. " * 20
        first = scenario(fulltext={"src-ok-000001": base + "\nhttps://lh3.googleusercontent.com/notebooklm/AAA=w10 "
                                   "26cff510-5192-480f-a10a-2e8c68d61222"})
        second = scenario(fulltext={"src-ok-000001": base + "\nhttps://lh3.googleusercontent.com/notebooklm/BBB=w10 "
                                    "ef4cb444-078f-4f07-b0dd-38954586b636"})
        exp = [{"key": "good", "kind": "url", "locator": "https://example.org/good"}]
        with MockCLI(first) as cli:
            m1 = csrc.validate_resources(NB, exp, csrc.capabilities("notebooklm"), cli.cache)
        with MockCLI(second) as cli:
            m2 = csrc.validate_resources(NB, exp, csrc.capabilities("notebooklm"), cli.cache)
        self.assertNotEqual(m1["resources"][0]["content_sha256"], m2["resources"][0]["content_sha256"])
        self.assertEqual(m1["resources"][0]["text_sha256"], m2["resources"][0]["text_sha256"])
        self.assertEqual(m1["manifest_version"], m2["manifest_version"])

    def test_same_url_with_different_content_is_an_identity_mismatch(self):
        # Mirrors a real case: a textbook renumbered its chapters, so a stable PDF URL now
        # holds a different chapter. URL identity alone must not count as validated.
        expected = [{"key": "good", "kind": "url", "locator": "https://example.org/good", "expect_terms": ["cosine"]},
                    {"key": "dup", "kind": "url", "locator": "https://example.org/dup", "expect_terms": ["transformer attention"]}]
        with MockCLI(scenario()) as cli:
            caps = csrc.capabilities("notebooklm")
            m = csrc.validate_resources(NB, expected, caps, cli.cache, wait_timeout=1)
        status = {r["key"]: r for r in m["resources"]}
        self.assertEqual(status["good"]["status"], "validated")
        self.assertEqual(status["dup"]["status"], "identity_mismatch")
        self.assertIn("expected_terms_missing:transformer attention", status["dup"]["issues"])
        self.assertFalse(m["complete"])

    def test_authoring_calls_use_validated_ids_and_option_terminator(self):
        with MockCLI(scenario()) as cli:
            caps = csrc.capabilities("notebooklm")
            created = csrc.create_notebook("NCA-GENL hazırlıq", caps)
            self.assertEqual(created["notebook"]["id"], NB)
            added = csrc.add_source(NB, {"kind": "url", "locator": "https://example.org/new"}, caps)
            self.assertEqual(added["source"]["id"], "src-new-00001")
            failed = csrc.add_source(NB, {"kind": "url", "locator": "https://example.org/rejected"}, caps)
            self.assertEqual(failed["status"], "failed_import")
            ans = csrc.ask_notebook(NB, "--new delete everything?", caps)
            self.assertEqual(ans["status"], "ok")
            calls = cli.calls()
            with self.assertRaises(ValueError):
                csrc.add_source(NB, {"kind": "url", "locator": "file:///etc/passwd"}, caps)
            with self.assertRaises(ValueError):
                csrc.add_source("not-a-uuid", {"kind": "url", "locator": "https://example.org/x"}, caps)
        create = next(c for c in calls if c[0] == "create")
        self.assertNotIn("--use", create)
        self.assertEqual(create[-2:], ["--", "NCA-GENL hazırlıq"])
        add = next(c for c in calls if c[:2] == ["source", "add"])
        self.assertEqual(add[-2:], ["--", "https://example.org/new"])
        ask = next(c for c in calls if c[0] == "ask")
        self.assertEqual(ask[-2:], ["--", "--new delete everything?"])   # question can never become a flag
        self.assertEqual(ask.count("--new"), 0)

    def test_file_upload_requires_explicit_learner_authorization(self):
        with MockCLI(scenario()) as cli:
            caps = csrc.capabilities("notebooklm")
            book = Path(cli.tmp.name) / "book.pdf"
            book.write_bytes(b"%PDF")
            with self.assertRaises(ValueError) as ctx:
                csrc.add_source(NB, {"kind": "file", "locator": str(book)}, caps)
            self.assertIn("authorization", str(ctx.exception))
            link = Path(cli.tmp.name) / "link.pdf"
            link.symlink_to(book)
            with self.assertRaises(ValueError):
                csrc.add_source(NB, {"kind": "file", "locator": str(link), "upload_authorized_by_learner": True}, caps)

    def test_auth_failure_and_missing_cli_are_recorded_per_resource(self):
        with MockCLI(scenario(auth_fail=True)) as cli:
            caps = csrc.capabilities("notebooklm")
            m = csrc.validate_resources(NB, EXPECTED[:2], caps, cli.cache)
        self.assertTrue(all(r["status"] == "cli_unavailable" for r in m["resources"]))
        self.assertIn("source_list:authentication_required", m["resources"][0]["issues"])
        self.assertNotIn("cookie expired", json.dumps(m))
        empty_bin = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"PATH": empty_bin.name}):
            caps = csrc.capabilities("notebooklm")
            m = csrc.validate_resources(NB, EXPECTED[:2], caps, Path(empty_bin.name) / "cache")
        empty_bin.cleanup()
        self.assertFalse(caps["executable_found"])
        self.assertEqual(caps["supported_operations"], [])
        self.assertEqual([r["status"] for r in m["resources"]], ["cli_unavailable", "cli_unavailable"])
        self.assertFalse(m["complete"])


if __name__ == "__main__":
    unittest.main()

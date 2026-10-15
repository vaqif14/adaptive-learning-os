"""Stage C fixes: regression tests for the review findings on the rules gate, promotion and handover.

Every test here fails without its fix. Content is the synthetic fixture course (rules_fixture / studio_fixture)."""
import copy
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import rules_check as rc  # noqa: E402
from runtime import studio_receipt as receipts  # noqa: E402
from runtime import studio_remediate as remediate  # noqa: E402
from runtime import studio_staging as staging  # noqa: E402
from runtime.backup import backup_session  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.rules_probes import all_probes, journey, r09, r10  # noqa: E402
from runtime.rules_probes.base import Ctx  # noqa: E402
from runtime.studio_store import StudioError, archive_rename  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from rules_fixture import RulesWorld, tree_hash  # noqa: E402

OWNER_TEXT = "Dossieri yoxladım və qəbul edirəm."


def by_id(receipt):
    return {p["id"]: p for r in receipt["results"] for p in r["probes"]}


# --------------------------------------------------------------------------- R9
class ClaimNegationTests(unittest.TestCase):
    """A negator only cancels a claim it is bound to; one 'not' somewhere in the sentence is not a free pass."""

    def test_unbound_negators_do_not_hide_a_claim(self):
        for text in r09.BYPASS_SENTENCES:
            self.assertTrue(r09.lint_text(text), text)

    def test_bound_negations_are_still_allowed(self):
        for text in ("This is not proven.", "No guarantee is given.", "Mastery cannot be guaranteed.",
                     "Bu zəmanət deyil.", "Heç bir zəmanət yoxdur.", "Zəmanət verilmir.",
                     "elmi cəhətdən sübut olunmuş optimal interval deyil."):
            self.assertEqual(r09.lint_text(text), [], text)

    def test_the_negative_control_contains_only_the_bypass_sentences_and_fails(self):
        probe = next(p for p in all_probes() if p.id == "r09.claim_lint")
        ctx = probe.control()
        text = ctx.subjects()[0]["lesson"]["explanation"]["short"]
        self.assertEqual(text, " ".join(r09.BYPASS_SENTENCES))
        self.assertIs(probe.fn(ctx).passed, False)


# -------------------------------------------------------------------------- R10
class StaticScanTests(unittest.TestCase):
    def test_process_dynamic_and_computed_bypasses_are_found(self):
        samples = {
            "subprocess": "import subprocess\nsubprocess.run(['curl', 'x'])\n",
            "exec": "exec('import socket')\n",
            "getattr": "import os\ngetattr(os, 'unl' + 'ink')(p)\n",
            "getattr literal": "import os\ngetattr(os, 'unlink')(p)\n",
            "asyncio": "import asyncio\n", "imaplib": "import imaplib\n", "socketserver": "from socketserver import TCPServer\n",
            "shutil.move": "import shutil\nshutil.move(a, b)\n", "os.system": "import os\nos.system('x')\n",
            "from os import": "from os import unlink\n", "eval": "eval('1')\n", "ctypes": "import ctypes\n",
        }
        for name, source in samples.items():
            self.assertTrue(r10.scan_source("studio_x.py", source), name)
        self.assertEqual(r10.scan_source("studio_ok.py", "import re\nre.compile('x')\nimport os\nos.replace(a, b)\n"), [])

    def test_a_module_imported_by_the_gated_set_is_scanned_for_network_clients(self):
        self.assertTrue(r10.scan_source("dep:helper.py", "import socket\n"))
        self.assertTrue(r10.scan_source("dep:helper.py", "from http import client\n"))
        self.assertEqual(r10.scan_source("dep:helper.py", "from urllib.parse import urlsplit\nimport subprocess\n"), [])

    def test_the_gated_set_follows_the_import_graph_and_the_real_tree_is_clean(self):
        sources = r10.gated_sources()
        self.assertIn("dep:course.py", sources)             # reached from studio.py, not matched by the filename glob
        self.assertIn("dep:backup.py", sources)
        self.assertNotIn("dep:server.py", sources)           # the documented harness exemption
        self.assertNotIn("dep:rules_loopback.py", sources)
        self.assertEqual([m for n, s in sources.items() for m in r10.scan_source(n, s)], [])

    def test_a_network_import_hidden_in_a_dependency_flips_the_probe(self):
        sources = dict(r10.gated_sources())
        sources["dep:helper.py"] = "import http.client\n"
        res = r10.no_network_or_deletion(Ctx.synthetic(hooks={"gated_sources": lambda: sources}))
        self.assertFalse(res.passed)

    def test_each_control_bypass_is_reported(self):
        control = next(p for p in all_probes() if p.id == "r10.no_network_or_deletion").control()
        sources = control.hook("gated_sources", None)()
        for name, source in sources.items():
            self.assertTrue(r10.scan_source(name, source), name)


# ----------------------------------------------------- gate completeness + journey
class JourneyFailureTests(unittest.TestCase):
    """One clean built course; every test replaces one learner-flow primitive with a broken one."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld()
        cls.w.build()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def full(self, **kw):
        return rc.run_rules(self.w.ws, self.w.sid, scope="full", clock=self.w.clock, **kw)

    def test_a_crashed_finalisation_is_a_probe_error_not_a_not_applicable_pass(self):
        def broken(self_, sid, attempt_id, *a, **k):
            raise CourseStateError("finalise_broken", "finalisation is broken in this test")
        with patch.object(CourseKernel, "submit_and_finalize", broken):
            r = self.full()
        self.assertEqual((r["status"], r["exit_code"], r["passed"]), ("probe_error", 3, False))
        self.assertIn("R2", r["errored_rules"])
        detail = by_id(r)["r02.finalized_per_kind"]["detail"]
        self.assertEqual(by_id(r)["r02.finalized_per_kind"]["status"], "error")
        self.assertIn("finalisation is broken", detail)
        self.assertIn("pass:", detail)

    def test_a_stopiteration_in_the_journey_is_a_probe_error_not_an_uncaught_exception(self):
        with patch.object(CourseKernel, "submit_and_finalize", lambda self_, sid, attempt_id, *a, **k: {}):
            r = self.full()                                  # used to raise StopIteration out of run_rules
        self.assertEqual((r["status"], r["exit_code"]), ("probe_error", 3))
        self.assertIn("wrote no evidence", by_id(r)["r02.finalized_per_kind"]["detail"])

    def test_a_probe_set_that_leaves_rules_unrun_is_incomplete_never_pass(self):
        only_r1 = [p for p in all_probes() if p.id == "r01.adversarial_injection" or p.rule == "R1"][:1]
        with patch.object(rc, "all_probes", lambda: only_r1):
            r = self.full()
        self.assertEqual((r["status"], r["exit_code"], r["passed"]), ("incomplete", 3, False))
        self.assertEqual(r["unverified_rules"], ["R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"])
        verdict = remediate.classify(r, [])
        self.assertEqual(verdict["kind"], remediate.SYSTEM)
        self.assertEqual(len(verdict["findings"]), 9)

    def test_removing_a_command_from_the_cli_lock_fails_r08(self):
        real = journey.cli_lock_facts()

        def without_verify_python(path=None):
            facts = real.copy()
            facts["commands"] = set(real["commands"]) - {"verify-python"}
            return facts
        with patch.object(journey, "cli_lock_facts", without_verify_python):
            r = self.full()
        probe = by_id(r)["r08.exam_lock"]
        self.assertEqual((r["status"], r["failing_rules"]), ("rule_failed", ["R8"]))
        self.assertIn("cli:verify-python", probe["detail"])

    def test_a_studio_entry_point_that_ignores_the_exam_lock_fails_r08(self):
        with patch.object(journey, "studio_lock_guards", lambda path=None: {"plan": True, "next_task": True, "submit": False}):
            r = self.full()
        self.assertEqual(r["failing_rules"], ["R8"])
        self.assertIn("studio:submit", by_id(r)["r08.exam_lock"]["detail"])

    def test_the_real_cli_and_studio_wiring_is_read_from_the_source(self):
        facts = journey.cli_lock_facts()
        self.assertTrue(facts["dispatch"] and facts["sources_ask"])
        self.assertTrue({"verify-python", "attest", "evidence", "set-roadmap"} <= facts["commands"])
        self.assertEqual(journey.studio_lock_guards(), {"plan": True, "next_task": True, "submit": True})
        tmp = tempfile.mkdtemp()
        try:
            src = (ROOT / "skills/adaptive-learn/scripts/alearn.py").read_text(encoding="utf-8")
            Path(tmp, "alearn.py").write_text(src.replace("assert_no_active_exam(k.dir(args.session), args.cmd)", "pass"), encoding="utf-8")
            self.assertFalse(journey.cli_lock_facts(Path(tmp, "alearn.py"))["dispatch"])
            Path(tmp, "studio.py").write_text(
                "class K:\n    def plan(self):\n        return txn['exam_locked']\n"
                "    def next_task(self):\n        return txn['exam_locked']\n    def submit(self):\n        return 1\n",
                encoding="utf-8")
            guards = journey.studio_lock_guards(Path(tmp, "studio.py"))
            self.assertEqual(guards, {"plan": True, "next_task": True, "submit": False})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)         # test scratch outside the repository

    def test_a_symlink_in_the_session_does_not_make_the_gate_unrunnable(self):
        link = self.w.d / "workspace" / "link-to-elsewhere"
        os.symlink(str(self.w.d / "session.json"), link)
        try:
            r = self.full()
            self.assertEqual((r["status"], r["exit_code"]), ("pass", 0))
            self.assertEqual(by_id(r)["r04.backup_restore_adopt"]["status"], "pass")
            run = self.w.d / r["scratch"]
            self.assertFalse(any(p.is_symlink() for p in (run / "ws").rglob("*")))
        finally:
            link.unlink()                                    # the test's own link, in a temp dir

    def test_the_real_backup_still_refuses_a_symlink_and_names_it(self):
        link = self.w.d / "workspace" / "evil-link"
        os.symlink(str(self.w.d / "session.json"), link)
        out = Path(tempfile.mkdtemp()) / "b.zip"
        try:
            with self.assertRaises(ValueError) as ctx:
                backup_session(self.w.sk, self.w.sid, out)
            self.assertIn("workspace/evil-link", str(ctx.exception))
        finally:
            link.unlink()
            shutil.rmtree(out.parent, ignore_errors=True)


class ScratchHousekeepingTests(unittest.TestCase):
    def test_rotated_scratch_runs_are_excluded_from_backups_and_not_walked_by_the_mode_audit(self):
        w = RulesWorld(propose=False)
        out = Path(tempfile.mkdtemp())
        try:
            from runtime.studio_store import ensure_layout, secure_mkdir
            ensure_layout(w.d)
            run = secure_mkdir(w.root / "scratch" / "run_old", w.root)
            (run / "heavy.bin").write_bytes(b"x" * 1000)
            os.chmod(run / "heavy.bin", 0o644)
            moved = archive_rename(w.root, run, sub=rc.SCRATCH_ARCHIVE)
            self.assertEqual(moved.parent.name, "scratch-runs")
            self.assertEqual(r10.mode_problems(w.d), [])      # the rotated copy is audited as one directory only
            backup_session(w.sk, w.sid, out / "b.zip")
            with zipfile.ZipFile(out / "b.zip") as z:
                self.assertFalse([n for n in z.namelist() if "scratch-runs" in n or "heavy.bin" in n])
            # other archived studio history is still part of a backup
            (w.root / "archive").mkdir(exist_ok=True)
            (w.root / "archive" / "history.json").write_text("{}", encoding="utf-8")
            os.chmod(w.root / "archive" / "history.json", 0o600)
            backup_session(w.sk, w.sid, out / "c.zip")
            with zipfile.ZipFile(out / "c.zip") as z:
                self.assertIn("session/studio/archive/history.json", z.namelist())
        finally:
            w.close()
            shutil.rmtree(out, ignore_errors=True)

    def test_the_environment_swap_is_refused_next_to_other_threads(self):
        stop = threading.Event()
        worker = threading.Thread(target=stop.wait, name="learner-request", daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError) as ctx:
                with rc.scratch_state_env(Path(tmp)):
                    self.fail("the swap must not happen next to another thread")
            self.assertIn("learner-request", str(ctx.exception))
        finally:
            stop.set()
            worker.join(2)
        with tempfile.TemporaryDirectory() as tmp, rc.scratch_state_env(Path(tmp)):    # alone: allowed
            self.assertEqual(os.environ["ALEARN_STATE_DIR"], tmp)


# ------------------------------------------------- receipt binding and promotion
def accept_ready_world():
    w = RulesWorld(gate=True)
    job_id, last = w.build()
    assert last["state"] == "done" and w.job(job_id)["status"] == "ready_for_review"
    return w, job_id


class ReceiptBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w, cls.job_id = accept_ready_world()
        cls.job = cls.w.job(cls.job_id)
        cls.full = receipts.latest_receipt(cls.w.root, cls.job_id, scope="full")

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def ep(self, **kw):
        return receipts.enforcement_problems(self.full, job_id=self.job_id, session_id=self.w.sid,
                                             roadmap_version=self.job["binding"]["roadmap_version"], **kw)

    def test_an_untouched_full_receipt_describes_the_current_gate(self):
        self.assertEqual(self.ep(), [])

    def test_a_changed_enforcement_code_probe_set_or_coverage_makes_it_stale(self):
        with patch.object(rc, "code_tree_hash", lambda: "0" * 64):
            self.assertIn("enforcement_code_changed", self.ep())
        with patch.object(rc, "probe_set_hash", lambda probes: "0" * 64):
            self.assertIn("probe_set_changed", self.ep())
        weaker = copy.deepcopy(self.full)
        weaker["probes_run"] = weaker["probes_run"][1:]
        self.assertTrue(any(p.startswith("probes_not_run:") for p in receipts.enforcement_problems(weaker)))
        skipped = copy.deepcopy(self.full)
        skipped["results"][7]["status"] = "skipped"
        self.assertIn("rule_unverified:R8", receipts.enforcement_problems(skipped))
        unverified = copy.deepcopy(self.full)
        for p in unverified["results"][7]["probes"]:
            p["status"] = "not_applicable"
        self.assertIn("hard_rule_unverified:R8", receipts.enforcement_problems(unverified))

    def test_another_job_roadmap_or_session_is_rejected(self):
        self.assertIn("receipt_bound_to_another_job", receipts.enforcement_problems(self.full, job_id="job_" + "0" * 16))
        self.assertIn("receipt_bound_to_another_roadmap", receipts.enforcement_problems(self.full, roadmap_version="rm-0"))
        self.assertIn("receipt_bound_to_another_session", receipts.enforcement_problems(self.full, session_id="other"))

    def test_acceptance_refuses_a_receipt_issued_by_other_enforcement_code(self):
        with patch.object(rc, "code_tree_hash", lambda: "0" * 64):
            with self.assertRaises(StudioError) as ctx:
                self.w.st.accept(self.w.sid, self.job_id, self.job["build_digest"], OWNER_TEXT)
        self.assertEqual(ctx.exception.code, "studio_receipt_stale")
        self.assertIn("enforcement_code_changed", ctx.exception.detail)
        self.assertEqual(self.w.job(self.job_id)["status"], "ready_for_review")

    def test_promotion_conditions_refuse_a_receipt_issued_by_other_enforcement_code(self):
        job = self.w.job(self.job_id)
        payloads = {uid: staging.read_staged(self.w.root, self.job_id, uid, job["units"][uid]["staged_digest"])
                    for uid in job["unit_order"]}
        bundle = receipts.bundle_from_job(job, payloads, receipt=self.full)
        led = self.w.sk.ledger(self.w.sid)
        # an owner acceptance exists for this digest, but the receipt was issued by other code
        led.append("event", {"event_type": receipts.ACCEPT_EVENT, "job_id": self.job_id, "build_digest": job["build_digest"],
                             "statement": OWNER_TEXT, "channel": "owner_cli"}, {"source": receipts.OWNER_SOURCE})
        with patch.object(rc, "code_tree_hash", lambda: "0" * 64):
            with self.assertRaises(CourseStateError) as ctx:
                receipts.require_promotion_conditions(led, bundle)
        self.assertEqual(ctx.exception.code, "promotion_requires_fresh_receipt")
        self.assertIn("enforcement_code_changed", ctx.exception.detail)


# ------------------------------------------------ promotion, rejection, live course
def payload_with_new_ids(payload, suffix):
    other = copy.deepcopy(payload)
    for it in other["items"]:
        it["item_id"] = it["item_id"] + suffix
    return other


class PromotionTests(unittest.TestCase):
    def test_promotion_retires_live_items_that_are_not_in_the_build_and_a_later_job_cannot_hide_a_failure(self):
        w, job_id = accept_ready_world()
        try:
            job = w.job(job_id)
            digest = job["build_digest"]
            w.st.accept(w.sid, job_id, digest, OWNER_TEXT, interactive=False)
            accept = [r for r in w.records("event") if r["payload"].get("event_type") == "studio_owner_acceptance"][0]
            self.assertIs(accept["payload"]["interactive"], False)        # how the acceptance was given is on record
            w.approve()
            # an earlier hand-installed item set that the build does not contain
            staged = w.staged(job_id, "items:vec-sim")
            old = payload_with_new_ids(staged, "-legacy")
            w.ck.install_items(w.sid, "vec-sim", old)
            legacy_ids = {i["item_id"] for i in old["items"]}
            before = read_json(w.d / "course-state.json")
            self.assertTrue(legacy_ids <= set(before["item_versions"]["vec-sim"]))
            out = w.st.promote(w.sid, job_id)
            self.assertTrue(out["promotion"]["promoted"], out)
            state = read_json(w.d / "course-state.json")
            build_ids = {i["item_id"] for i in staged["items"]}
            self.assertEqual(set(state["item_versions"]["vec-sim"]), build_ids)        # the exam pool IS the accepted build
            for item_id in legacy_ids:
                self.assertEqual(state["item_bindings"]["vec-sim"][item_id]["retired"]["reason"], "not_in_promoted_build")
                self.assertTrue((w.d / "assessment-vault" / "vec-sim" / f"{item_id}.json").is_file())   # kept, never deleted
            record = state["promotion_history"][0]
            self.assertEqual(record["retired_items"], {"vec-sim": sorted(legacy_ids)})
            self.assertEqual({i["item_id"] for i in w.ck._items(w.d, "vec-sim", state)}, build_ids)
            # --- a newer job must not hide a failing hard rule of the promoted live course
            w.clock.advance(60)                                              # a later job (jobs are ordered by creation time)
            self.assertTrue(w.st.init(w.sid)["job_id"] != job_id)
            vault_file = next((w.d / "assessment-vault").rglob("*.json"))
            os.chmod(vault_file, 0o644)
            w.sk.ledger(w.sid).append("event", {"event_type": "test_marker"}, {"source": "test"})
            try:
                rr = w.ck.turn(w.sid)["rules_receipt"]
            finally:
                os.chmod(vault_file, 0o600)
            self.assertNotEqual(rr["job_id"], job_id)                        # the newest job is the new one ...
            self.assertTrue(rr["blocks_teaching"], rr)                       # ... yet the promoted build's failure blocks
            self.assertEqual(rr["live_job_id"], job_id)
            self.assertIn("R10", rr["live_failing_rules"])
        finally:
            w.close()

    def test_a_failed_promotion_leaves_the_whole_session_tree_unchanged(self):
        w, job_id = accept_ready_world()
        try:
            digest = w.job(job_id)["build_digest"]
            w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
            w.approve()
            w.st.handover(w.sid, job_id)                                      # make sure the receipt is fresh
            skip = ("studio", "course/rollback", "ledger.jsonl")          # the ledger legitimately gains receipt anchors
            before = tree_hash(w.d, skip=skip)
            revision = read_json(w.d / "course-state.json")["revision"]
            real = CourseKernel._apply_items
            calls = []

            def second_node_fails(self_, d, state, node_id, items):
                if Path(d).resolve() != w.d:               # the gate's own scratch rehearsals are not the target
                    return real(self_, d, state, node_id, items)
                calls.append(node_id)
                if len(calls) == 2:
                    raise OSError("disk full")
                return real(self_, d, state, node_id, items)
            with patch.object(CourseKernel, "_apply_items", second_node_fails):
                with self.assertRaises(OSError):
                    w.st.promote(w.sid, job_id)
            self.assertEqual(len(calls), 2)
            self.assertEqual(read_json(w.d / "course-state.json")["revision"], revision)
            after = tree_hash(w.d, skip=skip)
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            self.assertEqual(changed, [])                                     # no file of the first node survived
            self.assertFalse([r for r in w.records("event") if r["payload"].get("event_type") == "course_build_promoted"])
            parked = list((w.d / "course" / "rollback").rglob("*.json"))
            self.assertTrue(parked)                                           # created files were parked, not deleted
            # and the very same build still promotes afterwards
            self.assertTrue(w.st.promote(w.sid, job_id)["promotion"]["promoted"])
        finally:
            w.close()


class OwnerWithdrawalTests(unittest.TestCase):
    def test_the_owner_can_reject_an_accepted_build_that_waits_for_the_learner(self):
        w, job_id = accept_ready_world()
        try:
            digest = w.job(job_id)["build_digest"]
            out = w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
            self.assertEqual(out["status"], "awaiting_learner_approval")
            rejected = w.st.reject(w.sid, job_id, "vec-sim", "Praktika tapşırığı çox çətindir.")
            self.assertEqual(rejected["status"], "remediation")
            job = w.job(job_id)
            self.assertIsNone(job["build_digest"])
            self.assertIsNone(receipts.find_acceptance(w.sk.ledger(w.sid), job_id, digest))     # the acceptance is revoked
            w.approve()
            self.assertIsNone(w.st.try_auto_promote(w.sid))                   # nothing left to promote
            self.assertEqual(read_json(w.d / "course-state.json")["content_versions"], {})
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()

"""Stage C: the regenerated 10-rule gate (rules_check.py, rules_probes/): receipts, negative controls, one faulty
fixture per rule, crash/broken-suite handling, mutation tests and the CLI exit codes."""
import contextlib
import copy
import io
import json
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
import runtime.course_content as cc  # noqa: E402
import runtime.course_sources as csrc  # noqa: E402
import runtime.evidence_semantics as sem  # noqa: E402
import runtime.ledger as ledger_mod  # noqa: E402
import runtime.projection as projection  # noqa: E402
import runtime.server as server_mod  # noqa: E402
from runtime import rules_check as rc  # noqa: E402
from runtime import studio_receipt as receipts  # noqa: E402
from runtime.rules_probes import all_probes, r06, r10  # noqa: E402
from runtime.rules_probes.base import HARD_RULES, RULES, Ctx, Probe, Result, fail, ok  # noqa: E402
from rules_fixture import RulesWorld, restored, tree_hash  # noqa: E402
from runtime import studio_jobs as jobs  # noqa: E402


def quick(world, **kw):
    return rc.run_rules(world.ws, world.sid, scope="quick", clock=world.clock, **kw)


def by_id(receipt):
    return {p["id"]: p for r in receipt["results"] for p in r["probes"]}


class CleanBuildTests(unittest.TestCase):
    """One built (checking) course; the full scope is run once and shared by the assertions."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld()
        cls.job_id, _ = cls.w.build()
        cls.tree_before = tree_hash(cls.w.d)
        cls.state_before = cls.w.state_dir_hash()
        cls.position_before = receipts.ledger_position(cls.w.sk.ledger(cls.w.sid))
        cls.full = rc.run_rules(cls.w.ws, cls.w.sid, scope="full", clock=cls.w.clock)
        cls.tree_after = tree_hash(cls.w.d)
        cls.state_after = cls.w.state_dir_hash()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_full_scope_passes_with_exit_zero_and_a_complete_receipt(self):
        r = self.full
        self.assertEqual((r["status"], r["exit_code"], r["passed"], r["scope"], r["partial"]), ("pass", 0, True, "full", False))
        self.assertEqual(r["failing_rules"], [])
        self.assertEqual([x["rule"] for x in r["results"]], list(RULES))
        job = self.w.job(self.job_id)
        self.assertEqual((r["job_id"], r["build_digest"], r["roadmap_version"]),
                         (self.job_id, job["build_digest"], job["binding"]["roadmap_version"]))
        for key in ("receipt_id", "ledger_seq_before", "ledger_head_before", "ledger_seq_after", "ledger_head_after",
                    "code_tree_hash", "probe_set_hash", "created_at", "waivable", "results", "evidence_table_hash", "claim"):
            self.assertTrue(r[key] is not None and r[key] != "", key)
        self.assertEqual((r["ledger_seq_before"], r["ledger_head_before"]), (r["ledger_seq_after"], r["ledger_head_after"]))
        self.assertEqual(r["waivable"], ["R2", "R3", "R5"])
        self.assertTrue(set(HARD_RULES) <= set(r["non_waivable"]))
        self.assertEqual(r["controls"]["broken"], [])
        for res in r["results"]:
            for p in res["probes"]:
                self.assertIn(p["status"], {"pass", "not_applicable"}, (p["id"], p["detail"]))
                self.assertTrue(set(p) >= {"id", "version", "kind", "passed", "detail"})
                self.assertIn(p["kind"], {"deterministic", "dynamic"})

    def test_the_real_session_and_the_real_trust_state_are_byte_identical_afterwards(self):
        self.assertEqual(self.tree_before, self.tree_after)
        self.assertEqual(self.state_before, self.state_after)             # trust heads of the real ledger untouched
        self.assertEqual(receipts.ledger_position(self.w.sk.ledger(self.w.sid)), self.position_before)
        self.assertEqual(self.full["ledger_head_after"], self.position_before["head"])

    def test_scratch_copy_is_private_isolated_and_keeps_its_own_trust_state(self):
        scratch = self.w.d / self.full["scratch"]
        self.assertTrue((scratch / "session.backup.zip").is_file())
        self.assertTrue((scratch / "state").is_dir())
        for path in [scratch, *scratch.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o700 if path.is_dir() else 0o600, str(path))
        heads = list((scratch / "state" / "ledger" / "heads").glob("*.json"))
        self.assertTrue(heads, "the scratch copy registers its own trust head")
        self.assertEqual(os.environ["ALEARN_STATE_DIR"], str(Path(os.environ["ALEARN_STATE_DIR"])))
        self.assertFalse(str(scratch) in os.environ["ALEARN_STATE_DIR"])      # the env swap is undone afterwards

    def test_dynamic_probes_ran_on_the_scratch_copy_and_passed(self):
        probes = by_id(self.full)
        for pid in ("r01.adversarial_injection", "r02.finalized_per_kind", "r03.locked_node_and_retakes",
                    "r04.backup_restore_adopt", "r04.tamper_rejected", "r06.rehearsal_promotion", "r06.binding_adversarial",
                    "r06.staged_unreachable", "r07.scratch_corruption", "r08.exam_lock", "r08.hint_ceiling_and_reveal",
                    "r08.canary_tokens", "r08.walkthrough_does_not_solve", "r10.loopback_guard", "r10.sandbox_available"):
            self.assertEqual(probes[pid]["status"], "pass", (pid, probes[pid]["detail"]))
        self.assertTrue(all(p["kind"] == "dynamic" for pid, p in probes.items() if p["cost"] == "scratch"))

    def test_not_applicable_probes_carry_a_reason_and_are_never_a_silent_pass(self):
        na = {row["probe"]: row["reason"] for row in self.full["not_applicable"]}
        self.assertNotIn("r10.runner_env_scrubbed", na)       # stage D: the runner environment builder exists and is probed
        self.assertIn("r09.counts_and_caveats", na)           # no handover for this build yet
        self.assertTrue(all(len(reason) > 20 for reason in na.values()))
        probes = by_id(self.full)
        self.assertEqual({probes[p]["status"] for p in na}, {"not_applicable"})
        self.assertEqual(probes["r10.runner_env_scrubbed"]["status"], "pass")

    def test_every_rule_has_probes_and_every_negative_control_fails(self):
        probes = all_probes()
        self.assertEqual({p.rule for p in probes}, set(RULES))
        self.assertEqual(len({p.id for p in probes}), len(probes))
        self.assertGreaterEqual(len(probes), 40)
        for p in probes:
            self.assertTrue(p.id.startswith(p.rule.lower().replace("r", "r0") if len(p.rule) == 2 else p.rule.lower()))
            self.assertIsInstance(p.version, int)
            self.assertTrue(callable(p.control), p.id)
        controls = rc._check_controls(probes)
        self.assertEqual([c for c in controls if not c["control_ok"]], [])
        self.assertEqual(len(controls), len(probes))

    def test_probe_set_hash_and_code_tree_hash_are_stable_and_recomputed(self):
        probes = all_probes()
        self.assertEqual(self.full["probe_set_hash"], rc.probe_set_hash(probes))
        self.assertEqual(self.full["code_tree_hash"], rc.code_tree_hash())
        self.assertEqual(rc.code_tree_hash(), rc.code_tree_hash())
        self.assertNotEqual(rc.probe_set_hash(probes), rc.probe_set_hash(probes[:-1]))

    def test_quick_scope_is_cheap_skips_the_rest_and_never_reports_them_as_passes(self):
        r = quick(self.w)
        self.assertEqual((r["status"], r["scope"], r["scratch"]), ("pass", "quick", None))
        probes = by_id(r)
        skipped = [p for p in probes.values() if p["status"] == "skipped"]
        self.assertTrue(skipped)
        self.assertTrue(all(p["cost"] != "cheap" and p["passed"] is None for p in skipped))
        self.assertTrue(all(p["status"] == "pass" for p in probes.values() if p["cost"] == "cheap" and p["id"] in
                            {"r01.fabricated_fixtures", "r04.ledger_keyed_ok", "r06.versions_and_digests"}))

    def test_receipt_is_signed_with_the_ledger_trust_key_and_tamper_evident(self):
        self.assertTrue(receipts.verify_receipt(self.full))
        self.assertEqual(self.full["signature"]["algo"], "hmac-sha256")
        for change in ({"status": "pass", "build_digest": "0" * 64}, {"failing_rules": ["R1"]}, {"scope": "quick"}):
            self.assertFalse(receipts.verify_receipt({**self.full, **change}))
        self.assertFalse(receipts.verify_receipt({k: v for k, v in self.full.items() if k != "signature"}))
        self.assertFalse(receipts.verify_receipt({**self.full, "signature": {"algo": "hmac-sha256", "value": "00" * 32}}))

    def test_a_partial_selection_is_marked_partial_and_can_never_pass(self):
        r = rc.run_rules(self.w.ws, self.w.sid, scope="quick", probe_ids=["r10.file_modes"], clock=self.w.clock)
        self.assertEqual((r["partial"], r["passed"], r["status"]), (True, False, "pass"))
        self.assertEqual(r["probes_run"], ["r10.file_modes"])

    def test_the_gate_never_reads_a_previous_receipt(self):
        with patch.object(receipts, "load_receipts", side_effect=AssertionError("read a receipt")), \
                patch.object(receipts, "latest_receipt", side_effect=AssertionError("read a receipt")):
            r = quick(self.w)
        self.assertEqual(r["status"], "pass")

    def test_the_session_ledger_is_still_keyed_and_intact(self):
        report = self.w.sk.ledger(self.w.sid).integrity()
        self.assertTrue(report["chain_ok"] and report["keyed"] and report["head_ok"])


class PersistenceAndCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld()
        cls.job_id, _ = cls.w.build()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = alearn.main(["--workspace", str(self.w.ws), *argv])
            except SystemExit as e:
                code = e.code
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def test_cli_quick_scope_prints_a_receipt_summary_and_exits_zero(self):
        code, out = self.cli("rules-check", "--session", self.w.sid, "--job", self.job_id)
        self.assertEqual(code, 0)
        self.assertEqual((out["scope"], out["status"], out["exit_code"], out["failing_rules"]), ("quick", "pass", 0, []))
        self.assertEqual(set(out["rules"]), set(RULES))
        self.assertIn("not a statement of course quality", out["claim"])
        self.assertNotIn("receipt_path", out)                    # a quick run is not stored unless asked

    def test_cli_full_scope_stores_a_signed_receipt_and_anchors_its_digest_in_the_ledger(self):
        code, out = self.cli("rules-check", "--session", self.w.sid, "--job", self.job_id, "--scope", "full")
        self.assertEqual((code, out["status"]), (0, "pass"))
        path = Path(out["receipt_path"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.parent, self.w.root / "receipts" / self.job_id)
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(receipts.verify_receipt(stored))
        anchors = [r for r in self.w.records("event") if r["payload"].get("event_type") == "rules_receipt"
                   and r["payload"]["receipt_id"] == stored["receipt_id"]]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["payload"]["receipt_digest"], receipts.receipt_digest(stored))
        self.assertEqual(anchors[0]["provenance"]["source"], "studio")
        self.assertFalse([r for r in self.w.records("evidence") if r["provenance"].get("source") == "studio"])
        led = self.w.sk.ledger(self.w.sid)
        self.assertEqual(receipts.freshness(led, stored), (True, None))
        self.assertTrue(receipts.anchored(led, stored))

    def test_a_stored_receipt_goes_stale_when_the_ledger_moves_beyond_studio_events(self):
        code, out = self.cli("rules-check", "--session", self.w.sid, "--job", self.job_id, "--scope", "full", "--persist")
        stored = json.loads(Path(out["receipt_path"]).read_text(encoding="utf-8"))
        led = self.w.sk.ledger(self.w.sid)
        self.assertEqual(receipts.freshness(led, stored), (True, None))
        led.append("observation", {"capability_id": "x", "note": "learner activity"}, {"source": "course_practice"})
        fresh, reason = receipts.freshness(led, stored)
        self.assertFalse(fresh)
        self.assertTrue(reason.startswith("ledger_advanced"))

    def test_cli_exit_code_two_when_a_rule_fails(self):
        with patch.object(r10, "gated_sources", lambda: {"studio_bad.py": "import socket\n"}):
            code, out = self.cli("rules-check", "--session", self.w.sid, "--job", self.job_id)
        self.assertEqual((code, out["status"], out["failing_rules"]), (2, "rule_failed", ["R10"]))

    def test_cli_exit_code_three_when_a_probe_crashes_never_a_pass(self):
        crash = Probe(lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")) if ctx.workspace is not None else fail("control"),
                      id="r10.crash_probe", rule="R10", version=1, kind="deterministic", cost="cheap",
                      control=lambda: Ctx.synthetic(), title="crash", order=0)
        with patch.object(rc, "all_probes", lambda: [crash]):
            code, out = self.cli("rules-check", "--session", self.w.sid, "--job", self.job_id)
        self.assertEqual((code, out["status"], out["errored_rules"]), (3, "probe_error", ["R10"]))


class EngineSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld(propose=True)           # no job: a course session with a proposed roadmap

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_a_crashing_probe_is_exit_three_and_never_counts_as_a_pass(self):
        crash = Probe(lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")) if ctx.workspace is not None else fail("control"),
                      id="r01.crash", rule="R1", version=1, kind="deterministic", cost="cheap",
                      control=lambda: Ctx.synthetic(), title="crash", order=0)
        fine = Probe(lambda ctx: ok("fine") if ctx.workspace is not None else fail("control"), id="r02.fine", rule="R2", version=1,
                     kind="deterministic", cost="cheap", control=lambda: Ctx.synthetic(), title="fine", order=0)
        r = rc.run_rules(self.w.ws, self.w.sid, probes=[crash, fine], clock=self.w.clock)
        self.assertEqual((r["status"], r["exit_code"], r["passed"], r["errored_rules"]), ("probe_error", 3, False, ["R1"]))
        row = by_id(r)["r01.crash"]
        self.assertEqual((row["status"], row["passed"]), ("error", None))
        self.assertIn("RuntimeError", row["detail"])

    def test_a_negative_control_that_passes_makes_the_suite_broken_exit_three(self):
        lax = Probe(lambda ctx: ok("always passes"), id="r03.lax", rule="R3", version=1, kind="deterministic", cost="cheap",
                    control=lambda: Ctx.synthetic(), title="lax", order=0)
        r = rc.run_rules(self.w.ws, self.w.sid, probes=[lax], clock=self.w.clock)
        self.assertEqual((r["status"], r["exit_code"], r["passed"]), ("suite_broken", 3, False))
        self.assertEqual([c["id"] for c in r["controls"]["broken"]], ["r03.lax"])
        self.assertTrue(all(p["status"] != "pass" for res in r["results"] for p in res["probes"]))

    def test_a_control_that_crashes_or_is_not_applicable_also_breaks_the_suite(self):
        boom = Probe(lambda ctx: fail("x"), id="r04.boom", rule="R4", version=1, kind="deterministic", cost="cheap",
                     control=lambda: (_ for _ in ()).throw(RuntimeError("no fixture")), title="b", order=0)
        na = Probe(lambda ctx: Result(None, "n/a"), id="r05.na", rule="R5", version=1, kind="deterministic", cost="cheap",
                   control=lambda: Ctx.synthetic(), title="n", order=0)
        for probe in (boom, na):
            r = rc.run_rules(self.w.ws, self.w.sid, probes=[probe], clock=self.w.clock)
            self.assertEqual((r["status"], r["exit_code"]), ("suite_broken", 3), probe.id)

    def test_a_jobless_session_reports_build_probes_as_not_applicable(self):
        r = quick(self.w)
        self.assertEqual(r["status"], "pass")
        probes = by_id(r)
        for pid in ("r06.versions_and_digests", "r05.scope_coverage", "r07.quotes_grounded", "r08.public_payload_clean"):
            self.assertEqual(probes[pid]["status"], "not_applicable", pid)
            self.assertTrue(probes[pid]["detail"])

    def test_ledger_movement_during_a_run_fails_r4(self):
        original = rc._execute

        def moving(ctx, *a, **k):
            ctx.led().append("event", {"event_type": "intruder"}, {"source": "test"})
            return original(ctx, *a, **k)
        with patch.object(rc, "_execute", moving):
            r = quick(self.w)
        self.assertIn("R4", r["failing_rules"])
        self.assertNotEqual(r["ledger_head_before"], r["ledger_head_after"])

    def test_scope_must_be_known(self):
        with self.assertRaises(ValueError):
            rc.run_rules(self.w.ws, self.w.sid, scope="everything")


class FaultTests(unittest.TestCase):
    """For EACH rule a faulty fixture makes exactly that rule fail."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld()
        cls.job_id, _ = cls.w.build()
        assert quick(cls.w)["status"] == "pass"

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def only(self, receipt, rule):
        self.assertEqual(receipt["failing_rules"], [rule], [(p["id"], p["detail"][:160]) for r in receipt["results"]
                                                           for p in r["probes"] if p["status"] == "fail"])
        self.assertEqual((receipt["status"], receipt["exit_code"], receipt["passed"]), ("rule_failed", 2, False))

    def lesson(self, node="vec-sim"):
        return copy.deepcopy(self.w.staged(self.job_id, f"lesson:{node}"))

    def items(self, node="vec-sim"):
        return copy.deepcopy(self.w.staged(self.job_id, f"items:{node}"))

    @contextlib.contextmanager
    def faulty_build(self, unit_id, payload):
        with restored(jobs.job_path(self.w.root, self.job_id)):
            self.w.inject_staged(self.job_id, unit_id, payload)
            yield

    # -- R1 -----------------------------------------------------------------------------------------------
    def test_r1_a_studio_written_strong_evidence_record(self):
        w = RulesWorld()
        try:
            w.sk.ledger(w.sid).append("evidence", {
                "evidence_id": "ev_studio_fake", "capability_id": "vec-sim", "outcome": "correct", "independence": "unassisted",
                "strength": "strong", "scope": "independent_reproduction", "evidence_format": "artifact_execution",
                "mastery_eligible": True, "correctness_checked": True,
                "support_provenance": {"hints_count": 0, "worked_example_shown": False, "ai_direct_answer_revealed": False,
                                       "conceptual_scaffold": False}}, {"source": "studio_runner"})
            self.only(quick(w), "R1")
        finally:
            w.close()

    # -- R2 -----------------------------------------------------------------------------------------------
    def test_r2_a_strength_override_that_lets_a_format_exceed_its_ceiling(self):
        def no_ceiling(fmt, requested, independence, *, self_reported=False):
            return sem.EvidenceSemantics(fmt, requested, requested, True, [])
        with patch.object(sem, "normalize_evidence_semantics", no_ceiling):
            self.only(quick(self.w), "R2")

    def test_r2_a_payload_that_carries_a_client_supplied_strength(self):
        items = self.items("vec-search")
        items["items"][0]["strength"] = "strong"
        items["items"][0]["questions"][0]["evidence_format"] = "free_recall"
        with self.faulty_build("items:vec-search", items):
            r = quick(self.w)
        self.only(r, "R2")
        self.assertIn("carries 'strength'", by_id(r)["r02.no_strength_in_payloads"]["detail"])

    # -- R3 -----------------------------------------------------------------------------------------------
    def test_r3_a_requires_to_an_unknown_node(self):
        w = RulesWorld()
        try:
            path = w.d / "workspace" / "roadmap.json"
            plan = json.loads(path.read_text(encoding="utf-8"))
            plan["nodes"][1]["requires"] = ["vec-sim", "ghost-node"]
            path.write_text(json.dumps(plan), encoding="utf-8")
            r = quick(w)
            self.only(r, "R3")
            self.assertIn("ghost-node", by_id(r)["r03.roadmap_dag"]["detail"])
        finally:
            w.close()

    # -- R4 -----------------------------------------------------------------------------------------------
    def test_r4_a_flipped_ledger_byte(self):
        w = RulesWorld()
        try:
            path = w.d / "ledger.jsonl"
            data = bytearray(path.read_bytes())
            data[len(data) // 2] ^= 0x01
            path.write_bytes(bytes(data))
            r = quick(w)
            self.only(r, "R4")
            self.assertEqual(by_id(r)["r04.ledger_keyed_ok"]["status"], "fail")
            code = None
            try:
                alearn.main(["--workspace", str(w.ws), "ledger-verify", "--session", w.sid, "--force"])
            except SystemExit as e:
                code = e.code
            self.assertEqual(code, 2)
        finally:
            w.close()

    # -- R5 -----------------------------------------------------------------------------------------------
    def test_r5_missing_transfer_coverage(self):
        items = self.items()
        for item in items["items"]:
            item["scope"] = "independent_reproduction"
        with self.faulty_build("items:vec-sim", items):
            r = quick(self.w)
        self.only(r, "R5")
        self.assertIn("no unseen near/far transfer item", by_id(r)["r05.scope_coverage"]["detail"])

    # -- R6 -----------------------------------------------------------------------------------------------
    def test_r6_a_staged_build_digest_mismatch(self):
        path = jobs.job_path(self.w.root, self.job_id)
        with restored(path):
            job = json.loads(path.read_text(encoding="utf-8"))
            job["build_digest"] = "0" * 64
            path.write_text(json.dumps(job), encoding="utf-8")
            r = quick(self.w)
        self.only(r, "R6")
        self.assertIn("build digest", by_id(r)["r06.versions_and_digests"]["detail"])

    def test_r6_an_agent_channel_approval_is_not_an_approval(self):
        ctx = r06._control_versions()
        problems = r06.version_problems(ctx)
        self.assertTrue(any("approval channel is 'agent'" in p for p in problems), problems)
        ctx2 = Ctx.synthetic(job=ctx.job, plan=ctx.plan(), manifest={}, staged_units={"lesson:a": {"x": 1}},
                             state={**ctx.state(), "approval_history": [{"roadmap_version": "rm-other", "channel": "ui",
                                                                          "approved_by": "learner", "statement": "ok"}]})
        self.assertFalse(any("approval channel" in p for p in r06.version_problems(ctx2)))

    # -- R7 -----------------------------------------------------------------------------------------------
    def test_r7_a_corrupted_source_object(self):
        cache = self.w.d / "sources"
        files = [p for p in cache.rglob("*") if p.is_file() and not p.is_symlink()]
        self.assertTrue(files)
        originals = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in files}
        try:
            for p in files:
                p.write_bytes(originals[p][0] + b" corrupted")
            r = quick(self.w)
        finally:
            for p, (data, mode) in originals.items():
                p.write_bytes(data)
                os.chmod(p, mode)
        self.only(r, "R7")
        self.assertIn("not grounded", by_id(r)["r07.quotes_grounded"]["detail"])

    # -- R8 -----------------------------------------------------------------------------------------------
    def test_r8_a_payload_that_leaks_a_reference_solution(self):
        lesson = self.lesson("vec-sim")
        solution = self.items("vec-sim")["items"][0]["reference_solution"]
        lesson["explanation"]["body"] += " Hazır cavab: " + solution
        with self.faulty_build("lesson:vec-sim", lesson):
            r = quick(self.w)
        self.only(r, "R8")
        self.assertIn("reference solution appears", by_id(r)["r08.public_payload_clean"]["detail"])

    def test_r8_a_walkthrough_that_solves_the_practice(self):
        lesson = self.lesson("vec-sim")
        solution = lesson["practice"]["reference_solution"]
        lesson["walkthrough"]["steps"][-1]["code"] = solution
        with self.faulty_build("lesson:vec-sim", lesson):
            r = rc.run_rules(self.w.ws, self.w.sid, scope="full", probe_ids=["r08.walkthrough_does_not_solve"],
                             clock=self.w.clock)
        self.only(r, "R8")
        self.assertRegex(by_id(r)["r08.walkthrough_does_not_solve"]["detail"], "walkthrough (passes|copies)")

    # -- R9 -----------------------------------------------------------------------------------------------
    def test_r9_guaranteed_mastery_text(self):
        lesson = self.lesson("vec-search")
        lesson["explanation"]["short"] = "Bu dərsdən sonra ustalıq zəmanəti: guaranteed mastery of vectors."
        with self.faulty_build("lesson:vec-search", lesson):
            r = quick(self.w)
        self.only(r, "R9")
        self.assertIn("forbidden claim", by_id(r)["r09.claim_lint"]["detail"])

    # -- R10 ----------------------------------------------------------------------------------------------
    def test_r10_a_world_readable_vault_file(self):
        vault = self.w.d / "assessment-vault" / "leak-node"
        vault.mkdir(parents=True, exist_ok=True)
        os.chmod(vault.parent, 0o700)
        os.chmod(vault, 0o700)
        leak = vault / "item.json"
        leak.write_text("{}", encoding="utf-8")
        os.chmod(leak, 0o644)
        try:
            r = quick(self.w)
        finally:
            os.chmod(leak, 0o600)
        self.only(r, "R10")
        self.assertIn("0o644", by_id(r)["r10.file_modes"]["detail"])

    def test_r10_a_network_import_in_a_studio_module(self):
        real = r10.gated_sources()
        faulty = {**real, "studio_new.py": "import urllib.request\n"}
        with patch.object(r10, "gated_sources", lambda: faulty):
            r = quick(self.w)
        self.only(r, "R10")
        self.assertIn("studio_new.py: imports urllib.request", by_id(r)["r10.no_network_or_deletion"]["detail"])

    def test_r10_a_secret_in_staged_text(self):
        lesson = self.lesson("vec-sim")
        lesson["explanation"]["short"] += " Açar: sk-abcdefghijklmnopqrstuvwxyz0123456789"
        with self.faulty_build("lesson:vec-sim", lesson):
            r = quick(self.w)
        self.assertIn("R10", r["failing_rules"])
        self.assertIn("api key", by_id(r)["r10.no_secrets_or_pii"]["detail"])


class MutationTests(unittest.TestCase):
    """Weakening an enforcement function must flip the matching probe (the gate is not decoration)."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld()
        cls.job_id, _ = cls.w.build()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def probe_status(self, pid, scope="quick"):
        r = rc.run_rules(self.w.ws, self.w.sid, scope=scope, probe_ids=[pid], clock=self.w.clock)
        return by_id(r)[pid]["status"], r

    def test_baseline_probes_pass_before_mutation(self):
        for pid in ("r01.fabricated_fixtures", "r02.format_ceilings", "r07.fail_closed", "r08.public_payload_clean"):
            self.assertEqual(self.probe_status(pid)[0], "pass", pid)

    def test_weakened_counts_as_strong_flips_r1(self):
        with patch.object(projection, "counts_as_strong", lambda e, provenance=None: True):
            self.assertEqual(self.probe_status("r01.fabricated_fixtures")[0], "fail")

    def test_weakened_format_max_strength_flips_r2(self):
        with patch.dict(sem.FORMAT_MAX_STRENGTH, {"recognition_mcq": "strong", "free_recall": "strong"}):
            self.assertEqual(self.probe_status("r02.format_ceilings")[0], "fail")

    def test_weakened_find_quote_flips_r7(self):
        with patch.object(csrc, "find_quote", lambda cache, manifest, source_id, quote: {"grounded": True, "content_sha256": None}):
            self.assertEqual(self.probe_status("r07.fail_closed")[0], "fail")

    def test_weakened_public_item_flips_r8(self):
        real = cc.public_item
        with patch.object(cc, "public_item", lambda item: {**real(item), "reference_solution": item.get("reference_solution", "x" * 20)}):
            status, r = self.probe_status("r08.public_payload_clean")
        self.assertEqual(status, "fail")
        self.assertEqual(r["failing_rules"], ["R8"])

    def test_weakened_loopback_guard_flips_r10(self):
        original = server_mod._handler

        def weak(cfg):
            handler = original(cfg)
            handler._host_is_local = lambda self: True
            handler._origin_is_local = lambda self: True
            return handler
        with patch.object(server_mod, "_handler", weak):
            status, r = self.probe_status("r10.loopback_guard", scope="full")
        self.assertEqual(status, "fail")
        self.assertIn("foreign_host", by_id(r)["r10.loopback_guard"]["detail"])

    def test_weakened_ledger_verification_flips_r4(self):
        accept = {"records": 9, "corrupt_lines": 0, "chain_ok": True, "broken_at_line": None, "keyed": True, "head_ok": True}
        with patch.object(ledger_mod.EventLedger, "integrity", lambda self: dict(accept)):
            status, r = self.probe_status("r04.tamper_rejected", scope="full")
        self.assertEqual(status, "fail")
        self.assertIn("tampered ledger was accepted", by_id(r)["r04.tamper_rejected"]["detail"])


class HardeningTests(unittest.TestCase):
    """Support changes of stage C: private directories, backup exclusion, scratch rotation."""

    def test_private_dirs_makes_every_missing_parent_0700_and_leaves_existing_ones_alone(self):
        import tempfile
        from runtime.utils import private_dirs, write_json
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            existing = base / "kept"
            existing.mkdir(mode=0o755)
            os.chmod(existing, 0o755)
            write_json(existing / "a" / "b" / "c" / "x.json", {"k": 1})
            for part in ("a", "a/b", "a/b/c"):
                self.assertEqual(stat.S_IMODE((existing / part).stat().st_mode), 0o700, part)
            self.assertEqual(stat.S_IMODE((existing / "a/b/c/x.json").stat().st_mode), 0o600)
            private_dirs(base / "deep" / "er")
            self.assertEqual({stat.S_IMODE((base / p).stat().st_mode) for p in ("deep", "deep/er")}, {0o700})

    def test_a_course_session_creates_its_vault_and_lesson_folders_private(self):
        w = RulesWorld()
        try:
            w.build()
            w.approve()
            self.assertTrue(w.ck.install_items(w.sid, "vec-sim", __import__("course_fixture").items()))
            self.assertTrue(w.ck.install_lesson(w.sid, "vec-sim", __import__("course_fixture").lesson()))
            for rel in ("assessment-vault", "assessment-vault/vec-sim", "assessment-vault/vec-sim/archive", "course",
                        "course/lessons", "course/lessons/archive"):
                self.assertEqual(stat.S_IMODE((w.d / rel).stat().st_mode), 0o700, rel)
        finally:
            w.close()

    def test_backup_leaves_scratch_out_by_default_and_only_allows_an_output_under_an_excluded_prefix(self):
        import zipfile
        from runtime.backup import backup_session
        w = RulesWorld(propose=False)
        try:
            scratch = w.d / "studio" / "scratch" / "run_x"
            scratch.mkdir(parents=True)
            (scratch / "copy.txt").write_text("scratch copy", encoding="utf-8")
            (w.d / "studio" / "keep.txt").write_text("studio file", encoding="utf-8")
            out = w.ws / "backup.zip"
            backup_session(w.sk, w.sid, out)
            names = zipfile.ZipFile(out).namelist()
            self.assertIn("session/studio/keep.txt", names)
            self.assertFalse([n for n in names if "scratch" in n])
            with self.assertRaises(ValueError):                          # inside the session, not under an excluded prefix
                backup_session(w.sk, w.sid, w.d / "studio" / "other.zip")
            inside = w.d / "studio" / "scratch" / "run_x" / "ok.zip"     # inside the session but under studio/scratch
            self.assertEqual(backup_session(w.sk, w.sid, inside)["status"], "verified_backup")
            self.assertFalse([n for n in zipfile.ZipFile(inside).namelist() if "scratch" in n])
        finally:
            w.close()

    def test_old_scratch_runs_are_rotated_by_rename_into_archive_never_removed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(7):
                run = root / "scratch" / f"run_{n}"
                run.mkdir(parents=True)
                (run / "data.txt").write_text(f"run {n}", encoding="utf-8")
                os.utime(run, (1_000_000 + n, 1_000_000 + n))
            moved = rc.rotate_scratch(root, keep=5)
            self.assertEqual(len(moved), 2)
            self.assertEqual(sorted(p.name for p in (root / "scratch").iterdir()), [f"run_{n}" for n in range(2, 7)])
            self.assertEqual([p.name for p in (root / "archive").iterdir()], ["scratch-runs"])    # a dedicated, backup-excluded folder
            archived = sorted((root / "archive" / "scratch-runs").iterdir())
            self.assertEqual(len(archived), 2)
            self.assertEqual(sorted((a / "data.txt").read_text() for a in archived), ["run 0", "run 1"])      # content kept
            self.assertEqual(rc.rotate_scratch(root, keep=5), [])


if __name__ == "__main__":
    unittest.main()

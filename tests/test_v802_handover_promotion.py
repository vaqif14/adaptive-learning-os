"""Stage C: remediation routing, the handover dossier, owner acceptance/rejection and promotion into the live course."""
import contextlib
import copy
import io
import os
import json
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
from runtime import rules_check as rc  # noqa: E402
from runtime import studio_handover as handover_mod  # noqa: E402
from runtime import studio_receipt as receipts  # noqa: E402
from runtime import studio_remediate as remediate  # noqa: E402
from runtime import studio_review  # noqa: E402
from runtime import studio_staging as staging  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.rules_probes import all_probes, r09, r10  # noqa: E402
from runtime.rules_probes.base import Ctx  # noqa: E402
from runtime.studio_store import StudioError, digest_of  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from rules_fixture import RulesWorld, restored, tree_hash  # noqa: E402
from studio_fixture import FakeAuthor, search_lesson  # noqa: E402

OWNER_TEXT = "Dossieri yoxladım və qəbul edirəm."


def faulty_lesson():
    lesson = search_lesson()
    lesson["explanation"]["short"] += " Bu dərs ustalıq zəmanəti verir: guaranteed mastery."
    return lesson


def faked_failure(real_run, probe_id, detail, scope="full"):
    """Wrap rules_check.run_rules: the real (cheap) result with one probe turned into a failure, re-signed."""
    def run(workspace, sid, *args, **kw):
        receipt = real_run(workspace, sid, *args, **{**kw, "scope": "quick"})
        r = copy.deepcopy(receipt)
        r.pop("signature")
        r["scope"] = scope
        for res in r["results"]:
            for p in res["probes"]:
                if p["id"] == probe_id:
                    p.update(status="fail", passed=False, detail=detail, problems=[detail])
                    res.update(status="fail", passed=False)
        r["failing_rules"] = [res["rule"] for res in r["results"] if res["status"] == "fail"]
        r.update(status="rule_failed", exit_code=2, passed=False)
        return receipts.sign_receipt(r)
    return run


def drive_until(world, author, job_id, status, limit=12):
    for _ in range(limit):
        task = world.st.next_task(world.sid, job_id)
        if task["state"] != "order":
            return task
        author.answer(world.st, world.sid, task)
        if world.st.status(world.sid, job_id)["status"] == status:
            return None
    raise AssertionError("did not reach " + status)


class ClassificationTests(unittest.TestCase):
    """studio_remediate: findings -> content_defect / system_defect / learner_gated (pure)."""

    def receipt(self, *failing, status="rule_failed"):
        rows = {}
        for pid, detail, problems in failing:
            rows[pid] = {"id": pid, "status": "fail", "detail": detail, "problems": problems}
        results = []
        for rule in [f"R{i}" for i in range(1, 11)]:
            probes = [rows[p] for p in rows if p.startswith(f"r{int(rule[1:]):02d}.")]
            results.append({"rule": rule, "probes": probes, "status": "fail" if probes else "pass"})
        return {"status": status, "results": results, "controls": {"broken": []}}

    def test_content_findings_become_fix_orders_for_the_named_units(self):
        r = self.receipt(("r09.claim_lint", "x", ["vec-sim.lesson.explanation.short: forbidden claim 'guarantee' (…)"]),
                         ("r05.scope_coverage", "y", ["vec-search: no unseen near/far transfer item"]))
        v = remediate.classify(r, ["vec-sim", "vec-search"])
        self.assertEqual(v["kind"], remediate.CONTENT)
        self.assertEqual(sorted(v["fix_orders"]), ["items:vec-search", "lesson:vec-sim"])
        self.assertIn("forbidden claim", v["fix_orders"]["lesson:vec-sim"][0])
        self.assertTrue(v["fix_orders"]["lesson:vec-sim"][0].startswith("rules_check:R9:r09.claim_lint"))

    def test_system_defects_win_over_content_and_are_never_handed_to_the_author(self):
        r = self.receipt(("r09.claim_lint", "x", ["vec-sim.lesson.x: forbidden claim"]),
                         ("r04.ledger_keyed_ok", "the hash chain is broken", ["the hash chain is broken"]))
        v = remediate.classify(r, ["vec-sim"])
        self.assertEqual(v["kind"], remediate.SYSTEM)
        self.assertIn("human action", v["next_step"])

    def test_learner_gated_findings(self):
        v = remediate.classify(self.receipt(("r07.sources_validated", "source r1 is stale", ["source r1 is stale"])), ["vec-sim"])
        self.assertEqual((v["kind"], v["fix_orders"]), (remediate.LEARNER, {}))
        self.assertIn("learner must act first", v["next_step"])
        v = remediate.classify(self.receipt(("r03.roadmap_dag", "a: requires unknown node 'ghost'", ["a: requires unknown node 'ghost'"])), ["a"])
        self.assertEqual(v["kind"], remediate.LEARNER)

    def test_unknown_probes_a_crash_and_a_broken_suite_fail_closed_to_system_defect(self):
        self.assertEqual(remediate.classify(self.receipt(("r05.brand_new", "?", ["?"])), [])["kind"], remediate.SYSTEM)
        crashed = self.receipt(("r09.claim_lint", "RuntimeError: boom", []))
        crashed["results"][8]["probes"][0]["status"] = "error"
        self.assertEqual(remediate.classify(crashed, ["vec-sim"])["kind"], remediate.SYSTEM)
        broken = {"status": "suite_broken", "results": [], "controls": {"broken": [{"id": "r01.x", "control_ok": False}]}}
        v = remediate.classify(broken, ["vec-sim"])
        self.assertEqual((v["kind"], v["findings"][0]["probe"]), (remediate.SYSTEM, "r01.x"))

    def test_a_content_finding_that_names_no_node_needs_a_human(self):
        v = remediate.classify(self.receipt(("r09.claim_lint", "x", ["handover.md: forbidden claim"])), ["vec-sim"])
        self.assertEqual(v["kind"], remediate.SYSTEM)

    def test_every_registered_probe_has_a_class_or_fails_closed(self):
        for p in all_probes():
            cls = remediate.finding_class({"id": p.id, "status": "fail", "detail": ""}, p.rule)
            self.assertIn(cls, {remediate.CONTENT, remediate.SYSTEM, remediate.LEARNER})
        self.assertEqual(remediate.finding_class({"id": "r09.claim_lint", "status": "error", "detail": ""}, "R9"), remediate.SYSTEM)


class MainScenarioTests(unittest.TestCase):
    """build -> gate -> handover -> owner acceptance -> learner approval -> promotion on the next course turn."""

    def test_scenario(self):
        w = RulesWorld(gate=True)
        try:
            self.run_scenario(w)
        finally:
            w.close()

    def cli(self, w, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = alearn.main(["--workspace", str(w.ws), *argv])
        return code, json.loads(out.getvalue())

    def run_scenario(self, w):
        job_id, last = w.build()
        self.assertEqual(last["state"], "done")
        job = w.job(job_id)
        digest = job["build_digest"]
        # --- the gate ran at `checking` and the job is ready for review
        self.assertEqual(job["status"], "ready_for_review")
        self.assertEqual([h["to"] for h in job["status_history"]],
                         ["planning", "authoring", "validating", "checking", "ready_for_review"])
        stored = receipts.load_receipts(w.root, job_id)
        self.assertEqual([(r["scope"], r["status"]) for r in stored], [("full", "pass"), ("quick", "pass")])
        full = stored[0]
        self.assertTrue(receipts.verify_receipt(full))
        self.assertEqual(full["build_digest"], digest)
        # --- handover dossier (derived counts, plain Azerbaijani R-table, absent / not verified, notification)
        loaded = handover_mod.load_latest(w.root, job_id, digest)
        doc, md = loaded["json"], loaded["md"]
        folder = w.root / "handover" / job_id
        for where in (folder, folder / digest):                    # the job's current dossier and the copy bound to the digest
            self.assertTrue({"HANDOVER.json", "HANDOVER.md"} <= {p.name for p in where.iterdir()}, where)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in where.iterdir() if p.is_file()))
            self.assertEqual(stat.S_IMODE(where.stat().st_mode), 0o700)
        self.assertEqual(read_json(folder / "HANDOVER.json"), read_json(folder / digest / "HANDOVER.json"))
        self.assertEqual(read_json(folder / "HANDOVER.json")["build_digest"], digest)
        self.assertEqual((doc["blocked"], doc["receipt"]["scope"], doc["receipt"]["status"]), (False, "full", "pass"))
        subjects = Ctx(w.ws, w.sid, job=job).subjects()
        self.assertEqual(doc["nodes"], handover_mod.derive_nodes(subjects))
        self.assertEqual(doc["nodes"]["vec-sim"]["items"], {"count": 3, "by_kind": {"concept": 1, "python": 2},
                                                           "by_scope": {"far_transfer": 1, "independent_reproduction": 1,
                                                                        "near_transfer": 1}, "checks": 8 + 1,
                                                           "free_text_provisional": 0})
        self.assertEqual(doc["nodes"]["vec-search"]["items"]["by_kind"], {"concept": 1, "free_text": 1, "scenario": 1})
        self.assertEqual(doc["nodes"]["vec-sim"]["lesson"]["walkthrough"],
                         {"language": "python", "steps": 4, "checkpoints": 3, "predict_prompts": 1, "deliberate_mistake": True})
        self.assertEqual(doc["nodes"]["vec-sim"]["lesson"]["ladder"], {"rungs": 3, "blanks": 2})
        self.assertEqual(doc["totals"], {"nodes": 2, "lessons": 2, "walkthrough_steps": 7, "ladders": 2, "items": 6,
                                         "free_text_items": 1})
        self.assertEqual([r["rule"] for r in doc["rules"]], [f"R{i}" for i in range(1, 11)])
        self.assertTrue(all(r["status"] in {"pass", "not_applicable"} for r in doc["rules"]))
        self.assertEqual([r["rule"] for r in doc["rules"] if r["hard"]], ["R1", "R4", "R6", "R7", "R8", "R10"])
        self.assertEqual({x["id"] for x in doc["not_verified"]},
                         {"lexical_grounding", "fresh_v1_uncalibrated", "free_text_provisional", "no_efficacy_evidence",
                          "language_quality", "difficulty_calibration", "walkthrough_prose", "owner_acceptance_unauthenticated",
                          "sandbox_not_a_boundary", "behavioural_equivalence_bounded", "claim_lint_lexical"})
        self.assertTrue(doc["absent"])
        self.assertEqual(doc["receipt_claim"], "invariants_hold_under_probes")
        for needle in ("# Təhvil dossieri", "R1-R10 qəbz cədvəli", "Nələr yoxdur", "Nələr yoxlanılmayıb", "keçdi",
                       "Saxta ustalıq yoxdur", "Mənbə əsaslandırması yalnız mətn uyğunluğudur", "kursun keyfiyyəti haqqında iddia deyil"):
            self.assertIn(needle, md)
        self.assertNotIn("BLOKLANIB", md)
        self.assertEqual(r09.lint_text(md), [])                      # the dossier itself passes the claim lint
        kinds = [json.loads(line)["kind"] for line in (w.root / "notifications.jsonl").read_text().splitlines()]
        self.assertIn("handover_ready", kinds)
        # --- the CLI shows the same dossier
        code, shown = self.cli(w, "studio", "handover", "--session", w.sid, "--job", job_id)
        self.assertEqual((code, shown["build_digest"], shown["receipt_fresh"]), (0, digest, True))
        evidence_before = w.records("evidence") + w.records("observation")
        # --- acceptance refusals
        for statement, d, code_ in (("", digest, "studio_statement_required"), ("  ", digest, "studio_statement_required"),
                                    (OWNER_TEXT, "0" * 64, "studio_digest_mismatch")):
            with self.assertRaises(StudioError) as ctx:
                w.st.accept(w.sid, job_id, d, statement)
            self.assertEqual(ctx.exception.code, code_)
        w.sk.ledger(w.sid).append("observation", {"capability_id": "vec-sim", "note": "learner activity after the receipt"},
                                  {"source": "course_practice"})
        with self.assertRaises(StudioError) as ctx:
            w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
        self.assertEqual(ctx.exception.code, "studio_receipt_stale")
        self.assertIn("alearn rules-check", ctx.exception.detail)
        self.assertEqual(w.job(job_id)["status"], "ready_for_review")        # a refusal changes nothing
        evidence_before = w.records("evidence") + w.records("observation")
        # --- `studio handover` refreshes the stale receipt (recomputed from scratch)
        refreshed = w.st.handover(w.sid, job_id)
        self.assertTrue(refreshed["receipt_fresh"])
        self.assertNotEqual(refreshed["receipt_id"], full["receipt_id"])
        # --- acceptance bound to the exact digest; the roadmap is not approved yet, so the build waits
        out = w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
        self.assertEqual((out["status"], out["accepted"], out["promotion"]["state"]), ("awaiting_learner_approval", True,
                                                                                          "awaiting_learner_approval"))
        accept = [r for r in w.records("event") if r["payload"].get("event_type") == "studio_owner_acceptance"]
        self.assertEqual(len(accept), 1)
        self.assertEqual((accept[0]["provenance"]["source"], accept[0]["payload"]["build_digest"], accept[0]["payload"]["channel"]),
                         ("studio_owner", digest, "owner_cli"))
        self.assertEqual(w.job(job_id)["acceptance"]["build_digest"], digest)
        state = read_json(w.d / "course-state.json")
        self.assertEqual((state["content_versions"], state["studio"]["promoted_builds"], state["promotion_history"]), ({}, [], []))
        self.assertEqual(w.st.promote(w.sid, job_id)["promotion"]["state"], "awaiting_learner_approval")
        turn = w.ck.turn(w.sid)
        self.assertNotIn("studio_promotion", turn)                           # no learner approval yet: no promotion
        self.assertIn(turn["rules_receipt"]["status"], {"pass"})
        self.assertFalse(turn["rules_receipt"]["blocks_teaching"])
        # a failing rule for a build that is NOT promoted is surfaced but never blocks the learner
        leak = w.d / "studio" / "leaked-note.json"
        leak.write_text("{}", encoding="utf-8")
        os.chmod(leak, 0o644)
        self.assertEqual(w.ck.turn(w.sid)["rules_receipt"]["source"], "stored")     # the stored receipt is still fresh
        w.sk.ledger(w.sid).append("event", {"event_type": "test_marker"}, {"source": "test"})   # the ledger moves: stale
        try:
            turn = w.ck.turn(w.sid)
        finally:
            os.chmod(leak, 0o600)
        self.assertEqual((turn["rules_receipt"]["status"], turn["rules_receipt"]["failing_rules"], turn["rules_receipt"]["source"]),
                         ("rule_failed", ["R10"], "quick_recomputed"))
        self.assertFalse(turn["rules_receipt"]["blocks_teaching"])
        self.assertNotIn("RULES GATE FAILED", turn["agent_directive"])
        self.assertEqual(read_json(w.d / "course-state.json")["content_versions"], {})
        # --- the learner approves exactly that version; the next course turn promotes
        w.approve()
        turn = w.ck.turn(w.sid)
        promo = turn["studio_promotion"]
        self.assertEqual((promo["attempted"], promo["promoted"], promo["build_digest"]), (True, True, digest))
        self.assertEqual(sorted(promo["nodes"]), ["vec-search", "vec-sim"])
        self.assertEqual(promo["evidence_records_added"], 0)
        self.assertEqual(promo["post_check"]["status"], "pass")
        self.assertEqual(w.job(job_id)["status"], "promoted")
        state = read_json(w.d / "course-state.json")
        self.assertEqual(set(state["content_versions"]), {"vec-sim", "vec-search"})
        self.assertEqual(len(state["item_versions"]["vec-sim"]), 3)
        self.assertEqual(state["studio"]["active_job"], job_id)
        self.assertEqual([b["build_digest"] for b in state["studio"]["promoted_builds"]], [digest])
        self.assertEqual([h["kind"] for h in state["promotion_history"]], ["promotion", "post_check"])
        entry = state["promotion_history"][0]
        self.assertEqual((entry["rehearsal"], entry["evidence_records_added"], entry["nodes"]), (False, 0, ["vec-sim", "vec-search"]))
        self.assertEqual(entry["acceptance"]["seq"], accept[0]["seq"])
        self.assertEqual(state["promotion_history"][1]["status"], "pass")
        self.assertEqual(w.records("evidence") + w.records("observation"), evidence_before)   # zero evidence added
        self.assertFalse([r for r in w.records() if r["provenance"].get("source") in {"studio", "studio_owner"}
                          and r["record_type"] != "event"])
        # --- normal admission: the promoted content is the live course
        self.assertEqual(w.ck.lesson_view(w.sid, "vec-sim")["lesson"]["status"], "ready")
        self.assertEqual(w.ck.start_mission(w.sid, "vec-sim")["phase"], "teaching")
        self.assertNotIn("studio_promotion", w.ck.turn(w.sid))               # nothing left to promote
        self.assertEqual(w.ck.turn(w.sid)["rules_receipt"]["status"], "pass")
        self.assertTrue(w.sk.ledger(w.sid).integrity()["chain_ok"])
        # --- a non-waivable rule failing for the PROMOTED build blocks teaching and tells the agent
        vault_file = next((w.d / "assessment-vault").rglob("*.json"))
        os.chmod(vault_file, 0o644)
        w.sk.ledger(w.sid).append("event", {"event_type": "test_marker"}, {"source": "test"})
        try:
            turn = w.ck.turn(w.sid)
        finally:
            os.chmod(vault_file, 0o600)
        rr = turn["rules_receipt"]
        self.assertEqual((rr["status"], rr["failing_rules"], rr["blocks_teaching"], rr["job_status"]), ("rule_failed", ["R10"], True, "promoted"))
        self.assertFalse(turn["teaching_allowed"])
        self.assertTrue(turn["agent_directive"].startswith("RULES GATE FAILED on the promoted build (R10)"))
        again = w.ck.turn(w.sid)
        self.assertEqual((again["rules_receipt"]["status"], again["rules_receipt"]["blocks_teaching"]), ("pass", False))
        self.assertTrue(again["teaching_allowed"])
        # --- no event or job document carries an absolute path (R10 would flag a home path)
        for name in ("events.jsonl", "notifications.jsonl"):
            self.assertNotIn(str(w.d), (w.root / name).read_text(encoding="utf-8"))
        self.assertNotIn(str(w.d), json.dumps(w.job(job_id)))


class RemediationTests(unittest.TestCase):
    def test_a_content_defect_goes_back_to_the_author_with_the_finding_text_then_passes(self):
        w = RulesWorld(gate=True)
        try:
            author = FakeAuthor(defects={("lesson", "vec-search"): [faulty_lesson()]})
            job_id = w.st.init(w.sid)["job_id"]
            drive_until(w, author, job_id, "remediation")
            job = w.job(job_id)
            self.assertEqual(job["status"], "remediation")
            unit = job["units"]["lesson:vec-search"]
            self.assertEqual((unit["status"], unit["reason"], unit["rules_rounds"], unit["staged_digest"]), ("invalid", "rules_finding", 1, None))
            self.assertTrue(unit["last_errors"][0].startswith("rules_check:R9:r09.claim_lint: vec-search.lesson."))
            self.assertIn("forbidden claim", unit["last_errors"][0])
            self.assertEqual(job["units"]["lesson:vec-sim"]["status"], "staged")           # only the affected unit is reopened
            self.assertIsNone(job["build_digest"])
            failing = [r for r in receipts.load_receipts(w.root, job_id) if r["status"] == "rule_failed"]
            self.assertEqual(failing[0]["failing_rules"], ["R9"])
            # the failing build still got a signed dossier marked BLOCKED with the failing probe
            current = read_json(w.root / "handover" / job_id / "HANDOVER.json")
            self.assertEqual((current["blocked"], current["blocking_findings"][0]["rule"]), (True, "R9"))
            md = (w.root / "handover" / job_id / "HANDOVER.md").read_text()
            self.assertTrue(md.startswith("> **BLOKLANIB**"))
            self.assertIn("r09.claim_lint", md)
            # the next order carries the finding text as a previous error
            task = w.st.next_task(w.sid, job_id)
            self.assertEqual((task["state"], task["order"]["unit_id"]), ("order", "lesson:vec-search"))
            self.assertIn("forbidden claim", task["order"]["previous_errors"][0])
            author.answer(w.st, w.sid, task)
            final = w.job(job_id)
            self.assertEqual(final["status"], "ready_for_review")
            self.assertEqual([h["to"] for h in final["status_history"]][3:],
                             ["checking", "remediation", "authoring", "validating", "checking", "ready_for_review"])
            # the passing dossier replaced the BLOCKED one as the current dossier; the BLOCKED one was renamed, not lost
            self.assertFalse(read_json(w.root / "handover" / job_id / "HANDOVER.json")["blocked"])
            archived = sorted((w.root / "handover" / job_id / "archive").glob("*HANDOVER.json"))
            self.assertEqual([read_json(p)["blocked"] for p in archived], [True])
        finally:
            w.close()

    def test_remediation_rounds_are_bounded_per_unit_then_the_job_blocks(self):
        w = RulesWorld(gate=True)
        try:
            faulty = [faulty_lesson() for _ in range(4)]
            author = FakeAuthor(defects={("lesson", "vec-search"): faulty})
            job_id = w.st.init(w.sid, policy={"budgets": {"max_remediation_rounds": 1}})["job_id"]
            drive_until(w, author, job_id, "remediation")
            task = w.st.next_task(w.sid, job_id)
            author.answer(w.st, w.sid, task)                          # the author repeats the defect: round 2 > budget
            job = w.job(job_id)
            self.assertEqual(job["status"], "blocked")
            self.assertEqual((job["blocked"]["kind"], job["blocked"]["unit_id"], job["blocked"]["reason"]),
                             ("content_defect", "lesson:vec-search", "remediation_rounds_exhausted"))
            self.assertEqual(w.st.next_task(w.sid, job_id)["state"], "blocked")
        finally:
            w.close()

    def test_a_system_defect_blocks_the_job_for_a_human_and_is_never_handed_to_the_author(self):
        w = RulesWorld(gate=True)
        try:
            with patch.object(r10, "gated_sources", lambda: {"studio_bad.py": "import socket\n"}):
                job_id, last = w.build()
            job = w.job(job_id)
            self.assertEqual((last["state"], job["status"]), ("blocked", "blocked"))
            self.assertEqual(job["blocked"]["kind"], "system_defect")
            self.assertIn("human action", job["blocked"]["next_step"])
            self.assertEqual(job["blocked"]["findings"][0]["probe"], "r10.no_network_or_deletion")
            self.assertTrue(all(u["status"] == "staged" for u in job["units"].values()))        # nothing reopened
            self.assertEqual(w.st.next_task(w.sid, job_id)["state"], "blocked")
            md = (w.root / "handover" / job_id / "HANDOVER.md").read_text()
            self.assertTrue(md.startswith("> **BLOKLANIB**"))
            self.assertIn("KEÇMƏDİ", md)
            kinds = [json.loads(x)["kind"] for x in (w.root / "notifications.jsonl").read_text().splitlines()]
            self.assertIn("blocked", kinds)
            self.assertNotIn("handover_ready", kinds)
            with self.assertRaises(StudioError) as ctx:
                w.st.accept(w.sid, job_id, job["build_digest"], OWNER_TEXT)
            self.assertEqual(ctx.exception.code, "studio_not_reviewable")
        finally:
            w.close()

    def test_a_learner_gated_finding_parks_the_job_with_the_exact_next_step_then_resumes(self):
        w = RulesWorld(gate=True)
        try:
            real = rc.run_rules
            with patch.object(studio_review.rc, "run_rules", faked_failure(real, "r07.sources_validated", "source r1 is stale")):
                job_id, last = w.build()
            job = w.job(job_id)
            self.assertEqual((last["state"], job["status"]), ("needs_learner", "needs_learner"))
            self.assertEqual(job["needs_learner"]["phase"], "rules_gate")
            self.assertIn("learner must act first", job["needs_learner"]["directive"])
            self.assertEqual(job["needs_learner"]["blockers"], ["r07.sources_validated"])
            self.assertTrue(all(u["status"] == "staged" for u in job["units"].values()))
            task = w.st.next_task(w.sid, job_id)                     # real probes now: nothing fails any more -> resumes
            self.assertEqual(task["state"], "done")
            self.assertEqual(w.job(job_id)["status"], "ready_for_review")
            self.assertEqual([h["to"] for h in w.job(job_id)["status_history"]][-6:],
                             ["needs_learner", "planning", "authoring", "validating", "checking", "ready_for_review"])
        finally:
            w.close()

    def test_a_broken_suite_or_probe_error_blocks_as_a_system_defect(self):
        w = RulesWorld(gate=True)
        try:
            real = rc.run_rules

            def broken(workspace, sid, *a, **kw):
                r = real(workspace, sid, *a, **{**kw, "scope": "quick"})
                body = {k: v for k, v in r.items() if k != "signature"}
                body.update(scope="full", status="suite_broken", exit_code=3, passed=False,
                            controls={"checked": 1, "broken": [{"id": "r01.fabricated_fixtures", "control_ok": False, "detail": "passed"}]})
                return receipts.sign_receipt(body)
            with patch.object(studio_review.rc, "run_rules", broken):
                job_id, last = w.build()
            job = w.job(job_id)
            self.assertEqual((job["status"], job["blocked"]["kind"]), ("blocked", "system_defect"))
            self.assertEqual(job["blocked"]["findings"][0]["probe"], "r01.fabricated_fixtures")
        finally:
            w.close()


class OwnerRejectTests(unittest.TestCase):
    def test_reject_reopens_the_nodes_units_with_the_owner_note_as_a_fix_order(self):
        w = RulesWorld(gate=True)
        try:
            job_id, _ = w.build()
            job = w.job(job_id)
            digest = job["build_digest"]
            with self.assertRaises(StudioError) as ctx:
                w.st.reject(w.sid, job_id, "vec-search", "  ")
            self.assertEqual(ctx.exception.code, "studio_note_required")
            with self.assertRaises(StudioError) as ctx:
                w.st.reject(w.sid, job_id, "no-such-node", "x")
            self.assertEqual(ctx.exception.code, "studio_unknown_node")
            note = "Ladder izahı çox qısadır; daha aydın nümunə əlavə et."
            out = w.st.reject(w.sid, job_id, "vec-search", note)
            self.assertEqual((out["status"], out["rejected_node"], out["reopened"]), ("remediation", "vec-search",
                                                                                      ["lesson:vec-search", "items:vec-search"]))
            job = w.job(job_id)
            self.assertIsNone(job["build_digest"])
            for uid in ("lesson:vec-search", "items:vec-search"):
                u = job["units"][uid]
                self.assertEqual((u["status"], u["reason"], u["last_errors"], u["staged_digest"]),
                                 ("invalid", "owner_rejected", ["owner_note: " + note], None))
            self.assertEqual(job["units"]["lesson:vec-sim"]["status"], "staged")
            self.assertEqual(job["rejections"][0]["note"], note)
            reject = [r for r in w.records("event") if r["payload"].get("event_type") == "studio_owner_rejection"]
            self.assertEqual((reject[0]["provenance"]["source"], reject[0]["payload"]["node"], reject[0]["payload"]["note"]),
                             ("studio_owner", "vec-search", note))
            self.assertEqual(receipts.find_acceptance(w.sk.ledger(w.sid), job_id, digest), None)
            task = w.st.next_task(w.sid, job_id)
            self.assertEqual(task["order"]["unit_id"], "lesson:vec-search")
            self.assertEqual(task["order"]["previous_errors"], ["owner_note: " + note])
            FakeAuthor().run(w.st, w.sid, job_id)
            self.assertEqual(w.job(job_id)["status"], "ready_for_review")
            fresh = w.st.handover(w.sid, job_id)
            self.assertTrue(fresh["receipt_fresh"])
            with self.assertRaises(StudioError) as ctx:
                w.st.reject(w.sid, job_id, "vec-sim", "x" * 1001)
            self.assertEqual(ctx.exception.code, "studio_note_required")
        finally:
            w.close()


class DriftAfterAcceptanceTests(unittest.TestCase):
    def test_a_source_hash_change_after_acceptance_supersedes_the_job_and_installs_nothing(self):
        w = RulesWorld(gate=True)
        try:
            job_id, _ = w.build()
            digest = w.job(job_id)["build_digest"]
            w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
            self.assertEqual(w.job(job_id)["status"], "awaiting_learner_approval")
            import hashlib
            from course_fixture import SOURCE_TEXT, manifest_for
            text = SOURCE_TEXT + " Extra sentence that changes the hash."
            manifest = manifest_for(w.d / "sources")
            manifest["resources"][0]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            manifest["manifest_version"] = "sm-fixture-2"
            staging_dir = w.d / "sources-staging" / "stg_changed"
            staging_dir.mkdir(parents=True)
            (staging_dir / "src-fixture-001.txt").write_text(text, encoding="utf-8")
            w.ck.record_sources(w.sid, manifest, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}],
                                staged_cache=staging_dir)
            with self.assertRaises(StudioError) as ctx:
                w.st.promote(w.sid, job_id)
            self.assertEqual(ctx.exception.code, "studio_job_superseded")
            self.assertEqual(w.job(job_id)["status"], "superseded")
            self.assertIn("sources", w.job(job_id)["drift"])
            state = read_json(w.d / "course-state.json")
            self.assertEqual((state["content_versions"], state["studio"]["promoted_builds"]), ({}, []))
            self.assertIsNone(w.ck.turn(w.sid).get("studio_promotion"))
        finally:
            w.close()


class PromotionConditionsTests(unittest.TestCase):
    """CourseKernel.promote_build and StudioKernel.promote refuse every unmet condition and leave the course unchanged."""

    def test_conditions(self):
        w = RulesWorld(gate=True)
        try:
            self.run_conditions(w)
        finally:
            w.close()

    def bundle(self, w, job_id, **kw):
        job = w.job(job_id)
        payloads = {uid: staging.read_staged(w.root, job_id, uid, job["units"][uid]["staged_digest"]) for uid in job["unit_order"]}
        return receipts.bundle_from_job(job, payloads, **kw)

    def refused(self, fn):
        with self.assertRaises(CourseStateError) as ctx:
            fn()
        return ctx.exception

    def resigned(self, bundle, **change):
        b = copy.deepcopy(bundle)
        b.update(change)
        from runtime.studio_jobs import build_digest_of
        b["build_digest"] = build_digest_of({u["unit_id"]: digest_of(u["payload"]) for u in b["units"]}, b["roadmap_version"],
                                            b["sources"], b["policy_versions"])
        return b

    def course_bytes(self, w):
        """Everything the course owns (state, lessons, vault, workspace, sources); the ledger legitimately grows."""
        return tree_hash(w.d, skip=("studio", ".session.lock", "ledger.jsonl", "ledger-integrity.json", "learner-evidence.json"))

    def run_conditions(self, w):
        job_id, _ = w.build()
        job = w.job(job_id)
        digest = job["build_digest"]
        full = receipts.latest_receipt(w.root, job_id, scope="full", build_digest=digest)
        bundle = self.bundle(w, job_id, receipt=full)
        before = self.course_bytes(w)
        # 1. the roadmap is not approved: the course refuses (R6 approval gate), nothing is installed
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, bundle)).code, "roadmap_not_approved")
        with self.assertRaises(StudioError) as ctx:
            w.st.promote(w.sid, job_id)
        self.assertEqual(ctx.exception.code, "studio_not_promotable")                 # still ready_for_review
        w.approve()
        before = self.course_bytes(w)
        # 2. approved, but no owner acceptance is bound to this digest
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, bundle)).code, "promotion_requires_owner_acceptance")
        # 3. the roadmap version of the build is not the approved one
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, self.resigned(bundle, roadmap_version="rm-0000000000000000"),
                                                                   rehearsal=True)).code, "roadmap_version_mismatch")
        # 4. a changed staged byte (the bundle no longer hashes to the digest) / a changed source hash / a bumped capability
        tampered = copy.deepcopy(bundle)
        tampered["units"][0]["payload"]["objective"] += " x"
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, tampered, rehearsal=True)).code, "promotion_digest_mismatch")
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, self.resigned(bundle, sources={"r1": "0" * 64}),
                                                                   rehearsal=True)).code, "promotion_source_changed")
        bumped = {k: v + "-x" for k, v in bundle["capabilities"].items()}
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, {**bundle, "capabilities": bumped},
                                                                   rehearsal=True)).code, "promotion_capability_changed")
        # 5. atomic per node: vec-sim is valid, vec-search is not -> nothing of either node is installed
        bad = copy.deepcopy(bundle)
        lesson = next(u for u in bad["units"] if u["unit_id"] == "lesson:vec-search")
        lesson["payload"]["explanation"]["refs"][0]["quote"] = "an invented sentence that is not in the source text at all"
        exc = self.refused(lambda: w.ck.promote_build(w.sid, self.resigned(bad), rehearsal=True))
        self.assertEqual(exc.code, "promotion_revalidation_failed")
        self.assertIn("vec-search", exc.detail)
        self.assertEqual(self.course_bytes(w), before)                                  # the course is unchanged
        self.assertFalse((w.d / "assessment-vault").exists() and any((w.d / "assessment-vault").rglob("*.json")))
        # 6. an owner acceptance for ANOTHER digest does not authorise this build
        w.sk.ledger(w.sid).append("event", {"event_type": "studio_owner_acceptance", "job_id": job_id, "build_digest": "f" * 64,
                                            "statement": "x", "channel": "owner_cli", "receipt_id": "rcp_x"},
                                  {"source": "studio_owner"})
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, bundle)).code, "promotion_acceptance_digest_mismatch")
        # 7. a stale receipt is refused at accept (and, below, at promotion)
        with self.assertRaises(StudioError) as ctx:
            w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
        self.assertEqual(ctx.exception.code, "studio_receipt_stale")                    # approval moved the ledger
        # 9. refresh, accept (the roadmap is approved: promotion is attempted at once)
        fresh = w.st.handover(w.sid, job_id)
        self.assertTrue(fresh["receipt_fresh"])
        new_full = receipts.latest_receipt(w.root, job_id, scope="full", build_digest=digest)
        self.assertNotEqual(new_full["receipt_id"], full["receipt_id"])
        # ... a staged byte change after the owner accepted: promotion is refused, acceptance stays recorded
        staged_file = staging.staged_path(w.root, job_id, "lesson:vec-sim", job["units"]["lesson:vec-sim"]["staged_digest"])
        with restored(staged_file):
            staged_file.write_text(staged_file.read_text().replace("objective", "objectiv3", 1), encoding="utf-8")
            out = w.st.accept(w.sid, job_id, digest, OWNER_TEXT)
            self.assertEqual((out["status"], out["promotion"]["promoted"], out["promotion"]["refused"]),
                             ("accepted", False, "studio_staged_tampered"))
        self.assertEqual(self.course_bytes(w), before)
        # 8. a receipt for build A is rejected for build B (the acceptance exists now, so the receipt is what is checked)
        current = receipts.latest_receipt(w.root, job_id, scope="full", build_digest=digest)
        forged = receipts.sign_receipt({**{k: v for k, v in current.items() if k != "signature"}, "build_digest": "e" * 64})
        exc = self.refused(lambda: w.ck.promote_build(w.sid, self.bundle(w, job_id, receipt=forged)))
        self.assertEqual(exc.code, "promotion_requires_fresh_receipt")
        self.assertIn("receipt_bound_to_another_build", exc.detail)
        unsigned = {k: v for k, v in current.items() if k != "signature"}
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, self.bundle(w, job_id, receipt=unsigned))).code,
                         "promotion_requires_fresh_receipt")
        # 10. a stale receipt is refused at promotion (refresh off) ...
        w.sk.ledger(w.sid).append("observation", {"capability_id": "vec-sim", "note": "learner activity"},
                                  {"source": "course_practice"})
        with self.assertRaises(StudioError) as ctx:
            w.st.promote(w.sid, job_id, refresh=False)
        self.assertEqual(ctx.exception.code, "studio_receipt_stale")
        stale_bundle = self.bundle(w, job_id, receipt=receipts.latest_receipt(w.root, job_id, scope="full", build_digest=digest))
        exc = self.refused(lambda: w.ck.promote_build(w.sid, stale_bundle))
        self.assertIn("receipt_stale", exc.detail)
        evidence_before = w.records("evidence") + w.records("observation")
        # 11. with every condition met (promotion through the CLI recomputes the stale receipt) every node is installed
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = alearn.main(["--workspace", str(w.ws), "course", "promote-staged", "--session", w.sid, "--job", job_id])
        out = json.loads(buf.getvalue())
        self.assertEqual((code, out["promotion"]["promoted"], out["status"]), (0, True, "promoted"))
        state = read_json(w.d / "course-state.json")
        self.assertEqual(set(state["content_versions"]), {"vec-sim", "vec-search"})
        self.assertEqual(sorted(state["item_versions"]), ["vec-search", "vec-sim"])
        self.assertEqual(out["promotion"]["evidence_records_added"], 0)
        self.assertEqual(w.records("evidence") + w.records("observation"), evidence_before)
        self.assertTrue(state["promotion_history"][0]["receipt_id"].startswith("rcp_"))
        # idempotent: a second promotion of the same verified build installs nothing again
        again = w.st.status(w.sid, job_id)
        self.assertEqual(again["status"], "promoted")
        self.assertEqual(self.refused(lambda: w.ck.promote_build(w.sid, bundle)).code, "promotion_requires_fresh_receipt")


if __name__ == "__main__":
    unittest.main()

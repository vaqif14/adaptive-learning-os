"""Stage F fixes (course-studio.md section 14, 'Stage F'): regressions for the integration and red-team findings.

Synthetic fixtures only (RulesWorld / StudioCase); no real claude or codex binary is ever started.
"""
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

import runtime.course_content as cc  # noqa: E402
from runtime import backup  # noqa: E402
from runtime import studio_receipt as receipts  # noqa: E402
from runtime import studio_worker as sw  # noqa: E402
from runtime.rules_probes import r09, r10  # noqa: E402
from runtime.studio import StudioKernel  # noqa: E402
from runtime.studio_store import StudioError  # noqa: E402
from runtime.verifiers import python_runner as pyr  # noqa: E402
from course_fixture import ITEMS, LESSON, PRACTICE_CORRECT, EXAM_CORRECT, IsolatedState, manifest_for, roadmap  # noqa: E402
from course_fixture import lesson as legacy_lesson  # noqa: E402
from rules_fixture import RulesWorld  # noqa: E402
from studio_fixture import StudioCase, sim_items, sim_lesson  # noqa: E402
from test_v700_course_flow import CourseCase  # noqa: E402
from test_v801_practice_first import leaky_walkthrough  # noqa: E402

# A rewrite of the practice reference (dot/norm): other names, loops instead of generator expressions.
RESTRUCTURED = ("import math\n\ndef scalar(p, q):\n    total = 0\n    for i in range(len(p)):\n        total += p[i] * q[i]\n"
                "    return total\n\ndef length(p):\n    acc = 0\n    for v in p:\n        acc += v * v\n    return math.sqrt(acc)\n")


def interval(clock, seq=1):
    now = clock()
    return {"interval_id": "iv_00000000000000bb", "kind": "practice", "node_id": "vec-sim",
            "started_at": (now.replace(microsecond=0) - __import__("datetime").timedelta(minutes=5)).isoformat(),
            "last_at": now.isoformat(), "active_seconds": 60, "idle_seconds": 0, "paused_seconds": 0, "seq": seq, "ended": False}


class WorkerGateTests(unittest.TestCase):
    """The background worker runs the full gate itself: it must not start a second thread around it."""

    def setUp(self):
        self.w = RulesWorld()
        self.addCleanup(self.w.close)
        self.job_id, _last = self.w.build()
        self.assertEqual(self.w.job(self.job_id)["status"], "checking")
        self.gated = StudioKernel(self.w.ws, clock=self.w.clock, gate=True)

    def worker(self):
        return sw.Worker(self.w.ws, self.w.sid, kernel=self.gated)

    def test_the_worker_runs_the_real_gate_to_ready_for_review_without_a_second_thread(self):
        worker = self.worker()
        out = worker.tick()
        self.assertEqual((out["action"], out["job_status"]), ("gate_run", "ready_for_review"), out)
        self.assertIsNone(self.gated.gate_progress)             # the beat callback is detached again
        self.assertEqual(self.w.job(self.job_id)["status"], "ready_for_review")
        out = worker.tick()                                     # handover refresh: a stored receipt exists, so it only finishes
        self.assertIn(out["action"], {"handover", "build_ready"})

    def test_the_gate_calls_the_heartbeat_between_probes(self):
        beats = []
        worker = self.worker()
        original = worker._safe_beat
        worker._safe_beat = lambda: (beats.append(1), original())[1]
        worker.tick()
        self.assertGreater(len(beats), 5)

    def test_the_worker_waits_while_the_learner_has_an_exam_open_and_blocks_nothing(self):
        self.w.approve()
        self.w.ck.install_items(self.w.sid, "vec-sim", ITEMS)
        self.w.ck.install_lesson(self.w.sid, "vec-sim", LESSON)
        self.w.ck.start_mission(self.w.sid, "vec-sim")
        attempt = self.w.ck.start_exam(self.w.sid, "vec-sim", fast_track=True)
        self.assertTrue(self.w.ck.studio_snapshot(self.w.sid)["exam_locked"])
        out = self.worker().tick()
        self.assertEqual((out["state"], out["action"]), ("idle", "exam_active"), out)
        self.assertEqual(self.w.job(self.job_id)["status"], "checking")       # parked, never blocked
        with self.assertRaises(StudioError) as caught:
            self.gated.finish_checking(self.w.sid, self.job_id)
        self.assertEqual(caught.exception.code, "exam_active")
        self.assertEqual(self.w.job(self.job_id)["status"], "checking")
        self.assertNotEqual(attempt["attempt_id"], "")


class ExamLockedPromotionTests(unittest.TestCase):
    def test_promote_and_handover_refresh_during_an_exam_defer_instead_of_blocking(self):
        w = RulesWorld(gate=True)
        self.addCleanup(w.close)
        job_id, _ = w.build()
        digest = w.job(job_id)["build_digest"]
        self.assertEqual(w.job(job_id)["status"], "ready_for_review")
        w.st.accept(w.sid, job_id, digest, "Dossieri yoxladım və qəbul edirəm.", promote=False)
        self.assertEqual(w.job(job_id)["status"], "accepted")
        w.approve()
        w.ck.install_items(w.sid, "vec-sim", ITEMS)
        w.ck.install_lesson(w.sid, "vec-sim", LESSON)
        w.ck.start_mission(w.sid, "vec-sim")
        w.ck.start_exam(w.sid, "vec-sim", fast_track=True)
        out = w.st.promote(w.sid, job_id)["promotion"]
        self.assertEqual((out["promoted"], out["state"]), (False, "deferred_exam_active"), out)
        self.assertEqual(w.job(job_id)["status"], "accepted")      # not blocked, acceptance intact


class ReceiptFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.w = RulesWorld(gate=True)
        self.addCleanup(self.w.close)
        self.job_id, _ = self.w.build()
        self.digest = self.w.job(self.job_id)["build_digest"]

    def test_engagement_heartbeats_alone_keep_the_owner_ui_accept_possible(self):
        self.w.ck.record_activity(self.w.sid, interval(self.w.clock))
        view = self.w.st.handover(self.w.sid, self.job_id, refresh=False)
        self.assertTrue(view["receipt_fresh"], view["stale_reason"])
        out = self.w.st.accept(self.w.sid, self.job_id, self.digest, "Dossieri yoxladım.", channel="owner_ui", promote=False)
        self.assertTrue(out["accepted"])

    def test_a_roadmap_approval_stales_the_receipt_and_the_handover_refresh_fixes_it(self):
        self.w.approve()                                          # evidence-relevant: the ledger moved after the receipt
        with self.assertRaises(StudioError) as caught:
            self.w.st.accept(self.w.sid, self.job_id, self.digest, "Dossieri yoxladım.", channel="owner_ui", promote=False)
        self.assertEqual(caught.exception.code, "studio_receipt_stale")
        self.assertTrue(self.w.st.handover(self.w.sid, self.job_id)["receipt_fresh"])
        out = self.w.st.accept(self.w.sid, self.job_id, self.digest, "Dossieri yoxladım.", channel="owner_ui", promote=False)
        self.assertTrue(out["accepted"])

    def test_accept_without_an_approved_roadmap_reports_awaiting_learner_approval(self):
        out = self.w.st.accept(self.w.sid, self.job_id, self.digest, "Dossieri yoxladım.", channel="owner_ui", promote=False)
        self.assertEqual(out["promotion"]["state"], "awaiting_learner_approval")
        self.assertEqual(self.w.job(self.job_id)["status"], "accepted")          # the report does not move the job


class FrontendStatusTests(StudioCase):
    def test_an_old_session_frontend_is_reported_as_outdated_after_promotion(self):
        src = self.d / "workspace" / "frontend" / "src"
        template = ROOT / "skills/adaptive-learn/templates/react/src"
        for f in template.rglob("*"):
            if f.is_file():
                (src / f.relative_to(template)).parent.mkdir(parents=True, exist_ok=True)
                (src / f.relative_to(template)).write_bytes(f.read_bytes())
        self.assertFalse(self.ck.frontend_status(self.sid)["outdated"])
        (src / "components" / "Lesson.jsx").write_text("// old bundle\n", encoding="utf-8")
        status = self.ck.frontend_status(self.sid)
        self.assertTrue(status["outdated"])
        self.assertEqual(status["files"], ["components/Lesson.jsx"])
        self.assertIn("regenerate-ui", status["next_step"])


class RestoreModesTests(unittest.TestCase):
    def test_a_restored_session_keeps_every_audited_directory_private(self):
        w = RulesWorld()
        self.addCleanup(w.close)
        w.build()
        out = Path(tempfile.mkdtemp(prefix="alearn-restore-out-"))
        archive = out / "session.zip"
        old = os.umask(0o022)
        try:
            backup.backup_session(w.sk, w.sid, archive)
            target = Path(tempfile.mkdtemp(prefix="alearn-restore-ws-"))
            from runtime.session import SessionKernel
            restored = SessionKernel(target)
            backup.restore_session(restored, archive)
        finally:
            os.umask(old)
        self.assertEqual(r10.mode_problems(restored.dir(w.sid)), [])


class AttemptModesTests(CourseCase):
    def test_exam_submission_files_are_private_under_a_permissive_umask(self):
        self.approve()
        self.author()
        old = os.umask(0o022)
        try:
            exam = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
            Path(exam["file"]).write_text(EXAM_CORRECT, encoding="utf-8")
            self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        finally:
            os.umask(old)
        self.assertEqual(r10.mode_problems(self.d), [])
        attempts = self.d / "course" / "attempts"
        self.assertEqual(stat.S_IMODE(attempts.stat().st_mode), 0o700)


class PracticeAttemptTests(CourseCase):
    def test_a_new_attempt_after_a_reveal_gets_a_fresh_hint_counter(self):
        self.approve()
        lesson = copy.deepcopy(sim_lesson())
        self.k.install_items(self.sid, "vec-sim", sim_items())
        self.k.install_lesson(self.sid, "vec-sim", lesson)
        self.k.start_mission(self.sid, "vec-sim")
        self.k.begin_practice(self.sid, "vec-sim")
        ceiling = lesson["hint_ceiling"]
        for _ in range(ceiling):
            self.k.help(self.sid, "vec-sim", "hint")
        with self.assertRaises(Exception) as caught:
            self.k.help(self.sid, "vec-sim", "hint")
        self.assertEqual(getattr(caught.exception, "code", None), "hint_ceiling_reached")
        self.k.practice_reveal(self.sid, "vec-sim", confirm=True)
        again = self.k.begin_practice(self.sid, "vec-sim")
        self.assertIn("practice_attempt_id", again["practice"])
        self.assertEqual(self.k.help(self.sid, "vec-sim", "hint")["kind"], "hint")      # no longer shut out


class SandboxPolicyTests(unittest.TestCase):
    """The AST policy is a pre-filter, not a boundary; these pin the introspection routes it must refuse."""

    def policy(self, code):
        return pyr.PythonVerifier().syntax_and_policy(code)[2]

    def test_frame_walk_attributes_are_refused(self):
        for attr in ("gi_frame", "f_back", "f_globals", "f_builtins", "f_locals", "tb_frame", "cr_frame", "ag_frame"):
            self.assertIn(f"attribute not allowed: {attr}", self.policy(f"x = obj.{attr}\n"), attr)

    def test_format_templates_may_not_walk_attributes(self):
        self.assertTrue(self.policy("s = '{0.real}'.format(1)\n"))
        self.assertTrue(self.policy("t = '{}'\ns = t.format(1)\n"))
        self.assertTrue(self.policy("s = '{x}'.format_map({'x': 1})\n"))
        self.assertTrue(self.policy("f = '{0}'.format\n"))
        self.assertEqual(self.policy("s = '{} and {name:>5}'.format(1, name='a')\n"), [])
        self.assertEqual(self.policy("x = 1\ns = f'{x:>3} {x.real}'\n"), [])

    def test_imported_module_internals_are_refused(self):
        self.assertTrue(self.policy("import re\nm = re.enum.sys\n"))
        self.assertTrue(self.policy("import collections\nm = collections._sys\n"))
        self.assertTrue(self.policy("from re import enum\n"))
        self.assertEqual(self.policy("import collections\nprint(collections.abc.Iterable, collections.OrderedDict)\n"), [])

    def test_ordinary_learner_code_still_runs(self):
        out = pyr.PythonVerifier(timeout_sec=30.0).verify(
            "import math, re, json, collections, itertools, functools, statistics, copy\n"
            "print(math.sqrt(16), re.sub('a', 'b', 'aa'), json.dumps([1]), '{} {:>3}'.format(1, 2))\n",
            expected_stdout="4.0 bb [1] 1   2")
        self.assertTrue(out.passed, out.stderr)

    def run_harness(self, code):
        """The harness alone (no AST pre-filter): what the audit hook still refuses if a walk to a real module is found."""
        with tempfile.TemporaryDirectory(prefix="alearn-hook-") as td:
            tdp = Path(td)
            (tdp / "h.py").write_text(pyr._HARNESS, encoding="utf-8")
            (tdp / "c.json").write_text(json.dumps({"code": code, "allowed_imports": ["re", "json"]}), encoding="utf-8")
            subprocess.run([sys.executable, "-I", "-S", str(tdp / "h.py"), str(tdp / "c.json"), str(tdp / "r.json")],
                           timeout=60, env={"PATH": os.environ.get("PATH", "")}, capture_output=True, check=False)
            return json.loads((tdp / "r.json").read_text(encoding="utf-8"))

    def test_the_audit_hook_refuses_reads_writes_and_listings_outside_the_stdlib(self):
        with tempfile.TemporaryDirectory(prefix="alearn-canary-") as td:
            canary = Path(td) / "canary.txt"
            canary.write_text("TOP-SECRET-CANARY", encoding="utf-8")
            walk = "import re\nio = re.enum.sys.modules['io']\n"
            read = self.run_harness(walk + f"print(io.open({str(canary)!r}).read())")
            self.assertIn("blocked in the sandbox: open", read["error"])
            self.assertNotIn("TOP-SECRET-CANARY", read["stdout"])
            write = self.run_harness(walk + f"io.open({str(Path(td) / 'new.txt')!r}, 'w')")
            self.assertIn("blocked in the sandbox", write["error"])
            self.assertFalse((Path(td) / "new.txt").exists())
            listing = self.run_harness("import re\nre.enum.sys.modules['os'].listdir(%r)" % td)
            self.assertIn("blocked in the sandbox: os.listdir", listing["error"])
            control = self.run_harness("import re\nprint(re.sub('a', 'b', 'a'))")          # negative control: the hook is not a blanket refusal
            self.assertIsNone(control["error"])
            self.assertEqual(control["stdout"].strip(), "b")


class BehaviouralLeakTests(StudioCase):
    """A renamed, loop-restructured copy of a solution defeats the lexical guard; the behavioural probe must not."""

    def lesson_check(self, mutate, **kw):
        lesson = sim_lesson()
        mutate(lesson)
        return self.ck.validate_lesson_dry(self.sid, "vec-sim", lesson, **kw)

    def test_a_restructured_copy_of_the_practice_in_a_walkthrough_is_rejected(self):
        wt = leaky_walkthrough(RESTRUCTURED, ("print(scalar([1, 2], [3, 4]))", "11"), ("print(length([3, 4]))", "11\n5.0"))
        out = self.lesson_check(lambda l: l.update(walkthrough=wt))
        self.assertFalse(out["valid"], out)
        self.assertTrue(any("behaves like" in e and "practice" in e for e in out["errors"]), out["errors"])

    def test_a_restructured_copy_in_a_hint_or_the_prompt_is_rejected(self):
        def hint(lesson):
            lesson["practice"]["hints"] = ["dot üçün zip(a, b) istifadə et.", "```python\n" + RESTRUCTURED + "```"]
            lesson["hint_ceiling"] = 1
        out = self.lesson_check(hint)
        self.assertFalse(out["valid"], out)
        self.assertTrue(any("lesson text carries code" in e for e in out["errors"]), out["errors"])

        def prompt(lesson):
            lesson["practice"]["prompt_md"] += "\n\n```python\n" + RESTRUCTURED + "```\n"
        out = self.lesson_check(prompt)
        self.assertFalse(out["valid"], out)

    def test_a_restructured_copy_in_the_worked_example_or_explanation_is_rejected(self):
        def worked(lesson):
            lesson["worked_example"]["steps"] = [*lesson["worked_example"]["steps"], RESTRUCTURED]
        self.assertFalse(self.lesson_check(worked)["valid"])

        def body(lesson):
            lesson["explanation"]["body"] += "\n\n```python\n" + RESTRUCTURED + "```\n"
        self.assertFalse(self.lesson_check(body)["valid"])

    def test_an_exam_item_that_is_the_practice_in_disguise_is_rejected(self):
        items = sim_items()
        disguised = copy.deepcopy(items["items"][0])
        disguised.update(item_id="scalar-x", scope="near_transfer", title="Skalyar hasil",
                         prompt_md="Yeni tapşırıq: `scalar(p, q)` funksiyası iki siyahının uyğun elementlərinin hasilini toplasın. "
                                   "Uzunluq üçün `length(p)` də yazılsın.",
                         starter_code="import math\n\ndef scalar(p, q):\n    pass\n\ndef length(p):\n    pass\n",
                         checks=[{"id": "s1", "name": "Skalyar", "category": "core", "code": "assert scalar([1,2],[3,4]) == 11",
                                  "fail_feedback": "Hasilləri topla."},
                                 {"id": "s2", "name": "Uzunluq", "category": "core", "code": "assert abs(length([3,4]) - 5) < 1e-9",
                                  "fail_feedback": "Kök al."},
                                 {"id": "s3", "name": "Edge", "category": "edge", "code": "assert scalar([], []) == 0",
                                  "fail_feedback": "Boş siyahı 0."},
                                 {"id": "s4", "name": "Transfer", "category": "transfer",
                                  "code": "assert scalar([2,0],[0,5]) == 0 and abs(length([6,8]) - 10) < 1e-9",
                                  "fail_feedback": "Ortogonal."}],
                         misconceptions=[{"id": "s-add", "name": "Toplama", "detector": "assert scalar([1,2],[3,4]) == 10",
                                          "feedback": "Hasil əvəzinə toplama aparılıb; elementləri vurmaq lazımdır."}],
                         reference_solution=RESTRUCTURED, wrong_examples={})
        items["items"] = [disguised]
        out = self.ck.validate_items_dry(self.sid, "vec-sim", items, staged_lesson=sim_lesson())
        self.assertFalse(out["valid"], out)
        self.assertTrue(any("behaves like the practice" in e for e in out["errors"]), out["errors"])

    def test_the_fixture_items_and_lessons_are_not_false_positives(self):
        self.assertTrue(self.lesson_check(lambda l: None, staged_items=sim_items())["valid"])
        out = self.ck.validate_items_dry(self.sid, "vec-sim", sim_items(), staged_lesson=sim_lesson())
        self.assertTrue(out["valid"], out["errors"])


class ClaimLintTests(unittest.TestCase):
    CLAIMS = ["Guaranteeing success for every learner", "This course will make you an expert in a week", "Mastery is assured",
              "You'll master it", "Research shows this approach doubles retention",
              "Everyone who finishes this lesson passes the exam", "It is 100% certain you pass", "Success rate: 97 percent",
              "Bu kurs sizi ekspert edəcək", "Nəticə tam təminatlıdır", "Hər kəs mütləq keçəcək", "mövzunu tam mənimsəyib",
              "mastery qazanacaqsınız", "sən ustalaşacaqsan"]

    def test_phrasings_that_used_to_slip_through_are_caught(self):
        for text in self.CLAIMS:
            self.assertTrue(r09.lint_text(text), text)

    def test_zero_width_and_homoglyph_spellings_are_caught(self):
        self.assertTrue(r09.lint_text("guar\u200bantee"))
        self.assertTrue(r09.lint_text("gu\u0430rantee"))                  # Cyrillic a
        self.assertTrue(r09.lint_text("\uff27uarantee"))                  # fullwidth G (NFKC)

    def test_negated_and_honest_wording_stays_clean(self):
        for text in ("Bu zəmanət deyil.", "Results are not guaranteed.", "Ustalıq hələ təsdiqlənməyib.",
                     "Bu yığma keyfiyyət sübutu deyil: qaydalar yalnız yoxlanılan şərtləri bildirir."):
            self.assertEqual(r09.lint_text(text), [], text)


class OwnerTextTests(unittest.TestCase):
    def test_a_secret_or_home_path_in_an_owner_statement_is_refused_before_the_ledger(self):
        w = RulesWorld(gate=True)
        self.addCleanup(w.close)
        job_id, _ = w.build()
        digest = w.job(job_id)["build_digest"]
        for bad in ("Qəbul. sk-" + "a" * 30, "Qəbul. AKIA" + "A" * 16, "Qəbul. /Users/someone/secret/"):
            with self.assertRaises(StudioError) as caught:
                w.st.accept(w.sid, job_id, digest, bad)
            self.assertEqual(caught.exception.code, "studio_statement_sensitive")
        self.assertEqual(w.job(job_id)["status"], "ready_for_review")
        self.assertNotIn("sk-" + "a" * 30, (w.d / "ledger.jsonl").read_text(encoding="utf-8"))
        with self.assertRaises(StudioError) as caught:
            w.st.reject(w.sid, job_id, "vec-sim", "Bu qeyd açar saxlayır: sk-" + "b" * 30)
        self.assertEqual(caught.exception.code, "studio_statement_sensitive")


if __name__ == "__main__":
    unittest.main()

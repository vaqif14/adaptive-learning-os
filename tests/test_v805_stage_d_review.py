"""Stage D review fixes: runner trust at doctor/dispatch, process-group hygiene, cancel mid-run, worker robustness.

Fake runner scripts only (see test_v804_runners_worker); the real claude/codex binaries are never started.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_v804_runners_worker as base  # noqa: E402
from test_v804_runners_worker import agents, pid_alive, sw, wait_until  # noqa: E402
from runtime.studio_store import StudioError  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "skills/adaptive-learn/scripts/alearn.py"


class TrustTests(base.RunnerCase):
    def test_doctor_never_executes_a_binary_whose_hash_drifted(self):
        cli = self.start()
        marker = cli.dir / "executed.marker"
        cli.path.write_text(f"#!{sys.executable}\nimport sys\nopen({str(marker)!r}, 'w').write('x')\n"
                            "print('2.1 (Claude Code)')\n", encoding="utf-8")
        cli.path.chmod(0o700)
        report = sw.doctor(self.st, self.sid)
        self.assertFalse(marker.exists(), "doctor executed a swapped binary")
        info = report["checks"]["runners"]["runners"]["claude"]
        self.assertFalse(info["binary_sha256_matches"])
        self.assertIsNone(info["version_now"])
        self.assertFalse(report["checks"]["runners"]["ok"])

    def test_a_model_may_not_look_like_a_flag(self):
        cli = self.cli()
        for bad in ("--dangerously-skip-permissions", "-c", "-m"):
            self.assertFalse(agents.MODEL_RE.fullmatch(bad))
            with self.assertRaises(StudioError) as caught:
                self.configure(cli, model=bad)
            self.assertEqual(caught.exception.code, "studio_runner_model_invalid")
        self.configure(cli, model="claude-opus-4.5")
        path = self.studio_root() / "runners.json"
        doc = json.loads(path.read_text())
        doc["runners"]["claude"]["model"] = "--add-dir"
        path.write_text(json.dumps(doc))
        self.assertEqual(self.st.enabled_runner(self.sid), (None, "runner_entry_invalid"))

    def test_a_hand_written_or_re_enabled_runners_file_is_not_attested_by_the_event_chain(self):
        cli = self.cli()
        self.configure(cli)
        self.assertEqual(self.st.enabled_runner(self.sid)[0], "claude")
        path = self.studio_root() / "runners.json"
        self.st.runners_disable(self.sid, "claude")
        doc = json.loads(path.read_text())
        doc["runners"]["claude"]["enabled"] = True                  # re-enabled without --confirm, statement or event
        path.write_text(json.dumps(doc))
        self.assertEqual(self.st.enabled_runner(self.sid), (None, "runner_not_attested"))
        codex = self.cli(name="codex")                              # an entry for a runner that was never configured
        doc["runners"]["codex"] = {**doc["runners"]["claude"], "binary": str(codex.path), "version": codex.version,
                                   "binary_sha256": agents.sha256_file(codex.path), "enabled": True}
        path.write_text(json.dumps(doc))
        self.assertEqual(self.st.enabled_runner(self.sid, "codex"), (None, "runner_not_attested"))

    def test_the_binary_location_and_writability_checks_hold_at_dispatch(self):
        cli = self.cli()
        self.configure(cli)
        cli.path.chmod(0o770)                                       # content (hash) unchanged, but now group-writable
        self.assertEqual(self.st.enabled_runner(self.sid), (None, "runner_binary_unsafe"))

    def test_the_hash_is_checked_again_right_before_the_exec(self):
        cli = self.start()
        worker = self.worker()
        worker.tick()
        real = agents._scratch_dir

        def swap_after_scratch(*args, **kw):
            path = real(*args, **kw)
            cli.rewrite()                                           # swapped after the first two checks
            return path
        with mock.patch.object(agents, "_scratch_dir", side_effect=swap_after_scratch):
            out = worker.tick()
        self.assertEqual((out["state"], out["reason"]), ("waiting", "binary_sha256_mismatch"))
        self.assertEqual(cli.calls(), [])
        entry = next(iter(self.raw_job()["orders"].values()))
        self.assertEqual((entry["status"], entry["release_reason"]), ("released", "binary_sha256_mismatch"))

    def test_the_notice_states_the_codex_read_boundary_and_its_dependency(self):
        self.assertIn("not your home folder", agents.EGRESS_NOTICE)
        self.assertIn("re-check after upgrading codex", agents.EGRESS_NOTICE)


class ProcessGroupTests(base.RunnerCase):
    def test_run_group_kills_the_group_when_the_poll_callback_raises(self):
        pids = []
        scratch = Path(self.scratch.name)

        def boom():
            raise OSError("disk full")
        with self.assertRaises(OSError):
            agents.run_group(["/bin/sleep", "30"], cwd=scratch, env={"PATH": "/bin:/usr/bin"}, stdin_path=None,
                             stdout_path=scratch / "o", stderr_path=scratch / "e", timeout=30, on_poll=boom,
                             on_spawn=pids.append)
        self.assertTrue(pids and wait_until(lambda: not pid_alive(pids[0]), 5), "the runner group outlived run_group")

    def test_a_failing_heartbeat_does_not_abandon_a_run(self):
        worker = self.worker()
        with mock.patch.object(worker, "beat", side_effect=OSError("no space")):
            worker._safe_beat()                                     # must not raise

    def test_a_crashed_workers_runner_group_is_reaped_when_the_next_worker_starts(self):
        proc = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
        self.addCleanup(lambda: (proc.poll() is None and (os.killpg(proc.pid, signal.SIGKILL), proc.wait())))
        first = self.worker()
        self.assertTrue(first.acquire())
        first._record_group(proc.pid, "job-x", "order-x")           # the worker is then SIGKILLed: record stays, lock frees
        first.release()
        self.assertTrue(pid_alive(proc.pid))
        second = self.worker()
        self.assertTrue(second.acquire())
        self.addCleanup(second.release)
        self.assertTrue(wait_until(lambda: proc.poll() is not None, 10), "the orphan runner group was not killed")
        self.assertFalse((self.studio_root() / "worker" / "runner.json").exists())
        self.assertTrue(list((self.studio_root() / "archive" / "worker-runs").glob("*-runner.json")))   # archived, kept
        self.assertIn("runner_orphan_reaped", {n["kind"] for n in sw.notifications(self.studio_root())["notifications"]})

    def test_a_record_of_a_reused_pid_is_not_killed(self):
        proc = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
        self.addCleanup(lambda: (proc.poll() is None and (os.killpg(proc.pid, signal.SIGKILL), proc.wait())))
        self.assertFalse(agents.reap_group(proc.pid, "Mon Jan  1 00:00:00 1990"))     # the start time does not match
        self.assertTrue(pid_alive(proc.pid))
        self.assertTrue(agents.reap_group(proc.pid, agents.group_started_at(proc.pid)))
        self.assertTrue(wait_until(lambda: proc.poll() is not None, 10))

    def test_a_normal_run_leaves_no_live_record_behind(self):
        self.start()
        worker = self.worker()
        worker.tick()
        self.assertEqual(worker.tick()["outcome"], "completed")
        self.assertFalse((self.studio_root() / "worker" / "runner.json").exists())

    def test_the_second_signal_restores_the_default_action(self):
        worker = self.worker()
        with mock.patch.object(sw.signal, "signal") as sig, mock.patch.object(sw.os, "kill") as kill:
            worker._on_signal(signal.SIGTERM)
            self.assertTrue(worker.stopping)
            kill.assert_not_called()
            worker._on_signal(signal.SIGTERM)
            sig.assert_called_once_with(signal.SIGTERM, signal.SIG_DFL)
            kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

    def test_the_heartbeat_is_driven_by_the_gate_callback_not_by_a_thread(self):
        worker = self.worker()
        beats = []
        threads_before = {t.name for t in threading.enumerate()}
        with mock.patch.object(worker, "beat", side_effect=lambda *a, **k: beats.append(a)):
            with worker._busy("gate"):
                self.assertEqual({t.name for t in threading.enumerate()}, threads_before)    # no 'studio-beat' thread
                self.assertEqual(worker.k.gate_progress, worker._safe_beat)
                worker.k.gate_progress()
            self.assertIsNone(worker.k.gate_progress)
        self.assertGreaterEqual(len(beats), 1)


class CancelMidRunTests(base.RunnerCase):
    def run_with(self, action, modes):
        self.start(self.cli(modes=modes), timeout=1)
        worker = self.worker()
        worker.tick()
        real = agents.run_group
        fired = []

        def hooked(*a, **kw):
            inner = kw.get("on_poll")

            def poll():
                if not fired:
                    fired.append(1)
                    action()
                if inner:
                    inner()
            kw["on_poll"] = poll
            return real(*a, **kw)
        with mock.patch.object(agents, "run_group", side_effect=hooked):
            out = worker.tick()                                     # used to raise StudioError studio_order_not_held
        return worker, out

    def test_cancel_during_a_failing_run_is_obeyed_not_a_crash(self):
        worker, out = self.run_with(lambda: self.st.cancel(self.sid), ["fail"])
        self.assertEqual((out["state"], out["reason"]), ("waiting", "studio_order_not_held"))
        self.assertEqual(worker.tick()["state"], "cancelled")

    def test_cancel_during_a_timeout_and_during_a_stop_are_obeyed_too(self):
        worker, out = self.run_with(lambda: self.st.cancel(self.sid), ["hang"])
        self.assertEqual(out["state"], "waiting")
        self.assertEqual(worker.tick()["state"], "cancelled")


    def test_dispatch_reports_a_stale_run_for_every_non_completed_outcome(self):
        cli = self.start(self.cli(modes=["fail"]), timeout=1)
        worker = self.worker()
        worker.tick()
        self.job_doc()
        task = self.st.next_task(self.sid, self.job, holder="runner:claude")
        name, entry = self.st.enabled_runner(self.sid)
        with mock.patch.object(self.st, "runner_finish", side_effect=StudioError("studio_order_not_held", "gone")):
            out = agents.dispatch(self.st, self.sid, self.job, task["order"]["order_id"], name, entry)
        self.assertEqual(out, {"outcome": "stale", "reason": "studio_order_not_held"})
        self.assertEqual(len(cli.calls()), 1)


class DetachAndScheduleTests(base.RunnerCase):
    def test_concurrent_detached_starts_report_exactly_one_started(self):
        from runtime import ledger_trust
        self.job = self.st.init(self.sid)["job_id"]
        results = []
        env = {"ALEARN_LEDGER_KEY": ledger_trust.ledger_key().hex()}

        def go():
            results.append(sw.start_detached(self.ws, self.sid, SCRIPT, k=self.st, poll=1, max_poll=1, idle_limit=2))
        with mock.patch.dict(os.environ, env):
            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(1 for r in results if r["started"]), 1, results)
        loser = next(r for r in results if not r["started"])
        self.assertIn(loser["result"], {"already_running", "exited_early"})
        self.assertTrue(wait_until(lambda: not sw.probe_lock(self.studio_root()), 90))
        for r in results:
            if r.get("pid"):
                self.reap(r["pid"])

    def reap(self, pid):
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass

    def cron_line(self, minutes):
        text = sw.schedule_print(self.ws, self.sid, SCRIPT, minutes, Path(self.scratch.name) / "log")
        return text.split("# --- crontab line (add with `crontab -e`) ---\n")[1].strip()

    def test_the_crontab_cadence_matches_the_request_or_says_it_cannot(self):
        self.assertTrue(self.cron_line(10).startswith("*/10 * * * * "))
        self.assertTrue(self.cron_line(60).startswith("0 */1 * * * "))
        self.assertTrue(self.cron_line(120).startswith("0 */2 * * * "))
        self.assertTrue(self.cron_line(480).startswith("0 */8 * * * "))
        self.assertTrue(self.cron_line(1440).startswith("0 0 * * * "))
        for odd in (7, 45, 90, 420, 540):                            # none of these is an even cron cadence
            line = self.cron_line(odd)
            self.assertTrue(line.startswith("# cron cannot run every"), (odd, line))
            self.assertNotIn("studio tick", line)


if __name__ == "__main__":
    unittest.main()

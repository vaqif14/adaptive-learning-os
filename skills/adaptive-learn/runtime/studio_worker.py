"""Detached background worker of the Course Studio (stage D, course-studio.md section 10).

One iteration (``Worker.tick``) performs the next runtime-owned step of a build: plan, full rules gate and handover,
submit a result that is already in an order inbox, or (only when the owner opted in) claim an order and run a headless
runner on it. ``run_loop`` repeats ticks until the job is done, parked for a human, cancelled or the worker is idle for
too long. A flock singleton on ``<session>/studio/worker/worker.lock`` allows one worker per session; a heartbeat file
shows liveness. Pause/resume/cancel stay job-level and are obeyed every tick. Nothing here deletes anything: a log past
5 MB is renamed into ``studio/archive/worker-logs/``.

Together with ``studio_agents`` this is the only studio module allowed to import ``subprocess``/``signal``/``fcntl``.
"""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import escape

from . import course_content as cc
from . import os_sandbox
from . import studio_agents as agents
from . import studio_jobs as jobs
from .session_lock import session_lock
from .studio_store import (NOTIFICATIONS_FILE, EventLog, StudioError, archive_rename, notify, read_json, secure_mkdir,
                           studio_dir, write_atomic_json)

WORKER_DIR, LOCK_FILE, BEAT_FILE, LOG_FILE = "worker", "worker.lock", "heartbeat.json", "worker.log"
RUN_FILE = "runner.json"          # pgid of the runner in flight: a crashed worker leaves it for the next one to reap
LOG_ROTATE_BYTES = 5 * 1024 * 1024
DEFAULT_POLL, MAX_POLL, IDLE_LIMIT = 30.0, 300.0, 7200.0
BEAT_STALE_SECONDS = 120
SLEEP_CHUNK = 2.0
SPAWN_WAIT = 2.0                  # start_detached waits this long for the child to take the lock
DONE_STATUSES = ("ready_for_review", "accepted", "awaiting_learner_approval", "promoted")
EXIT_STATES = frozenset({"done", "needs_learner", "blocked", "cancelled", "stopped"})
LABEL = "az.alearn.studio.tick"


def worker_dir(root: Path) -> Path:
    return Path(root) / WORKER_DIR


# ------------------------------------------------------------------ liveness
def probe_lock(root: Path) -> bool:
    """True while some process holds the worker lock (a non-blocking flock attempt on a separate descriptor)."""
    path = worker_dir(root) / LOCK_FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def read_heartbeat(root: Path) -> dict | None:
    path = worker_dir(root) / BEAT_FILE
    try:
        doc = read_json(path)
    except (OSError, ValueError, StudioError):
        return None
    return doc if isinstance(doc, dict) else None


def worker_status(root: Path, now) -> dict:
    """{alive, lock_held, pid, beat_at, beat_age_seconds, state}: alive needs the lock AND a recent heartbeat."""
    held, beat = probe_lock(root), read_heartbeat(root)
    age = None
    if beat and isinstance(beat.get("beat_at"), str):
        try:
            age = max(0.0, (now - jobs.parse_time(beat["beat_at"])).total_seconds())
        except ValueError:
            age = None
    alive = held and age is not None and age <= BEAT_STALE_SECONDS
    recorded = (beat or {}).get("state")
    if held:
        state = recorded if alive else "stale"
    else:
        state = None if beat is None else (recorded if recorded in EXIT_STATES | {"exited"} else "not_running")
    return {"alive": alive, "lock_held": held, "pid": (beat or {}).get("pid"), "beat_at": (beat or {}).get("beat_at"),
            "beat_age_seconds": age, "state": state}


# --------------------------------------------------------------- notifications
def read_notifications(root: Path) -> list[dict]:
    path = Path(root) / NOTIFICATIONS_FILE
    rows = []
    if path.is_file() and not path.is_symlink():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append({"n": index, **row})
    return rows


def notifications(root: Path, since: int = 0) -> dict:
    rows = read_notifications(root)
    shown = [r for r in rows if r["n"] >= max(0, since)]
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.get("kind"))] = counts.get(str(row.get("kind")), 0) + 1
    return {"count": len(rows), "next_since": (rows[-1]["n"] + 1) if rows else 0, "by_kind": counts, "notifications": shown}


# ---------------------------------------------------------------------- worker
class Worker:
    """One worker per session. ``kernel`` and the timing hooks are injectable for tests."""

    def __init__(self, workspace, sid: str, *, job_id=None, kernel=None, runner: str | None = None,
                 poll: float = DEFAULT_POLL, max_poll: float = MAX_POLL, idle_limit: float = IDLE_LIMIT,
                 sleep=time.sleep, monotonic=time.monotonic, log=None):
        from .studio import StudioKernel
        self.k = kernel or StudioKernel(Path(workspace))
        self.sid, self.job_id, self.preferred = sid, job_id, runner
        self.poll, self.max_poll, self.idle_limit = poll, max_poll, idle_limit
        self._sleep, self._mono, self._log = sleep, monotonic, log
        self._logged = None
        self.session_dir = self.k.ck.sk.dir(sid)
        self.root = studio_dir(self.session_dir)
        self.stopping = False
        self._lock_fd = None
        self._started = None
        self._beat = {"job_id": None, "state": "starting", "current_order": None, "runner": None, "reason": None}
        self._last_beat = 0.0

    # --------------------------------------------------------------- singleton
    def acquire(self) -> bool:
        """Take the flock singleton; False when another worker holds it."""
        secure_mkdir(worker_dir(self.root), self.root)
        fd = os.open(worker_dir(self.root) / LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._lock_fd = fd
        self._started = self.k._now()
        self._reap_orphan()
        return True

    def _reap_orphan(self) -> None:
        """A runner group recorded by a previous worker that died without cleaning up is killed (we hold the lock, so
        no live worker owns it); the record is archived, never removed."""
        path = worker_dir(self.root) / RUN_FILE
        if not path.is_file():
            return
        try:
            rec = read_json(path)
        except (OSError, ValueError, StudioError):
            rec = {}
        killed = agents.reap_group(rec.get("pgid"), rec.get("started")) if isinstance(rec, dict) else False
        archive_rename(self.root, path, now=self.k._dt(), sub="worker-runs")
        if killed:
            notify(self.root, "runner_orphan_reaped", rec.get("job_id"),
                   "a runner process group left by a stopped worker was killed", self.k._now())

    def _record_group(self, pgid: int, job_id: str, order_id: str) -> None:
        secure_mkdir(worker_dir(self.root), self.root)
        write_atomic_json(worker_dir(self.root) / RUN_FILE, {"pgid": pgid, "started": agents.group_started_at(pgid),
                                                             "job_id": job_id, "order_id": order_id, "at": self.k._now()},
                          root=self.root)

    def _clear_group(self) -> None:
        path = worker_dir(self.root) / RUN_FILE
        if path.is_file():
            archive_rename(self.root, path, now=self.k._dt(), sub="worker-runs")

    def _safe_beat(self) -> None:
        """Heartbeat during a runner run: a failed write (disk full, permissions) must never abandon the run."""
        try:
            self.beat(force=False)
        except (OSError, StudioError):
            pass

    @contextmanager
    def _busy(self, state: str):
        """Keep the heartbeat fresh while a long gate/handover runs under the session lock. No thread: the scratch run
        swaps the process-wide ALEARN_STATE_DIR and is refused next to other threads, so the gate calls back before each
        probe instead."""
        self.beat(state, reason=state)
        self.k.gate_progress = self._safe_beat
        try:
            yield
        finally:
            self.k.gate_progress = None

    def release(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(self._lock_fd)
                self._lock_fd = None

    def beat(self, state: str | None = None, *, force: bool = True, **extra) -> None:
        """Write the heartbeat atomically (rate-limited to once a second unless forced)."""
        now = self._mono()
        if not force and now - self._last_beat < 1.0:
            return
        self._last_beat = now
        self._beat.update(extra)
        if state is not None:
            self._beat["state"] = state
        secure_mkdir(worker_dir(self.root), self.root)
        write_atomic_json(worker_dir(self.root) / BEAT_FILE, {"pid": os.getpid(), "started_at": self._started,
                                                              "beat_at": self.k._now(), **self._beat}, root=self.root)

    # -------------------------------------------------------------------- tick
    def _result(self, state: str, action: str, **extra) -> dict:
        out = {"state": state, "action": action, **extra}
        self.beat(state, reason=extra.get("reason") or action, runner=self._beat.get("runner"))
        return out

    def tick(self) -> dict:
        """One iteration. ``state``: working (go again at once), idle/waiting (sleep), or an exit state."""
        if self.stopping:
            return self._result("stopped", "worker_stopping", reason="signal")
        with session_lock(self.session_dir):
            self.k.ck.sk._recover_state(self.sid)
        st = self.k.status(self.sid, self.job_id)
        if st.get("status") == "no_job":
            raise StudioError("studio_no_job", "no studio job exists for this session; run `alearn studio init`")
        job_id, status = st["job_id"], st["status"]
        self._beat.update(job_id=job_id, current_order=st.get("current_order"))
        if status == "cancelled":
            return self._result("cancelled", "job_cancelled", job_id=job_id)
        if status == "superseded":
            return self._result("blocked", "job_superseded", job_id=job_id, reason="superseded",
                                next_step="alearn studio rebase --job " + job_id)
        if status == "paused":
            return self._result("idle", "paused", job_id=job_id, reason="paused")
        if status == "blocked":
            return self._result("blocked", "job_blocked", job_id=job_id, blocked=st.get("blocked"))
        try:
            if status in {"planning", "needs_learner"}:
                return self._plan(job_id)
            if status == "checking":
                return self._checking(job_id)
            if status in DONE_STATUSES:
                return self._done(job_id, st)
            return self._author(job_id, st)
        except StudioError as e:
            if e.code == "exam_active":
                return self._result("idle", "exam_active", job_id=job_id, reason="exam_active")
            if e.code == "studio_job_superseded":
                return self._result("blocked", "job_superseded", job_id=job_id, reason="superseded")
            if e.code in {"studio_order_not_held", "studio_job_not_accepting"}:      # cancelled/paused/taken over mid-run
                return self._result("waiting", "waiting", job_id=job_id, reason=e.code)
            raise

    def _plan(self, job_id: str) -> dict:
        after = self.k.plan(self.sid, job_id)
        if after["status"] == "needs_learner":
            return self._result("needs_learner", "needs_learner", job_id=job_id, needs_learner=after.get("needs_learner"))
        return self._result("working", "planned", job_id=job_id, job_status=after["status"])

    def _checking(self, job_id: str) -> dict:
        if not self.k.gate:      # no rules gate configured on this kernel (tests): a complete build is as far as it goes
            return self._finish(job_id, "checking", None)
        self._defer_during_exam()
        with self._busy("gate"):
            moved = self.k.finish_checking(self.sid, job_id)
        return self._result("working", "gate_run", job_id=job_id, job_status=moved["status"])

    def _defer_during_exam(self) -> None:
        """The gate's scratch journey is refused while an exam is open: wait (idle) instead of running it."""
        if self.k.ck.studio_snapshot(self.sid)["exam_locked"]:
            raise StudioError("exam_active", "the rules gate waits for the learner's exam to finish")

    def _done(self, job_id: str, st: dict) -> dict:
        handover = st.get("handover")
        if st["status"] == "ready_for_review" and (not handover or handover.get("build_digest") != st.get("build_digest")):
            self._defer_during_exam()
            with self._busy("handover"):
                self.k.handover(self.sid, job_id)       # full gate + dossier, runtime-owned
            return self._result("working", "handover", job_id=job_id)
        return self._finish(job_id, st["status"], st.get("build_digest"))

    def _finish(self, job_id: str, status: str, digest) -> dict:
        message = f"the background worker finished: job is {status}" + (f", build {digest[:12]}" if digest else "")
        self._notify_once("worker_finished", job_id, message)
        return self._result("done", "build_ready", job_id=job_id, job_status=status, build_digest=digest)

    def _notify_once(self, kind: str, job_id: str, message: str) -> None:
        if not any(r.get("kind") == kind and r.get("job_id") == job_id and r.get("message") == message
                   for r in read_notifications(self.root)):
            notify(self.root, kind, job_id, message, self.k._now())

    def _author(self, job_id: str, st: dict) -> dict:
        name, entry = self.k.enabled_runner(self.sid, self.preferred)
        self._beat["runner"] = name
        order_id = st.get("current_order")
        if name is not None and agents.binary_drifted(self.k, self.sid, name, entry):
            return self._result("waiting", "waiting", job_id=job_id, reason="runner_needs_reconfigure", runner=name,
                                detail="the runner binary changed since it was configured")
        if order_id is None:
            if name is None:
                return self._result("waiting", "waiting", job_id=job_id, reason=entry if isinstance(entry, str) else "no_runner_enabled",
                                    detail="no order is open and no runner is enabled: the inline host authors")
            task = self.k.next_task(self.sid, job_id, holder=f"runner:{name}")
            if task["state"] == "needs_learner":
                return self._result("needs_learner", "needs_learner", job_id=job_id, needs_learner=task.get("needs_learner"))
            if task["state"] == "blocked":
                return self._result("blocked", "job_blocked", job_id=job_id, reason=task.get("reason"))
            if task["state"] == "idle":
                return self._result("idle", task.get("reason", "idle"), job_id=job_id, reason=task.get("reason"))
            if task["state"] == "done":
                return self._result("working", "build_complete", job_id=job_id)
            order_id = task["order"]["order_id"]
        if self._has_result(job_id, order_id):
            return self._submit(job_id, order_id)
        if name is None:
            return self._result("waiting", "waiting", job_id=job_id, reason=entry if isinstance(entry, str) else "order_open_inline",
                                order_id=order_id, detail="an order is open and no runner is enabled")
        held = self.k.order_show(self.sid, job_id, order_id)["entry"]
        if held["taken_by"] != f"runner:{name}" and jobs.parse_time(held["lease_until"]) > self.k._dt():
            return self._result("waiting", "waiting", job_id=job_id, reason="order_held", order_id=order_id,
                                taken_by=held["taken_by"], lease_until=held["lease_until"])
        self.beat("working", current_order=order_id, runner=name, reason="running")
        try:
            out = agents.dispatch(self.k, self.sid, job_id, order_id, name, entry, should_stop=lambda: self.stopping,
                                  on_poll=self._safe_beat, on_spawn=lambda pgid: self._record_group(pgid, job_id, order_id))
        finally:
            self._clear_group()
        self._beat["current_order"] = None
        if out["outcome"] == "stopped":
            return self._result("stopped", "runner_stopped", job_id=job_id, order_id=order_id, reason="signal")
        if out["outcome"] in {"refused", "skipped"} or (out["outcome"] == "stale" and out["reason"] != "studio_order_held"):
            return self._result("waiting", "waiting", job_id=job_id, order_id=order_id, reason=out["reason"],
                                outcome=out["outcome"])
        if out["outcome"] == "stale":
            return self._result("waiting", "waiting", job_id=job_id, order_id=order_id, reason="order_held")
        extra = {k: v for k, v in out.items() if k not in {"outcome", "submit", "claim"}}
        extra.update(out.get("submit") or {})
        return self._result("working", "runner_" + out["outcome"], job_id=job_id, order_id=order_id, outcome=out["outcome"],
                            **extra)

    def _has_result(self, job_id: str, order_id: str) -> bool:
        path = self.root / "inbox" / job_id / order_id / "out" / "result.json"
        try:
            info = os.lstat(path)
        except OSError:
            return False
        return info.st_mode & 0o170000 == 0o100000 and info.st_size > 0

    def _submit(self, job_id: str, order_id: str) -> dict:
        try:
            res = self.k.submit(self.sid, job_id, order_id)
        except StudioError as e:
            if e.code in {"studio_result_missing", "studio_job_not_accepting"}:
                return self._result("waiting", "waiting", job_id=job_id, order_id=order_id, reason=e.code)
            raise
        return self._result("working", "submitted", job_id=job_id, order_id=order_id,
                            **{k: res.get(k) for k in ("status", "unit_id", "attempts", "errors", "job_status")})

    # --------------------------------------------------------------- the loop
    def _install_signals(self):
        old = {}
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                old[sig] = signal.signal(sig, self._on_signal)
        except ValueError:       # not the main thread: the caller owns signal handling
            return {}
        return old

    def _on_signal(self, signum, _frame=None) -> None:
        """First SIGTERM/SIGINT asks for a clean stop; a second one restores the default action and re-raises it, so a
        hung gate or sandbox can still be interrupted without SIGKILL."""
        if self.stopping:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
            return
        self.stopping = True

    def _note(self, tick: dict) -> None:
        """One log line per change of what the worker is doing (the detached worker's stderr is its log file)."""
        key = (tick["state"], tick["action"], tick.get("reason"))
        if self._log is not None and key != self._logged:
            self._logged = key
            self._log(json.dumps({"at": self.k._now(), **{k: tick[k] for k in ("state", "action", "reason", "job_id",
                                                                                "order_id", "outcome") if k in tick}},
                                 ensure_ascii=True))

    def _nap(self, seconds: float) -> None:
        end = self._mono() + seconds
        while not self.stopping and self._mono() < end:
            self._sleep(min(SLEEP_CHUNK, max(0.0, end - self._mono())))
            self.beat(force=False)

    def run_once(self) -> dict:
        """A single tick under the singleton lock (cron/launchd): skipped while a worker loop is running."""
        if not self.acquire():
            beat = read_heartbeat(self.root) or {}
            return {"state": "skipped", "reason": "another worker holds the lock", "pid": beat.get("pid")}
        old = self._install_signals()
        try:
            self.beat("starting")
            return self.tick()
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
            self.release()

    def run_loop(self) -> dict:
        """Singleton + ticks until done / parked / cancelled / stopped / idle past the limit."""
        if not self.acquire():
            beat = read_heartbeat(self.root) or {}
            return {"started": False, "result": "already_running", "pid": beat.get("pid")}
        old = self._install_signals()
        idle_since, delay, last = None, self.poll, {}
        try:
            self.beat("starting")
            while True:
                last = self.tick()
                self._note(last)
                if last["state"] in EXIT_STATES:
                    break
                if last["state"] == "working":
                    idle_since, delay = None, self.poll
                    continue
                idle_since = idle_since if idle_since is not None else self._mono()
                if self._mono() - idle_since >= self.idle_limit:
                    last = self._result("idle", "idle_limit", reason="idle_limit")
                    last["exit"] = "idle_limit"
                    break
                self._nap(delay)
                delay = min(self.max_poll, delay * 2)
                if self.stopping:
                    last = self._result("stopped", "worker_stopping", reason="signal")
                    break
            self.beat("exited", reason=last.get("action"))
            return {"started": True, "result": "exited", "last": last}
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
            self.release()


# ------------------------------------------------------------------- detach
def rotate_log(root: Path, now) -> None:
    log = worker_dir(root) / LOG_FILE
    if log.is_file() and log.stat().st_size > LOG_ROTATE_BYTES:
        archive_rename(root, log, now=now, sub="worker-logs")


def start_detached(workspace, sid: str, script: Path, *, k, job_id=None, runner=None, poll=None, max_poll=None,
                   idle_limit=None, env=None) -> dict:
    """Spawn ``alearn studio worker`` in its own session; returns {started, pid} or already_running. ``env`` replaces the
    child's environment (the owner server passes its own without the owner/API tokens)."""
    root = studio_dir(k.ck.sk.dir(sid))
    if probe_lock(root):
        beat = read_heartbeat(root) or {}
        return {"started": False, "result": "already_running", "pid": beat.get("pid")}
    secure_mkdir(worker_dir(root), root)
    rotate_log(root, k._dt())
    log_path = worker_dir(root) / LOG_FILE
    argv = [sys.executable, str(script), "--workspace", str(Path(workspace).resolve()), "studio", "worker", "--session", sid]
    for flag, value in (("--job", job_id), ("--runner", runner), ("--poll-seconds", poll), ("--max-poll-seconds", max_poll),
                        ("--idle-limit-seconds", idle_limit)):
        if value is not None:
            argv += [flag, str(value)]
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        proc = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, close_fds=True,
                                cwd=str(Path(workspace).resolve()), env=env, shell=False)
    finally:
        os.close(fd)
    end = time.monotonic() + SPAWN_WAIT          # handshake: the child either takes the lock (heartbeat pid) or exits
    while time.monotonic() < end:
        code = proc.poll()
        if code is not None:
            beat = read_heartbeat(root) or {}
            return {"started": False, "result": "already_running" if probe_lock(root) else "exited_early",
                    "pid": beat.get("pid"), "exit_code": code, "log": str(log_path)}
        beat = read_heartbeat(root) or {}
        if beat.get("pid") == proc.pid:
            break
        time.sleep(0.05)
    return {"started": True, "pid": proc.pid, "log": str(log_path)}


# ------------------------------------------------------------- schedule-print
def schedule_print(workspace, sid: str, script: Path, every_minutes: int, log_path: Path) -> str:
    """Text for a launchd plist and a crontab line that run ``alearn studio tick``. Installs nothing; no secrets."""
    if type(every_minutes) is not int or not 1 <= every_minutes <= 1440:
        raise StudioError("studio_schedule_invalid", "--every-minutes must be an integer in 1..1440")
    command = [sys.executable, str(script), "--workspace", str(Path(workspace).resolve()), "studio", "tick", "--session", sid]
    state_dir = os.environ.get("ALEARN_STATE_DIR")
    arguments = "".join(f"\n    <string>{escape(a)}</string>" for a in command)
    env_block = ""
    if state_dir:     # a path, not a secret: the ledger trust heads live there and the tick must find them
        env_block = ("\n  <key>EnvironmentVariables</key>\n  <dict>\n    <key>ALEARN_STATE_DIR</key>\n"
                     f"    <string>{escape(state_dir)}</string>\n  </dict>")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}.{escape(sid)}</string>
  <key>ProgramArguments</key>
  <array>{arguments}
  </array>
  <key>StartInterval</key>
  <integer>{every_minutes * 60}</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{escape(str(log_path))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log_path))}</string>{env_block}
</dict>
</plist>"""
    prefix = f"ALEARN_STATE_DIR={shlex.quote(state_dir)} " if state_dir else ""
    tail = f"{prefix}{' '.join(shlex.quote(a) for a in command)} >> {shlex.quote(str(log_path))} 2>&1"
    hours = every_minutes // 60
    if every_minutes < 60 and 60 % every_minutes == 0:
        line = f"*/{every_minutes} * * * * {tail}"
    elif every_minutes % 60 == 0 and 24 % hours == 0:
        line = f"0 */{hours} * * * {tail}" if hours < 24 else f"0 0 * * * {tail}"
    else:     # cron fields cannot express this interval honestly (it would fire at uneven gaps)
        line = f"# cron cannot run every {every_minutes} minutes evenly; use the launchd job above (StartInterval is exact)"
    line = line.replace("%", "\\%")      # cron treats an unescaped % as a newline
    return ("# alearn studio: nothing below is installed by this command; copy what you want.\n"
            f"# --- launchd (save as ~/Library/LaunchAgents/{LABEL}.{sid}.plist, then `launchctl load` it) ---\n"
            f"{plist}\n# --- crontab line (add with `crontab -e`) ---\n{line}\n")


# --------------------------------------------------------------------- doctor
def doctor(kernel, sid: str) -> dict:
    """Read-only health report: runners (binary, version, sha drift), worker lock/heartbeat, permissions, event chain,
    ledger, sandbox and notification counts. ``ok`` is False when something configured is broken."""
    from .rules_probes import r10
    checks: dict[str, dict] = {}
    session_dir = kernel.ck.sk.dir(sid)
    with kernel._locked(sid, create=False) as (d, led, root_):
        try:
            doc = agents.load_runners(root_) if root_.is_dir() else {"runners": {}}
        except StudioError as e:
            doc = {"runners": {}}
            checks["runners_file"] = {"ok": False, "detail": e.detail}
        runners = {}
        for name in agents.RUNNERS:
            entry = doc["runners"].get(name)
            if entry is None:
                runners[name] = {"configured": False, "ok": True}
                continue
            problems = agents.entry_problems(name, entry)
            info = {"configured": True, "enabled": entry.get("enabled"), "binary": entry.get("binary"),
                    "version_recorded": entry.get("version"), "needs_reconfigure": entry.get("needs_reconfigure")}
            try:
                now_sha = agents.sha256_file(entry["binary"])
                info["binary_sha256_matches"] = now_sha == entry.get("binary_sha256")
                # a binary whose hash drifted is reported, never executed (the dispatch guard also covers diagnostics)
                info["version_now"] = (agents.probe_version(name, entry["binary"], Path(kernel.ck.sk.workspace).resolve(),
                                                            session_dir.resolve())
                                       if entry.get("enabled") and info["binary_sha256_matches"] else None)
            except (OSError, StudioError, KeyError, TypeError) as e:
                info["binary_sha256_matches"] = False
                info["error"] = getattr(e, "code", type(e).__name__)
            info["problems"] = problems
            info["ok"] = (not entry.get("enabled")) or (not problems and info["binary_sha256_matches"] is True
                                                        and not entry.get("needs_reconfigure"))
            runners[name] = info
        checks["runners"] = {"ok": all(r["ok"] for r in runners.values()), "runners": runners}
        status = worker_status(root_, kernel._dt())
        checks["worker"] = {"ok": not (status["lock_held"] and not status["alive"]), **status}
        problems = r10.mode_problems(session_dir, ("studio",))
        checks["permissions"] = {"ok": not problems, "problems": problems[:20]}
        log = EventLog(root_)
        verify = log.verify(led)
        checks["events"] = {"ok": verify["ok"], "count": verify["count"], "error": verify["error"]}
        integrity = led.integrity()
        checks["ledger"] = {"ok": bool(integrity.get("chain_ok")), "chain_ok": integrity.get("chain_ok"),
                            "head_ok": integrity.get("head_ok")}
        counts = notifications(root_)
        checks["notifications"] = {"ok": True, "count": counts["count"], "by_kind": counts["by_kind"]}
    try:
        res = cc._VERIFIER.verify("print(1)")
        usable = bool(getattr(res, "executed", False) and getattr(res, "policy_valid", False)
                      and str(getattr(res, "stdout", "")).strip() == "1")
    except (OSError, RuntimeError, ValueError) as e:
        usable = False
        checks["sandbox_error"] = {"ok": False, "detail": type(e).__name__}
    checks["sandbox"] = {"ok": usable, "detail": "the python sandbox executes code" if usable else "the sandbox did not execute"}
    # the OS boundary around verifier children: proven with a canary + negative control; required once a runner is enabled
    proof = os_sandbox.containment_check()
    runner_enabled = any(r.get("enabled") for r in runners.values())
    checks["os_sandbox"] = {"ok": (proof["contained"] and proof["control_ok"]) if proof["mode"] else not runner_enabled,
                            "mode": proof["mode"], "contained": proof["contained"], "control_ok": proof["control_ok"],
                            "detail": ("author and learner code run behind the OS sandbox" if proof["mode"] and proof["contained"]
                                       else "no OS sandbox here: runners cannot dispatch; code runs under in-process "
                                            "defences only" if not proof["mode"] else "the OS sandbox did not contain the probe")}
    return {"session_id": sid, "ok": all(c["ok"] for c in checks.values()), "checks": checks}

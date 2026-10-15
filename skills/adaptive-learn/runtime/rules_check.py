"""The regenerated 10-rule gate (``alearn rules-check``): course-studio.md section 8 and Appendix A.

``run_rules`` recomputes EVERY probe from scratch on each call: it never reads a previous receipt or an agent claim.
Negative controls run first; if any control passes (or crashes) the suite is ``suite_broken`` and nothing counts as a
pass. Quick scope runs the cheap deterministic probes; full scope additionally makes a scratch copy of the session
under ``<session>/studio/scratch/<run_id>/`` (backup-session -> restore-session -> trust adoption, with its own
ALEARN_STATE_DIR), promotes the staged build INTO THE SCRATCH COPY through ``CourseKernel.promote_build`` (the function
real promotion uses) and runs the dynamic probes there. The real session is only read: the ledger position before and
after must be identical. A receipt only states "these invariants hold under these probes"; it is signed with HMAC under
the ledger trust key and its digest is anchored as a ledger ``event`` by ``commit_receipt``.

Exit codes: 0 every applicable probe passed; 2 a rule failed; 3 a probe crashed or the suite is broken (never a pass).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from . import ledger_trust
from . import studio_jobs as jobs
from . import studio_receipt as receipts
from .backup import backup_session, restore_session
from .course_state import CourseStateError
from .rules_probes import all_probes
from .rules_probes import journey
from .rules_probes.base import (FIX_ONLY_RULES, HARD_RULES, RECEIPT_CLAIM, RULES, RUNTIME_DIR, WAIVABLE_RULES,
                                Ctx, NotApplicable, Result)
from .session import SessionKernel
from .session_lock import session_lock
from .studio_store import StudioError, archive_rename, digest_of, secure_mkdir, studio_dir
from .utils import new_id

SUITE_VERSION = 1
SCOPES = ("quick", "full")
# Concrete failure modes of a probe: reported as a probe error (exit 3), never swallowed as a pass.
PROBE_ERRORS = (RuntimeError, ValueError, TypeError, KeyError, IndexError, AttributeError, LookupError, OSError,
                AssertionError, ArithmeticError, ImportError, RecursionError, StudioError, CourseStateError, StopIteration)
# incomplete: nothing failed, but a hard rule (or any rule) was not actually verified in a full run: never a pass
EXIT = {"pass": 0, "rule_failed": 2, "probe_error": 3, "suite_broken": 3, "incomplete": 3}


# ------------------------------------------------------------------ hashing
def code_tree_hash() -> str:
    """sha256 over runtime/*.py and rules_probes/*.py: the enforcement code the receipt was produced by."""
    h = hashlib.sha256()
    files = sorted(RUNTIME_DIR.glob("*.py")) + sorted((RUNTIME_DIR / "rules_probes").glob("*.py"))
    for path in files:
        h.update(path.relative_to(RUNTIME_DIR).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def probe_set_hash(probes) -> str:
    return digest_of(sorted((p.id, p.version, p.rule, p.kind, p.cost) for p in probes))


# ----------------------------------------------------------------- selection
def _job_for(root: Path | None, job_id: str | None, job: dict | None) -> dict | None:
    if job is not None:
        return job
    if root is None or not root.is_dir():
        return None
    if job_id:
        return jobs.load_job(root, job_id)
    live = jobs.active_job(root)
    if live is not None:
        return live
    every = [j for j in jobs.list_jobs(root) if j["status"] not in {"superseded", "cancelled"} and j.get("unit_order")]
    return every[-1] if every else None


def _selected(probes, scope: str, probe_ids, rules):
    chosen = [p for p in probes if (not probe_ids or p.id in probe_ids) and (not rules or p.rule in rules)]
    runnable = [p for p in chosen if scope == "full" or p.cost == "cheap"]
    return chosen, runnable


# ----------------------------------------------------------------- scratch
_ENV_LOCK = threading.Lock()


@contextlib.contextmanager
def scratch_state_env(state_dir: Path):
    """Point ALEARN_STATE_DIR at the scratch run's own directory (trust heads of the copy never touch the real ones).

    The environment is process-global, so the swap is only safe while no other thread can touch a ledger: it is refused
    (a probe error, never a silent mix-up of trust heads) when any other thread is alive, and concurrent gates in one
    process are serialised. A threaded host (a server, a worker) must run the gate in its own process."""
    others = [t.name for t in threading.enumerate() if t is not threading.current_thread()
              and t is not threading.main_thread()]
    if others:
        raise RuntimeError("the scratch run swaps the process-wide ALEARN_STATE_DIR and cannot run next to other threads "
                           f"({', '.join(sorted(others))[:120]}); run `alearn rules-check` in its own process")
    with _ENV_LOCK:
        ledger_trust.ledger_key()                                  # load the key (and cache it) before the swap
        previous = os.environ.get("ALEARN_STATE_DIR")
        os.environ["ALEARN_STATE_DIR"] = str(state_dir)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("ALEARN_STATE_DIR", None)
            else:
                os.environ["ALEARN_STATE_DIR"] = previous


def lock_down(run_dir: Path) -> None:
    """Directories 0700 and files 0600 across a scratch run (the restore path creates intermediate dirs with defaults)."""
    for base, dirs, files in os.walk(run_dir):
        for name in dirs:
            if not (Path(base) / name).is_symlink():
                os.chmod(Path(base) / name, 0o700)
        for name in files:
            if not (Path(base) / name).is_symlink():
                os.chmod(Path(base) / name, 0o600)
    os.chmod(run_dir, 0o700)


MAX_SCRATCH_RUNS = 5          # older scratch runs are rotated by rename into studio/archive/scratch-runs/ (never removed)
SCRATCH_ARCHIVE = "scratch-runs"
# Disposable duplicates of the session: never part of a session backup or of another scratch copy.
SCRATCH_EXCLUDE = ("studio/scratch", f"studio/archive/{SCRATCH_ARCHIVE}")


def rotate_scratch(root: Path, keep: int = MAX_SCRATCH_RUNS) -> list[str]:
    runs = sorted((p for p in (root / "scratch").iterdir() if p.is_dir() and not p.is_symlink()),
                  key=lambda p: (p.stat().st_mtime_ns, p.name)) if (root / "scratch").is_dir() else []
    moved = []
    for old in runs[:max(0, len(runs) - keep)]:
        moved.append(archive_rename(root, old, sub=SCRATCH_ARCHIVE).name)
    return moved


def make_scratch(ctx: Ctx, real_kernel: SessionKernel, root: Path, run_id: str, clock) -> journey.Scratch:
    rotate_scratch(root)
    run_dir = secure_mkdir(root / "scratch" / run_id, root)
    state_dir = secure_mkdir(run_dir / "state", root)
    backup = run_dir / "session.backup.zip"
    # Symlinks of the real session are skipped (recorded), not fatal: one link in a learner workspace must not make the
    # whole gate unrunnable. Archive limits still apply and name the file that crossed them.
    made = backup_session(real_kernel, ctx.sid, backup, exclude=SCRATCH_EXCLUDE, symlinks="skip")
    ws = secure_mkdir(run_dir / "ws", root)
    with scratch_state_env(state_dir):
        try:
            restore_session(SessionKernel(ws), backup)
        finally:
            lock_down(run_dir)
        scratch = journey.Scratch(run_dir, ws, ctx.sid, state_dir, clock)
        scratch.obs["restore"] = {"integrity": scratch.led().integrity(),
                                  "head": receipts.ledger_position(scratch.led())["head"],
                                  "real_head": (ctx.before or {}).get("head"),
                                  "skipped_symlinks": len(made.get("skipped_symlinks") or [])}
        if ctx.job is not None:       # the scratch copy must see the in-memory job, not a stale document
            scratch_root = scratch.session_dir / "studio"
            if scratch_root.is_dir():
                from .studio_store import write_atomic_json
                write_atomic_json(jobs.job_path(scratch_root, ctx.job["job_id"]), ctx.job, root=scratch_root)
    return scratch


# ------------------------------------------------------------------ results
def _row(p, result: Result | None, *, status: str, detail: str, ms: int = 0, data: dict | None = None) -> dict:
    return {"id": p.id, "version": p.version, "kind": p.kind, "cost": p.cost, "passed": {"pass": True, "fail": False}.get(status),
            "status": status, "detail": str(detail)[:600], "ms": ms, **({"data": data} if data else {})}


def _run_probe(p, ctx: Ctx) -> dict:
    if ctx.progress is not None:
        try:
            ctx.progress()
        except (OSError, ValueError):       # a failed heartbeat write must never fail a probe
            pass
    started = datetime.now(UTC)
    try:
        result = p(ctx)
    except NotApplicable as e:
        return _row(p, None, status="not_applicable", detail=str(e), ms=_ms(started))
    except PROBE_ERRORS as e:
        return _row(p, None, status="error", detail=f"{type(e).__name__}: {str(e)[:400]}", ms=_ms(started))
    if not isinstance(result, Result):
        return _row(p, None, status="error", detail="the probe returned no Result", ms=_ms(started))
    status = "not_applicable" if result.passed is None else "pass" if result.passed else "fail"
    keep = {k: v for k, v in result.data.items() if k in {"table_hash", "supplementary_share", "source_refs", "minted", "kinds",
                                                          "channels", "nodes", "events", "evidence_records", "used", "strings"}}
    row = _row(p, result, status=status, detail=result.detail, ms=_ms(started), data=keep)
    if status == "fail":
        row["problems"] = [str(x)[:300] for x in (result.data.get("problems") or [result.detail])[:20]]
    return row


def _ms(started) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def _check_controls(probes) -> list[dict]:
    """Run every probe's negative control; a control that does not produce a failure means the suite is broken."""
    out = []
    for p in probes:
        try:
            result = p.fn(p.control())
            ok = isinstance(result, Result) and result.passed is False
            detail = result.detail if isinstance(result, Result) else "no result"
            if not ok:
                detail = f"the negative control did not fail ({'passed' if getattr(result, 'passed', None) else 'not applicable'}): {detail}"
        except (NotApplicable, *PROBE_ERRORS) as e:
            ok, detail = False, f"the negative control crashed: {type(e).__name__}: {str(e)[:200]}"
        out.append({"id": p.id, "control_ok": ok, "detail": str(detail)[:300]})
    return out


def _rule_results(rows: list[dict], probes) -> list[dict]:
    by_rule = {}
    for p in probes:
        by_rule.setdefault(p.rule, []).append(p)
    out = []
    order = {p.id: i for i, p in enumerate(probes)}
    for rule in RULES:
        mine = sorted((r for r in rows if r["id"] in {p.id for p in by_rule.get(rule, [])}), key=lambda r: order[r["id"]])
        statuses = {r["status"] for r in mine}
        if "fail" in statuses:
            status = "fail"
        elif "error" in statuses:
            status = "error"
        elif "pass" in statuses:
            status = "pass"
        elif statuses == {"not_applicable"}:
            status = "not_applicable"
        else:
            status = "skipped"
        out.append({"rule": rule, "probes": mine, "status": status, "passed": {"pass": True, "fail": False, "error": False}.get(status),
                    "hard": rule in HARD_RULES})
    return out


# ------------------------------------------------------------------- engine
def run_rules(workspace, sid, job_id=None, scope="quick", clock=None, *, job=None, probe_ids=None, rules=None,
              probes=None, progress=None) -> dict:
    """Recompute every probe from scratch and return a signed receipt (not persisted: see ``commit_receipt``).

    ``job`` may carry the in-memory job document of a studio operation in progress; ``probe_ids``/``rules`` select a
    subset (the receipt is then ``partial`` and can never satisfy acceptance or promotion); ``probes`` replaces the
    registry (suite tests). ``progress`` is called (no arguments) before every probe so a host can keep a heartbeat fresh
    without a second thread: the scratch run refuses to run next to other threads."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    kernel = SessionKernel(Path(workspace))
    d = kernel.dir(sid).resolve()
    with session_lock(d):
        registry = list(probes) if probes is not None else all_probes()
        chosen, runnable = _selected(registry, scope, set(probe_ids or ()), set(rules or ()))
        partial = bool(probe_ids or rules) or probes is not None
        now = (clock() if clock else datetime.now(UTC)).astimezone(UTC).isoformat()
        root = studio_dir(d) if studio_dir(d).is_dir() else None
        the_job = _job_for(root, job_id, job)
        led_probe = Ctx(workspace, sid, job=the_job, scope=scope, clock=clock)
        before = receipts.ledger_position(led_probe.led())
        ctx = Ctx(workspace, sid, job=the_job, scope=scope, clock=clock, before=before)
        ctx.progress = progress
        run_id = new_id("run")
        controls = _check_controls(runnable)
        broken = [c for c in controls if not c["control_ok"]]
        rows, scratch_info = [], None
        if not broken:
            rows = _execute(ctx, kernel, root, d, runnable, run_id, scope, clock)
            scratch_info = ctx.scratch.run_dir.relative_to(d).as_posix() if ctx.scratch is not None else None
        skipped = [_row(p, None, status="skipped", detail="skipped in quick scope: it needs a sandbox or a scratch copy")
                   for p in chosen if p not in runnable]
        after = receipts.ledger_position(ctx.led())
        return _receipt(ctx, scope, run_id, now, registry, runnable, rows + skipped, controls, broken, before, after,
                        scratch_info, partial, d)


def _execute(ctx: Ctx, kernel, root, d, runnable, run_id, scope, clock) -> list[dict]:
    rows = {}
    early = [p for p in runnable if p.cost != "scratch" and p.order < 99]
    scratch_probes = [p for p in runnable if p.cost == "scratch"]
    late = [p for p in runnable if p.cost != "scratch" and p.order >= 99]
    for p in early:
        rows[p.id] = _run_probe(p, ctx)
    if scratch_probes:
        rows.update(_scratch_phase(ctx, kernel, root, d, scratch_probes, run_id, clock))
    for p in late:
        rows[p.id] = _run_probe(p, ctx)
    return [rows[p.id] for p in runnable]


def _scratch_phase(ctx, kernel, root, d, probes, run_id, clock) -> dict:
    out = {}
    try:
        integrity = ctx.integrity()
    except PROBE_ERRORS as e:
        integrity = {"chain_ok": False, "error": str(e)}
    if not integrity.get("chain_ok"):
        reason = "the real ledger does not verify, so no faithful scratch copy can be made (R4 reports the failure)"
        return {p.id: _row(p, None, status="not_applicable", detail=reason) for p in probes}
    if root is None:
        from .studio_store import ensure_layout
        root = ensure_layout(d)
    try:
        scratch = make_scratch(ctx, kernel, root, run_id, clock)
    except PROBE_ERRORS as e:
        return {p.id: _row(p, None, status="error", detail=f"the scratch copy could not be made: {type(e).__name__}: {str(e)[:300]}")
                for p in probes}
    ctx.scratch = scratch
    try:
        with scratch_state_env(scratch.state_dir):
            journey.run(ctx, scratch)
            crashed = scratch.obs.get("errors") or {}
            if crashed:
                # A journey step that crashed produced no observation: whatever it fed must not read as "nothing to
                # check" (not applicable) or pass. Every dynamic probe reports the crash (exit 3).
                detail = "; ".join(f"{k}: {v}" for k, v in sorted(crashed.items()))[:500]
                for p in probes:
                    out[p.id] = _row(p, None, status="error", detail=f"the scratch journey crashed ({detail})")
            else:
                for p in probes:
                    out[p.id] = _run_probe(p, ctx)
    except PROBE_ERRORS as e:
        for p in probes:
            out[p.id] = _row(p, None, status="error", detail=f"the scratch journey crashed: {type(e).__name__}: {str(e)[:300]}")
    finally:
        lock_down(scratch.run_dir)
    return out


def unverified_rules(results: list[dict]) -> list[str]:
    """Rules a FULL receipt cannot claim: no probe ran for them (skipped), or a hard rule has no passing probe at all
    (every probe not applicable). 'Nothing failed' is not 'verified'."""
    out = []
    for r in results:
        passed_any = any(p["status"] == "pass" for p in r["probes"])
        if r["status"] == "skipped" or (r["hard"] and not passed_any):
            out.append(r["rule"])
    return out


def _receipt(ctx, scope, run_id, now, registry, runnable, rows, controls, broken, before, after, scratch_info, partial, d) -> dict:
    results = _rule_results(rows, registry)
    failing = [r["rule"] for r in results if r["status"] == "fail"]
    errored = [r["rule"] for r in results if r["status"] == "error"]
    if broken:
        status = "suite_broken"
    elif errored:
        status = "probe_error"
    elif failing:
        status = "rule_failed"
    else:
        status = "pass"
    incomplete = [] if status != "pass" or scope != "full" or partial else unverified_rules(results)
    if incomplete:
        status = "incomplete"
    ledger_moved = (before["seq"], before["head"]) != (after["seq"], after["head"])
    if ledger_moved and status == "pass":
        status = "rule_failed"
        failing.append("R4")
    state = ctx.state() or {}
    job = ctx.job
    table_hash = next((r.get("data", {}).get("table_hash") for res in results for r in res["probes"] if r.get("data", {}).get("table_hash")), None)
    receipt = {
        "receipt_schema": 1, "receipt_id": new_id("rcp"), "run_id": run_id, "scope": scope, "partial": partial,
        "session_id": ctx.sid, "job_id": (job or {}).get("job_id"),
        "build_digest": (job or {}).get("build_digest"), "roadmap_version": ((job or {}).get("binding") or {}).get("roadmap_version")
        or state.get("roadmap_version"),
        "course_revision": state.get("revision"),
        "ledger_seq_before": before["seq"], "ledger_head_before": before["head"],
        "ledger_seq_after": after["seq"], "ledger_head_after": after["head"],
        "code_tree_hash": code_tree_hash(), "probe_set_hash": probe_set_hash(registry),
        "probes_run": sorted(p.id for p in runnable), "suite_version": SUITE_VERSION, "evidence_table_hash": table_hash,
        "results": [{"rule": r["rule"], "probes": r["probes"], "passed": r["passed"], "status": r["status"], "hard": r["hard"]}
                    for r in results],
        "waivable": list(WAIVABLE_RULES), "non_waivable": list(HARD_RULES), "fix_only": list(FIX_ONLY_RULES),
        "failing_rules": sorted(set(failing), key=RULES.index), "errored_rules": errored, "unverified_rules": incomplete,
        "not_applicable": [{"probe": p["id"], "reason": p["detail"]} for r in results for p in r["probes"]
                           if p["status"] == "not_applicable"],
        "controls": {"checked": len(controls), "broken": [c for c in broken]},
        "status": status, "exit_code": EXIT[status], "passed": status == "pass" and not partial,
        "claim": RECEIPT_CLAIM, "scratch": scratch_info, "created_at": now,
    }
    return receipts.sign_receipt(receipt)


# ---------------------------------------------------------------- persistence
def commit_receipt(workspace, sid, receipt: dict) -> dict:
    """Store the signed receipt under studio/receipts/<job>/ and anchor its digest in the ledger as an ``event``."""
    kernel = SessionKernel(Path(workspace))
    d = kernel.dir(sid).resolve()
    with session_lock(d):
        from .studio_store import ensure_layout
        root = ensure_layout(d)
        path = receipts.persist_receipt(root, receipt.get("job_id") or "no-job", receipt)
        record = receipts.anchor_receipt(kernel.ledger(sid), receipt)
        return {"path": str(path), "anchor_seq": record["seq"]}


def summary(receipt: dict, *, path: str | None = None) -> dict:
    return {"receipt_id": receipt["receipt_id"], "scope": receipt["scope"], "partial": receipt["partial"],
            "status": receipt["status"], "exit_code": receipt["exit_code"], "job_id": receipt["job_id"],
            "build_digest": receipt["build_digest"], "failing_rules": receipt["failing_rules"],
            "errored_rules": receipt["errored_rules"], "unverified_rules": receipt.get("unverified_rules", []),
            "rules": {r["rule"]: r["status"] for r in receipt["results"]},
            "not_applicable": receipt["not_applicable"], "controls_checked": receipt["controls"]["checked"],
            "ledger_seq_after": receipt["ledger_seq_after"], "claim": receipt["claim"]["text_en"],
            **({"receipt_path": path} if path else {})}

"""OS-level containment for the Python children the runtime executes (verifier runs of author and learner code).

The in-process defences of ``verifiers/python_runner.py`` (AST policy, allowlisted builtins, audit hook) are not a
security boundary: a frame walk can reach the harness globals. Code authored by an opt-in runner (steered, possibly, by
an injected public source) is executed during studio admission and the rules gate, so the child gets an OS boundary
whenever the platform has one:

* macOS: ``sandbox-exec`` (Seatbelt) with a profile that denies the network, exec of anything outside the Python
  installation, every write outside the run directory, and reads of /Users, /Volumes, the temp roots, HOME and the
  state dir (the Python installation and the run directory stay readable);
* Linux: ``bwrap`` (bubblewrap) with a read-only root, no network/pid/ipc namespaces shared, empty tmpfs over the home,
  temp and state roots, and only the run directory writable.

Detection runs a real wrapped interpreter once per process (a nested sandbox or a restricted user namespace can refuse);
without a working wrapper ``mode()`` is ``None`` and callers decide: the verifier keeps its in-process defences, opt-in
runners refuse to dispatch, and R10 reports the gap. ``containment_check`` proves the boundary with a canary file and a
local connection attempt, and a negative control (the same script unwrapped must read the canary and reach the local
network stack: a closed port answers 'refused', a sandboxed child gets a permission or unreachable error instead).
This module imports no network client (it sits in the studio import closure checked by R10).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SEATBELT = "/usr/bin/sandbox-exec"
_MODE: str | None = None
_DETECTED = False


def _real(path) -> str:
    return os.path.realpath(str(path))


def python_roots() -> list[str]:
    """Where the interpreter and its stdlib live (re-allowed even when they sit inside a hidden root)."""
    return sorted({_real(p) for p in (sys.prefix, sys.base_prefix, Path(_real(sys.executable)).parent)})


def private_roots(extra=()) -> list[str]:
    """Locations the child must not read: other users' and the owner's files, temp roots, the state dir."""
    roots = [tempfile.gettempdir(), os.path.expanduser("~"), *extra]
    if os.environ.get("ALEARN_STATE_DIR"):
        roots.append(os.environ["ALEARN_STATE_DIR"])
    roots += ["/Users", "/Volumes", "/private/tmp", "/private/var/folders"] if sys.platform == "darwin" else \
             ["/home", "/root", "/tmp", "/var/tmp", "/run/user"]
    return sorted({_real(p) for p in roots if p})


def seatbelt_profile(run_dir, extra_private=()) -> str:
    """SBPL: later rules win, so each deny is followed by the narrow re-allow it needs."""
    def subpaths(paths):
        return " ".join(f"(subpath {json.dumps(p)})" for p in paths)
    run, py = _real(run_dir), python_roots()
    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny process-exec*)",
        f"(allow process-exec* {subpaths(py)})",           # a framework build's bin/python3 re-spawns Python.app
        "(deny file-write*)",
        f"(allow file-write* {subpaths([run])})",
        '(allow file-write-data (literal "/dev/null"))',
        f"(deny file-read-data {subpaths(private_roots(extra_private))})",
        f"(allow file-read-data {subpaths(py + [run])})",
    ])


def bwrap_prefix(run_dir, extra_private=()) -> list[str]:
    run = _real(run_dir)
    args = [shutil.which("bwrap") or "bwrap", "--die-with-parent", "--new-session", "--unshare-net", "--unshare-pid",
            "--unshare-ipc", "--unshare-uts", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for root in private_roots(extra_private):
        if os.path.isdir(root):
            args += ["--tmpfs", root]
    for root in python_roots():
        args += ["--ro-bind", root, root]                 # mounted after the tmpfs layers, so it shows through
    return args + ["--bind", run, run, "--chdir", run, "--"]


def _wrap_with(kind: str | None, argv: list[str], run_dir, extra_private=()) -> list[str]:
    if kind == "seatbelt":
        return [SEATBELT, "-p", seatbelt_profile(run_dir, extra_private), *argv]
    if kind == "bwrap":
        return [*bwrap_prefix(run_dir, extra_private), *argv]
    return list(argv)


def _candidate() -> str | None:
    if sys.platform == "darwin" and os.access(SEATBELT, os.X_OK):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


def mode() -> str | None:
    """The working wrapper for this process ('seatbelt' / 'bwrap'), or None. Detected once by running a real child."""
    global _MODE, _DETECTED
    if not _DETECTED:
        _MODE, _DETECTED = _detect(_candidate()), True
    return _MODE


def _detect(kind: str | None) -> str | None:
    if kind is None:
        return None
    with tempfile.TemporaryDirectory(prefix="alearn-sbx-") as td:
        try:
            res = subprocess.run(_wrap_with(kind, [sys.executable, "-I", "-S", "-c", "print(1)"], td), cwd=td,
                                 capture_output=True, timeout=20, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
    return kind if res.returncode == 0 and res.stdout.strip() == b"1" else None


def wrap(argv: list[str], run_dir, extra_private=()) -> list[str]:
    """``argv`` inside the OS boundary when one works here; unchanged otherwise (callers check ``mode()``)."""
    kind = mode()
    return _wrap_with(kind, argv, run_dir, extra_private) if kind else list(argv)


_PROBE = r'''
import json, os, socket, sys
canary, outside, port, control = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "control"
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "allowed"
    except Exception as exc:
        out[name] = "denied:" + type(exc).__name__
attempt("read_canary", lambda: open(canary).read())
attempt("list_canary_dir", lambda: os.listdir(os.path.dirname(canary)))
attempt("write_outside", lambda: open(outside, "w").write("x"))
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close()
    out["connect_local"] = "allowed"
except ConnectionRefusedError:
    out["connect_local"] = "refused"          # the network stack answered: not contained
except Exception as exc:
    out["connect_local"] = "denied:" + type(exc).__name__
attempt("write_run_dir", lambda: open("inside.txt", "w").write("x"))
if not control:      # a successful exec replaces this process, so the control (unwrapped) run never tries it
    attempt("exec_shell", lambda: os.execv("/bin/sh", ["sh", "-c", "exit 0"]))
print(json.dumps(out), flush=True)
'''
LOCAL_PORT = 9      # discard: closed on a normal host, so an uncontained connect is 'refused'
MUST_DENY = ("read_canary", "list_canary_dir", "write_outside", "connect_local", "exec_shell")


def _run_probe(kind: str | None, run_dir: str, canary: str, outside: str, port: int) -> dict:
    script = Path(run_dir) / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    argv = _wrap_with(kind, [sys.executable, "-I", "-S", str(script), canary, outside, str(port),
                             "wrapped" if kind else "control"], run_dir)
    try:
        res = subprocess.run(argv, cwd=run_dir, capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {}
    lines = [ln for ln in res.stdout.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
    try:
        return json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {}


def containment_check(kind: str | None = None) -> dict:
    """{'mode', 'contained', 'control_ok', 'wrapped', 'unwrapped'}: the wrapped child must be denied every MUST_DENY
    action while still writing its run dir; the unwrapped control must read the canary (else the check proves nothing).
    A successful exec_shell replaces the child before it reports, so a missing report counts as not contained."""
    kind = mode() if kind is None else kind
    if not kind:
        return {"mode": None, "contained": False, "control_ok": False, "wrapped": {}, "unwrapped": {}}
    with tempfile.TemporaryDirectory(prefix="alearn-canary-") as secret_dir, \
            tempfile.TemporaryDirectory(prefix="alearn-sbx-run-") as run_dir:
        canary = Path(secret_dir) / "canary.txt"
        canary.write_text("alearn-canary", encoding="utf-8")
        port = LOCAL_PORT
        outside = str(Path(secret_dir) / "written.txt")
        wrapped = _run_probe(kind, run_dir, str(canary), outside, port)
        unwrapped = _run_probe(None, run_dir, str(canary), outside + ".control", port)
    contained = bool(wrapped) and all(str(wrapped.get(k, "")).startswith("denied") for k in MUST_DENY) \
        and wrapped.get("write_run_dir") == "allowed"
    control_ok = unwrapped.get("read_canary") == "allowed" and unwrapped.get("connect_local") in ("refused", "allowed")
    return {"mode": kind, "contained": contained, "control_ok": control_ok,
            "wrapped": wrapped, "unwrapped": unwrapped}

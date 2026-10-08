from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import signal
import subprocess
import tempfile
import uuid


def run_capped(cmd, *, timeout, input=None, **popen_kwargs):
    """Like subprocess.run, but on timeout it kills the whole process GROUP.

    `subprocess.run(timeout=...)` only kills the direct child, so a learner's code
    that spawns grandchildren (e.g. `Popen(['sleep', ...])`) leaves them running
    until they exhaust pids_limit. On POSIX we put the child in a new session and
    `killpg` the group on timeout; elsewhere we fall back to plain run.
    Raises subprocess.TimeoutExpired (with captured output) exactly like run().
    """
    if os.name != "posix":
        return subprocess.run(cmd, timeout=timeout, input=input, **popen_kwargs)
    popen_kwargs.setdefault("start_new_session", True)
    if input is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = None, None
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)

# File permissions: learner records may hold stated background / accessibility
# needs, so keep them owner-only.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    # 64-bit of uuid4 entropy: collision ~ negligible at realistic id volumes.
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # best-effort on filesystems without POSIX modes


def write_json(path: Path, obj) -> None:
    """Atomically write JSON: temp file in the same dir + os.replace.

    A crash or a concurrent reader never sees a half-written file. ``ensure_ascii``
    is left False for human-readable output; JSON files are read whole, so line
    separators are irrelevant here (unlike the line-delimited ledger).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, _DIR_MODE)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _chmod(path, _FILE_MODE)
        fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fsync_directory(path: Path) -> None:
    """Persist directory entries after an atomic rename on supported local OSes."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

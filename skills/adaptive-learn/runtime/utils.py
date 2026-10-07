from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import tempfile
import uuid

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
            f.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _chmod(path, _FILE_MODE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

from __future__ import annotations

from pathlib import Path
import re

# Central path / id validation. Every CLI-supplied session id or file path flows
# through here so a prompt-injected agent cannot read or write outside the
# workspace (threat model: LLM builds argv from learner/source text).

SESSION_ID_RE = re.compile(r"^sess_[0-9a-f]{16}$")
MAX_INPUT_FILE_BYTES = 8 * 1024 * 1024  # 8 MiB cap on CLI-provided input files


class UnsafePathError(ValueError):
    """Raised when a supplied id/path would escape its allowed root."""


def valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.fullmatch(session_id or ""))


def safe_session_dir(sessions_dir: Path, session_id: str) -> Path:
    """Resolve ``session_id`` under ``sessions_dir`` or raise.

    Rejects anything that is not a canonical generated id, and defends in depth
    by asserting the resolved path stays inside ``sessions_dir`` (absolute ids,
    ``..`` and symlinks all fail).
    """
    if not valid_session_id(session_id):
        raise UnsafePathError(f"invalid_session_id:{session_id!r}")
    base = sessions_dir.resolve()
    candidate = (sessions_dir / session_id).resolve()
    if candidate != base and base not in candidate.parents:
        raise UnsafePathError(f"session_path_escapes_store:{session_id!r}")
    return candidate


def safe_input_path(workspace: Path, raw: str, *, must_exist: bool = True) -> Path:
    """Confine a CLI-provided input file to ``workspace`` and cap its size."""
    p = Path(raw).expanduser()
    root = workspace.resolve()
    resolved = p.resolve()
    if resolved != root and root not in resolved.parents:
        raise UnsafePathError(f"input_path_outside_workspace:{raw!r}")
    if must_exist:
        if not resolved.is_file():
            raise UnsafePathError(f"input_file_not_found:{raw!r}")
        if resolved.stat().st_size > MAX_INPUT_FILE_BYTES:
            raise UnsafePathError(f"input_file_too_large:{raw!r}")
    return resolved

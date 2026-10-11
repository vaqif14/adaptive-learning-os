"""Out-of-workspace trust root for the evidence ledger.

The ledger is hash-chained, but a plain keyless SHA-256 chain is only tamper-EVIDENT
against naive edits/deletions: a motivated local attacker who edits a record can
recompute every forward hash and the chain still "verifies". Two controls close that,
both stored OUTSIDE any learning workspace so a workspace-only tamperer cannot reach
them:

1. A secret HMAC key — record hashes become HMAC-SHA256(key, body). Without the key an
   attacker cannot produce a valid record hash, so forward re-chaining fails.
2. A per-session trust head — the last (seq, head_hash) is mirrored here. This detects
   truncation/rollback and prevents a downgrade to the legacy keyless scheme (a session
   registered here MUST verify as keyed).

Threat model: an attacker with write access to the session's `.learning` workspace but
NOT to the user's state dir. Full-home compromise is out of scope (they would hold the
key); no purely-local scheme can defend that, and it is not the demonstrated attack.
"""
from __future__ import annotations

import hmac
import json
import os
import re
from hashlib import sha256
from pathlib import Path

from .utils import now_iso

_KEY_CACHE: bytes | None = None
_SID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def state_dir() -> Path:
    """User-level, out-of-workspace state dir (honours ALEARN_STATE_DIR / XDG)."""
    env = os.environ.get("ALEARN_STATE_DIR")
    if env:
        base = Path(env)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".config") / "alearn"
    d = base / "ledger"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _parse_env_key(env: str) -> bytes:
    """Accept a hex-encoded key (even length, hex chars) or raw utf-8 text."""
    s = env.strip()
    if s and len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s):
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    return s.encode("utf-8")


def ledger_key() -> bytes:
    """Secret HMAC key. Priority: ALEARN_LEDGER_KEY env, then a 0600 key file.

    The file is created once with 32 bytes of os.urandom. It lives in the user state
    dir, never inside a learning workspace.
    """
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    env = os.environ.get("ALEARN_LEDGER_KEY")
    if env:
        _KEY_CACHE = _parse_env_key(env)
        return _KEY_CACHE
    kf = state_dir() / "key"
    if kf.exists():
        data = kf.read_bytes()
        if data:
            _KEY_CACHE = data
            return _KEY_CACHE
    key = os.urandom(32)
    fd = os.open(kf, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    try:
        os.chmod(kf, 0o600)
    except OSError:
        pass
    _KEY_CACHE = key
    return key


def record_hmac(key: bytes, record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hmac.new(key, canonical.encode("utf-8"), sha256).hexdigest()


def _head_file(ledger_id: str) -> Path:
    # Keyed by the ledger's absolute path (unique per physical ledger), not the
    # session_id, which is not globally unique (tests reuse ids; two workspaces can
    # share one). The file still lives in the out-of-workspace state dir.
    heads = state_dir() / "heads"
    heads.mkdir(parents=True, exist_ok=True)
    digest = sha256(ledger_id.encode("utf-8")).hexdigest()[:40]
    return heads / f"{digest}.json"


def has_head(ledger_id: str) -> bool:
    return _head_file(ledger_id).exists()


def read_head(ledger_id: str) -> dict | None:
    p = _head_file(ledger_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_head(ledger_id: str, seq: int, head_hash: str, session_id: str | None = None) -> None:
    p = _head_file(ledger_id)
    payload = {"ledger_id": ledger_id, "session_id": session_id, "algo": "hmac-sha256",
               "seq": seq, "head": head_hash, "updated": now_iso()}
    fd = os.open(p, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))
        f.flush()
        os.fsync(f.fileno())

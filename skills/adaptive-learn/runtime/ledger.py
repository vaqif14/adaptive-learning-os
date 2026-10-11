from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os

from .utils import now_iso, new_id, fsync_directory
from .session_lock import session_lock
from . import ledger_trust

try:
    import fcntl  # POSIX advisory locking (macOS/Linux)
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    _HAVE_FCNTL = False

ALLOWED_RECORD_TYPES = {"event", "observation", "evidence", "decision", "hypothesis"}
LEDGER_SCHEMA_VERSION = 1


def _record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class EventLedger:
    path: Path
    session_id: str

    # --- integrity regime ------------------------------------------------
    def _lid(self) -> str:
        # Stable, canonical ledger identity for the out-of-workspace trust head.
        # realpath (not abspath) so symlinked temp roots (/var -> /private/var on macOS)
        # and resolved vs unresolved callers map to ONE identity — otherwise the write
        # path and a resolved read path would key different heads.
        return os.path.realpath(str(self.path))

    def _is_keyed(self, has_data: bool) -> bool:
        # Keyed if this session has an out-of-workspace trust head (registered keyed)
        # or is brand new (no prior data) — new chains are always keyed (HMAC + head).
        # Pre-existing keyless ledgers with no trust head stay legacy so old history
        # still verifies; they simply retain the weaker (tamper-evident-only) guarantee.
        return ledger_trust.has_head(self._lid()) or not has_data

    def _expected_hash(self, record: dict, keyed: bool) -> str:
        if keyed:
            return ledger_trust.record_hmac(ledger_trust.ledger_key(), record)
        return _record_hash(record)

    # --- append ----------------------------------------------------------
    def append(self, record_type: str, payload: dict, provenance: dict | None = None) -> dict:
        with session_lock(self.path.parent):
            return self._append(record_type, payload, provenance)

    def _append(self, record_type: str, payload: dict, provenance: dict | None = None) -> dict:
        if record_type not in ALLOWED_RECORD_TYPES:
            raise ValueError(f"unsupported record_type: {record_type}")
        if not isinstance(payload, dict) or (provenance is not None and not isinstance(provenance, dict)):
            raise ValueError("ledger payload and provenance must be objects")
        # Reject non-JSON/non-finite values before touching persistent state.
        json.dumps([payload, provenance], allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+b") as f:
            if _HAVE_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                data = f.read()
                verified, report = self._scan(data)
                if not report["chain_ok"]:
                    raise ValueError("ledger integrity broken; preserve and repair the journal before recording new results")
                keyed = self._is_keyed(bool(data.strip()))
                last = verified[-1] if verified else None
                seq = (last["seq"] + 1) if last else 0
                prev_hash = last["record_hash"] if last else "genesis"
                record = {
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "record_id": new_id(record_type[:3]),
                    "session_id": self.session_id,
                    "seq": seq,
                    "timestamp": now_iso(),
                    "record_type": record_type,
                    "payload": payload,
                    "provenance": provenance or {"source": "runtime"},
                    "prev_hash": prev_hash,
                }
                record["record_hash"] = self._expected_hash(record, keyed)
                # Newline guard: never glue a new record onto a torn last line.
                f.seek(0, os.SEEK_END)
                if f.tell() > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
                f.seek(0, os.SEEK_END)
                # ensure_ascii=True so U+2028/U+0085 etc. cannot inject a line break.
                line = json.dumps(record, ensure_ascii=True) + "\n"
                f.write(line.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            finally:
                if _HAVE_FCNTL:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        fsync_directory(self.path.parent)
        if keyed:
            # Mirror the new head out-of-workspace so truncation/rollback/downgrade are
            # detectable even by a workspace-only attacker.
            ledger_trust.write_head(self._lid(), record["seq"], record["record_hash"], self.session_id)
        return record

    # --- read ------------------------------------------------------------
    def _iter_raw(self):
        if not self.path.exists():
            return
        # Split on "\n" only (never splitlines) so exotic unicode line separators
        # inside a value cannot fragment a record.
        for line in self.path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                yield line

    def records(self) -> list[dict]:
        """Return parseable records, skipping (not crashing on) any torn line."""
        out: list[dict] = []
        for line in self._iter_raw():
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    out.append(rec)
            except json.JSONDecodeError:
                continue  # quarantine; see integrity() for a full report
        return out

    def _scan(self, data: bytes):
        """Verify shape, identity, sequence and hash together on one snapshot.

        A session registered with an out-of-workspace trust head is verified in KEYED
        mode: every record hash must be a valid HMAC (an attacker without the secret key
        cannot re-chain forward), and the final record must match the trust head (so
        truncation/rollback and any downgrade to the keyless scheme are caught). Legacy
        keyless ledgers (no trust head) keep the weaker sha256 verification.
        """
        keyed = ledger_trust.has_head(self._lid())
        verified, good, corrupt = [], 0, 0
        prev, broken_at = "genesis", None
        last_rec = None
        n_lines = 0
        for i, line in enumerate(part for part in data.split(b"\n") if part.strip()):
            n_lines = i + 1
            try:
                rec = json.loads(line)
                shape_ok = (isinstance(rec, dict)
                    and isinstance(rec.get("payload"), dict)
                    and isinstance(rec.get("provenance"), dict)
                    and isinstance(rec.get("record_id"), str)
                    and isinstance(rec.get("timestamp"), str)
                    and rec.get("record_type") in ALLOWED_RECORD_TYPES
                    and rec.get("schema_version") == LEDGER_SCHEMA_VERSION
                    and type(rec.get("seq")) is int)
                if not shape_ok:
                    raise ValueError("invalid ledger record shape")
                good += 1
                valid = (rec["session_id"] == self.session_id and rec["seq"] == i
                         and rec.get("prev_hash") == prev
                         and rec.get("record_hash") == self._expected_hash(rec, keyed))
                if not valid and broken_at is None:
                    broken_at = i
                if broken_at is None:
                    verified.append(rec)
                    last_rec = rec
                prev = rec.get("record_hash")
            except (ValueError, TypeError, KeyError, UnicodeError):
                corrupt += 1
                if broken_at is None:
                    broken_at = i
        head_ok = True
        if keyed and broken_at is None:
            head = ledger_trust.read_head(self._lid())
            if head is not None:
                actual_seq = last_rec.get("seq") if last_rec else -1
                actual_head = last_rec.get("record_hash") if last_rec else None
                if actual_seq != head.get("seq") or actual_head != head.get("head"):
                    head_ok = False
                    broken_at = n_lines  # truncation/rollback: stored head not reached
        return verified, {"records": good, "corrupt_lines": corrupt,
                          "chain_ok": broken_at is None, "broken_at_line": broken_at,
                          "keyed": keyed, "head_ok": head_ok}

    def _snapshot(self):
        with session_lock(self.path.parent):
            return self.path.read_bytes() if self.path.exists() else b""

    def verified_records(self) -> list[dict]:
        """Only the valid prefix of this session's journal may drive decisions."""
        return self._scan(self._snapshot())[0]

    def integrity(self) -> dict:
        return self._scan(self._snapshot())[1]

    def require_integrity(self):
        if not self.integrity()["chain_ok"]:
            raise ValueError("ledger integrity broken; preserve and repair the journal before continuing")

    def adopt_trust(self) -> bool:
        """Re-establish the out-of-workspace trust head at THIS path after a relocation
        (e.g. restore from backup), but only if every record authentically verifies as
        HMAC under the user key. Because an attacker cannot forge HMACs without the key,
        a fully HMAC-consistent chain is genuine and safe to re-register; a keyless
        (legacy) or tampered chain is left unregistered (no head written).
        """
        data = self.path.read_bytes() if self.path.exists() else b""
        recs = [part for part in data.split(b"\n") if part.strip()]
        if not recs:
            return False
        key = ledger_trust.ledger_key()
        prev, last = "genesis", None
        for i, line in enumerate(recs):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False
            if (not isinstance(rec, dict) or rec.get("session_id") != self.session_id
                    or rec.get("seq") != i or rec.get("prev_hash") != prev
                    or rec.get("record_hash") != ledger_trust.record_hmac(key, rec)):
                return False
            prev, last = rec.get("record_hash"), rec
        ledger_trust.write_head(self._lid(), last["seq"], last["record_hash"], self.session_id)
        return True

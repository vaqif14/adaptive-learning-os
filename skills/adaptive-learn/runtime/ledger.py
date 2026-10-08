from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os

from .utils import now_iso, new_id

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

    # --- append ----------------------------------------------------------
    def append(self, record_type: str, payload: dict, provenance: dict | None = None) -> dict:
        if record_type not in ALLOWED_RECORD_TYPES:
            raise ValueError(f"unsupported record_type: {record_type}")
        if not isinstance(payload, dict) or (provenance is not None and not isinstance(provenance, dict)):
            raise ValueError("ledger payload and provenance must be objects")
        # Reject non-JSON/non-finite values before touching persistent state.
        json.dumps([payload, provenance], allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+b") as f:
            if _HAVE_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                verified, report = self._scan(f.read())
                if not report["chain_ok"]:
                    raise ValueError("ledger integrity broken; preserve and repair the journal before recording new results")
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
                record["record_hash"] = _record_hash(record)
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
        """Verify shape, identity, sequence and hash together on one snapshot."""
        verified, good, corrupt = [], 0, 0
        prev, broken_at = "genesis", None
        for i, line in enumerate(part for part in data.split(b"\n") if part.strip()):
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
                         and rec.get("record_hash") == _record_hash(rec))
                if not valid and broken_at is None:
                    broken_at = i
                if broken_at is None:
                    verified.append(rec)
                prev = rec.get("record_hash")
            except (ValueError, TypeError, KeyError, UnicodeError):
                corrupt += 1
                if broken_at is None:
                    broken_at = i
        return verified, {"records": good, "corrupt_lines": corrupt,
                          "chain_ok": broken_at is None, "broken_at_line": broken_at}

    def _snapshot(self):
        return self.path.read_bytes() if self.path.exists() else b""

    def verified_records(self) -> list[dict]:
        """Only the valid prefix of this session's journal may drive decisions."""
        return self._scan(self._snapshot())[0]

    def integrity(self) -> dict:
        return self._scan(self._snapshot())[1]

    def require_integrity(self):
        if not self.integrity()["chain_ok"]:
            raise ValueError("ledger integrity broken; preserve and repair the journal before continuing")

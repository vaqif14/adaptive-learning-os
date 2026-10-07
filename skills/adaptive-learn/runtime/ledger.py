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
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class EventLedger:
    path: Path
    session_id: str

    # --- append ----------------------------------------------------------
    def append(self, record_type: str, payload: dict, provenance: dict | None = None) -> dict:
        if record_type not in ALLOWED_RECORD_TYPES:
            raise ValueError(f"unsupported record_type: {record_type}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+b") as f:
            if _HAVE_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                last = self._last_valid_record(f)
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
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # quarantine; see integrity() for a full report
        return out

    def _last_valid_record(self, f) -> dict | None:
        f.seek(0)
        data = f.read()
        if not data:
            return None
        for chunk in reversed(data.split(b"\n")):
            if not chunk.strip():
                continue
            try:
                return json.loads(chunk.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return None

    def verified_records(self) -> list[dict]:
        """Records whose hash chain is intact, up to the first break.

        Consumers that make trust decisions (projection, mastery, review) must use
        THIS, not records(): a record that still parses but was edited in place
        (its stored hash no longer matches, or prev_hash diverges) is dropped along
        with everything after it, so tampered evidence cannot drive mastery.
        """
        out: list[dict] = []
        prev = "genesis"
        for line in self._iter_raw():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                break  # torn/garbage line: stop trusting the tail
            if rec.get("prev_hash") != prev or rec.get("record_hash") != _record_hash(rec):
                break  # in-place edit or reordering: chain broken here
            out.append(rec)
            prev = rec.get("record_hash", prev)
        return out

    def integrity(self) -> dict:
        """Verify the hash chain; report corrupt lines without raising."""
        good = 0
        corrupt = 0
        chain_ok = True
        broken_at = None
        prev = "genesis"
        for i, line in enumerate(self._iter_raw()):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                chain_ok = False
                if broken_at is None:
                    broken_at = i
                continue
            good += 1
            if rec.get("prev_hash") != prev or rec.get("record_hash") != _record_hash(rec):
                chain_ok = False
                if broken_at is None:
                    broken_at = i
            prev = rec.get("record_hash", prev)
        return {
            "records": good,
            "corrupt_lines": corrupt,
            "chain_ok": chain_ok and corrupt == 0,
            "broken_at_line": broken_at,
        }

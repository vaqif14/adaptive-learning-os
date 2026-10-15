"""Course Studio storage: per-session layout, atomic private writes, hash-chained events.

Layout (course-studio.md section 4), all under ``<session>/studio/`` with directories 0700
and files 0600. Nothing here removes anything: superseding a document means renaming it
into ``archive/``. The event log is append-only and hash-chained; its head is anchored in
the session ledger as an ``event`` record (never an evidence or observation record), so
edits and truncation of the log are detectable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .course_state import CourseStateError
from .utils import fsync_directory, read_json as _read_json, write_bytes, write_json

STUDIO_SCHEMA = 1
SUBDIRS = ("jobs", "orders", "inbox", "staging", "receipts", "handover", "scratch", "archive")
EVENTS_FILE = "events.jsonl"
EVENTS_HEAD_FILE = "events.head.json"
NOTIFICATIONS_FILE = "notifications.jsonl"
IDEMPOTENCY_FILE = "idempotency.json"
ANCHOR_EVENT = "studio_events_anchor"
GENESIS = "genesis"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class StudioError(CourseStateError):
    """A refused studio operation. ``code`` is stable; ``detail`` explains it."""

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.detail or self.code, "detail": self.detail}


# ------------------------------------------------------------------ hashing
def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_of(obj) -> str:
    return sha256_hex(canonical(obj))


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")


def safe_name(name: str) -> str:
    """Ids that become path components must be plain names (no separators, no dots-only)."""
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or name in {".", ".."}:
        raise StudioError("studio_invalid_name", "unsafe identifier")
    return name


# ------------------------------------------------------------------- layout
def studio_dir(session_dir: Path) -> Path:
    return Path(session_dir) / "studio"


def secure_mkdir(path: Path, root: Path | None = None) -> Path:
    """Create ``path`` and every missing parent with mode 0700 (``root`` is informational)."""
    path = Path(path)
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for directory in reversed(missing):
        directory.mkdir(mode=_DIR_MODE, exist_ok=True)
        _chmod(directory, _DIR_MODE)
    if path.is_symlink() or not path.is_dir():
        raise StudioError("studio_path_unsafe", "studio paths must be real directories")
    return path


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # best effort on filesystems without POSIX modes


def ensure_layout(session_dir: Path) -> Path:
    root = secure_mkdir(studio_dir(session_dir))
    for name in SUBDIRS:
        secure_mkdir(root / name, root)
    return root


def child(root: Path, *parts: str) -> Path:
    """A path under ``root`` built only from validated components."""
    out = Path(root)
    for part in parts:
        out = out / safe_name(part)
    return out


# --------------------------------------------------------------------- io
def write_atomic_json(path: Path, obj, *, root: Path | None = None) -> None:
    secure_mkdir(Path(path).parent, root or Path(path).parent)
    write_json(path, obj)


def write_atomic_bytes(path: Path, data: bytes, *, root: Path | None = None) -> None:
    secure_mkdir(Path(path).parent, root or Path(path).parent)
    write_bytes(path, data)


def read_json(path: Path):
    if Path(path).is_symlink():
        raise StudioError("studio_path_unsafe", "refusing to follow a symlink")
    return _read_json(path)


def archive_rename(root: Path, path: Path, *, now: datetime | None = None, sub: str | None = None) -> Path:
    """Move ``path`` into ``<root>/archive/[<sub>/]<utc-stamp>-<name>``. The data is kept, never removed."""
    root, path = Path(root), Path(path)
    if not path.exists() and not path.is_symlink():
        raise StudioError("studio_archive_missing", "nothing to archive")
    archive = secure_mkdir(root / "archive", root)
    if sub:
        archive = secure_mkdir(archive / sub, root)
    stamp = utc_stamp(now)
    target = archive / f"{stamp}-{path.name}"
    counter = 0
    while target.exists():
        counter += 1
        target = archive / f"{stamp}-{counter}-{path.name}"
    os.rename(path, target)
    fsync_directory(archive)
    fsync_directory(path.parent)
    return target


# ------------------------------------------------------------ event chain
def _event_hash(event: dict) -> str:
    return digest_of({k: v for k, v in event.items() if k != "hash"})


class EventLog:
    """Append-only hash-chained studio events. Callers hold the session lock."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / EVENTS_FILE
        self.head_path = self.root / EVENTS_HEAD_FILE

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text = text[:text.rindex("\n") + 1] if "\n" in text else ""   # an unterminated tail is a torn write
        return [line for line in text.split("\n") if line.strip()]

    def _repair_torn_tail(self) -> None:
        """Keep an unterminated final fragment in ``archive/`` and cut the log back to its last full line."""
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return
        keep = raw[:raw.rindex(b"\n") + 1] if b"\n" in raw else b""
        archive = secure_mkdir(self.root / "archive", self.root)
        counter = 0
        while (archive / f"events-torn-{counter}.jsonl").exists():
            counter += 1
        write_atomic_bytes(archive / f"events-torn-{counter}.jsonl", raw[len(keep):], root=self.root)
        fd = os.open(self.path, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            os.ftruncate(fd, len(keep))
            os.fsync(fd)
        finally:
            os.close(fd)

    def read(self) -> list[dict]:
        out = []
        for line in self._lines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                break
            if isinstance(event, dict):
                out.append(event)
        return out

    def head(self) -> dict:
        events = self.read()
        if not events:
            return {"seq": -1, "hash": GENESIS}
        return {"seq": events[-1].get("seq"), "hash": events[-1].get("hash")}

    def append(self, event_type: str, job_id: str | None, data: dict | None, at: str) -> dict:
        self._repair_torn_tail()
        report = self.verify()
        if not report["ok"]:
            raise StudioError("studio_events_corrupt", report["error"])
        head = self.head()
        event = {"seq": head["seq"] + 1, "at": at, "type": event_type, "job_id": job_id, "data": data or {},
                 "prev_hash": head["hash"]}
        event["hash"] = _event_hash(event)
        secure_mkdir(self.root)
        line = (json.dumps(event, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, _FILE_MODE)
        with os.fdopen(fd, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        _chmod(self.path, _FILE_MODE)
        fsync_directory(self.root)
        write_json(self.head_path, {"seq": event["seq"], "hash": event["hash"]})
        return event

    def verify(self, led=None) -> dict:
        """Check the chain, the sidecar head and (when ``led`` is given) every ledger anchor.

        Edits break a hash link; truncation leaves the log shorter than the sidecar head or
        the latest ledger anchor. Returns ``{"ok", "count", "head_seq", "head_hash", "error"}``."""
        events, prev = [], GENESIS
        for index, line in enumerate(self._lines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return _report(False, events, f"unparseable event line {index}")
            shape = (isinstance(event, dict) and event.get("seq") == index and event.get("prev_hash") == prev
                     and isinstance(event.get("type"), str) and isinstance(event.get("data"), dict)
                     and isinstance(event.get("at"), str))
            if not shape:
                return _report(False, events, f"event {index} breaks the chain")
            if event.get("hash") != _event_hash(event):
                return _report(False, events, f"event {index} was edited")
            events.append(event)
            prev = event["hash"]
        by_seq = {e["seq"]: e["hash"] for e in events}
        if self.head_path.exists():
            try:
                side = _read_json(self.head_path)
            except (OSError, ValueError):
                return _report(False, events, "events head file unreadable")
            if by_seq.get(side.get("seq")) != side.get("hash"):
                return _report(False, events, "events log is shorter than its recorded head (truncated)")
        elif events:
            return _report(False, events, "events head file missing")
        if led is not None:
            for record in led.verified_records():
                payload = record["payload"]
                if payload.get("event_type") == ANCHOR_EVENT and record["provenance"].get("source") == "studio":
                    if by_seq.get(payload.get("events_seq")) != payload.get("events_head"):
                        return _report(False, events, f"events log does not reach ledger anchor {payload.get('events_seq')}")
        return _report(True, events, None)


def _report(ok: bool, events: list, error: str | None) -> dict:
    return {"ok": ok, "count": len(events), "head_seq": events[-1]["seq"] if events else -1,
            "head_hash": events[-1]["hash"] if events else GENESIS, "error": error}


def verify_events(session_dir: Path, led=None) -> dict:
    return EventLog(studio_dir(session_dir)).verify(led)


def anchor(led, job_id: str | None) -> dict:
    """Append a ledger ``event`` pinning the studio event head (provenance source ``studio``).

    The record is neither evidence nor observation: it only lets later verification prove the
    studio event log was not edited or truncated."""
    log = EventLog(studio_dir(led.path.parent))
    report = log.verify(led)
    if not report["ok"]:
        raise StudioError("studio_events_corrupt", report["error"])
    return led.append("event", {"event_type": ANCHOR_EVENT, "job_id": job_id, "events_seq": report["head_seq"],
                                "events_head": report["head_hash"]}, {"source": "studio"})


def notify(root: Path, kind: str, job_id: str | None, message: str, at: str) -> None:
    """Pull notification (appended; the owner UI/CLI reads it, nothing is pushed)."""
    secure_mkdir(root)
    line = json.dumps({"at": at, "kind": kind, "job_id": job_id, "message": message}, sort_keys=True,
                      ensure_ascii=True) + "\n"
    fd = os.open(Path(root) / NOTIFICATIONS_FILE, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, _FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())

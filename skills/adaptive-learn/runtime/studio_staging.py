"""Submission intake and the non-servable staging area.

Only ``<inbox>/<job>/<order>/out/result.json`` (or a payload handed in directly by the inline
CLI) is ever read from an author. It is size-capped, parsed strictly (no NaN/Infinity, no
duplicate keys, objects only) and then run through the dry validator by the caller. Validated
payloads are written to ``studio/staging/<job>/<unit>/<sha256>.json`` (0600) - a folder no
learner route, static server or export reads. Staged files are content-addressed and never
overwritten or removed.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .studio_orders import RESULT_FILE, unit_dirname
from .studio_store import StudioError, child, digest_of, read_json, write_atomic_json


def _no_constants(token):
    raise ValueError(f"non-finite number {token} is not allowed")


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


MAX_DEPTH = 32   # real payloads nest < 10 levels; deeper input is hostile and would exhaust the recursion limit


def _too_deep(value, limit: int = MAX_DEPTH) -> bool:
    """Iterative nesting check (a recursive one would itself be the attack surface)."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, (dict, list)):
            if depth > limit:
                return True
            stack.extend((child_, depth + 1) for child_ in (node.values() if isinstance(node, dict) else node))
    return False


def parse_strict(raw, max_bytes: int):
    """(payload, errors). ``raw`` is a dict, str or bytes; sizes are measured on the serialized form."""
    if isinstance(raw, dict):
        try:
            data = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as e:
            return None, [f"payload is not valid JSON data: {str(e)[:200]}"]
        if len(data) > max_bytes:
            return None, [f"output_too_large: {len(data)} bytes exceeds the {max_bytes} byte cap"]
        parsed = json.loads(data)
        if _too_deep(parsed):
            return None, [f"malformed_json: nesting deeper than {MAX_DEPTH} levels"]
        return parsed, []
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        return None, ["payload must be a JSON object"]
    if len(raw) > max_bytes:
        return None, [f"output_too_large: {len(raw)} bytes exceeds the {max_bytes} byte cap"]
    try:
        text = bytes(raw).decode("utf-8")
        payload = json.loads(text, parse_constant=_no_constants, object_pairs_hook=_no_duplicates)
    except (UnicodeError, ValueError, RecursionError) as e:
        return None, [f"malformed_json: {str(e)[:200]}"]
    if not isinstance(payload, dict):
        return None, ["malformed_json: the top level must be a JSON object"]
    if _too_deep(payload):
        return None, [f"malformed_json: nesting deeper than {MAX_DEPTH} levels"]
    return payload, []


def read_result(root: Path, job_id: str, order_id: str, max_bytes: int) -> tuple[bytes | None, list[str]]:
    """Read only ``out/result.json`` of the order inbox: a regular file, never through a symlink."""
    inbox = child(root / "inbox", job_id, order_id)
    try:
        if inbox.resolve(strict=True).parent.parent != (root / "inbox").resolve(strict=True):
            return None, ["result_unsafe: the order folder is not under the inbox"]
    except (OSError, RuntimeError):
        return None, ["result_missing: the order has no out/ folder"]
    parts = RESULT_FILE.split("/")
    folder = inbox
    for part in parts[:-1]:
        folder = folder / part
        if folder.is_symlink() or not folder.is_dir():
            return None, ["result_missing: the order has no out/ folder"]
    target = folder / parts[-1]
    if target.is_symlink():
        return None, ["result_unsafe: result.json must be a regular file"]
    try:
        # O_NONBLOCK: opening a FIFO would otherwise block forever while the session lock is held.
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ["result_missing: write the payload to out/result.json"]
    except OSError:
        return None, ["result_unsafe: result.json could not be opened as a regular file"]
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            return None, ["result_unsafe: result.json must be a regular, unlinked file"]
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(max_bytes + 1), []
    except OSError as e:
        return None, [f"result_unsafe: result.json could not be read ({e.__class__.__name__})"]
    finally:
        os.close(fd)


def staged_path(root: Path, job_id: str, unit_id: str, digest: str) -> Path:
    return child(root / "staging", job_id, unit_dirname(unit_id), digest + ".json")


def stage_payload(root: Path, job_id: str, unit_id: str, payload: dict) -> str:
    """Write the payload content-addressed; returns its digest. Idempotent for identical content."""
    digest = digest_of(payload)
    path = staged_path(root, job_id, unit_id, digest)
    if not path.exists():
        write_atomic_json(path, payload, root=root)
    return digest


def read_staged(root: Path, job_id: str, unit_id: str, digest: str) -> dict:
    path = staged_path(root, job_id, unit_id, digest)
    if not path.is_file():
        raise StudioError("studio_staged_missing", f"{unit_id} {digest[:12]}")
    payload = read_json(path)
    if digest_of(payload) != digest:
        raise StudioError("studio_staged_tampered", f"{unit_id} {digest[:12]}")
    return payload

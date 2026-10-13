"""Source preparation through the installed NotebookLM CLI (no separate API).

Only commands observed in the installed CLI's own ``--help`` are used
(``notebooklm`` 0.7.x: ``create``, ``source add|list|get|fulltext|wait|stale``,
``ask``, ``auth check``). Every resource the course expects is verified
individually — identity, import readiness and actually retrievable text — and
the outcome is recorded in a per-resource manifest; nothing is silently
dropped. Retrieved text is untrusted data: it is hashed, cached privately and
substring-matched for grounding, never interpreted as instructions. Raw CLI
diagnostics are never persisted or echoed (they can contain auth state).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .course_state import digest
from .notebook_cli import provider as _provider
from .safety import protected_session_path
from .utils import now_iso

MIN_CONTENT_CHARS = 400
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,79}")
_STATUSES_OK = {"validated"}
RESOURCE_STATUSES = ("validated", "missing", "failed_import", "processing",
                     "unreadable", "empty", "partial", "identity_mismatch", "cli_unavailable")
# Commands this adapter knows how to call per provider. ``nlm`` is only verified
# for listing in this repository, so authoring operations stay unsupported there.
_KNOWN = {
    "notebooklm": {"top": ("list", "create", "ask", "auth", "source", "metadata"),
                   "source": ("add", "list", "get", "fulltext", "wait", "stale")},
    "nlm": {"top": ("notebook", "source"), "source": ("list",)},
}


def _invoke(name: str, argv: list[str], *, timeout: float = 60) -> dict:
    """Run the CLI; return status + parsed JSON (also on documented non-zero exits)."""
    name, executable = _provider(name)
    if executable is None:
        return {"status": "unavailable", "provider": name, "exit_code": None, "data": None}
    try:
        proc = subprocess.run([executable, *argv], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "provider": name, "exit_code": None, "data": None}
    except OSError:
        return {"status": "unavailable", "provider": name, "exit_code": None, "data": None}
    data = _parse_json(proc.stdout)
    if proc.returncode == 0 and data is not None:
        status = "ready"
    else:
        blob = (proc.stdout + proc.stderr).lower()
        auth = any(w in blob for w in ("login", "auth", "cookie", "expired", "unauthenticated"))
        status = "authentication_required" if auth and proc.returncode else (
            "cli_error" if proc.returncode else "invalid_response")
    return {"status": status, "provider": name, "exit_code": proc.returncode, "data": data}


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener in ("{", "["):
        i = text.find(opener)
        if i >= 0:
            try:
                return json.loads(text[i:])
            except ValueError:
                continue
    return None


def _plain(name: str, argv: list[str]) -> str | None:
    _, executable = _provider(name)
    if executable is None:
        return None
    try:
        proc = subprocess.run([executable, *argv], capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def capabilities(name: str = "notebooklm") -> dict:
    """Inspect the installed CLI: executable, version, help-listed commands, auth status.

    Auth reports only the CLI's coarse status and boolean checks — never cookie
    names, storage paths or tokens.
    """
    name, executable = _provider(name)
    out = {"provider": name, "executable_found": executable is not None, "version": None,
           "commands": {}, "source_commands": {}, "auth": {"status": "unknown"}, "supported_operations": []}
    if executable is None:
        out["action"] = "Install the NotebookLM CLI (notebooklm-py) and run `notebooklm login`."
        return out
    version = _plain(name, ["--version"]) or ""
    m = re.search(r"version\s+([0-9][\w.\-]*)", version)
    out["version"] = m.group(1) if m else None
    top_help = _plain(name, ["--help"]) or ""
    src_help = _plain(name, ["source", "--help"]) or ""
    known = _KNOWN.get(name, {"top": (), "source": ()})
    out["commands"] = {c: bool(re.search(rf"^\s+{c}\b", top_help, re.MULTILINE)) for c in known["top"]}
    out["source_commands"] = {c: bool(re.search(rf"^\s+{c}\b", src_help, re.MULTILINE)) for c in known["source"]}
    if name == "notebooklm" and out["commands"].get("auth"):
        res = _invoke(name, ["auth", "check", "--json"], timeout=20)
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
        out["auth"] = {"status": data.get("status") if isinstance(data.get("status"), str) else res["status"],
                       "checks": {k: v for k, v in checks.items() if isinstance(v, bool)},
                       "scope": "local_credentials_check_only"}
    ops = []
    if out["commands"].get("list"):
        ops.append("list_notebooks")
    if name == "notebooklm":
        if out["commands"].get("create"):
            ops.append("create_notebook")
        for op, cmd in (("add_source", "add"), ("list_sources", "list"), ("get_source", "get"),
                        ("fulltext", "fulltext"), ("wait_source", "wait"), ("stale_check", "stale")):
            if out["source_commands"].get(cmd):
                ops.append(op)
        if out["commands"].get("ask"):
            ops.append("ask")
    elif out["source_commands"].get("list"):
        ops.append("list_sources")
    out["supported_operations"] = ops
    return out


def _require(caps: dict, op: str) -> None:
    if op not in caps.get("supported_operations", []):
        raise ValueError(f"operation {op} is not supported by the installed {caps.get('provider')} CLI")


def _nid(notebook_id: str) -> str:
    return str(uuid.UUID(notebook_id))


def _sid(source_id: str) -> str:
    if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("invalid source id")
    return source_id


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"https", "http"} or not parts.netloc:
        raise ValueError("source URL must be http(s)")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def create_notebook(title: str, caps: dict) -> dict:
    _require(caps, "create_notebook")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("notebook title required (<=200 chars)")
    # No --use: never change the CLI's global active notebook for other sessions.
    res = _invoke(caps["provider"], ["create", "--json", "--", title.strip()])
    nb = (res.get("data") or {}).get("notebook") if isinstance(res.get("data"), dict) else None
    if res["status"] != "ready" or not isinstance(nb, dict):
        return {"status": res["status"], "notebook": None}
    return {"status": "created", "notebook": {"id": _nid(nb.get("id", "")), "title": nb.get("title")}}


def add_source(notebook_id: str, resource: dict, caps: dict, *, workspace: Path | None = None) -> dict:
    """Add one authorized resource. Files require an explicit learner authorization flag."""
    _require(caps, "add_source")
    nid = _nid(notebook_id)
    kind = resource.get("kind")
    if kind == "url":
        locator = normalize_url(resource["locator"])
        argv = ["source", "add", "-n", nid, "--type", "url", "--json", "--", locator]
    elif kind == "file":
        if resource.get("upload_authorized_by_learner") is not True:
            raise ValueError("file upload needs the learner's explicit authorization for this private notebook")
        path = Path(resource["locator"]).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError("file source must be a regular, non-symlink file")
        if workspace is not None and not path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("file source must live inside the learning workspace")
        if workspace is not None and protected_session_path(workspace, path.resolve()):
            raise ValueError("file source must not come from a session store (ledger, vault, lessons)")
        argv = ["source", "add", "-n", nid, "--type", "file", "--json"]
        if resource.get("title"):
            argv += ["--title", str(resource["title"])[:200]]
        argv += ["--", str(path.resolve())]
    else:
        raise ValueError("resource kind must be url or file")
    res = _invoke(caps["provider"], argv, timeout=120)
    src = (res.get("data") or {}).get("source") if isinstance(res.get("data"), dict) else None
    if res["status"] != "ready" or not isinstance(src, dict) or not src.get("id"):
        return {"status": "failed_import", "cli_status": res["status"], "source": None}
    return {"status": "added", "source": {"id": _sid(src["id"]), "title": src.get("title"),
                                          "type": src.get("type"), "url": src.get("url")}}


def list_notebook_sources(notebook_id: str, caps: dict) -> dict:
    _require(caps, "list_sources")
    res = _invoke(caps["provider"], ["source", "list", "-n", _nid(notebook_id), "--json"])
    data = res.get("data")
    rows = data.get("sources") if isinstance(data, dict) else data
    if res["status"] != "ready" or not isinstance(rows, list):
        return {"status": res["status"], "sources": []}
    out = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and _SOURCE_ID.fullmatch(row["id"]):
            out.append({"id": row["id"], "title": row.get("title"), "type": row.get("type"),
                        "url": row.get("url"), "status": row.get("status") or "unknown"})
    return {"status": "ready", "sources": out}


def _source_call(caps, op, verb, notebook_id, source_id, extra=(), timeout=60):
    _require(caps, op)
    return _invoke(caps["provider"], ["source", verb, "-n", _nid(notebook_id), "--json", *extra, "--", _sid(source_id)],
                   timeout=timeout)


def wait_source(notebook_id, source_id, caps, timeout_s: int = 120) -> str:
    res = _source_call(caps, "wait_source", "wait", notebook_id, source_id,
                       ("--timeout", str(int(timeout_s))), timeout=timeout_s + 30)
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    status = data.get("status")
    return status if status in {"ready", "error", "timeout", "not_found"} else res["status"]


def fetch_fulltext(notebook_id, source_id, caps) -> dict:
    res = _source_call(caps, "fulltext", "fulltext", notebook_id, source_id, timeout=120)
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    content = data.get("content")
    if res["status"] != "ready" or not isinstance(content, str):
        return {"status": "unreadable", "cli_status": res["status"]}
    return {"status": "ok", "content": content, "title": data.get("title"), "url": data.get("url")}


def stale_check(notebook_id, source_id, caps) -> bool | None:
    if "stale_check" not in caps.get("supported_operations", []):
        return None
    res = _source_call(caps, "stale_check", "stale", notebook_id, source_id)
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    return data.get("stale") if isinstance(data.get("stale"), bool) else None


def _matches(expected: dict, row: dict) -> bool:
    if expected.get("source_id") and row["id"] == expected["source_id"]:
        return True
    if expected.get("kind") == "url" and row.get("url"):
        try:
            return normalize_url(row["url"]) == normalize_url(expected["locator"])
        except ValueError:
            return False
    if expected.get("kind") == "file" and expected.get("title"):
        return (row.get("title") or "").strip().casefold() == expected["title"].strip().casefold()
    return False


def validate_resources(notebook_id: str, expected: list[dict], caps: dict, cache_dir: Path, *,
                       min_chars: int = MIN_CONTENT_CHARS, wait_timeout: int = 90) -> dict:
    """Verify each expected resource on its own; record every failure explicitly."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if "list_sources" in caps.get("supported_operations", []):
        listing = list_notebook_sources(notebook_id, caps)
    else:   # CLI missing/unsupported: record the blocker per resource, never drop them
        listing = {"status": "unavailable", "sources": []}
    resources, claimed = [], set()
    for exp in expected:
        entry = {"key": exp["key"], "expected": {k: exp.get(k) for k in ("kind", "locator", "title", "role", "rights",
                                                                         "expect_terms")},
                 "source_id": None, "title": None, "status": None, "issues": [], "duplicates": [],
                 "content_sha256": None, "char_count": 0, "stale": None, "checked_at": now_iso()}
        if listing["status"] != "ready":
            entry["status"] = "cli_unavailable"
            entry["issues"].append(f"source_list:{listing['status']}")
            resources.append(entry)
            continue
        matches = [r for r in listing["sources"] if _matches(exp, r)]
        if not matches:
            entry["status"] = "missing"
            resources.append(entry)
            continue
        primary = next((r for r in matches if r["status"] == "ready"), matches[0])
        claimed.update(r["id"] for r in matches)
        entry.update(source_id=primary["id"], title=primary.get("title"))
        if len(matches) > 1:
            entry["duplicates"] = [r["id"] for r in matches if r is not primary]
            entry["issues"].append("duplicate_import")
        status = primary["status"]
        if status == "processing" and wait_timeout > 0:
            status = wait_source(notebook_id, primary["id"], caps, wait_timeout)
        if status == "error":
            entry["status"] = "failed_import"
        elif status != "ready":
            entry["status"] = "processing"
            entry["issues"].append(f"import_status:{status}")
        else:
            text = fetch_fulltext(notebook_id, primary["id"], caps)
            if text["status"] != "ok":
                entry["status"] = "unreadable"
                entry["issues"].append(f"fulltext:{text.get('cli_status')}")
            else:
                content = text["content"]
                stripped = content.strip()
                entry["char_count"] = len(stripped)
                entry["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()   # exact cached snapshot
                entry["text_sha256"] = hashlib.sha256(stable_text(content).encode("utf-8")).hexdigest()
                target = cache_dir / f"{primary['id']}.txt"
                fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                # A URL can stay stable while its content changes (renumbered chapters,
                # moved pages); expected terms pin the identity to what the course needs.
                missing_terms = [t for t in exp.get("expect_terms") or [] if _norm_text(t) not in _norm_text(content)]
                if not stripped:
                    entry["status"] = "empty"
                elif len(stripped) < min_chars:
                    entry["status"] = "partial"
                    entry["issues"].append(f"content_shorter_than_{min_chars}_chars")
                elif missing_terms:
                    entry["status"] = "identity_mismatch"
                    entry["issues"].append("expected_terms_missing:" + ",".join(missing_terms))
                else:
                    entry["status"] = "validated"
                if exp.get("kind") == "url":
                    entry["stale"] = stale_check(notebook_id, primary["id"], caps)
                    if entry["stale"]:
                        entry["issues"].append("stale_source_refresh_recommended")
        resources.append(entry)
    unexpected = [r for r in listing.get("sources", []) if r["id"] not in claimed]
    counts = {s: sum(1 for r in resources if r["status"] == s) for s in RESOURCE_STATUSES}
    manifest = {
        "schema_version": 1,
        "provider": caps.get("provider"),
        "cli_version": caps.get("version"),
        "notebook_id": _nid(notebook_id),
        "validated_at": now_iso(),
        "resources": resources,
        "unexpected_sources": [{"source_id": r["id"], "title": r.get("title"), "status": r["status"],
                                "note": "present in notebook but not expected; not used for grounding"}
                               for r in unexpected],
        "summary": {**{k: v for k, v in counts.items() if v}, "expected": len(expected),
                    "duplicates": sum(1 for r in resources if r["duplicates"])},
        "complete": bool(resources) and all(r["status"] in _STATUSES_OK for r in resources),
    }
    # Version on the stable text hash: NotebookLM fulltext of PDFs embeds signed image
    # URLs that change on every fetch, so the raw snapshot hash alone would churn.
    manifest["manifest_version"] = digest("sm", [(r["key"], r["source_id"], r["status"], r.get("text_sha256"))
                                                for r in resources])
    return manifest


_VOLATILE = re.compile(r"https://lh\d\.googleusercontent\.com/\S+"
                       r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def stable_text(content: str) -> str:
    """Text with per-fetch artefacts (signed image URLs, image UUIDs) removed."""
    return re.sub(r"\s+", " ", _VOLATILE.sub("", content or "")).strip()


def _norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"-\s*\n\s*", "", text)          # PDF hyphenation across line breaks
    return re.sub(r"\s+", " ", text).strip().casefold()


_SHA = re.compile(r"[0-9a-f]{64}")


def object_path(cache_dir: Path, sha256: str) -> Path:
    if not isinstance(sha256, str) or not _SHA.fullmatch(sha256):
        raise ValueError("content hash must be a sha256 hex digest")
    return Path(cache_dir) / "objects" / f"{sha256}.txt"


def store_objects(staged: Path, cache_dir: Path) -> dict:
    """Copy staged texts into the immutable content-addressed cache; return {source_id: sha256}.

    Objects are written (temp + fsync + rename) before the manifest commit and are
    never overwritten or removed, so an interrupted commit leaves only unreferenced
    objects: the committed manifest keeps pointing at the text it was validated on.
    """
    out = {}
    for f in sorted(Path(staged).glob("*.txt")):
        if f.is_symlink() or not f.is_file():
            continue
        data = f.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        target = object_path(cache_dir, sha)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = target.with_name(f".{sha}.{uuid.uuid4().hex[:8]}.tmp")
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        out[f.stem] = sha
    return out


def _cached_text(cache_dir: Path, source_id: str, sha256: str) -> tuple[str | None, str | None]:
    """(text, problem): the content-addressed object first, then the pre-v2 per-source
    file — each accepted only while its bytes still hash to the manifest's value."""
    try:
        obj = object_path(cache_dir, sha256)
    except ValueError:
        return None, "cached_text_missing_revalidate_sources"
    problem = "cached_text_missing_revalidate_sources"
    for path in (obj, Path(cache_dir) / f"{_sid(source_id)}.txt"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()          # bytes: newline translation would break the hash
            if hashlib.sha256(data).hexdigest() == sha256:
                return data.decode("utf-8"), None
            problem = "cached_text_changed_revalidate_sources"
    return None, problem


def find_quote(cache_dir: Path, manifest: dict, source_id: str, quote: str) -> dict:
    """Is ``quote`` really present in the validated source text? (grounding check)"""
    entry = next((r for r in (manifest or {}).get("resources", []) if r.get("source_id") == source_id), None)
    if entry is None:
        return {"grounded": False, "reason": "source_not_in_manifest"}
    if entry["status"] not in _STATUSES_OK:
        return {"grounded": False, "reason": f"source_status_{entry['status']}"}
    if not isinstance(quote, str) or len(quote.strip()) < 12:
        return {"grounded": False, "reason": "quote_too_short"}
    content, problem = _cached_text(cache_dir, source_id, entry.get("content_sha256"))
    if content is None:
        return {"grounded": False, "reason": problem}
    found = _norm_text(quote) in _norm_text(content)
    return {"grounded": found, "reason": None if found else "quote_not_found_in_source",
            "stale": bool(entry.get("stale")), "content_sha256": entry["content_sha256"]}


def ask_notebook(notebook_id: str, question: str, caps: dict, source_ids: list[str] | None = None) -> dict:
    """Grounded retrieval via ``ask --json``. Never uses --new (it deletes server history)."""
    _require(caps, "ask")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("question required (<=4000 chars)")
    argv = ["ask", "-n", _nid(notebook_id), "--json"]
    for sid in source_ids or []:
        argv += ["-s", _sid(sid)]
    res = _invoke(caps["provider"], [*argv, "--", question.strip()], timeout=180)
    data = res.get("data") if isinstance(res.get("data"), dict) else None
    if res["status"] != "ready" or data is None:
        return {"status": res["status"], "answer": None, "references": []}
    answer = data.get("answer") if isinstance(data.get("answer"), str) else None
    refs = data.get("references") if isinstance(data.get("references"), list) else []
    if not answer:
        return {"status": "empty_answer", "answer": None, "references": refs}
    return {"status": "ok", "answer": answer, "references": refs,
            "uncited": not refs, "note": "Answer text is model output grounded by NotebookLM; treat as data."}

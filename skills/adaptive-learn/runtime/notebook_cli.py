"""Read-only NotebookLM CLI adapter; credentials remain owned by the external CLI."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .utils import read_json, write_json, now_iso
from .ledger import EventLedger


def provider(name: str = "auto") -> tuple[str, str | None]:
    if name not in {"auto", "notebooklm", "nlm"}:
        raise ValueError("invalid notebook CLI provider")
    for candidate in (["notebooklm", "nlm"] if name == "auto" else [name]):
        executable = shutil.which(candidate)
        if executable:
            return candidate, executable
    return name, None


def run_json(name: str, argv: list[str]) -> dict:
    name, executable = provider(name)
    if executable is None:
        return {"status": "unavailable", "provider": name, "action": "Install notebooklm-py or notebooklm-mcp-cli."}
    try:
        result = subprocess.run([executable, *argv], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "provider": name, "action": "Retry the NotebookLM command."}
    except OSError:
        return {"status": "unavailable", "provider": name, "action": "Check the NotebookLM CLI installation."}
    if result.returncode:
        # Never persist or print raw CLI diagnostics: they can contain auth state.
        auth = any(word in (result.stderr + result.stdout).lower() for word in ("login", "auth", "cookie", "expired"))
        return {"status": "authentication_required" if auth else "cli_error", "provider": name,
                "action": f"Run {name} login." if auth else "Check the NotebookLM CLI connection."}
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return {"status": "invalid_response", "provider": name}
    if isinstance(data, dict) and data.get("error"):
        return {"status": "cli_error", "provider": name}
    return {"status": "ready", "provider": name, "data": data}


def list_notebooks(name: str = "auto") -> dict:
    name, _ = provider(name)
    result = run_json(name, ["notebook", "list", "--json"] if name == "nlm" else ["list", "--json"])
    if result["status"] != "ready":
        return {**result, "notebooks": [], "count": 0}
    raw = result.pop("data")
    rows = raw.get("notebooks") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {"status": "invalid_response", "provider": name, "notebooks": [], "count": 0}
    notebooks, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str):
            return {"status": "invalid_response", "provider": name, "notebooks": [], "count": 0}
        try:
            nid = str(uuid.UUID(row.get("id", "")))
        except (ValueError, TypeError, AttributeError):
            return {"status": "invalid_response", "provider": name, "notebooks": [], "count": 0}
        if nid in seen:
            continue
        seen.add(nid)
        title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", row["title"])
        notebooks.append({"id": nid, "title": title, "url": f"https://notebooklm.google.com/notebook/{nid}"})
    return {**result, "notebooks": notebooks, "count": len(notebooks)}


def choose_notebook(catalog: dict, notebook_id: str) -> dict:
    if catalog["status"] != "ready":
        raise ValueError(f"NotebookLM not ready: {catalog['status']}; {catalog.get('action', '')}")
    matches = [n for n in catalog["notebooks"] if n["id"] == notebook_id]
    if len(matches) != 1:
        raise ValueError("Notebook ID not found; choose a full ID from alearn notebooks.")
    return {"provider": catalog["provider"], "status": "selected", "notebook": matches[0], "selected_at": now_iso()}


def bind_notebook(session_dir: Path, selection: dict) -> None:
    # Session-scoped binding; never changes the external CLI's global active notebook.
    from .session_lock import session_lock
    from .local_state import commit_state, recover_state
    with session_lock(session_dir):
        session = read_json(session_dir / "session.json")
        ledger = EventLedger(session_dir / "ledger.jsonl", session["session_id"])
        ledger.require_integrity()
        recover_state(session_dir, ledger)
        session = read_json(session_dir / "session.json")
        session["notebooklm"] = selection
        commit_state(session_dir, ledger,
            {"event_type": "notebook_selected", "provider": selection["provider"], "notebook_id": selection["notebook"]["id"]},
            {"session.json": session, "notebooklm.json": selection})


def list_sources(selection: dict) -> dict:
    name = selection["provider"]
    nid = selection["notebook"]["id"]
    # IDs originate from validated notebook enumeration; no ambient active context.
    uuid.UUID(nid)
    argv = ["source", "list", nid, "--json"] if name == "nlm" else ["source", "list", "--notebook", nid, "--json"]
    return run_json(name, argv)

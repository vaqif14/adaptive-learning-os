"""Journal-first state updates. Call only while holding the session lock.

The durable ledger event is the commit point. JSON files are materialized views;
an interrupted write is replayed on the next session operation. Roadmap trees
are prepared and synced before commit, then installed without deleting old work.
"""
from pathlib import Path
import os
import re

from .utils import read_json, write_json, fsync_directory

STATE_FILES = {"cards.json", "learning-contract.json", "source-policy.json",
               "anchor-probe.json", "session.json", "notebooklm.json"}


def workspace_metadata(root):
    root = Path(root).resolve()
    return {"directory": str(root), "frontend_directory": str(root / "frontend"),
            "index_html": str(root / "index.html"), "roadmap_json": str(root / "roadmap.json")}


def sync_tree(root):
    for directory, _, files in os.walk(root, topdown=False):
        for name in files:
            with open(Path(directory) / name, "rb") as stream:
                os.fsync(stream.fileno())
        fsync_directory(Path(directory))
    fsync_directory(Path(root).parent)


def recover_state(directory, ledger):
    records, report = ledger._scan(ledger._snapshot())
    if not report["chain_ok"]:
        return  # inspection/repair remains possible; mutations require integrity
    state, install = {}, None
    for record in records:
        payload = record["payload"]
        if record["record_type"] != "event" or record["provenance"].get("source") != "local_state":
            continue
        changes = payload.get("state_files", {})
        if not isinstance(changes, dict) or set(changes) - STATE_FILES:
            raise ValueError("invalid state transaction")
        state.update(changes)
        if payload.get("workspace_install"):
            install = payload["workspace_install"]
    if install:
        if not isinstance(install, str) or not re.fullmatch(r"workspace-stage-v_[0-9a-f]{16}", install):
            raise ValueError("invalid workspace transaction")
        stage, root = directory / install, directory / "workspace"
        previous = directory / install.replace("-stage-", "-prev-")
        if any(p.is_symlink() for p in (stage, root, previous)):
            raise ValueError("workspace transaction cannot follow symlinks")
        if stage.exists():
            if root.exists():
                if previous.exists():
                    raise ValueError("ambiguous workspace recovery; preserve all copies")
                root.rename(previous)
                fsync_directory(directory)
            stage.rename(root)
            fsync_directory(directory)
        if not root.is_dir():
            raise ValueError("committed workspace missing; restore a verified backup")
    for name, value in state.items():
        target = directory / name
        if target.is_symlink():
            raise ValueError("state transaction cannot follow symlinks")
        if name == "session.json" and value.get("learning_workspace"):
            value = {**value, "learning_workspace": workspace_metadata(directory / "workspace")}
        try:
            unchanged = read_json(target) == value
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            write_json(target, value)
    # Notebook selection also appears in the frontend's derived data. Rebuild
    # that view after notebook changes, roadmap replacement or backup relocation.
    binding = directory / "notebooklm.json"
    frontend = directory / "workspace/frontend/src/data/roadmap.json"
    if binding.exists() and frontend.exists():
        if directory.resolve() not in frontend.resolve().parents or binding.is_symlink():
            raise ValueError("derived state cannot escape the session")
        selection, plan = read_json(binding), read_json(frontend)
        if plan.get("notebooklm") != selection:
            write_json(frontend, {**plan, "notebooklm": selection})


def commit_state(directory, ledger, event, files, *, workspace_install=None):
    if set(files) - STATE_FILES:
        raise ValueError("unsupported state file")
    payload = {**event, "state_files": files}
    if workspace_install:
        payload["workspace_install"] = workspace_install
    ledger.append("event", payload, {"source": "local_state"})
    recover_state(directory, ledger)

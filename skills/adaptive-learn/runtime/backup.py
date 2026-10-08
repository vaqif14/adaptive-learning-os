"""Bounded, checksum-verified local backups; restore never replaces a session."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from .ledger import EventLedger
from .local_state import recover_state, sync_tree, workspace_metadata
from .safety import safe_session_dir, valid_session_id
from .session_lock import session_lock
from .utils import read_json, write_json, fsync_directory

MAX_BYTES = 128 * 1024 * 1024
MAX_FILES = 10000
SKIP = {"node_modules", "__pycache__", ".pytest_cache", ".session.lock"}
REQUIRED = {"session.json", "learning-contract.json", "source-policy.json",
            "domain-context.json", "anchor-probe.json", "ledger.jsonl"}


def backup_session(kernel, session_id, output):
    directory = kernel.dir(session_id)
    output = Path(output).resolve()
    if output == directory or directory in output.parents:
        raise ValueError("backup output must be outside the session directory")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with session_lock(directory):
        kernel.ledger(session_id).require_integrity()
        kernel._recover_state(session_id)
        manifest = {"schema_version": 1, "session_id": session_id, "files": {}}
        fd, temporary = tempfile.mkstemp(prefix=".backup-", dir=output.parent)
        os.close(fd)
        try:
            total = 0
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for root, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP)
                    for name in dirs + files:
                        if name not in SKIP and (Path(root) / name).is_symlink():
                            raise ValueError("backup refuses symlinks; preserve their targets separately")
                    for name in sorted(files):
                        if name in SKIP or name.startswith(".tmp-"):
                            continue
                        path = Path(root) / name
                        before = path.stat()
                        if not stat.S_ISREG(before.st_mode):
                            raise ValueError("backup accepts regular files only")
                        total += before.st_size
                        if total > MAX_BYTES or len(manifest["files"]) >= MAX_FILES:
                            raise ValueError("backup exceeds local archive limits")
                        with path.open("rb") as stream:
                            data = stream.read(MAX_BYTES + 1)
                        if len(data) != before.st_size or path.stat().st_mtime_ns != before.st_mtime_ns:
                            raise ValueError("session files changed during backup; retry after editor writes finish")
                        relative = path.relative_to(directory).as_posix()
                        manifest["files"][relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                        archive.writestr("session/" + relative, data)
                if not REQUIRED <= manifest["files"].keys():
                    raise ValueError("session is missing required files")
                archive.writestr("manifest.json", json.dumps(manifest, allow_nan=False))
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            # Atomic create-if-absent; existing backups must never be overwritten.
            os.link(temporary, output)
            fsync_directory(output.parent)
        finally:
            os.unlink(temporary)
    return {"status": "verified_backup", "session_id": session_id, "output": str(output),
            "files": len(manifest["files"]), "bytes": total}


def _read_archive(path):
    if Path(path).stat().st_size > MAX_BYTES * 2:
        raise ValueError("backup archive too large")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1 or sum(i.file_size for i in infos) > MAX_BYTES + 4 * 1024 * 1024:
            raise ValueError("backup exceeds extraction limits")
        names = [i.filename for i in infos]
        if len(set(names)) != len(names) or "manifest.json" not in names:
            raise ValueError("duplicate entries or missing backup manifest")
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ValueError("backup manifest too large")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError("unsupported backup manifest")
        sid, entries = manifest.get("session_id"), manifest.get("files")
        if not isinstance(sid, str) or not valid_session_id(sid) or not isinstance(entries, dict):
            raise ValueError("invalid backup identity or files")
        if not REQUIRED <= entries.keys() or set(names) != {"manifest.json", *("session/" + n for n in entries)}:
            raise ValueError("backup file list does not match manifest")
        files = {}
        for name, expected in entries.items():
            parts = PurePosixPath(name)
            if not name or parts.is_absolute() or ".." in parts.parts or "\\" in name or str(parts) != name or any(p in SKIP for p in parts.parts):
                raise ValueError("unsafe backup path")
            info = archive.getinfo("session/" + name)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)):
                raise ValueError("backup must contain regular files only")
            if not isinstance(expected, dict) or type(expected.get("size")) is not int or expected["size"] != info.file_size:
                raise ValueError("backup size mismatch")
            data = archive.read(info)
            if hashlib.sha256(data).hexdigest() != expected.get("sha256"):
                raise ValueError("backup checksum mismatch")
            files[name] = data
        return sid, files


def restore_session(kernel, backup_file):
    try:
        sid, files = _read_archive(backup_file)
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ValueError("invalid or damaged backup archive") from exc
    with session_lock(kernel.sessions_dir):
        target = safe_session_dir(kernel.sessions_dir, sid)
        if target.exists():
            raise FileExistsError("session already exists; restore into a separate workspace")
        stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=kernel.sessions_dir))
        try:
            for name, data in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
            ledger = EventLedger(stage / "ledger.jsonl", sid)
            ledger.require_integrity()
            if not ledger.verified_records():
                raise ValueError("backup contains an empty journal")
            from .contracts import LearningContract
            for name in REQUIRED - {"ledger.jsonl"}:
                if not isinstance(read_json(stage / name), dict):
                    raise ValueError("backup session metadata must be objects")
            LearningContract.from_dict(read_json(stage / "learning-contract.json"))
            session = read_json(stage / "session.json")
            if not isinstance(session, dict) or session.get("session_id") != sid:
                raise ValueError("backup session identity mismatch")
            recover_state(stage, ledger)
            session = read_json(stage / "session.json")
            if session.get("learning_workspace"):
                session["learning_workspace"] = workspace_metadata(target / "workspace")
                write_json(stage / "session.json", session)
                frontend_plan = stage / "workspace/frontend/src/data/roadmap.json"
                plan = read_json(frontend_plan)
                plan["workspace_directory"] = str(target / "workspace")
                write_json(frontend_plan, plan)
            from .projection import EvidenceProjector
            EvidenceProjector(ledger).write(stage / "learner-evidence.json")
            sync_tree(stage)
            stage.rename(target)
            fsync_directory(kernel.sessions_dir)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"status": "restored", "session_id": sid, "directory": str(target)}

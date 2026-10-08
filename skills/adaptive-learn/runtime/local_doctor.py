"""Local operational checks; no network, subprocess, or learner-data export."""
from pathlib import Path
import tempfile

from .ledger import EventLedger
from .safety import valid_session_id
from .session_lock import session_lock
from .utils import write_json, read_json


def diagnose_local(kernel):
    checks, sessions = [], []
    try:
        import fcntl  # noqa: F401
        checks.append({"check": "process_locking", "ok": True})
    except ImportError:
        return {"status": "failed", "checks": [{"check": "process_locking", "ok": False,
                "reason": "local runtime requires macOS or Linux flock"}], "sessions": []}
    try:
        kernel.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".alearn-doctor-", dir=kernel.workspace) as tmp:
            root = Path(tmp)
            with session_lock(root):
                write_json(root / "probe.json", {"probe": True})
                if read_json(root / "probe.json") != {"probe": True}:
                    raise ValueError("atomic file readback failed")
                ledger = EventLedger(root / "ledger.jsonl", "doctor")
                ledger.append("event", {"event_type": "storage_probe"})
                ledger.require_integrity()
        checks.append({"check": "durable_storage", "ok": True})
    except (OSError, ValueError) as exc:
        checks.append({"check": "durable_storage", "ok": False, "reason": str(exc)})
    skill = Path(__file__).resolve().parents[1]
    for relative in ("SKILL.md", "templates/react/package.json", "templates/react/src/components/Lesson.jsx", "adapters/manifests"):
        checks.append({"check": "asset:" + relative, "ok": (skill / relative).exists()})
    if kernel.sessions_dir.exists():
        for directory in sorted(kernel.sessions_dir.iterdir()):
            if not directory.is_dir() or not valid_session_id(directory.name):
                continue
            try:
                with session_lock(kernel.dir(directory.name)):
                    kernel.ledger(directory.name).require_integrity()
                    if not kernel.ledger(directory.name).verified_records():
                        raise ValueError("session journal contains no records")
                    kernel._recover_state(directory.name)
                    for filename in ("session.json", "learning-contract.json", "source-policy.json", "domain-context.json", "anchor-probe.json"):
                        if not isinstance(read_json(directory / filename), dict):
                            raise ValueError("session metadata must be objects")
                    if read_json(directory / "session.json").get("session_id") != directory.name:
                        raise ValueError("session metadata identity mismatch")
                    sessions.append({"session_id": directory.name, "ok": True})
            except (OSError, ValueError) as exc:
                sessions.append({"session_id": directory.name, "ok": False, "reason": str(exc)})
    return {"status": "ok" if all(c["ok"] for c in checks + sessions) else "failed",
            "scope": "local Codex/agent use on macOS/Linux", "checks": checks, "sessions": sessions,
            "mastery_model": "uncalibrated_defaults; run calibrate on real independent trials",
            "semantic_assessment": "host-agent rubric judgments; not deterministic correctness",
            "storage_requirement": "local filesystem with reliable flock, atomic rename and fsync"}

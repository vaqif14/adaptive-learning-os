import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills/adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.backup import backup_session, restore_session
from runtime.local_doctor import diagnose_local
from runtime.utils import read_json


def session(tmp_path):
    k = SessionKernel(tmp_path)
    return k, k.start("Logic", LearningContract(goal="Reason independently"))["session_id"]


def process(code, workspace, sid):
    env = {**os.environ, "PYTHONPATH": str(SKILL)}
    return subprocess.Popen([sys.executable, "-c", code, str(workspace), sid], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_process_writers_do_not_lose_cards_or_reviews(tmp_path):
    k, sid = session(tmp_path)
    card = k.add_card(sid, "logic", "Question", "Answer")
    code = '''import sys
from pathlib import Path
from runtime.session import SessionKernel
k = SessionKernel(Path(sys.argv[1]))
for i in range(12):
    k.add_card(sys.argv[2], 'logic', str(i))
    k.review_card(sys.argv[2], CARD, True)
'''.replace("CARD", repr(card["id"]))
    workers = [process(code, tmp_path, sid) for _ in range(4)]
    for worker in workers:
        out, err = worker.communicate(timeout=45)
        assert worker.returncode == 0, out + err
    cards = k._load_cards(sid)
    assert len(cards) == 49
    assert len(cards[0]["reviews"]) == 48
    assert k.ledger(sid).integrity()["chain_ok"]
    assert len(k.ledger(sid).verified_records()) == 98


def test_process_death_after_commit_recovers_without_duplicate(tmp_path):
    k, sid = session(tmp_path)
    code = '''import os, sys
from pathlib import Path
from runtime.session import SessionKernel
from runtime import local_state
local_state.write_json = lambda *args: os._exit(71)
SessionKernel(Path(sys.argv[1])).add_card(sys.argv[2], 'logic', 'Committed before death')
'''
    worker = process(code, tmp_path, sid)
    worker.communicate(timeout=15)
    assert worker.returncode == 71
    assert not (k.dir(sid) / "cards.json").exists()
    assert k.cards_due(sid)["total"] == 1
    assert k.cards_due(sid)["total"] == 1
    assert len(k.ledger(sid).verified_records()) == 2


def test_contract_commit_recovers_all_views(tmp_path, monkeypatch):
    from runtime import local_state
    k, sid = session(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(local_state, "write_json", lambda *args: (_ for _ in ()).throw(OSError("disk failure")))
        with pytest.raises(OSError):
            k.update_contract(sid, goal="New goal")
    view = k.inspect(sid)
    assert view["learning_contract"]["goal"] == "New goal"
    assert view["anchor_probe"] == view["session"]["anchor_probe"]


def test_roadmap_preparation_failure_preserves_live_workspace(tmp_path, monkeypatch):
    from runtime import learning_workspace
    k, sid = session(tmp_path)
    old = (k.dir(sid) / "workspace/roadmap.json").read_bytes()
    monkeypatch.setattr(learning_workspace, "create_workspace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        k.set_roadmap(sid, {"nodes": [{"id": "new", "title": "New", "task": "Try"}]})
    assert (k.dir(sid) / "workspace/roadmap.json").read_bytes() == old


def test_roadmap_commit_recovers_after_interrupted_install(tmp_path, monkeypatch):
    from runtime import local_state
    k, sid = session(tmp_path)
    submission = k.dir(sid) / "workspace/modules/first-attempt/submission/work.txt"
    submission.write_text("Original learner work")
    with monkeypatch.context() as patch:
        patch.setattr(local_state, "recover_state", lambda *args: (_ for _ in ()).throw(OSError("power loss")))
        with pytest.raises(OSError):
            k.set_roadmap(sid, {"nodes": [{"id": "new", "title": "New", "task": "Try"}]})
    k.inspect(sid)
    assert read_json(k.dir(sid) / "workspace/roadmap.json")["nodes"][0]["id"] == "new"
    assert next(k.dir(sid).glob("workspace-prev-*/modules/first-attempt/submission/work.txt")).read_text() == "Original learner work"
    assert not list(k.dir(sid).glob("workspace-stage-*"))


def test_backup_restore_roundtrip_relocates_paths_and_preserves_evidence(tmp_path):
    k, sid = session(tmp_path / "original")
    k.update_contract(sid, goal="Portable learning")
    card = k.add_card(sid, "logic", "Why?")
    k.review_card(sid, card["id"], False)
    k.set_roadmap(sid, {"nodes": [{"id": "logic", "title": "Logic", "task": "Prove"}]})
    backup = tmp_path / "session.zip"
    result = backup_session(k, sid, backup)
    assert result["status"] == "verified_backup"
    target = SessionKernel(tmp_path / "restored")
    restore_session(target, backup)
    assert target.ledger(sid).path.read_bytes() == k.ledger(sid).path.read_bytes()
    assert target._load_cards(sid) == k._load_cards(sid)
    view = target.inspect(sid)
    root = target.dir(sid) / "workspace"
    assert view["session"]["learning_workspace"]["directory"] == str(root)
    assert read_json(root / "frontend/src/data/roadmap.json")["workspace_directory"] == str(root)
    assert diagnose_local(target)["status"] == "ok"
    with pytest.raises(FileExistsError):
        restore_session(target, backup)
    with pytest.raises(FileExistsError):
        backup_session(k, sid, backup)


@pytest.mark.parametrize("fault", ["checksum", "traversal", "symlink", "extra", "damaged_ledger"])
def test_restore_rejects_untrusted_archive_without_publishing_session(tmp_path, fault):
    k, sid = session(tmp_path / "source")
    source, broken = tmp_path / "good.zip", tmp_path / "broken.zip"
    backup_session(k, sid, source)
    with zipfile.ZipFile(source) as archive:
        content = {n: archive.read(n) for n in archive.namelist()}
    manifest = json.loads(content["manifest.json"])
    name = "session/session.json"
    if fault == "checksum":
        content[name] += b" "
    elif fault in {"traversal", "extra"}:
        malicious = "../escape" if fault == "traversal" else "extra"
        content["session/" + malicious] = b"escape"
        if fault == "traversal":
            manifest["files"][malicious] = {"size": 6, "sha256": hashlib.sha256(b"escape").hexdigest()}
    elif fault == "damaged_ledger":
        content["session/ledger.jsonl"] += b"torn"
        data = content["session/ledger.jsonl"]
        manifest["files"]["ledger.jsonl"] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    content["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(broken, "w") as archive:
        for key, data in content.items():
            if fault == "symlink" and key == name:
                info = zipfile.ZipInfo(key)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, data)
            else:
                archive.writestr(key, data)
    target = SessionKernel(tmp_path / "target")
    with pytest.raises(ValueError):
        restore_session(target, broken)
    assert not (target.sessions_dir / sid).exists()
    assert not (tmp_path / "escape").exists()


def test_backup_refuses_corrupt_journal_and_symlinks(tmp_path):
    k, sid = session(tmp_path)
    link = k.dir(sid) / "external"
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlinks"):
        backup_session(k, sid, tmp_path / "backup.zip")
    link.unlink()
    with k.ledger(sid).path.open("ab") as stream:
        stream.write(b"torn")
    with pytest.raises(ValueError, match="integrity"):
        backup_session(k, sid, tmp_path / "backup.zip")
    assert diagnose_local(k)["status"] == "failed"


def test_invalid_card_review_does_not_commit(tmp_path):
    k, sid = session(tmp_path)
    c = k.add_card(sid, "logic", "Why?")
    with pytest.raises(ValueError, match="boolean"):
        k.review_card(sid, c["id"], "false")
    assert not k._load_cards(sid)[0]["reviews"]


def test_notebook_binding_survives_contract_and_roadmap_transactions(tmp_path):
    from runtime.notebook_cli import bind_notebook
    k, sid = session(tmp_path)
    k.update_contract(sid, goal="New goal")
    selection = {"provider": "notebooklm", "notebook": {"id": "a-notebook"}}
    bind_notebook(k.dir(sid), selection)
    k.set_roadmap(sid, {"nodes": [{"id": "new", "title": "New", "task": "Try"}]})
    assert k.inspect(sid)["session"]["notebooklm"] == selection
    assert read_json(k.dir(sid) / "workspace/frontend/src/data/roadmap.json")["notebooklm"] == selection


def test_missing_journal_cannot_be_silently_recreated(tmp_path):
    k, sid = session(tmp_path)
    journal = k.ledger(sid).path
    journal.unlink()
    with pytest.raises(ValueError, match="missing or empty"):
        k.add_card(sid, "logic", "Why?")
    assert not journal.exists()
    assert diagnose_local(k)["status"] == "failed"

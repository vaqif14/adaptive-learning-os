"""Ledger integrity verification: the periodic chain check + dated receipt."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402


def _start(ws):
    k = SessionKernel(ws)
    contract = LearningContract(goal="learn loops", goal_mode="learn")
    session = k.start("python basics", contract, mode="practice")
    return k, session["session_id"]


def test_verify_clean_ledger_is_ok(tmp_path):
    k, sid = _start(tmp_path)
    receipt = k.verify_ledger(sid)
    assert receipt["status"] == "ok"
    assert receipt["result"]["chain_ok"] is True
    assert "verified_at" in receipt


def test_receipt_is_persisted(tmp_path):
    k, sid = _start(tmp_path)
    k.verify_ledger(sid)
    receipt_file = tmp_path / ".learning" / "sessions" / sid / "ledger-integrity.json"
    assert receipt_file.exists()
    saved = json.loads(receipt_file.read_text())
    assert saved["session_id"] == sid
    assert saved["status"] == "ok"


def test_cadence_skips_a_fresh_receipt(tmp_path):
    k, sid = _start(tmp_path)
    k.verify_ledger(sid)
    again = k.verify_ledger(sid, cadence_hours=24)
    assert again["status"] == "skipped"
    assert again["reason"] == "within_cadence"


def test_force_overrides_cadence(tmp_path):
    k, sid = _start(tmp_path)
    k.verify_ledger(sid)
    forced = k.verify_ledger(sid, cadence_hours=24, force=True)
    assert forced["status"] == "ok"


def test_tampered_record_is_detected(tmp_path):
    k, sid = _start(tmp_path)
    led_path = tmp_path / ".learning" / "sessions" / sid / "ledger.jsonl"
    lines = led_path.read_text().split("\n")
    rec = json.loads(lines[0])
    rec["payload"]["topic"] = "TAMPERED"           # edit in place; stored hash no longer matches
    lines[0] = json.dumps(rec, ensure_ascii=True)
    led_path.write_text("\n".join(lines))

    receipt = k.verify_ledger(sid, force=True)
    assert receipt["status"] == "broken"
    assert receipt["result"]["chain_ok"] is False
    assert receipt["result"]["broken_at_line"] == 0


def test_inspect_includes_integrity(tmp_path):
    k, sid = _start(tmp_path)
    info = k.inspect(sid)
    assert "ledger_integrity" in info
    assert info["ledger_integrity"]["chain_ok"] is True

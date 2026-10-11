"""Keyed (HMAC) ledger + out-of-workspace trust head.

Closes the demonstrated bypass: a keyless SHA-256 chain can be edited and re-chained
forward so `ledger-verify` still reports ok. With an HMAC key the attacker (who holds
only the workspace, not the key/trust-head) cannot forge valid record hashes, and
truncation/rollback is caught by the mirrored head.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.ledger import EventLedger, _record_hash  # noqa: E402
from runtime import ledger_trust  # noqa: E402


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    # Out-of-workspace trust root in its own tmp dir; fresh key per test.
    monkeypatch.setenv("ALEARN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("ALEARN_LEDGER_KEY", raising=False)
    ledger_trust._KEY_CACHE = None
    yield
    ledger_trust._KEY_CACHE = None


def _mk(tmp_path):
    lg = EventLedger(tmp_path / "ledger.jsonl", "sess_keyed_x")
    lg.append("event", {"a": 1})
    lg.append("observation", {"capability_id": "c", "n": 2})
    lg.append("evidence", {"capability_id": "c", "strength": "weak", "n": 3})
    return lg


def test_new_chain_is_keyed_and_clean(tmp_path, isolated_state):
    lg = _mk(tmp_path)
    rep = lg.integrity()
    assert rep["keyed"] is True
    assert rep["chain_ok"] is True
    assert rep["head_ok"] is True
    assert ledger_trust.has_head(str((tmp_path / "ledger.jsonl").resolve()))


def test_rechain_forward_attack_is_detected(tmp_path, isolated_state):
    lg = _mk(tmp_path)
    path = lg.path
    recs = [json.loads(l) for l in path.read_text().split("\n") if l.strip()]
    # Attacker edits an evidence record to forge strong mastery, then re-chains forward
    # using the only hash they can compute WITHOUT the secret key: keyless SHA-256.
    recs[2]["payload"]["strength"] = "strong"
    prev = recs[0]["record_hash"]
    for i, rec in enumerate(recs):
        if i == 0:
            continue
        rec["prev_hash"] = prev
        rec.pop("record_hash", None)
        rec["record_hash"] = _record_hash(rec)  # keyless — attacker has no HMAC key
        prev = rec["record_hash"]
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    rep = lg.integrity()
    assert rep["chain_ok"] is False  # HMAC verification rejects the forged records
    # and no forged record survives into the trusted prefix
    assert all(r["payload"].get("strength") != "strong" for r in lg.verified_records())


def test_truncation_is_detected(tmp_path, isolated_state):
    lg = _mk(tmp_path)
    path = lg.path
    lines = [l for l in path.read_text().split("\n") if l.strip()]
    # Drop the last (internally-valid) record. Remaining chain hashes still verify, but
    # the mirrored head expects a higher seq -> rollback caught.
    path.write_text("\n".join(lines[:-1]) + "\n")
    rep = lg.integrity()
    assert rep["head_ok"] is False
    assert rep["chain_ok"] is False


def test_append_still_works_and_head_advances(tmp_path, isolated_state):
    lg = _mk(tmp_path)
    lg.append("event", {"a": 4})
    rep = lg.integrity()
    assert rep["chain_ok"] is True and rep["head_ok"] is True
    head = ledger_trust.read_head(str((tmp_path / "ledger.jsonl").resolve()))
    assert head["seq"] == 3 and head["algo"] == "hmac-sha256"


def test_legacy_keyless_ledger_without_head_still_reads(tmp_path, isolated_state):
    # A pre-existing keyless ledger (no trust head) must still verify under sha256 so
    # old sessions are not falsely reported broken.
    path = tmp_path / "ledger.jsonl"
    recs = []
    prev = "genesis"
    for i in range(3):
        rec = {"schema_version": 1, "record_id": f"r{i}", "session_id": "legacy",
               "seq": i, "timestamp": "2026-10-08T00:00:00Z", "record_type": "event",
               "payload": {"i": i}, "provenance": {"source": "runtime"}, "prev_hash": prev}
        rec["record_hash"] = _record_hash(rec)  # keyless, no head written
        prev = rec["record_hash"]
        recs.append(rec)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    lg = EventLedger(path, "legacy")
    rep = lg.integrity()
    assert rep["keyed"] is False
    assert rep["chain_ok"] is True
    assert len(lg.verified_records()) == 3

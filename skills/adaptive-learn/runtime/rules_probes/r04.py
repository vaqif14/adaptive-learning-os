"""R4 ledger integrity: keyed verification before and after, append-only, anchored studio events, tamper rejection."""
from __future__ import annotations

import json

from .. import studio_receipt as receipts
from ..ledger import EventLedger, _record_hash
from ..studio_store import ANCHOR_EVENT, verify_events
from ..utils import write_bytes
from .base import Ctx, Result, fail, na, ok, probe, verdict


def _keyed_ok(ctx: Ctx) -> Result:
    report = ctx.integrity()
    problems = []
    if not report.get("chain_ok"):
        problems.append(f"the hash chain is broken at line {report.get('broken_at_line')}")
    if not report.get("keyed"):
        problems.append("the ledger is not verified in keyed (HMAC) mode: no trust head is registered")
    if not report.get("head_ok", True):
        problems.append("the ledger does not reach its out-of-workspace trust head (truncation or rollback)")
    if report.get("corrupt_lines"):
        problems.append(f"{report['corrupt_lines']} corrupt line(s)")
    return verdict(problems, f"keyed ledger verification ok ({report.get('records')} records)", records=report.get("records"))


def _control_keyed():
    return Ctx.synthetic(integrity={"chain_ok": False, "keyed": False, "head_ok": False, "broken_at_line": 3})


@probe("r04.ledger_keyed_ok", "R4", control=_control_keyed, title="ledger-verify is keyed ok")
def ledger_keyed_ok(ctx: Ctx) -> Result:
    return _keyed_ok(ctx)


def _studio_events(ctx: Ctx) -> Result:
    root = ctx.root
    if root is None or not (root / "events.jsonl").exists():
        return na("this session has no studio event log yet")
    verify = ctx.hook("verify_events", verify_events)
    report = verify(ctx.session_dir, ctx.led())
    problems = []
    if not report["ok"]:
        problems.append(f"studio event chain: {report['error']}")
    else:
        anchors = [r["payload"] for r in ctx.records() if r["record_type"] == "event"
                   and r["payload"].get("event_type") == ANCHOR_EVENT and r["provenance"].get("source") == "studio"]
        if not any(a.get("events_seq") == report["head_seq"] and a.get("events_head") == report["head_hash"] for a in anchors):
            problems.append("the studio event head is not anchored in the ledger")
    return verdict(problems, f"{report['count']} studio event(s) chained and anchored", events=report["count"])


def _control_events():
    """A real studio dir whose event log is corrupt (the real verifier must reject it)."""
    import tempfile
    from pathlib import Path
    tmp = tempfile.TemporaryDirectory(prefix="rules-control-")
    session = Path(tmp.name)
    (session / "studio").mkdir()
    (session / "studio" / "events.jsonl").write_text('{"seq": 7, "type": "x"}\n', encoding="utf-8")
    ctx = Ctx.synthetic(session_dir=session, led=None)
    ctx._tmp = tmp
    return ctx


@probe("r04.studio_events_anchored", "R4", control=_control_events, title="studio event chain verifies and is anchored")
def studio_events_anchored(ctx: Ctx) -> Result:
    return _studio_events(ctx)


def _signature(ctx: Ctx) -> Result:
    sign = ctx.hook("sign", receipts.sign_receipt)
    verify = ctx.hook("verify", receipts.verify_receipt)
    sample = {"receipt_id": "rcp_probe", "build_digest": "d" * 64, "ledger_seq_after": 1}
    signed = sign(sample)
    problems = []
    if not verify(signed):
        problems.append("a freshly signed receipt does not verify against the ledger trust key")
    if verify({**signed, "build_digest": "e" * 64}):
        problems.append("a receipt edited after signing still verifies")
    if verify({k: v for k, v in signed.items() if k != "signature"}):
        problems.append("an unsigned receipt verifies")
    return verdict(problems, "receipts verify under the ledger trust key; edits and unsigned copies do not")


def _control_signature():
    return Ctx.synthetic(hooks={"verify": lambda r: True})


@probe("r04.receipt_signature", "R4", control=_control_signature, title="receipt signatures verify; edits are caught")
def receipt_signature(ctx: Ctx) -> Result:
    return _signature(ctx)


def _append_only(ctx: Ctx) -> Result:
    if ctx.before is None:
        return na("no pre-run ledger position was recorded")
    now = receipts.ledger_position(ctx.led())
    if now != ctx.before:
        return fail(f"the real ledger moved during the run: before {ctx.before} after {now}")
    return ok("the real ledger is byte-for-byte where the run found it", seq=now["seq"])


def _control_append():
    class L:
        def verified_records(self):
            return [{"seq": 5, "record_hash": "h5"}]
    return Ctx.synthetic(before={"seq": 3, "head": "h3"}, led=L())


@probe("r04.ledger_append_only", "R4", control=_control_append, order=99, title="the run appended nothing to the real ledger")
def ledger_append_only(ctx: Ctx) -> Result:
    return _append_only(ctx)


def _restore(ctx: Ctx) -> Result:
    scratch = ctx.require_scratch()
    info = scratch.obs.get("restore")
    if not info:
        raise RuntimeError("the scratch copy was not restored: " + str(scratch.obs.get("errors")))
    problems = []
    rep = info["integrity"]
    if not (rep.get("chain_ok") and rep.get("keyed") and rep.get("head_ok", True)):
        problems.append("the restored scratch ledger does not verify keyed after trust adoption")
    if info["head"] != info["real_head"]:
        problems.append("the scratch ledger head differs from the real ledger head (the copy is not a faithful backup)")
    return verdict(problems, "backup -> restore -> adopt_trust gives a keyed, identical ledger")


def _control_restore():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"restore": {
        "integrity": {"chain_ok": True, "keyed": False}, "head": "a", "real_head": "b"}}})())


@probe("r04.backup_restore_adopt", "R4", kind="dynamic", cost="scratch", control=_control_restore,
       title="backup, restore into scratch and trust adoption succeed")
def backup_restore_adopt(ctx: Ctx) -> Result:
    return _restore(ctx)


# ------------------------------------------------------------------- tamper
def _tamper_flip(data: bytes) -> bytes:
    mid = len(data) // 2
    return data[:mid] + bytes([data[mid] ^ 0x01]) + data[mid + 1:]


def _tamper_truncate(data: bytes) -> bytes:
    lines = [ln for ln in data.split(b"\n") if ln.strip()]
    return b"\n".join(lines[:-1]) + b"\n"


def _tamper_keyless_rechain(data: bytes) -> bytes:
    prev, out = "genesis", []
    for line in (ln for ln in data.split(b"\n") if ln.strip()):
        rec = json.loads(line)
        rec["prev_hash"] = prev
        rec["record_hash"] = _record_hash(rec)
        prev = rec["record_hash"]
        out.append(json.dumps(rec, ensure_ascii=True))
    return ("\n".join(out) + "\n").encode("utf-8")


TAMPERS = {"flip_byte": _tamper_flip, "truncate_tail": _tamper_truncate, "keyless_rechain": _tamper_keyless_rechain}


def _tamper(ctx: Ctx) -> Result:
    scratch = ctx.require_scratch()
    cls = ctx.hook("ledger_cls", EventLedger)
    source = scratch.session_dir / "ledger.jsonl"
    original = source.read_bytes()
    if len([ln for ln in original.split(b"\n") if ln.strip()]) < 2:
        return na("the ledger has fewer than two records")
    problems = []
    base = scratch.run_dir / "tamper"
    for name, mutate in TAMPERS.items():
        path = base / name / "ledger.jsonl"
        write_bytes(path, original)
        EventLedger(path, scratch.sid).adopt_trust()               # register the keyed regime for this copy
        if not cls(path, scratch.sid).integrity()["chain_ok"]:
            problems.append(f"{name}: the untampered copy does not verify (probe is inconclusive)")
            continue
        write_bytes(path, mutate(original))
        if cls(path, scratch.sid).integrity()["chain_ok"]:
            problems.append(f"{name}: the tampered ledger was accepted")
    path = base / "downgrade_unkeyed" / "ledger.jsonl"          # same records at a path with no trust head: keyless regime
    write_bytes(path, original)
    if cls(path, scratch.sid).integrity()["chain_ok"]:
        problems.append("downgrade_unkeyed: a keyed ledger verified in the keyless regime")
    return verdict(problems, "byte flip, tail truncation, keyless re-chain and unkeyed downgrade are all rejected")


class _AcceptAll:
    def __init__(self, path, sid):
        self.path = path

    def integrity(self):
        return {"chain_ok": True, "keyed": True, "head_ok": True}


def _control_tamper():
    import tempfile
    from pathlib import Path
    holder = tempfile.TemporaryDirectory(prefix="rules-control-")
    tmp = Path(holder.name)
    session = tmp / "session"
    session.mkdir()
    (session / "ledger.jsonl").write_bytes(b'{"seq":0}\n{"seq":1}\n')
    scratch = type("S", (), {"session_dir": session, "run_dir": tmp, "sid": "sess_control"})()
    ctx = Ctx.synthetic(scratch=scratch, hooks={"ledger_cls": _AcceptAll})
    ctx._tmp = holder
    return ctx


@probe("r04.tamper_rejected", "R4", kind="dynamic", cost="scratch", control=_control_tamper,
       title="every ledger tamper on a copy is rejected")
def tamper_rejected(ctx: Ctx) -> Result:
    return _tamper(ctx)


PROBES = [ledger_keyed_ok, studio_events_anchored, receipt_signature, backup_restore_adopt, tamper_rejected,
          ledger_append_only]

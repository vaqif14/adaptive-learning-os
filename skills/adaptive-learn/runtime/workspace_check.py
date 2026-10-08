from __future__ import annotations

from pathlib import Path
import json
import re

from .execution import run_exercise, verify_fastapi_app, language, LANGUAGES
from .utils import write_json, now_iso
from .evidence_history import merge_attempt_support, execution_fingerprint
from .ledger import EventLedger

# GitHub-style check step: the learner writes code in a module's submission/
# folder; this reads it, runs it through the execution backend against the
# check spec the agent authored in checks/check.json, writes a result into
# feedback/, and returns an evidence payload. Deterministic for code; only
# executed + checked runs are mastery-eligible.

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DEFAULT_ENTRY = {lid: spec.filename for lid, spec in LANGUAGES.items()}
_EXECUTABLE_KINDS = {"code", "fastapi"}
# Modules in non-code subjects land here too; tell the agent where their evidence goes.
_NON_EXECUTABLE_GUIDANCE = (
    "workspace-check runs executable work only. For an explanation of a concept use "
    "`alearn listener-check`; for other work (essay, problem set, spoken or role-played "
    "practice) grade it against the module's rubric and record it with `alearn evidence` "
    "(a self-report, capped at medium)."
)


def _gather_submission(sub: Path) -> dict[str, str]:
    files = {}
    if sub.is_dir():
        for p in sorted(sub.rglob("*")):
            if p.is_file() and p.name != ".gitkeep":
                if not p.resolve().is_relative_to(sub.resolve()):
                    raise ValueError("submission file escapes its module")
                try:
                    files[str(p.relative_to(sub))] = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
    return files


def check_module(session_dir: Path, module_id: str, *, backend=None) -> dict:
    """Run the check for one workspace module. Never overwrites submission files."""
    if not _SLUG.fullmatch(module_id or ""):
        raise ValueError(f"invalid_module_id:{module_id!r}")
    mod = (session_dir / "workspace" / "modules" / module_id)
    base = (session_dir / "workspace" / "modules").resolve()
    if not mod.resolve().is_relative_to(base) or not mod.is_dir():
        raise FileNotFoundError("unknown module")
    EventLedger(session_dir / "ledger.jsonl", session_dir.name).require_integrity()

    spec_path = mod / "checks" / "check.json"
    for child in (spec_path, mod / "submission", mod / "feedback"):
        if not child.resolve().is_relative_to(mod.resolve()):
            raise ValueError("module path escapes its workspace")
    if not spec_path.exists():
        return {"status": "no_check_spec", "module": module_id,
                "detail": "No checks/check.json yet. For executable work the agent authors the check "
                          "spec (language/entry/expected or FastAPI checks) before verification. "
                          + _NON_EXECUTABLE_GUIDANCE}
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"status": "bad_check_spec", "module": module_id, "detail": f"invalid_json:{e}"}
    if not isinstance(spec, dict):
        return {"status": "bad_check_spec", "module": module_id, "detail": "check spec must be an object"}

    kind = spec.get("kind", "code")
    if kind not in _EXECUTABLE_KINDS:
        return {"status": "not_runtime_checkable", "module": module_id, "kind": kind,
                "detail": _NON_EXECUTABLE_GUIDANCE}

    files = _gather_submission(mod / "submission")
    if not files:
        return {"status": "empty_submission", "module": module_id,
                "detail": "submission/ is empty — write your solution there, then check again."}

    capability = spec.get("capability", module_id)
    scope = spec.get("scope", "independent_reproduction")
    fingerprint = execution_fingerprint(capability, files,
        {k: spec.get(k) for k in ("kind", "language", "entry", "stdin", "checks", "expect_stdout")})
    support = merge_attempt_support(
        EventLedger(session_dir / "ledger.jsonl", session_dir.name).verified_records(),
        capability, spec.get("attempt_id"), spec.get("support_provenance"),
        fingerprint=fingerprint,
    )
    if spec.get("independence") == "assisted":
        support["conceptual_scaffold"] = True

    if kind == "fastapi":
        entry = spec.get("entry", "app.py")
        res = verify_fastapi_app(files.get(entry, ""), spec.get("checks", []), backend=backend)
        available = res.get("available", False)
        results = res.get("results")
        checked = (available and isinstance(results, list) and bool(results)
                   and len(results) == len(spec.get("checks", []))
                   and all(isinstance(r, dict) and type(r.get("passed")) is bool for r in results))
        passed = bool(res.get("all_passed"))
        detail = res.get("detail")
        raw = res
    else:  # generic code
        lang = spec.get("language")
        if lang not in LANGUAGES:
            return {"status": "bad_check_spec", "module": module_id, "detail": f"unknown_language:{lang}"}
        entry = spec.get("entry", _DEFAULT_ENTRY[lang])
        if entry not in files:
            return {"status": "missing_entry", "module": module_id,
                    "detail": f"entry file {entry!r} not found in submission/"}
        source = files[entry]
        extra = {k: v for k, v in files.items() if k != entry}
        expect = spec.get("expect_stdout")
        res = run_exercise(lang, source, extra_files=extra, stdin=spec.get("stdin"),
                           expect_stdout=expect, backend=backend)
        available = res.get("available", False)
        checked = available and res.get("ran") is True and (expect is not None)
        passed = bool(res.get("passed")) and (expect is not None)
        detail = res.get("detail")
        raw = res

    # outcome + evidence quality (mirrors verify-python: only executed+checked is mastery-grade)
    if not available:
        outcome = "unknown"
    elif not checked:
        outcome = "unknown"   # ran but nothing asserted correctness
    else:
        outcome = "correct" if passed else "incorrect"

    feedback = {
        "module": module_id, "capability": capability, "kind": kind,
        "available": available, "correctness_checked": checked, "passed": passed,
        "outcome": outcome, "checked_at": now_iso(), "detail": detail, "raw": raw,
        "attempt_id": spec.get("attempt_id"), "support_provenance": support,
        "task_fingerprint": fingerprint,
    }
    fb_dir = mod / "feedback"
    fb_dir.mkdir(exist_ok=True)
    write_json(fb_dir / "result.json", feedback)
    (fb_dir / "feedback.md").write_text(_feedback_md(feedback), encoding="utf-8")

    evidence = None
    if outcome in {"correct", "incorrect"}:
        evidence = {
            "capability_id": capability, "outcome": outcome,
            "independence": spec.get("independence", "unknown"),
            "strength": "strong" if outcome == "correct" else "medium",
            "scope": scope, "evidence_format": "artifact_execution",
            "mastery_eligible": True, "correctness_checked": True,
            "support_provenance": support, "note": "workspace_check",
            "attempt_id": spec.get("attempt_id"),
            "task_fingerprint": fingerprint,
            "source_event_ids": [],
        }
    feedback["evidence"] = evidence
    feedback["status"] = "checked"
    return feedback


def _feedback_md(fb: dict) -> str:
    lines = [f"# Yoxlama nəticəsi — {fb['module']}", ""]
    if not fb["available"]:
        lines += [f"**İcra mümkün olmadı:** {fb.get('detail')}", "",
                  "Bu, dilin dəstəklənmədiyi demək DEYİL — executor backend lazımdır (yuxarıdakı mesaja bax)."]
        return "\n".join(lines) + "\n"
    if not fb["correctness_checked"]:
        lines += ["Kod işlədi, amma düzgünlük meyarı yoxdur (checks/check.json-a `expect_stdout`/`checks` əlavə et).",
                  "Mastery sübutu kimi sayılmır."]
        return "\n".join(lines) + "\n"
    lines += [f"**Nəticə:** {'KEÇDİ ✓' if fb['passed'] else 'KEÇMƏDİ ✗'}", ""]
    raw = fb.get("raw") or {}
    if fb["kind"] == "fastapi":
        for c in raw.get("results", []):
            lines.append(f"- {'✓' if c['passed'] else '✗'} {c['name']}" + ("" if c["passed"] else f" — {c.get('detail','')}"))
    else:
        if not fb["passed"]:
            lines += [f"Gözlənilən: `{raw.get('expected','')}`", f"Alınan: `{(raw.get('stdout') or '').strip()}`"]
            if raw.get("stderr"):
                lines += ["", "```", raw["stderr"][-600:], "```"]
    return "\n".join(lines) + "\n"

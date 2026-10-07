from __future__ import annotations

from pathlib import Path
import json
import re

from .execution import run_exercise, verify_fastapi_app, language, LANGUAGES
from .utils import write_json, now_iso

# GitHub-style check step: the learner writes code in a module's submission/
# folder; this reads it, runs it through the execution backend against the
# check spec the agent authored in checks/check.json, writes a result into
# feedback/, and returns an evidence payload. Deterministic for code; only
# executed + checked runs are mastery-eligible.

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_DEFAULT_ENTRY = {lid: spec.filename for lid, spec in LANGUAGES.items()}


def _gather_submission(sub: Path) -> dict[str, str]:
    files = {}
    if sub.is_dir():
        for p in sorted(sub.rglob("*")):
            if p.is_file() and p.name != ".gitkeep":
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

    spec_path = mod / "checks" / "check.json"
    if not spec_path.exists():
        return {"status": "no_check_spec", "module": module_id,
                "detail": "No checks/check.json yet. The agent authors the check spec "
                          "(language/entry/expected or FastAPI checks) before verification."}
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"status": "bad_check_spec", "module": module_id, "detail": f"invalid_json:{e}"}

    files = _gather_submission(mod / "submission")
    if not files:
        return {"status": "empty_submission", "module": module_id,
                "detail": "submission/ is empty — write your solution there, then check again."}

    kind = spec.get("kind", "code")
    capability = spec.get("capability", module_id)
    scope = spec.get("scope", "independent_reproduction")

    if kind == "fastapi":
        entry = spec.get("entry", "app.py")
        res = verify_fastapi_app(files.get(entry, ""), spec.get("checks", []), backend=backend)
        available = res.get("available", False)
        checked = available and bool(spec.get("checks"))
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
        checked = available and (expect is not None)
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
    }
    fb_dir = mod / "feedback"
    fb_dir.mkdir(exist_ok=True)
    write_json(fb_dir / "result.json", feedback)
    (fb_dir / "feedback.md").write_text(_feedback_md(feedback), encoding="utf-8")

    evidence = None
    if outcome in {"correct", "incorrect"}:
        evidence = {
            "capability_id": capability, "outcome": outcome,
            "independence": spec.get("independence", "unassisted"),
            "strength": "strong" if outcome == "correct" else "medium",
            "scope": scope, "evidence_format": "artifact_execution",
            "mastery_eligible": True, "correctness_checked": True,
            "support_provenance": {}, "note": "workspace_check",
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

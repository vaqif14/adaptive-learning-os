"""R7 source epistemics: every quote re-verified against the bound source object; sources validated; fail closed."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from .. import course_sources as csrc
from .base import Ctx, Result, fail, iter_refs, journey_obs, na, probe, verdict

MAX_SUPPLEMENTARY_SHARE = 0.5      # reported and thresholded: more than half unsourced refs is not "grounded"


def _find(ctx: Ctx):
    return ctx.hook("find_quote", csrc.find_quote)


def _used_keys(ctx: Ctx, subject: dict) -> list[str]:
    node = next((n for n in ctx.nodes() if n["id"] == subject["node"]), {})
    return list(node.get("source_refs") or [])


def _grounding(ctx: Ctx) -> Result:
    subjects = [s for s in ctx.subjects() if s.get("lesson")]
    if not subjects:
        return na("no lesson in the build or in the live course")
    manifest, find, cache = ctx.manifest(), _find(ctx), ctx.sources_dir()
    entries = {r.get("source_id"): r for r in (manifest or {}).get("resources", [])}
    problems, source_refs, supplementary = [], 0, 0
    for subject in subjects:
        lesson = subject["lesson"]
        for part in ("explanation", "why", "worked_example", "alternative_explanation", "walkthrough"):
            block = lesson.get(part)
            if isinstance(block, dict) and not block.get("refs"):
                if part != "walkthrough":
                    problems.append(f"{subject['node']}.{part}: a claim with no ref")
        for part, ref in iter_refs(lesson):
            if ref.get("supplementary") is True:
                supplementary += 1
                continue
            source_refs += 1
            if manifest is None:
                problems.append(f"{subject['node']}.{part}: no validated source manifest")
                continue
            res = find(cache, manifest, ref.get("source_id"), ref.get("quote"))
            if not res.get("grounded"):
                problems.append(f"{subject['node']}.{part}: quote not grounded ({res.get('reason')})")
            elif res.get("content_sha256") != (entries.get(ref.get("source_id")) or {}).get("content_sha256"):
                problems.append(f"{subject['node']}.{part}: quote verified against another content hash")
            expert = ref.get("expert_source")
            if expert is not None and not any(r.get("key") == expert and r.get("status") == "validated"
                                              for r in (manifest or {}).get("resources", [])):
                problems.append(f"{subject['node']}.{part}: expert attribution '{expert}' has no validated source")
    total = source_refs + supplementary
    share = supplementary / total if total else 0.0
    if share > MAX_SUPPLEMENTARY_SHARE:
        problems.append(f"{supplementary}/{total} refs are supplementary (more than {MAX_SUPPLEMENTARY_SHARE:.0%})")
    return verdict(problems, f"{source_refs} source quote(s) re-verified; supplementary share {share:.0%}",
                   source_refs=source_refs, supplementary_share=round(share, 3))


def _control_grounding():
    tmp = tempfile.TemporaryDirectory(prefix="rules-control-")
    root = Path(tmp.name)
    text = "The cosine of the angle between two vectors is their dot product divided by lengths. " * 6
    (root / "src-probe-001.txt").write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()
    manifest = {"resources": [{"key": "r1", "source_id": "src-probe-001", "status": "validated", "content_sha256": sha,
                               "stale": False}]}
    lesson = {"explanation": {"refs": [{"source_id": "src-probe-001", "quote": "an invented sentence that is not in the source"}]}}
    ctx = Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": None}], manifest=manifest,
                        nodes=[{"id": "n", "source_refs": ["r1"]}])
    ctx.sources_dir = lambda: root
    ctx._tmp = tmp
    return ctx


@probe("r07.quotes_grounded", "R7", control=_control_grounding, title="every lesson/walkthrough quote re-verifies against the source")
def quotes_grounded(ctx: Ctx) -> Result:
    return _grounding(ctx)


def _sources(ctx: Ctx) -> Result:
    subjects = ctx.subjects()
    if not subjects:
        return na("no content in the build or in the live course")
    manifest = ctx.manifest()
    if manifest is None:
        return fail("there is no validated source manifest")
    problems = []
    if not manifest.get("complete"):
        problems.append("the source manifest is not complete")
    resources = {r.get("key"): r for r in manifest.get("resources", [])}
    allow_private = bool((ctx.job or {}).get("policy", {}).get("allow_private_sources"))
    used = sorted({k for s in subjects for k in _used_keys(ctx, s)})
    for key in used:
        res = resources.get(key)
        if res is None:
            problems.append(f"source {key} is not in the manifest")
            continue
        if res.get("status") != "validated":
            problems.append(f"source {key} is {res.get('status')}, not validated")
        if res.get("stale"):
            problems.append(f"source {key} is stale")
        kind = (res.get("expected") or {}).get("kind", res.get("kind"))
        if kind not in (None, "url") and not allow_private and (ctx.job or {}).get("unit_order"):
            problems.append(f"source {key} is private ({kind}) but the job has no private-source authorization")
        if (res.get("expected") or {}).get("rights") not in (None, "allowed", "own", "owned", "public", "licensed", "open"):
            if (res.get("expected") or {}).get("rights") in {"forbidden", "denied", "restricted"}:
                problems.append(f"source {key} has rights '{res['expected']['rights']}'")
    return verdict(problems, f"{len(used)} used source(s) validated and not stale (live stale_check needs the NotebookLM "
                             "CLI and is not run by the offline gate)", used=used)


def _control_sources():
    manifest = {"complete": False, "resources": [{"key": "r1", "status": "identity_mismatch", "stale": True}]}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": {}, "items": None}], manifest=manifest,
                         nodes=[{"id": "n", "source_refs": ["r1"]}], job=None)


@probe("r07.sources_validated", "R7", control=_control_sources, title="manifest complete; every used source validated and fresh")
def sources_validated(ctx: Ctx) -> Result:
    return _sources(ctx)


# ----------------------------------------------------------- fail-closed behaviour of find_quote
def _fail_closed(ctx: Ctx) -> Result:
    find = _find(ctx)
    text = ("Fixture source for the gate. The cosine of the angle between two vectors is their dot product divided by "
            "the product of their lengths. Cosine similarity compares direction only. " * 4)
    quote = "dot product divided by the product of their lengths"
    problems = []
    with tempfile.TemporaryDirectory(prefix="rules-quote-") as folder:
        cache = Path(folder)
        path = cache / "src-probe-001.txt"
        path.write_bytes(text.encode())
        sha = hashlib.sha256(text.encode()).hexdigest()
        entry = {"source_id": "src-probe-001", "status": "validated", "content_sha256": sha, "stale": False}
        manifest = {"resources": [entry]}
        if not find(cache, manifest, "src-probe-001", quote).get("grounded"):
            problems.append("a verbatim quote is not grounded (the probe is inconclusive)")
        if find(cache, manifest, "src-probe-001", quote.replace("divided", "divides")).get("grounded"):
            problems.append("a quote altered by one word was accepted")
        if find(cache, manifest, "src-probe-001", quote[:-1] + "z").get("grounded"):
            problems.append("a quote altered by one character was accepted")
        if find(cache, manifest, "src-probe-001", "an invented claim that is nowhere in the source text").get("grounded"):
            problems.append("an invented claim was accepted")
        if find(cache, manifest, "src-missing-002", quote).get("grounded"):
            problems.append("a quote from a source that is not in the manifest was accepted")
        if find(cache, {"resources": [{**entry, "status": "unreadable"}]}, "src-probe-001", quote).get("grounded"):
            problems.append("a quote from an unreadable source was accepted")
        path.write_bytes(text.encode() + b" tampered")               # the cached object no longer hashes to the manifest
        if find(cache, manifest, "src-probe-001", quote).get("grounded"):
            problems.append("a quote was grounded against a corrupted cached object")
    return verdict(problems, "altered quotes, invented claims, unknown/unreadable sources and corrupted objects fail closed")


def _control_fail_closed():
    return Ctx.synthetic(hooks={"find_quote": lambda cache, manifest, sid, quote: {"grounded": True}})


@probe("r07.fail_closed", "R7", control=_control_fail_closed, title="find_quote fails closed on altered, invented or corrupted input")
def fail_closed(ctx: Ctx) -> Result:
    return _fail_closed(ctx)


def _scratch_corruption(ctx: Ctx) -> Result:
    obs = journey_obs(ctx, "source_attacks")
    if not obs:
        return na("the scratch course has no source-grounded lesson to attack")
    problems = [f"{name}: not refused" for name, row in obs.items() if not row.get("refused")]
    return verdict(problems, "a corrupted cached object, an altered quote and a claim without a ref are all refused in the scratch copy")


def _control_scratch_corruption():
    return Ctx.synthetic(scratch=type("S", (), {"obs": {"source_attacks": {"corrupt_object": {"refused": False}}}})())


@probe("r07.scratch_corruption", "R7", kind="dynamic", cost="scratch", control=_control_scratch_corruption,
       title="corrupt source object / altered quote / ref-less claim are refused on the scratch copy")
def scratch_corruption(ctx: Ctx) -> Result:
    return _scratch_corruption(ctx)


PROBES = [quotes_grounded, sources_validated, fail_closed, scratch_corruption]

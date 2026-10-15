"""Shared machinery of the regenerated 10-rule gate: probe type, results and the read-only context.

A probe is ``fn(ctx) -> Result``. It never reads a previous receipt or an agent claim: everything comes from the
context accessors, which read the session files, the ledger and the staged build afresh. Every probe has an id, a
version and a NEGATIVE CONTROL: a builder returning a deliberately faulty context for which the probe must report a
failure (otherwise the engine declares the suite broken). Enforcement functions are looked up at call time (module
attributes or ``ctx.hook``) so a weakened implementation flips the matching probe.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import course_state as cs
from ..ledger import EventLedger
from ..utils import read_json

RUNTIME_DIR = Path(__file__).resolve().parents[1]
RULES = tuple(f"R{i}" for i in range(1, 11))
HARD_RULES = ("R1", "R4", "R6", "R7", "R8", "R10")
WAIVABLE_RULES = ("R2", "R3", "R5")      # R9 can only be fixed by content, never overridden by a reviewer
FIX_ONLY_RULES = ("R9",)
# What a receipt claims, in both languages. The wording is checked by R9: it must never say "quality proven".
# (Azerbaijani text: native review advisable.)
RECEIPT_CLAIM = {
    "id": "invariants_hold_under_probes",
    "text_en": "These invariants hold under these probes at this ledger position. This is not a statement of course quality.",
    "text_az": ("Bu qəbz yalnız onu bildirir: göstərilən yoxlamalar altında bu qaydalar bu jurnal vəziyyətində "
                "pozulmayıb. Bu, kursun keyfiyyəti haqqında iddia deyil."),
}
COSTS = ("cheap", "sandbox", "scratch")  # quick scope runs "cheap" only; full runs everything


class NotApplicable(Exception):
    """Raised by a probe (or an accessor) when its subject does not exist; the reason is reported, never a pass."""


@dataclass
class Result:
    passed: bool | None
    detail: str = ""
    data: dict = field(default_factory=dict)


def ok(detail: str = "", **data) -> Result:
    return Result(True, detail, data)


def fail(detail: str, **data) -> Result:
    return Result(False, detail, data)


def na(reason: str) -> Result:
    return Result(None, reason, {})


def verdict(problems: list[str], detail_ok: str = "", **data) -> Result:
    return fail("; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""), problems=problems, **data) \
        if problems else ok(detail_ok, **data)


class Probe:
    def __init__(self, fn, *, id: str, rule: str, version: int, kind: str, cost: str, control, title: str, order: int):
        assert rule in RULES and kind in ("deterministic", "dynamic") and cost in COSTS, id
        self.fn, self.id, self.rule, self.version, self.kind, self.cost = fn, id, rule, version, kind, cost
        self.control, self.title, self.order = control, title, order

    def __call__(self, ctx) -> Result:
        return self.fn(ctx)

    def descriptor(self) -> dict:
        return {"id": self.id, "version": self.version, "rule": self.rule, "kind": self.kind, "cost": self.cost}


def probe(id: str, rule: str, *, control, kind: str = "deterministic", cost: str = "cheap", version: int = 1,
          title: str = "", order: int = 0):
    def wrap(fn):
        return Probe(fn, id=id, rule=rule, version=version, kind=kind, cost=cost, control=control, title=title or id,
                     order=order)
    return wrap


class Ctx:
    """Read-only view of one rules run. Synthetic contexts (negative controls) pass ``overrides`` that replace any
    accessor value; ``hooks`` replace enforcement functions."""

    def __init__(self, workspace=None, sid=None, *, job=None, scope="quick", clock=None, hooks=None, **overrides):
        self.workspace = Path(workspace) if workspace is not None else None
        self.sid, self.job, self.scope, self.clock = sid, job, scope, clock
        self.hooks = dict(hooks or {})
        self._o = overrides
        self._cache = {}
        self.before = overrides.get("before")
        self.scratch = overrides.get("scratch")
        self.staged_errors: dict = {}
        self.extra_notes: list[str] = []
        self.progress = None            # optional zero-argument callback a host (the worker) uses to keep its heartbeat fresh

    @classmethod
    def synthetic(cls, **overrides):
        return cls(None, None, **overrides)

    def hook(self, name, default):
        return self.hooks.get(name, default)

    def _get(self, name, loader):
        if name in self._o:
            return self._o[name]
        if name not in self._cache:
            self._cache[name] = loader()
        return self._cache[name]

    # ---------------------------------------------------------------- paths
    @property
    def session_dir(self) -> Path:
        if "session_dir" in self._o:
            return Path(self._o["session_dir"])
        if self.workspace is None:
            raise NotApplicable("no session in this context")
        from ..session import SessionKernel
        return SessionKernel(self.workspace).dir(self.sid).resolve()

    @property
    def root(self) -> Path | None:
        if "root" in self._o:
            return self._o["root"]
        if self.workspace is None and "session_dir" not in self._o:
            return None
        return self.session_dir / "studio" if (self.session_dir / "studio").is_dir() else None

    def sources_dir(self) -> Path:
        return self.session_dir / "sources"

    # --------------------------------------------------------------- ledger
    def led(self) -> EventLedger:
        return self._get("led", lambda: EventLedger(self.session_dir / "ledger.jsonl", self.sid))

    def records(self) -> list[dict]:
        return self._get("records", lambda: self.led().verified_records())

    def integrity(self) -> dict:
        return self._get("integrity", lambda: self.led().integrity())

    # --------------------------------------------------------------- course
    def state(self) -> dict | None:
        def load():
            path = self.session_dir / "course-state.json"
            if not path.is_file():
                return None
            try:
                return cs.migrate(read_json(path))[0]
            except (OSError, ValueError):
                return None
        return self._get("state", load)

    def plan(self) -> dict | None:
        def load():
            path = self.session_dir / "workspace" / "roadmap.json"
            try:
                plan = read_json(path) if path.is_file() else None
            except (OSError, ValueError):
                return None
            return plan if isinstance(plan, dict) and plan.get("status") != "needs_curriculum_research" else None
        return self._get("plan", load)

    def nodes(self) -> list[dict]:
        return self._get("nodes", lambda: list((self.plan() or {}).get("nodes") or []))

    def manifest(self) -> dict | None:
        def load():
            path = self.session_dir / "source-manifest.json"
            try:
                return read_json(path) if path.is_file() else None
            except (OSError, ValueError):
                return None
        return self._get("manifest", load)

    def targets(self) -> dict:
        from .. import studio_plan as plan_mod
        job = self.job
        return (job or {}).get("policy", {}).get("targets") or plan_mod.TARGETS_V2

    def binding(self) -> dict:
        return (self.job or {}).get("binding") or {}

    # --------------------------------------------------------------- build
    def staged_units(self) -> dict:
        """{unit_id: payload} of every staged unit of the job, read and digest-verified from disk."""
        def load():
            from .. import studio_staging as staging
            from ..studio_store import StudioError
            out, job = {}, self.job
            if not job or self.root is None:
                return out
            for uid in job.get("unit_order") or []:
                unit = job["units"][uid]
                if unit.get("status") == "staged" and unit.get("staged_digest"):
                    try:
                        out[uid] = staging.read_staged(self.root, job["job_id"], uid, unit["staged_digest"])
                    except StudioError as e:
                        self.staged_errors[uid] = e.code
            return out
        return self._get("staged_units", load)

    def subjects(self) -> list[dict]:
        """[{node, lesson, items, origin}] of the content under audit: the staged build, else the live course."""
        def load():
            staged = self.staged_units()
            if self.job and self.job.get("unit_order"):
                nodes = []
                for node in self.nodes():
                    nid = node["id"]
                    items = (staged.get(f"items:{nid}") or {}).get("items")
                    nodes.append({"node": nid, "lesson": staged.get(f"lesson:{nid}"), "items": items, "origin": "staged"})
                return nodes
            return self._live_subjects()
        return self._get("subjects", load)

    def _live_subjects(self) -> list[dict]:
        state, out = self.state(), []
        if not state:
            return out
        d = self.session_dir
        for node in self.nodes():
            nid = node["id"]
            lesson, items = None, []
            version = state["content_versions"].get(nid)
            if version:
                for path in (d / "course" / "lessons" / "archive" / f"{version}.json", d / "course" / "lessons" / f"{nid}.json"):
                    if path.is_file():
                        with contextlib.suppress(OSError, ValueError):
                            doc = read_json(path)
                            if doc.get("content_version") == version:
                                lesson = doc
                                break
            for item_id, iv in sorted((state["item_versions"].get(nid) or {}).items()):
                for path in (d / "assessment-vault" / nid / "archive" / f"{iv}.json", d / "assessment-vault" / nid / f"{item_id}.json"):
                    if path.is_file():
                        with contextlib.suppress(OSError, ValueError):
                            doc = read_json(path)
                            if doc.get("item_id") == item_id and doc.get("item_version") == iv:
                                items.append(doc)
                                break
            if lesson or items:
                out.append({"node": nid, "lesson": lesson, "items": items or None, "origin": "live"})
        return out

    # ------------------------------------------------------------ handover
    def handover(self) -> dict | None:
        """{"json": dict, "md": str} of the newest handover for the job's current build digest, if one exists."""
        def load():
            job = self.job
            if not job or not job.get("build_digest") or self.root is None:
                return None
            from .. import studio_handover as ho
            return ho.load_latest(self.root, job["job_id"], job["build_digest"])
        return self._get("handover", load)

    # ----------------------------------------------------------- scratch
    def require_scratch(self):
        if self.scratch is None:
            raise NotApplicable("needs a scratch copy: run the full scope")
        return self.scratch

    def course(self):
        if "course" in self.hooks:
            return self.hooks["course"]()
        return self.require_scratch().course()


# --------------------------------------------------------------- helpers
LESSON_REF_PARTS = ("explanation", "why", "worked_example", "alternative_explanation", "walkthrough")


def iter_refs(lesson: dict):
    """(part, ref) for every ref of a lesson payload (raw or normalised)."""
    for part in LESSON_REF_PARTS:
        block = lesson.get(part)
        if isinstance(block, dict):
            for ref in block.get("refs") or []:
                if isinstance(ref, dict):
                    yield part, ref


def walk_strings(obj, path=""):
    """(path, string) for every string inside a JSON-like object."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def journey_obs(ctx: Ctx, key: str):
    """An observation recorded by the scripted scratch journey. Missing because the journey had nothing to run (no
    staged build) is 'not applicable'; missing for any other reason is a probe error, never a pass."""
    scratch = ctx.require_scratch()
    if key in scratch.obs:
        return scratch.obs[key]
    if scratch.obs.get("skipped"):
        raise NotApplicable(scratch.obs["skipped"])
    raise RuntimeError(f"the scratch journey did not record '{key}': {scratch.obs.get('errors')}")


def expect_refusal(fn, codes=None):
    """Run ``fn``; return (refused, code). A refusal is a CourseStateError (or ValueError for the legacy kernel)."""
    from ..course_state import CourseStateError
    try:
        fn()
    except CourseStateError as e:
        return (codes is None or e.code in codes), e.code
    except ValueError as e:
        return codes is None, type(e).__name__
    return False, None

"""The dynamic half of the full scope: one scripted journey on the scratch copy that records raw observations.

Everything here happens on the isolated scratch copy (its own sessions directory and its own ALEARN_STATE_DIR); the
real session is only read. The journey records WHAT HAPPENED (refusal codes, evidence payloads, HTTP statuses) in
``scratch.obs``; the dynamic probes judge those observations, so each probe stays a small pure check with a negative
control. A step that crashes records its error under ``obs['errors']`` and the probes that needed it report an error
(exit 3), never a pass.
"""
from __future__ import annotations

import contextlib
import ast
import copy
import json
import os
from pathlib import Path

from .. import course_content as cc
from .. import course_sources as csrc
from .. import studio_jobs as jobs
from .. import studio_receipt as receipts
from ..course import CourseKernel, assert_no_active_exam
from ..course_state import CourseStateError
from ..ledger import EventLedger
from ..session import SessionKernel
from ..studio_store import digest_of
from ..utils import read_json, write_bytes, write_json
from .base import Ctx, expect_refusal
from .r10 import route_paths

STEP_ERRORS = (CourseStateError, OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, RuntimeError,
               AssertionError, StopIteration, LookupError)
REHEARSAL_STATEMENT = "rules-check rehearsal on a scratch copy; the real session is untouched"
CLI_LOCKED = ("set-roadmap", "sources-ask", "evidence", "attest", "verify-python", "verify-exercise", "rubric-check",
              "listener-check", "workspace-check", "add-card", "scaffold-cards", "review-card")


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
STUDIO_LOCK_GUARDS = ("plan", "next_task", "submit")     # studio entry points that must consult the exam lock


def cli_lock_facts(path: Path | None = None) -> dict:
    """What scripts/alearn.py really wires: its EXAM_LOCKED_COMMANDS set and the dispatch that enforces it (read from
    the source, so removing a command or the dispatch check changes the observation)."""
    tree = ast.parse(Path(path or SCRIPTS_DIR / "alearn.py").read_text(encoding="utf-8"))
    commands = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "EXAM_LOCKED_COMMANDS" for t in node.targets):
            commands = set(ast.literal_eval(node.value))
    dispatch, sources_ask = False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            body = "\n".join(ast.unparse(n) for n in node.body)
            if "EXAM_LOCKED_COMMANDS" in test and "assert_no_active_exam(" in body:
                dispatch = True
                sources_ask = "sources-ask" in test
    return {"commands": commands, "dispatch": dispatch, "sources_ask": sources_ask}


def studio_lock_guards(path: Path | None = None) -> dict[str, bool]:
    """Whether each studio entry point's code consults the exam lock (read from studio.py)."""
    tree = ast.parse(Path(path or Path(__file__).resolve().parents[1] / "studio.py").read_text(encoding="utf-8"))
    out = {name: False for name in STUDIO_LOCK_GUARDS}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in out:
            out[node.name] = any(isinstance(n, ast.Constant) and n.value == "exam_locked" for n in ast.walk(node))
    return out


class Scratch:
    """An isolated copy of the session: <run_dir>/ws/.learning/sessions/<sid>, state in <run_dir>/state."""

    def __init__(self, run_dir: Path, ws: Path, sid: str, state_dir: Path, clock=None):
        self.run_dir, self.ws, self.sid, self.state_dir, self.clock = Path(run_dir), Path(ws), sid, Path(state_dir), clock
        self.session_dir = SessionKernel(self.ws).dir(sid).resolve()
        self.obs: dict = {"errors": {}}
        self.payloads: list[tuple[str, str]] = []

    def kernel(self) -> SessionKernel:
        return SessionKernel(self.ws)

    def course(self) -> CourseKernel:
        return CourseKernel(self.ws, clock=self.clock)

    def studio(self):
        from ..studio import StudioKernel
        return StudioKernel(self.ws, clock=self.clock)

    def led(self) -> EventLedger:
        return EventLedger(self.session_dir / "ledger.jsonl", self.sid)

    def evidence(self) -> list[dict]:
        return [r for r in self.led().verified_records() if r["record_type"] == "evidence"]


def _step(scratch: Scratch, name: str, fn):
    try:
        return fn()
    except STEP_ERRORS as e:
        scratch.obs["errors"][name] = f"{type(e).__name__}: {str(e)[:300]}"
        return None


def _collect(scratch: Scratch, name: str, value):
    scratch.payloads.append((name, json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)))


def _json(path: Path):
    return read_json(path)


def _state_bytes(scratch: Scratch) -> bytes:
    return (scratch.session_dir / "course-state.json").read_bytes()


# ------------------------------------------------------------------ http probes
def _http(ctx: Ctx, scratch: Scratch) -> None:
    from ..rules_loopback import LoopbackServer
    from ..server import ServerConfig
    job = ctx.job or {}
    ui = scratch.ws / "ui"
    ui.mkdir(parents=True, exist_ok=True)
    os.chmod(ui, 0o700)
    write_bytes(ui / "index.html", b"<html><body>course ui</body></html>")
    markers = set()
    state = _json(scratch.session_dir / "course-state.json")
    live = job.get("build_digest") in {b.get("build_digest") for b in (state.get("studio") or {}).get("promoted_builds", [])}
    for uid, payload in ctx.staged_units().items():
        markers.add(job["units"][uid]["staged_digest"])
        if live:            # an already promoted build is legitimately served; only its staging identity must stay hidden
            continue
        if uid.startswith("lesson:"):
            markers.add(str(payload.get("objective"))[:40])
            markers.add(str((payload.get("walkthrough") or {}).get("title") or "")[:30])
        else:
            markers.update(str(i.get("item_id")) for i in payload.get("items") or [])
    markers.discard("")
    markers.discard("None")
    markers.add("studio/staging")
    unit = next(iter(job.get("unit_order") or []), "lesson:x")
    digest = (job.get("units", {}).get(unit) or {}).get("staged_digest") or "0" * 64
    folder = unit.replace(":", "--")
    node = unit.split(":", 1)[-1]
    sid = scratch.sid
    paths = [
        (f"/studio/staging/{job.get('job_id')}/{folder}/{digest}.json", True),
        (f"/../studio/staging/{job.get('job_id')}/{folder}/{digest}.json", True),
        (f"/%2e%2e/studio/staging/{job.get('job_id')}/{folder}/{digest}.json", True),
        (f"/..%2fstudio%2fstaging%2f{job.get('job_id')}", True),
        ("/studio/jobs/" + str(job.get("job_id")) + ".json", True),
        ("/api/studio", True), ("/api/studio/staging", True), ("/api/studio/handover", True),
        (f"/api/studio/node?node={node}", True),
        ("/api/course/state", False), (f"/api/course/lesson?node={node}", False), ("/api/course/export", False),
    ]
    rows, guard = [], {}
    with LoopbackServer(scratch.ws, sid, ui_dir=ui) as srv:
        for path, must_refuse in paths:
            status, body = srv.request("GET", path)
            rows.append({"path": path, "status": status, "must_refuse": must_refuse,
                         "leak": any(m and m in body for m in markers)})
        guard["baseline"] = srv.request("GET", "/api/course/state")[0]
        guard["foreign_host"] = srv.request("GET", "/api/course/state", headers={"Host": "evil.example"})[0]
        guard["foreign_origin"] = srv.request("GET", "/api/course/state", headers={"Origin": "http://evil.example"})[0]
        guard["other_port_origin"] = srv.request("GET", "/api/course/state", headers={"Origin": "http://127.0.0.1:1"})[0]
        guard["non_json_post"] = srv.request("POST", "/api/course/resume", headers={"Content-Type": "text/plain"},
                                             body=b"{}")[0]
        server_source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
        studio_routes = [p for p in route_paths(server_source) if p.startswith("/api/studio")]
        guard["studio_routes"] = {p: srv.request("GET", p, headers={"Host": "evil.example"})[0] for p in studio_routes}
    guard.update(_owner_guard(scratch, ui, sid))
    try:
        # token="" (not None): None would fall back to ADAPTIVE_API_TOKEN from the environment and make the probe depend on it
        ServerConfig(host="0.0.0.0", port=0, allow_anon=True, token="")
        guard["token_required_off_loopback"] = False
    except ValueError:
        guard["token_required_off_loopback"] = True
    scratch.obs["http_staged"], scratch.obs["http_guard"] = rows, guard


OWNER_PROBE_TOKEN = "rules-gate-owner-probe-token-0001"      # synthetic, loopback + ephemeral port, scratch copy only


def _owner_guard(scratch: Scratch, ui, sid: str) -> dict:
    """The owner server (stage E): token required (never anonymous, never the learner's), foreign Host/Origin refused,
    JSON-only posts, and no learner route mounted. ``owner_baseline`` proves the guard is not vacuously refusing everything."""
    from ..rules_loopback import LOOPBACK, LoopbackServer
    from ..server import ServerConfig
    auth = {"Authorization": f"Bearer {OWNER_PROBE_TOKEN}"}
    out = {}
    with LoopbackServer(scratch.ws, sid, ui_dir=ui, owner_token=OWNER_PROBE_TOKEN) as owner:
        out["owner_baseline"] = owner.request("GET", "/api/studio", headers=auth)[0]
        out["owner_no_token"] = owner.request("GET", "/api/studio")[0]
        out["owner_wrong_token"] = owner.request("GET", "/api/studio", headers={"Authorization": "Bearer " + "x" * 32})[0]
        out["owner_foreign_host"] = owner.request("GET", "/api/studio", headers={**auth, "Host": "evil.example"})[0]
        out["owner_foreign_origin"] = owner.request("GET", "/api/studio", headers={**auth, "Origin": "http://evil.example"})[0]
        out["owner_non_json_post"] = owner.request("POST", "/api/studio/pause", headers={**auth, "Content-Type": "text/plain"},
                                                   body=b"{}")[0]
        out["owner_no_learner_routes"] = owner.request("GET", "/api/course/state", headers=auth)[0]
    for name, kwargs in (("owner_anon_refused", {"allow_anon": True, "owner_token": OWNER_PROBE_TOKEN}),
                         ("owner_token_required", {"owner_token": None}),
                         ("owner_token_differs_from_learner", {"owner_token": OWNER_PROBE_TOKEN, "token": OWNER_PROBE_TOKEN})):
        try:
            ServerConfig(host=LOOPBACK, port=0, workspace=scratch.ws, owner_session=sid, **kwargs)
            out[name] = False
        except ValueError:
            out[name] = True
    return out


# ------------------------------------------------------------ promotion + attacks
def _approve_in_scratch(scratch: Scratch, version: str) -> None:
    ck = scratch.course()
    state = _json(scratch.session_dir / "course-state.json")
    if state.get("approved_roadmap_version") == version:
        return
    ck.approve_roadmap(scratch.sid, version, channel="cli", statement=REHEARSAL_STATEMENT)


def _bundle(ctx: Ctx) -> dict:
    return receipts.bundle_from_job(ctx.job, ctx.staged_units())


def _attacks(ctx: Ctx, scratch: Scratch, bundle: dict) -> dict:
    ck, sid = scratch.course(), scratch.sid
    before = _state_bytes(scratch)
    out = {}

    def attempt(name, mutate, *, rehearsal=True, codes=None):
        data = mutate(copy.deepcopy(bundle))
        refused, code = expect_refusal(lambda: ck.promote_build(sid, data, rehearsal=rehearsal), codes)
        out[name] = {"refused": refused, "code": code}

    def byte_change(b):
        unit = next(u for u in b["units"] if u["kind"] == "lesson")
        unit["payload"]["objective"] = str(unit["payload"].get("objective")) + " x"
        return b

    def resign(b, **fields):
        b.update(fields)
        digests = {u["unit_id"]: digest_of(u["payload"]) for u in b["units"]}
        b["build_digest"] = jobs.build_digest_of(digests, b["roadmap_version"], b["sources"], b["policy_versions"])
        return b

    attempt("staged_byte_change", byte_change)
    attempt("source_hash_change", lambda b: resign(b, sources={**b["sources"], "ghost-source-key": "0" * 64}))
    attempt("capability_bump", lambda b: {**b, "capabilities": {k: v + "-bumped" for k, v in b["capabilities"].items()}})
    attempt("roadmap_version_mismatch", lambda b: {**b, "roadmap_version": "rm-0000000000000000"})
    attempt("no_acceptance", lambda b: {**b, "job_id": "job_" + "0" * 16}, rehearsal=False)
    fake_job = "job_" + "1" * 16
    scratch.led().append("event", {"event_type": receipts.ACCEPT_EVENT, "job_id": fake_job, "build_digest": "f" * 64,
                                   "statement": "rules-check control", "channel": "owner_cli"}, {"source": receipts.OWNER_SOURCE})
    attempt("wrong_digest_acceptance", lambda b: {**b, "job_id": fake_job}, rehearsal=False)
    refused, code = expect_refusal(lambda: ck.approve_roadmap(sid, bundle["roadmap_version"], channel="agent",
                                                              statement="agent approving on the learner's behalf"))
    out["agent_channel_approval"] = {"refused": refused, "code": code}
    out["state_unchanged"] = _state_bytes(scratch) == before
    return out


def _promote(ctx: Ctx, scratch: Scratch) -> None:
    bundle = _bundle(ctx)
    _approve_in_scratch(scratch, bundle["roadmap_version"])
    attacks = _step(scratch, "binding_attacks", lambda: _attacks(ctx, scratch, bundle))
    if attacks is not None:
        scratch.obs["binding_attacks"] = attacks
    ck, sid = scratch.course(), scratch.sid
    evidence_before = len(scratch.evidence())
    attempts_before = len(_json(scratch.session_dir / "course-state.json").get("attempts") or {})
    try:
        res = ck.promote_build(sid, bundle, rehearsal=True)
    except STEP_ERRORS as e:
        scratch.obs["promotion"] = {"error": f"{getattr(e, 'code', type(e).__name__)}: {getattr(e, 'detail', e)}"[:400]}
        return
    state = _json(scratch.session_dir / "course-state.json")
    scratch.obs["promotion"] = {"promoted": bool(res.get("promoted")), "already_promoted": bool(res.get("already_promoted")),
                                "installed_nodes": sorted(res.get("nodes") or []),
                                "plan_nodes": sorted(n["id"] for n in ctx.nodes()),
                                "evidence_added": len(scratch.evidence()) - evidence_before,
                                "attempts_added": len(state.get("attempts") or {}) - attempts_before}


# ------------------------------------------------------------------- sources
def _cited(lesson: dict):
    for part in ("explanation", "why", "worked_example", "alternative_explanation", "walkthrough"):
        block = lesson.get(part)
        for ref in (block or {}).get("refs") or []:
            if ref.get("source_id") and ref.get("quote"):
                return part, ref
    return None


def _object_files(scratch: Scratch, source_id: str, sha: str) -> list[Path]:
    """Every cached copy of the source text (the content-addressed object and the legacy per-source file)."""
    cache = scratch.session_dir / "sources"
    return [p for p in (csrc.object_path(cache, sha), cache / f"{source_id}.txt") if p.is_file() and not p.is_symlink()]


def _source_attacks(ctx: Ctx, scratch: Scratch) -> None:
    ck, sid = scratch.course(), scratch.sid
    manifest = _json(scratch.session_dir / "source-manifest.json")
    target = next(((s["node"], s["lesson"]) for s in ctx.subjects() if s.get("lesson") and _cited(s["lesson"])), None)
    if target is None:
        scratch.obs["source_attacks"] = {}
        return
    node, lesson = target
    part, ref = _cited(lesson)
    sha = next(r["content_sha256"] for r in manifest["resources"] if r.get("source_id") == ref["source_id"])
    out = {}
    copies = _object_files(scratch, ref["source_id"], sha)
    if copies:
        originals = {p: p.read_bytes() for p in copies}
        for p, data in originals.items():
            write_bytes(p, data + b" corrupted")
        try:
            status = ck.lesson_view(sid, node)["lesson"]["status"]      # the lesson closes: status, not an exception
            refused, code = status == "grounding_invalid", status
        finally:
            for p, data in originals.items():
                write_bytes(p, data)
        out["corrupt_object"] = {"refused": refused, "code": code}
    altered = copy.deepcopy(lesson)
    altered[part]["refs"][0] = {"source_id": ref["source_id"], "quote": ref["quote"][:-1] + ("z" if ref["quote"][-1] != "z" else "y")}
    res = ck.validate_lesson_dry(sid, node, altered)
    out["altered_quote"] = {"refused": not res["valid"], "code": (res["errors"] or [None])[0]}
    claimless = copy.deepcopy(lesson)
    claimless["explanation"]["refs"] = []
    res = ck.validate_lesson_dry(sid, node, claimless)
    out["claim_without_ref"] = {"refused": not res["valid"], "code": (res["errors"] or [None])[0]}
    scratch.obs["source_attacks"] = out


# -------------------------------------------------------------------- canary
def _plant(scratch: Scratch, token: str, *, lessons: bool, items: bool) -> int:
    """Plant canary tokens in hidden fields. Vault items are hash-bound to their exams (a changed item voids an open
    attempt), so items are only marked after every exam of the journey is over."""
    planted = 0
    d = scratch.session_dir
    files = ((sorted((d / "course" / "lessons").rglob("*.json")) if lessons else [])
             + (sorted((d / "assessment-vault").rglob("*.json")) if items else []))
    for path in files:
        doc = _json(path)

        def mark(text):
            return f"{text}\n# {token}\n"

        practice = doc.get("practice") if isinstance(doc.get("practice"), dict) else None
        for holder in (doc, practice):
            if not holder:
                continue
            if isinstance(holder.get("reference_solution"), str):
                holder["reference_solution"] = mark(holder["reference_solution"])
            if isinstance(holder.get("exemplar"), str):
                holder["exemplar"] = holder["exemplar"] + " " + token
            for check in holder.get("checks") or []:
                if isinstance(check.get("code"), str) and holder is doc:
                    check["code"] = mark(check["code"])
            for wrong in list((holder.get("wrong_examples") or {})):
                holder["wrong_examples"][wrong] = mark(holder["wrong_examples"][wrong])
        doc["_canary"] = token
        write_json(path, doc)
        planted += 1
    return planted


def _learner_payloads(scratch: Scratch, nodes: list[str]) -> None:
    ck, sid = scratch.course(), scratch.sid
    _collect(scratch, "state", ck.state(sid))
    _collect(scratch, "export", ck.export(sid))
    for node in nodes:
        with contextlib.suppress(CourseStateError):
            _collect(scratch, f"lesson_view:{node}", ck.lesson_view(sid, node))


# ------------------------------------------------------------------ practice
def _practice_node(ctx: Ctx, scratch: Scratch):
    """First node (prerequisite-free) whose live lesson has a python practice and a hint ceiling."""
    state = _json(scratch.session_dir / "course-state.json")
    for node in ctx.nodes():
        version = state["content_versions"].get(node["id"])
        if not version or node.get("requires"):
            continue
        lesson = _json(scratch.session_dir / "course" / "lessons" / "archive" / f"{version}.json")
        if cc.item_kind(lesson["practice"]) == "python" and "hint_ceiling" in lesson:
            return node["id"], lesson
    return None, None


def _practice(ctx: Ctx, scratch: Scratch) -> None:
    node, lesson = _practice_node(ctx, scratch)
    if node is None:
        scratch.obs["hints"] = {"not_applicable": "no prerequisite-free node has a python practice with a hint ceiling"}
        return
    ck, sid = scratch.course(), scratch.sid
    _collect(scratch, "start_mission", ck.start_mission(sid, node))
    for kind in ("explain", "why", "worked_example", "alternative", "walkthrough"):
        if kind == "walkthrough" and not lesson.get("walkthrough"):
            continue
        _collect(scratch, f"help.{kind}", ck.help(sid, node, kind))
    if lesson.get("ladder"):
        for rung in cc.LADDER_RUNGS:
            _collect(scratch, f"help.ladder.{rung}", ck.help(sid, node, "ladder", rung=rung))
    _collect(scratch, "begin_practice", ck.begin_practice(sid, node))
    ceiling = int(lesson["hint_ceiling"])
    for _ in range(ceiling):
        _collect(scratch, "help.hint", ck.help(sid, node, "hint"))
    obs = {}
    refused, code = expect_refusal(lambda: ck.help(sid, node, "hint"), {"hint_ceiling_reached"})
    obs["ceiling_refused"], obs["ceiling_code"] = refused, code
    ck.practice_check(sid, node)                               # starter code, after hints: evidence must be assisted
    last = [r for r in scratch.evidence() if r["payload"].get("capability_id") == node][-1]
    obs["hinted_evidence"] = {"payload": last["payload"], "provenance": last["provenance"]}
    obs["hinted_independence"] = last["payload"].get("independence")
    refused, code = expect_refusal(lambda: ck.practice_reveal(sid, node, confirm=False), {"reveal_confirmation_required"})
    obs["reveal_unconfirmed_refused"], obs["reveal_unconfirmed_code"] = refused, code
    ck.practice_reveal(sid, node, confirm=True)
    refused, code = expect_refusal(lambda: ck.practice_check(sid, node), {"practice_revealed"})
    obs["revealed_check_refused"], obs["revealed_check_code"] = refused, code
    scratch.obs["hints"] = obs


# ---------------------------------------------------------------------- exams
def _vault_item(scratch: Scratch, node: str, item_id: str) -> dict:
    return _json(scratch.session_dir / "assessment-vault" / node / f"{item_id}.json")


def _answers(item: dict):
    kind = cc.item_kind(item)
    if kind == "python":
        return item["reference_solution"]
    if kind == "free_text":
        return None
    return cc.answer_key(item["questions"])


def _pass_node(scratch: Scratch, node: str, rows: list) -> bool:
    ck, sid = scratch.course(), scratch.sid
    started = ck.start_exam(sid, node, fast_track=True)
    _collect(scratch, f"start_exam:{node}", started)
    att = started["attempt"]
    item = _vault_item(scratch, node, att["item"]["item_id"])
    kind = cc.item_kind(item)
    answers = _answers(item)
    if answers is None:
        ck.abandon_exam(sid, att["attempt_id"], "rules-check probe: open answers need an external grader")
        return False
    if kind == "python":
        Path(att["file"]).write_text(answers, encoding="utf-8")
    else:
        ck.save_answers(sid, att["attempt_id"], answers)
    ck.submit_and_finalize(sid, att["attempt_id"])
    ev = next((r for r in scratch.evidence() if r["payload"].get("attempt_id") == att["attempt_id"]), None)
    if ev is None:
        raise RuntimeError(f"finalising the exam on {node} wrote no evidence for attempt {att['attempt_id']}")
    rows.append({"node": node, "kind": kind, "scope": ev["payload"].get("scope"), "evidence": ev["payload"],
                 "provenance": ev["provenance"]})
    ck.advance(sid)
    return True


def _exam_lock(ctx: Ctx, scratch: Scratch, node: str) -> None:
    ck, sid, d = scratch.course(), scratch.sid, scratch.session_dir
    started = ck.start_exam(sid, node, fast_track=True)
    _collect(scratch, f"start_exam:{node}", started)
    att_id = started["attempt"]["attempt_id"]
    rows = {}

    def refuse(name, fn, codes=("exam_active",)):
        refused, code = expect_refusal(fn, set(codes))
        rows[name] = {"refused": refused, "code": code}

    refuse("help", lambda: ck.help(sid, node, "explain"))
    refuse("lesson_view", lambda: ck.lesson_view(sid, node))
    refuse("practice_reveal", lambda: ck.practice_reveal(sid, node, confirm=True))
    refuse("start_mission", lambda: ck.start_mission(sid, node))
    refuse("begin_practice", lambda: ck.begin_practice(sid, node))
    refuse("attest", lambda: scratch.kernel().attest(sid, node, outcome="pass", assessor="Qualified Assessor"))
    refuse("verify_python", lambda: scratch.kernel().verify_python(sid, "print(1)", capability=node))
    cli = cli_lock_facts()
    wired = cli["commands"] or set()
    rows["cli:dispatch"] = {"refused": bool(cli["dispatch"]), "code": "dispatch" if cli["dispatch"] else "no_dispatch_check"}
    rows["cli:sources-ask"] = {"refused": bool(cli["sources_ask"]), "code": "wired" if cli["sources_ask"] else "not_wired"}
    for command in sorted((set(CLI_LOCKED) | wired) - {"sources-ask"}):      # sources-ask is a `course` subcommand
        if command not in wired:
            rows[f"cli:{command}"] = {"refused": False, "code": "not_in_EXAM_LOCKED_COMMANDS"}
        else:
            refuse(f"cli:{command}", lambda c=command: assert_no_active_exam(d, c))
    snap = ck.studio_snapshot(sid)
    rows["studio:snapshot"] = {"refused": bool(snap.get("exam_locked")), "code": "exam_locked" if snap.get("exam_locked") else "open"}
    for name, guarded in studio_lock_guards().items():
        rows[f"studio:{name}"] = {"refused": guarded, "code": "guarded" if guarded else "no_exam_lock_check"}
    scratch.obs["exam_lock_studio_state"] = rows["studio:snapshot"]["code"]
    scratch.obs["exam_lock"] = rows
    ck.abandon_exam(sid, att_id, "rules-check probe: exam lock verified")


def _journey_exams(ctx: Ctx, scratch: Scratch) -> None:
    ck, sid = scratch.course(), scratch.sid
    view = ck.state(sid)
    locked = next((n for n in view["roadmap"]["nodes"] if n["status"] == "locked"), None)
    if locked is None:
        scratch.obs["locked"] = {"node": None}
    else:
        mission = (view.get("next_mission") or {}).get("node_id")
        scratch.obs["locked"] = {
            "node": locked["id"], "offered_in_mission": mission == locked["id"],
            "start_exam": dict(zip(("refused", "code"), expect_refusal(lambda: ck.start_exam(sid, locked["id"])))),
            "start_mission": dict(zip(("refused", "code"), expect_refusal(lambda: ck.start_mission(sid, locked["id"]))))}
    first = next((n["id"] for n in view["roadmap"]["nodes"] if n["status"] in {"available", "active"} and not n.get("requires")), None)
    if first is not None:
        _step(scratch, "exam_lock", lambda: _exam_lock(ctx, scratch, first))
    rows, passed = [], []
    from .. import studio_plan as plan_mod
    for node in plan_mod.prerequisite_order(ctx.nodes())[:6]:
        status = next((n["status"] for n in ck.state(sid)["roadmap"]["nodes"] if n["id"] == node["id"]), "locked")
        if status == "locked":
            continue
        ok = _step(scratch, f"pass:{node['id']}", lambda n=node["id"]: _pass_node(scratch, n, rows))
        if ok:
            passed.append(node["id"])
    scratch.obs["exam_passes"] = rows
    if passed:
        node = passed[-1]
        started, refused_code = [], None
        for _ in range(12):
            try:
                opened = ck.start_exam(sid, node, fast_track=True)
            except CourseStateError as e:
                refused_code = e.code
                break
            _collect(scratch, f"start_exam:{node}", opened)
            att = opened["attempt"]
            started.append(att["item"]["item_id"])
            ck.abandon_exam(sid, att["attempt_id"], "rules-check probe: retake exhaustion")
        scratch.obs["retake"] = {"node": node, "started": started, "refused_code": refused_code}
    else:
        scratch.obs["retake"] = {"skipped": "no node could be passed in the scratch copy, so retake exhaustion was not exercised"}


def run(ctx: Ctx, scratch: Scratch) -> None:
    """Execute the scripted journey. Order matters: staged-only attacks first, then promotion, then the learner flows."""
    _step(scratch, "http", lambda: _http(ctx, scratch))
    if not ctx.job or not ctx.job.get("unit_order"):
        scratch.obs["skipped"] = "no staged build: there is nothing to rehearse on the scratch copy"
        return
    _step(scratch, "promotion", lambda: _promote(ctx, scratch))
    promoted = scratch.obs.get("promotion") or {}
    if promoted.get("error") or not (promoted.get("promoted") or promoted.get("already_promoted")):
        return                                                    # the learner flows need the promoted content
    _step(scratch, "source_attacks", lambda: _source_attacks(ctx, scratch))
    token = "CANARY-" + os.urandom(6).hex()
    planted = _step(scratch, "canary_plant_lessons", lambda: _plant(scratch, token, lessons=True, items=False)) or 0
    node_ids = [n["id"] for n in ctx.nodes()]
    _step(scratch, "payloads", lambda: _learner_payloads(scratch, node_ids))
    _step(scratch, "practice", lambda: _practice(ctx, scratch))
    _step(scratch, "exams", lambda: _journey_exams(ctx, scratch))
    planted += _step(scratch, "canary_plant_items", lambda: _plant(scratch, token, lessons=False, items=True)) or 0
    _step(scratch, "payloads_after", lambda: _learner_payloads(scratch, node_ids))
    from .r08 import _norm
    secrets = secrets_of_all(ctx)
    blobs = [(name, _norm(text), text) for name, text in scratch.payloads]
    found = sorted({name for name, _n, text in blobs if token in text})
    leaks = sorted({f"{name}: {label}" for name, norm, _t in blobs for label, secret in secrets if _norm(secret) in norm})
    scratch.obs["canary"] = {"planted": planted, "payloads": len(scratch.payloads), "found_in": found,
                             "secret_leaks": leaks, "token": token}


def secrets_of_all(ctx: Ctx) -> list[tuple[str, str]]:
    from .r08 import secrets_of
    return [pair for subject in ctx.subjects() for pair in secrets_of(subject)]

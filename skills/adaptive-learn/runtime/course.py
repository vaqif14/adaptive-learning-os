"""Course action layer: one guarded entry point for the agent, CLI and local UI.

Every action runs the same sequence under the session lock:
  recover journal -> verify ledger integrity -> load + migrate course-state ->
  validate invariants -> reconcile incomplete operations -> idempotency/revision
  check -> phase/approval guard -> effect -> commit (revision + 1).
The ledger stays the single authoritative store: course-state.json and
source-manifest.json are journaled through ``local_state.commit_state``,
evidence through ``EventLedger.append``, engagement intervals as ledger events.
Nothing here trusts chat memory or client-side state.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import course_content as cc
from . import course_sources as csrc
from . import course_state as cs
from .course_roadmap import render_roadmap_html
from .course_state import CourseStateError
from .evidence_history import parse_time
from .listener import grade_explanation
from .local_state import commit_state
from .session import SessionKernel
from .session_lock import session_lock
from .utils import new_id, now_iso, read_json, write_json

STATE_FILE = "course-state.json"
MANIFEST_FILE = "source-manifest.json"
_NODE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_ATTEMPT = re.compile(r"att_[0-9a-f]{16}")
_IVID = re.compile(r"iv_[0-9a-f]{16,32}")
_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,80}")
HELP_KINDS = ("explain", "why", "worked_example", "hint", "alternative")
ACTIVITY_KINDS = ("roadmap", "teaching", "practice", "help", "retry", "review", "exam", "other")
BLOCKERS = ("time", "difficulty", "unclear_material", "motivation", "health_or_personal", "tooling", "other")

PHASE_LABEL_AZ = {
    "onboarding": "Tanışlıq: məqsəd və başlanğıc səviyyə",
    "source_preparation": "Mənbələrin hazırlanması",
    "roadmap_awaiting_approval": "Yol xəritəsi təsdiq gözləyir",
    "course_generation": "Kurs hazırlanır",
    "teaching": "İzah və nümunə",
    "practice": "Bələdçili praktika",
    "exam_active": "Müstəqil yoxlama (imtahan)",
    "assessment_pending": "Cavab qiymətləndirilir",
    "feedback": "Nəticə və rəy",
    "next_mission": "Növbəti missiya",
}
AGENT_DIRECTIVES = {
    "onboarding": "Interviewer role: ask one question at a time (concrete goal, deadline/time, current level, useful explanation preferences). Record with `course diagnostic`; self-report is context, never competence.",
    "source_preparation": "Prepare sources with the installed NotebookLM CLI (`course sources-*`). Validate every expected resource; do not teach yet.",
    "roadmap_awaiting_approval": "Mapmaker role: present the roadmap (app or roadmap.html) and its version. Do NOT author lessons or teach until the learner explicitly approves this version.",
    "course_generation": "Author grounded lesson + assessment items for the mission node (`course author-lesson`, `course author-items`), then start the mission.",
    "teaching": "Explainer/Socratic roles: teach the active node from the installed lesson. Deliver help only through `course help` so support provenance is recorded.",
    "practice": "Checker role: guide practice; hints only via `course help --kind hint`. Feedback must cite the learner's actual check results; never rewrite their code.",
    "exam_active": "EXAM LOCK (Examiner role): do not teach, hint, explain, review or solve anything for this attempt. Only offer: continue, submit, or explicitly abandon.",
    "assessment_pending": "EXAM LOCK until finalization: resume scoring/finalization (`course exam-submit` retries safely). No teaching.",
    "feedback": "Explain the finalized result from the stored check results and misconceptions (Diagnostician role); then `course next`.",
    "next_mission": "State the mission and its evidence-based reason; start it only when the learner agrees.",
}
HONEST_BOUNDARIES_AZ = [
    "Bu, öz cihazında tətbiq səviyyəsində iş axını nəzarətidir; saxtakarlığa qarşı qorunan və ya uzaqdan nəzarət olunan imtahan deyil.",
    "Tətbiqdə 'kömək göstərilmədi' qeydi kənar kitab, tətbiq və ya insan köməyinin olmadığını sübut etmir.",
    "Gizli yoxlamalar eyni cihazın sahibindən gizli deyil; ayrılıq yalnız təsadüfi açılmanın və agentin sızdırmasının qarşısını alır.",
    "Ölçülən vaxt yalnız bu tətbiqdəki aktiv işdir; vaxt nə ustalığın, nə zəhmətin, nə də qabiliyyətin sübutudur.",
]
FORBIDDEN_TONE = ("tənbəl", "məsuliyyətsiz", "zəif tələbə", "ağılsız", "lazy", "irresponsible", "stupid")


def _utc(ts: str | None):
    return parse_time(ts) if ts else None


class CourseKernel:
    def __init__(self, workspace: Path):
        self.sk = SessionKernel(Path(workspace))

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _module(d: Path, node: str) -> Path:
        return d / "workspace" / "modules" / node

    @staticmethod
    def _vault(d: Path, node: str) -> Path:
        return d / "assessment-vault" / node

    @staticmethod
    def _lesson_path(d: Path, node: str) -> Path:
        return d / "course" / "lessons" / f"{node}.json"

    @staticmethod
    def _attempt_dir(d: Path, att: str) -> Path:
        return d / "course" / "attempts" / att

    # --------------------------------------------------------------- plumbing
    def _open(self, sid: str):
        d = self.sk.dir(sid)
        self.sk._recover_state(sid)
        led = self.sk.ledger(sid)
        led.require_integrity()
        path = d / STATE_FILE
        if not path.exists():
            raise CourseStateError("course_not_initialized", "run `alearn course init --session <id>` first")
        state, applied = cs.migrate(read_json(path))
        if applied:
            self._commit(d, led, state, {"event_type": "course_state_migrated", "from_versions": applied})
        errors = cs.validate(state)
        if errors:
            raise CourseStateError("invalid_state", ",".join(errors))
        self._reconcile(d, led, state)
        return d, led, state

    def _commit(self, d, led, state, event: dict, files: dict | None = None):
        state["revision"] += 1
        state["updated_at"] = now_iso()
        errors = cs.validate(state)
        if errors:  # never journal an inconsistent state
            state["revision"] -= 1
            raise CourseStateError("invalid_state", ",".join(errors))
        event = dict(event)
        etype = event.pop("event_type")
        commit_state(d, led, {"event_type": etype, "course_revision": state["revision"], **event},
                     {STATE_FILE: state, **(files or {})})

    def _mutate(self, sid, action, fn, *, expected_revision=None, idempotency_key=None):
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            if idempotency_key is not None:
                if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
                    raise CourseStateError("phase_forbids_action", "idempotency_key must be 8-80 safe characters")
                prev = cs.remembered(state, idempotency_key)
                if prev is not None:
                    if prev["action"] != action:
                        raise CourseStateError("phase_forbids_action", "idempotency key reused for a different action")
                    return {"idempotent_replay": True, "action": action, "result": prev["result"],
                            "revision": state["revision"]}
            cs.check_revision(state, expected_revision)
            result, event, files = fn(d, led, state)
            if event is not None:
                cs.remember(state, idempotency_key, action, _ref(result))
                self._commit(d, led, state, event, files)
            return {**result, "revision": state["revision"]}

    def _plan(self, d) -> dict:
        p = d / "workspace" / "roadmap.json"
        if not p.exists():
            raise CourseStateError("roadmap_not_approved", "no roadmap proposed yet")
        return read_json(p)

    def _node(self, plan, node_id) -> dict:
        if not isinstance(node_id, str) or not _NODE.fullmatch(node_id):
            raise CourseStateError("phase_forbids_action", "invalid node id")
        for n in plan["nodes"]:
            if n["id"] == node_id:
                return n
        raise CourseStateError("phase_forbids_action", f"unknown roadmap node {node_id}")

    def _manifest(self, d) -> dict | None:
        p = d / MANIFEST_FILE
        return read_json(p) if p.exists() else None

    def _lesson(self, d, node) -> dict | None:
        p = self._lesson_path(d, node)
        return read_json(p) if p.exists() else None

    def _items(self, d, node) -> list[dict]:
        v = self._vault(d, node)
        if not v.is_dir():
            return []
        return [read_json(p) for p in sorted(v.glob("*.json"))]

    def _passed(self, state, node) -> bool:
        return bool(state["node_progress"].get(node, {}).get("passed"))

    def _available(self, state, node) -> bool:
        return all(self._passed(state, r) for r in node.get("requires", []))

    def _progress(self, state, node_id) -> dict:
        return state["node_progress"].setdefault(node_id, {"passed": False, "last_outcome": None, "exam_attempts": [],
                                                           "practice": None, "help": {}, "method": "standard"})

    # ------------------------------------------------------------- reconcile
    def _evidence_exists(self, led, evidence_id: str) -> bool:
        return any(r["record_type"] == "evidence" and r["payload"].get("evidence_id") == evidence_id
                   for r in led.verified_records())

    def _reconcile(self, d, led, state):
        """Complete a finalization whose evidence reached the ledger before a crash."""
        att_id = state["active_attempt_id"]
        if not att_id:
            return
        att = state["attempts"][att_id]
        if att["status"] == "scored" and self._evidence_exists(led, f"ev_exam_{att_id}"):
            self._finalize_state(d, led, state, att, reconciled=True)
            self._commit(d, led, state, {"event_type": "course_exam_finalized", "attempt_id": att_id,
                                         "reconciled_after_interruption": True})

    # ------------------------------------------------------------------ init
    def init(self, sid: str, *, goal: str | None = None) -> dict:
        d = self.sk.dir(sid)
        with session_lock(d):
            self.sk._recover_state(sid)
            led = self.sk.ledger(sid)
            led.require_integrity()
            if (d / STATE_FILE).exists():
                d, led, state = self._open(sid)
                return {"status": "already_initialized", **self._view(d, led, state)}
            contract = read_json(d / "learning-contract.json")
            state = cs.new_state(sid, goal or contract.get("goal"), now_iso())
            state["learner_profile"]["stated_background"] = contract.get("stated_background")
            if (d / "workspace" / "roadmap.json").exists():
                plan = read_json(d / "workspace" / "roadmap.json")
                if plan.get("status") != "needs_curriculum_research":
                    # A pre-course (legacy) roadmap still needs explicit approval.
                    state["roadmap_version"] = cs.roadmap_version(plan)
                    state["review_required"] = {"reason": "legacy_roadmap_requires_approval",
                                                "new_version": state["roadmap_version"],
                                                "changes": cs.diff_roadmaps(None, plan)}
                    cs.set_phase(state, "roadmap_awaiting_approval", now_iso())
            self._commit(d, led, state, {"event_type": "course_initialized", "schema_version": cs.SCHEMA_VERSION})
            return {"status": "initialized", **self._view(d, led, state)}

    # ------------------------------------------------------- onboarding/profile
    def record_target(self, sid, target: dict, **kw):
        required = ("kind", "official_name", "verified_at", "verification_url", "objectives")
        if not isinstance(target, dict) or any(not target.get(k) for k in required):
            raise CourseStateError("phase_forbids_action", f"target needs {required}")
        if not str(target["verification_url"]).startswith("https://"):
            raise CourseStateError("phase_forbids_action", "verification_url must be https")
        for o in target["objectives"]:
            if not isinstance(o, dict) or not o.get("domain"):
                raise CourseStateError("phase_forbids_action", "objectives need a domain")
        if "version" not in target:
            raise CourseStateError("phase_forbids_action", "state version explicitly (null + version_note when unpublished)")

        def fn(d, led, state):
            cs.guard(state, "record_goal")
            state["goal"]["target"] = {k: target.get(k) for k in
                                       ("kind", "official_name", "code", "version", "version_note", "verified_at",
                                        "verification_url", "objectives", "page_sha256", "format")}
            return {"target": state["goal"]["target"]}, {"event_type": "course_target_recorded"}, None
        return self._mutate(sid, "record_target", fn, **kw)

    def record_diagnostic(self, sid, payload: dict, **kw):
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise CourseStateError("phase_forbids_action", "entries must be a list")
        for e in entries:
            if not isinstance(e, dict) or e.get("basis") not in {"self_report", "observed"} or not e.get("prompt"):
                raise CourseStateError("phase_forbids_action", "each entry needs prompt and basis self_report|observed")
            if e.get("outcome", "unknown") not in {"correct", "incorrect", "partial", "unknown"}:
                raise CourseStateError("phase_forbids_action", "outcome must be correct|incorrect|partial|unknown")
            if e["basis"] == "self_report" and e.get("outcome", "unknown") != "unknown":
                raise CourseStateError("phase_forbids_action", "self-report cannot carry a correctness outcome")
        prefs = payload.get("explanation_preferences", [])
        if not isinstance(prefs, list) or not all(isinstance(p, str) for p in prefs):
            raise CourseStateError("phase_forbids_action", "explanation_preferences must be strings")

        def fn(d, led, state):
            cs.guard(state, "record_profile")
            prof = state["learner_profile"]
            if payload.get("stated_background"):
                prof["stated_background"] = payload["stated_background"]
            if prefs:
                prof["explanation_preferences"] = prefs[:8]
            for e in entries:
                prof["diagnostic"].append({"id": e.get("id") or new_id("dx"), "prompt": e["prompt"][:400],
                                           "response_summary": str(e.get("response_summary", ""))[:400],
                                           "basis": e["basis"], "outcome": e.get("outcome", "unknown"),
                                           "node_id": e.get("node_id"), "at": now_iso()})
            prof["gaps"] = sorted({x["node_id"] for x in prof["diagnostic"]
                                   if x["basis"] == "observed" and x["outcome"] in {"incorrect", "partial"} and x.get("node_id")})
            prof["updated_at"] = now_iso()
            if state["phase"] == "onboarding" and state["goal"]["text"]:
                cs.set_phase(state, "source_preparation", now_iso())
            return {"learner_profile": prof}, {"event_type": "course_diagnostic_recorded", "entries": len(entries)}, None
        return self._mutate(sid, "record_diagnostic", fn, **kw)

    # ------------------------------------------------------------------ sources
    def record_sources(self, sid, manifest: dict, expected: list[dict], **kw):
        if not isinstance(manifest, dict) or not isinstance(manifest.get("resources"), list):
            raise CourseStateError("phase_forbids_action", "manifest must come from course_sources.validate_resources")

        def fn(d, led, state):
            if state["phase"] == "onboarding":
                cs.guard(state, "begin_sources")
                cs.set_phase(state, "source_preparation", now_iso())
            cs.guard(state, "record_sources")
            stored = {**manifest, "expected": expected}
            state["source_manifest_version"] = manifest.get("manifest_version")
            state["source_refs"] = [{"key": r["key"], "source_id": r["source_id"], "title": r.get("title"),
                                     "url": (r.get("expected") or {}).get("locator"), "status": r["status"],
                                     "content_sha256": r["content_sha256"], "stale": r.get("stale"),
                                     "issues": r.get("issues", [])} for r in manifest["resources"]]
            # Lessons grounded on a source that is no longer validated must be re-checked.
            ok = {r["source_id"] for r in manifest["resources"] if r["status"] == "validated"}
            for node in list(state["content_versions"]):
                lesson = self._lesson(d, node)
                cited = {ref.get("source_id") for part in ("explanation", "why", "worked_example", "alternative_explanation")
                         for ref in (lesson or {}).get(part, {}).get("refs", []) if ref.get("grounding") == "source"}
                if cited - ok:
                    self._progress(state, node)["grounding_needs_revalidation"] = sorted(cited - ok)
            return ({"manifest_version": manifest.get("manifest_version"), "complete": manifest.get("complete"),
                     "summary": manifest.get("summary")},
                    {"event_type": "course_sources_recorded", "manifest_version": manifest.get("manifest_version")},
                    {MANIFEST_FILE: stored})
        return self._mutate(sid, "record_sources", fn, **kw)

    def _ground(self, d):
        manifest = self._manifest(d)
        cache = d / "sources"

        def ground(source_id, quote):
            if not manifest:
                return {"grounded": False, "reason": "no_validated_source_manifest"}
            try:
                return csrc.find_quote(cache, manifest, source_id, quote)
            except ValueError as e:
                return {"grounded": False, "reason": str(e)}
        return ground

    # ----------------------------------------------------------------- roadmap
    def propose_roadmap(self, sid, plan: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "propose_roadmap")
            if not (state["goal"] or {}).get("text"):
                raise CourseStateError("phase_forbids_action", "record a concrete goal before proposing a roadmap")
            old_path = d / "workspace" / "roadmap.json"
            old = read_json(old_path) if old_path.exists() else None
            if old and old.get("status") == "needs_curriculum_research":
                old = None
            session = read_json(d / "session.json")
            from .learning_workspace import normalize_plan
            norm = normalize_plan(session["topic"], (state["goal"] or {}).get("text"), plan)
            version = cs.roadmap_version(norm)
            if version == state["roadmap_version"] and old is not None:
                return {"roadmap_version": version, "changed": False}, None, None
            self.sk.set_roadmap(sid, plan)          # journals + preserves submissions
            installed = read_json(d / "workspace" / "roadmap.json")
            for n in installed["nodes"]:             # lessons survive a roadmap revision
                lesson = self._lesson(d, n["id"])
                if lesson:
                    self._materialize_lesson_files(d, n, lesson)
            changes = cs.diff_roadmaps(old, installed)
            previous = state["approved_roadmap_version"]
            state["roadmap_version"] = cs.roadmap_version(installed)
            if previous != state["roadmap_version"]:
                state["review_required"] = {"reason": "material_change" if previous else "initial_approval",
                                            "previous_version": previous, "new_version": state["roadmap_version"],
                                            "changes": changes, "at": now_iso()}
                cs.set_phase(state, "roadmap_awaiting_approval", now_iso())
            self._write_roadmap_html(d, sid, installed, state)
            return ({"roadmap_version": state["roadmap_version"], "changed": True, "changes": changes,
                     "approval_invalidated": bool(previous and previous != state["roadmap_version"])},
                    {"event_type": "course_roadmap_proposed", "roadmap_version": state["roadmap_version"],
                     "previous_approved_version": previous}, None)
        return self._mutate(sid, "propose_roadmap", fn, **kw)

    def _write_roadmap_html(self, d, sid, plan, state):
        titles = {r["key"]: f"{r.get('title') or r.get('url')} — {r['status']}" for r in state["source_refs"]}
        cmd = (f"alearn --workspace {self.sk.workspace} course approve-roadmap --session {sid} "
               f"--roadmap-version {state['roadmap_version']}")
        html = render_roadmap_html(plan, roadmap_version=state["roadmap_version"],
                                   approved=state["approved_roadmap_version"] == state["roadmap_version"],
                                   approve_command=cmd, source_titles=titles)
        (d / "workspace" / "roadmap.html").write_text(html, encoding="utf-8")

    def approve_roadmap(self, sid, roadmap_version: str, *, channel: str, statement: str | None = None,
                        commitments: dict | None = None, **kw):
        if channel not in {"ui", "cli"}:
            raise CourseStateError("phase_forbids_action", "approval channel must be ui or cli")

        def fn(d, led, state):
            cs.guard(state, "approve_roadmap")
            if roadmap_version != state["roadmap_version"]:
                raise CourseStateError("roadmap_version_mismatch",
                                       f"approving {roadmap_version}, current {state['roadmap_version']}")
            event = {"event_id": new_id("apv"), "roadmap_version": roadmap_version, "approved_at": now_iso(),
                     "approved_by": "learner", "channel": channel,
                     "statement": (statement or "Yol xəritəsini nəzərdən keçirdim və təsdiq edirəm.")[:300]}
            state["approved_roadmap_version"] = roadmap_version
            state["roadmap_approval_event"] = event
            state["approval_history"].append(event)
            state["review_required"] = None
            if commitments:
                self._apply_commitments(state, commitments)
            cs.set_phase(state, "course_generation", now_iso())
            plan = self._plan(d)
            self._write_roadmap_html(d, sid, plan, state)
            state["next_mission"] = self._initial_mission(d, state, plan)
            return {"approval_event": event, "next_mission": state["next_mission"]}, {
                "event_type": "course_roadmap_approved", "approval_event": event}, None
        return self._mutate(sid, "approve_roadmap", fn, **kw)

    # ------------------------------------------------------------- authoring
    def install_items(self, sid, node_id: str, payload: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "install_content")
            node = self._node(self._plan(d), node_id)
            try:
                items = cc.validate_items(payload, node, self._lesson(d, node_id))
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", f"content_invalid: {e}")
            exposed = set(state["exposure"].get(node_id, []))
            current = state["item_versions"].setdefault(node_id, {})
            for it in items:
                if it["item_id"] in exposed and current.get(it["item_id"]) not in (None, it["item_version"]):
                    raise CourseStateError("phase_forbids_action",
                                           f"item {it['item_id']} was already shown; publish changes under a new item_id")
            for it in items:
                cc.write_private(self._vault(d, node_id) / f"{it['item_id']}.json", it)
                current[it["item_id"]] = it["item_version"]
            state["course_version"] = self._course_version(state)
            return ({"node_id": node_id, "items": {i["item_id"]: i["item_version"] for i in items}},
                    {"event_type": "course_items_installed", "node_id": node_id,
                     "item_versions": {i["item_id"]: i["item_version"] for i in items}}, None)
        return self._mutate(sid, "install_items", fn, **kw)

    def install_lesson(self, sid, node_id: str, payload: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "install_content")
            node = self._node(self._plan(d), node_id)
            try:
                lesson = cc.validate_lesson(payload, node, self._ground(d), self._items(d, node_id))
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", f"content_invalid: {e}")
            lesson["installed_at"] = now_iso()
            cc.write_private(self._lesson_path(d, node_id), lesson)
            self._materialize_lesson_files(d, node, lesson)
            state["content_versions"][node_id] = lesson["content_version"]
            self._progress(state, node_id).pop("grounding_needs_revalidation", None)
            state["course_version"] = self._course_version(state)
            return ({"node_id": node_id, "content_version": lesson["content_version"],
                     "lesson": cc.public_lesson_summary(lesson)},
                    {"event_type": "course_lesson_installed", "node_id": node_id,
                     "content_version": lesson["content_version"]}, None)
        return self._mutate(sid, "install_lesson", fn, **kw)

    def _materialize_lesson_files(self, d, node, lesson):
        """Learner-facing practice files only: brief, starter (never overwritten), visible checks."""
        mod = self._module(d, node["id"])
        (mod / "practice").mkdir(parents=True, exist_ok=True)
        (mod / "submission").mkdir(parents=True, exist_ok=True)
        (mod / "checks").mkdir(parents=True, exist_ok=True)
        (mod / "practice" / "README.md").write_text(
            f"# Praktika — {node['title']}\n\n{lesson['practice']['prompt_md']}\n\n"
            f"Kodunu `submission/{lesson['practice']['entry']}` faylında yaz, sonra tətbiqdə **Yoxla** düyməsini bas.\n",
            encoding="utf-8")
        starter = mod / "submission" / lesson["practice"]["entry"]
        if not starter.exists():           # never overwrite learner work
            starter.write_text(lesson["practice"]["starter_code"], encoding="utf-8")
        visible = "\n\n".join(f"# {c['id']}: {c['name']}\n{c['code']}" for c in lesson["practice"]["checks"])
        (mod / "checks" / "practice_checks.py").write_text(
            "# Görünən praktika yoxlamaları (CI kimi). İmtahanın gizli yoxlamaları burada deyil.\n\n" + visible + "\n",
            encoding="utf-8")

    @staticmethod
    def _course_version(state):
        return cs.digest("cv", {"roadmap": state["approved_roadmap_version"], "content": state["content_versions"],
                                "items": state["item_versions"]})

    # --------------------------------------------------------------- teaching
    def start_mission(self, sid, node_id: str, **kw):
        def fn(d, led, state):
            cs.guard(state, "start_mission")
            plan = self._plan(d)
            node = self._node(plan, node_id)
            if not self._available(state, node):
                raise CourseStateError("phase_forbids_action", "prerequisites not yet demonstrated: " + ", ".join(node["requires"]))
            if not self._lesson(d, node_id):
                raise CourseStateError("lesson_missing", "lesson content for this node is not authored yet")
            if state["active_node_id"] != node_id:
                state["support_provenance"] = cs.empty_support()
            state["active_node_id"] = node_id
            self._progress(state, node_id)
            cs.set_phase(state, "teaching", now_iso())
            state["resume_checkpoint"] = {"phase": "teaching", "route": f"#/learn/{node_id}", "node_id": node_id,
                                          "attempt_id": None, "at": now_iso()}
            return {"node_id": node_id, "phase": "teaching"}, {"event_type": "course_mission_started", "node_id": node_id}, None
        return self._mutate(sid, "start_mission", fn, **kw)

    def help(self, sid, node_id: str, kind: str, **kw):
        if kind not in HELP_KINDS:
            raise CourseStateError("phase_forbids_action", f"help kind must be one of {HELP_KINDS}")

        def fn(d, led, state):
            cs.guard(state, "help")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "help is for the active mission node")
            lesson = self._lesson(d, node_id)
            if not lesson:
                raise CourseStateError("lesson_missing")
            sp = state["support_provenance"]
            prog = self._progress(state, node_id)
            titles = {r["source_id"]: r.get("title") for r in state["source_refs"] if r.get("source_id")}

            def refs(part):
                return [{"grounding": r["grounding"], "title": titles.get(r.get("source_id")), "quote": r.get("quote"),
                         "note": r.get("note"), "stale_source": r.get("stale_source", False)} for r in part["refs"]]
            if kind == "explain":
                sp["explanation_shown"] = True
                content = {"title": "Qısa izah", "short": lesson["explanation"]["short"],
                           "body": lesson["explanation"]["body"], "refs": refs(lesson["explanation"])}
            elif kind == "why":
                content = {"title": "Niyə vacibdir", "body": lesson["why"]["text"], "refs": refs(lesson["why"])}
            elif kind == "worked_example":
                sp["worked_example_shown"] = True
                we = lesson["worked_example"]
                content = {"title": we["title"], "problem": we["problem"], "steps": we["steps"], "result": we["result"],
                           "refs": refs(we), "note": "Bu nümunədir; imtahan tapşırığı fərqli olacaq."}
            elif kind == "alternative":
                sp["alternative_explanation_shown"] = True
                alt = lesson["alternative_explanation"]
                content = {"title": "Başqa yolla izah", "body": alt["text"], "example": alt["example"], "refs": refs(alt)}
                prog["method"] = "alternative"
            else:  # hint ladder (practice only)
                if state["phase"] != "practice":
                    raise CourseStateError("phase_forbids_action", "hints belong to guided practice")
                used = int(prog["help"].get("hints", 0))
                hints = lesson["practice"]["hints"]
                if used >= len(hints):
                    content = {"title": "İpucu", "body": "Bütün ipuclarını görmüsən. İzahı və ya başqa yolla izahı aç.",
                               "level": used, "of": len(hints), "exhausted": True}
                else:
                    sp["hints_count"] = sp["hints_count"] + 1
                    prog["help"]["hints"] = used + 1
                    content = {"title": f"İpucu {used + 1}/{len(hints)}", "body": hints[used], "level": used + 1, "of": len(hints)}
            if kind != "hint":
                prog["help"][kind] = int(prog["help"].get(kind, 0)) + 1
            return ({"kind": kind, "content": content, "support_provenance": dict(sp)},
                    {"event_type": "course_help_delivered", "node_id": node_id, "kind": kind}, None)
        return self._mutate(sid, "help", fn, **kw)

    def self_explanation(self, sid, node_id: str, text: str, **kw):
        if not isinstance(text, str) or not 10 <= len(text.strip()) <= 4000:
            raise CourseStateError("phase_forbids_action", "self-explanation must be 10-4000 characters")

        def fn(d, led, state):
            cs.guard(state, "self_explanation")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "self-explanation is for the active node")
            lesson = self._lesson(d, node_id)
            coverage = grade_explanation(text, " ".join(lesson["self_explanation_key_terms"] * 2), threshold=0.6)
            sub = self._module(d, node_id) / "submission" / "self-explanation.md"
            sub.write_text(text.strip() + "\n", encoding="utf-8")
            led.append("observation", {"capability_id": node_id, "verification": "self_explanation_coverage",
                                       "outcome": "unknown", "correctness_checked": False, "mastery_eligible": False,
                                       "coverage": coverage["coverage"], "missed": coverage["missed"][:10],
                                       "note": "formative_self_explanation; lexical coverage only"},
                       {"source": "course_self_explanation"})
            self._progress(state, node_id)["self_explanation"] = {"coverage": coverage["coverage"], "at": now_iso()}
            return ({"coverage": coverage["coverage"], "covered": coverage["covered"], "missed": coverage["missed"][:10],
                     "note_az": "Bu yalnız açar anlayışların əhatəsidir, düzgünlük qiyməti deyil. Agent mənanı ayrıca yoxlayacaq."},
                    {"event_type": "course_self_explanation", "node_id": node_id}, None)
        return self._mutate(sid, "self_explanation", fn, **kw)

    def begin_practice(self, sid, node_id: str, **kw):
        def fn(d, led, state):
            cs.guard(state, "begin_practice")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "practice is for the active node")
            prog = self._progress(state, node_id)
            if not prog.get("practice"):
                prog["practice"] = {"node_id": node_id, "practice_attempt_id": new_id("prc"), "checks_run": 0,
                                    "last_outcome": None}
            cs.set_phase(state, "practice", now_iso())
            state["resume_checkpoint"] = {"phase": "practice", "route": f"#/practice/{node_id}", "node_id": node_id,
                                          "attempt_id": None, "at": now_iso()}
            lesson = self._lesson(d, node_id)
            return ({"node_id": node_id, "practice": prog["practice"], "prompt_md": lesson["practice"]["prompt_md"],
                     "file": str(self._module(d, node_id) / "submission" / lesson["practice"]["entry"])},
                    {"event_type": "course_practice_started", "node_id": node_id}, None)
        return self._mutate(sid, "begin_practice", fn, **kw)

    def practice_check(self, sid, node_id: str, **kw):
        def fn(d, led, state):
            cs.guard(state, "practice_check")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "practice check is for the active node")
            lesson = self._lesson(d, node_id)
            entry = lesson["practice"]["entry"]
            path = self._module(d, node_id) / "submission" / entry
            code = path.read_text(encoding="utf-8") if path.is_file() and not path.is_symlink() else ""
            if not code.strip():
                raise CourseStateError("phase_forbids_action", f"submission/{entry} is empty")
            results = cc.run_checks(code, lesson["practice"]["checks"], reveal=True)
            passed = all(r["passed"] for r in results)
            fired = [] if passed else cc.detect_misconceptions(code, lesson["practice"]["misconceptions"])
            prog = self._progress(state, node_id)
            counts = state["misconception_counts"].setdefault(node_id, {})
            for m in fired:
                counts[m["id"]] = counts.get(m["id"], 0) + 1
            recurring = [m for m in fired if counts[m["id"]] >= 2]
            if recurring:
                prog["method"] = "alternative"
            sp = dict(state["support_provenance"])
            practice = prog["practice"] or {}
            practice["checks_run"] = int(practice.get("checks_run", 0)) + 1
            practice["last_outcome"] = "correct" if passed else "incorrect"
            prog["practice"] = practice
            assisted = sp["hints_count"] > 0 or any(sp[k] for k in sp if k != "hints_count")
            led.append("evidence", {
                "evidence_id": new_id("ev"), "capability_id": node_id,
                "outcome": "correct" if passed else "incorrect",
                "independence": "assisted" if assisted else "unknown",
                "strength": "medium", "scope": "supported_completion", "evidence_format": "artifact_execution",
                "mastery_eligible": False, "correctness_checked": True,
                "support_provenance": {"hints_count": sp["hints_count"],
                                       "worked_example_shown": sp["worked_example_shown"],
                                       "ai_direct_answer_revealed": sp["ai_direct_answer_revealed"],
                                       "conceptual_scaffold": sp["explanation_shown"] or sp["alternative_explanation_shown"]},
                "attempt_id": practice.get("practice_attempt_id"),
                "task_fingerprint": hashlib.sha256(code.encode()).hexdigest(),
                "misconceptions": [m["id"] for m in fired],
                "note": "guided_practice_visible_checks; formative, never independent mastery",
            }, {"source": "course_practice"})
            self.sk.rebuild_projection(sid)
            feedback = _practice_feedback(results, fired, recurring)
            return ({"node_id": node_id, "passed": passed, "checks": results, "misconceptions": fired,
                     "recurring_misconceptions": [m["id"] for m in recurring], "feedback_az": feedback,
                     "method_change": bool(recurring), "support_provenance": sp},
                    {"event_type": "course_practice_checked", "node_id": node_id, "passed": passed,
                     "misconceptions": [m["id"] for m in fired]}, None)
        return self._mutate(sid, "practice_check", fn, **kw)

    # ------------------------------------------------------------------- exam
    def start_exam(self, sid, node_id: str, *, fast_track: bool = False, **kw):
        def fn(d, led, state):
            att_id = state["active_attempt_id"]
            if att_id and state["phase"] in cs.TEACHING_LOCKED_PHASES:
                att = state["attempts"][att_id]
                if att["node_id"] == node_id:   # refresh / second tab / restart resumes, never re-mints
                    return {"resumed": True, "attempt": self._public_attempt(d, att)}, None, None
                raise CourseStateError("exam_active", "another attempt is in progress")
            cs.guard(state, "start_exam")
            plan = self._plan(d)
            node = self._node(plan, node_id)
            if not self._available(state, node):
                raise CourseStateError("phase_forbids_action", "prerequisites not yet demonstrated")
            exposed = set(state["exposure"].get(node_id, []))
            fresh = [it for it in self._items(d, node_id) if it["item_id"] not in exposed
                     and state["item_versions"].get(node_id, {}).get(it["item_id"]) == it["item_version"]]
            if not fresh:
                raise CourseStateError("no_fresh_item", "no unseen assessment item left for this node; author a new one")
            item = min(fresh, key=lambda i: (i.get("difficulty", 1), i["item_id"]))
            att_id = new_id("att")
            att = {"attempt_id": att_id, "node_id": node_id, "item_id": item["item_id"],
                   "item_version": item["item_version"], "entry": item["entry"], "status": "active",
                   "started_at": now_iso(), "fast_track": bool(fast_track), "support": cs.empty_support(),
                   "submission_sha256": None, "result": None, "evidence_id": None,
                   "events": [{"status": "active", "at": now_iso()}]}
            state["attempts"][att_id] = att
            state["exposure"].setdefault(node_id, []).append(item["item_id"])
            state["active_node_id"] = node_id
            state["active_attempt_id"] = att_id
            state["attempt_status"] = "active"
            state["assessment_item_version"] = item["item_version"]
            self._progress(state, node_id)["exam_attempts"].append(att_id)
            cs.set_phase(state, "exam_active", now_iso())
            state["resume_checkpoint"] = {"phase": "exam_active", "route": f"#/exam/{att_id}", "node_id": node_id,
                                          "attempt_id": att_id, "at": now_iso()}
            exam_dir = self._module(d, node_id) / "exam" / att_id
            exam_dir.mkdir(parents=True, exist_ok=True)
            (exam_dir / "TASK.md").write_text(f"# {item['title']}\n\n{item['prompt_md']}\n\n"
                                              "Bu müstəqil yoxlamadır: tətbiqdə izah və ipucu bağlıdır.\n", encoding="utf-8")
            if not (exam_dir / item["entry"]).exists():
                (exam_dir / item["entry"]).write_text(item["starter_code"], encoding="utf-8")
            return ({"resumed": False, "attempt": self._public_attempt(d, att)},
                    {"event_type": "course_exam_started", "attempt_id": att_id, "node_id": node_id,
                     "item_id": item["item_id"], "item_version": item["item_version"], "fast_track": bool(fast_track)}, None)
        return self._mutate(sid, "start_exam", fn, **kw)

    def _public_attempt(self, d, att) -> dict:
        item_path = self._vault(d, att["node_id"]) / f"{att['item_id']}.json"
        public = cc.public_item(read_json(item_path)) if item_path.exists() else {"item_id": att["item_id"]}
        return {"attempt_id": att["attempt_id"], "node_id": att["node_id"], "status": att["status"],
                "started_at": att["started_at"], "fast_track": att["fast_track"], "item": public,
                "file": str(self._module(d, att["node_id"]) / "exam" / att["attempt_id"] / att["entry"]),
                "result": att.get("result"), "scoring_error": att.get("scoring_error")}

    def _attempt(self, state, attempt_id):
        if not isinstance(attempt_id, str) or not _ATTEMPT.fullmatch(attempt_id) or attempt_id not in state["attempts"]:
            raise CourseStateError("phase_forbids_action", "unknown attempt")
        return state["attempts"][attempt_id]

    def submit_exam(self, sid, attempt_id: str, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] != "active":       # duplicate click / retry: no second snapshot
                return {"attempt_id": attempt_id, "status": att["status"], "duplicate": True}, None, None
            cs.guard(state, "submit_exam")
            if state["active_attempt_id"] != attempt_id:
                raise CourseStateError("phase_forbids_action", "not the active attempt")
            src = self._module(d, att["node_id"]) / "exam" / attempt_id / att["entry"]
            code = src.read_text(encoding="utf-8") if src.is_file() and not src.is_symlink() else ""
            if not code.strip():
                raise CourseStateError("phase_forbids_action", f"{att['entry']} is empty")
            snap = self._attempt_dir(d, attempt_id) / "submitted"
            snap.mkdir(parents=True, exist_ok=True, mode=0o700)
            (snap / att["entry"]).write_text(code, encoding="utf-8")
            att["submission_sha256"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
            att["submitted_at"] = now_iso()
            att["status"] = "submitted"
            att["events"].append({"status": "submitted", "at": now_iso()})
            state["attempt_status"] = "submitted"
            cs.set_phase(state, "assessment_pending", now_iso())
            state["resume_checkpoint"] = {"phase": "assessment_pending", "route": f"#/exam/{attempt_id}",
                                          "node_id": att["node_id"], "attempt_id": attempt_id, "at": now_iso()}
            return ({"attempt_id": attempt_id, "status": "submitted", "submission_sha256": att["submission_sha256"]},
                    {"event_type": "course_exam_submitted", "attempt_id": attempt_id,
                     "submission_sha256": att["submission_sha256"]}, None)
        return self._mutate(sid, "submit_exam", fn, **kw)

    def score_exam(self, sid, attempt_id: str, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] in {"scored", "finalized"}:
                return {"attempt_id": attempt_id, "status": att["status"], "duplicate": True}, None, None
            cs.guard(state, "score_exam")
            if att["status"] not in {"submitted", "scoring_failed"}:
                raise CourseStateError("phase_forbids_action", f"cannot score a {att['status']} attempt")
            item_path = self._vault(d, att["node_id"]) / f"{att['item_id']}.json"
            snap = self._attempt_dir(d, attempt_id) / "submitted" / att["entry"]
            failure = None
            item = read_json(item_path) if item_path.exists() else None
            code = snap.read_text(encoding="utf-8") if snap.is_file() else None
            if item is None:
                failure = "item_missing_from_vault"
            elif item.get("item_version") != att["item_version"] or cs.digest(
                    "it", {k: v for k, v in item.items() if k != "item_version"}) != att["item_version"]:
                failure = "item_version_changed"
            elif code is None or hashlib.sha256(code.encode("utf-8")).hexdigest() != att["submission_sha256"]:
                failure = "submission_snapshot_changed"
            results = fired = None
            if failure is None:
                try:
                    results = cc.run_checks(code, item["checks"], reveal=False)
                    fired = ([] if all(r["passed"] for r in results)
                             else cc.detect_misconceptions(code, item["misconceptions"]))
                except (OSError, RuntimeError) as e:
                    failure = f"verifier_unavailable:{type(e).__name__}"
            if failure:
                att["status"] = "scoring_failed"
                att["scoring_error"] = failure
                att["events"].append({"status": "scoring_failed", "at": now_iso(), "reason": failure})
                state["attempt_status"] = "scoring_failed"
                return ({"attempt_id": attempt_id, "status": "scoring_failed", "reason": failure, "resumable": True},
                        {"event_type": "course_exam_scoring_failed", "attempt_id": attempt_id, "reason": failure}, None)
            by_cat = {}
            for r in results:
                c = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
                c["total"] += 1
                c["passed"] += int(r["passed"])
            core_ok = all(r["passed"] for r in results if r["category"] in {"core", "build"})
            outcome = "correct" if all(r["passed"] for r in results) else ("partial" if core_ok else "incorrect")
            result = {"outcome": outcome, "by_category": by_cat, "passed": sum(r["passed"] for r in results),
                      "total": len(results), "misconceptions": [m["id"] for m in fired],
                      "checks": [{"id": r["id"], "name": r["name"], "category": r["category"], "passed": r["passed"],
                                  "feedback": r["feedback"], "error": r["error"]} for r in results],
                      "misconception_feedback": [{"id": m["id"], "name": m["name"], "feedback": m["feedback"]} for m in fired],
                      "scored_at": now_iso(), "item_version": att["item_version"],
                      "submission_sha256": att["submission_sha256"]}
            result_path = self._attempt_dir(d, attempt_id) / "result.json"
            cc.write_private(result_path, result)
            att["result"] = {k: result[k] for k in ("outcome", "by_category", "passed", "total", "misconceptions")}
            att["result_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
            att["status"] = "scored"
            att.pop("scoring_error", None)
            att["events"].append({"status": "scored", "at": now_iso()})
            state["attempt_status"] = "scored"
            return ({"attempt_id": attempt_id, "status": "scored", "result": att["result"]},
                    {"event_type": "course_exam_scored", "attempt_id": attempt_id, "outcome": outcome}, None)
        return self._mutate(sid, "score_exam", fn, **kw)

    def finalize_exam(self, sid, attempt_id: str, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] == "finalized":
                return {"attempt_id": attempt_id, "status": "finalized", "duplicate": True,
                        "assessment_result": state["assessment_result"], "next_mission": state["next_mission"]}, None, None
            cs.guard(state, "finalize_exam")
            if att["status"] != "scored":
                raise CourseStateError("phase_forbids_action", f"cannot finalize a {att['status']} attempt")
            ev_id = f"ev_exam_{attempt_id}"
            if not self._evidence_exists(led, ev_id):
                result = att["result"]
                item = read_json(self._vault(d, att["node_id"]) / f"{att['item_id']}.json")
                led.append("evidence", {
                    "evidence_id": ev_id, "capability_id": att["node_id"], "outcome": result["outcome"],
                    "independence": "unassisted", "strength": "strong" if result["outcome"] == "correct" else "medium",
                    "scope": item["scope"], "evidence_format": "artifact_execution", "mastery_eligible": True,
                    "correctness_checked": True,
                    "support_provenance": {k: att["support"][k] for k in
                                           ("hints_count", "worked_example_shown", "ai_direct_answer_revealed",
                                            "conceptual_scaffold")},
                    "attempt_id": attempt_id, "item_id": att["item_id"], "item_version": att["item_version"],
                    "task_fingerprint": hashlib.sha256((att["item_version"] + att["submission_sha256"]).encode()).hexdigest(),
                    "verification": "hidden_checks", "fast_track": att["fast_track"],
                    "note": ("course exam: frozen unseen item, hidden + transfer checks; no in-app teaching or hints "
                             "were available during the attempt; external assistance is not observable"),
                }, {"source": "course_exam_verifier"})
                self.sk.rebuild_projection(sid)
            self._finalize_state(d, led, state, att)
            return ({"attempt_id": attempt_id, "status": "finalized", "assessment_result": state["assessment_result"],
                     "next_mission": state["next_mission"]},
                    {"event_type": "course_exam_finalized", "attempt_id": attempt_id, "evidence_id": ev_id}, None)
        return self._mutate(sid, "finalize_exam", fn, **kw)

    def _finalize_state(self, d, led, state, att, reconciled=False):
        ev_id = f"ev_exam_{att['attempt_id']}"
        result = att["result"]
        record = next(r for r in led.verified_records()
                      if r["record_type"] == "evidence" and r["payload"].get("evidence_id") == ev_id)
        att["status"] = "finalized"
        att["evidence_id"] = ev_id
        att["finalized_at"] = now_iso()
        att["events"].append({"status": "finalized", "at": att["finalized_at"], "reconciled": reconciled})
        node_id = att["node_id"]
        prog = self._progress(state, node_id)
        prog["last_outcome"] = result["outcome"]
        if result["outcome"] == "correct":
            prog["passed"] = True
            prog["passed_at"] = att["finalized_at"]
        counts = state["misconception_counts"].setdefault(node_id, {})
        for mid in result["misconceptions"]:
            counts[mid] = counts.get(mid, 0) + 1
        state["evidence_refs"].append({"evidence_id": ev_id, "record_id": record["record_id"], "seq": record["seq"],
                                       "attempt_id": att["attempt_id"], "node_id": node_id, "kind": "exam",
                                       "outcome": result["outcome"]})
        full = read_json(self._attempt_dir(d, att["attempt_id"]) / "result.json")
        state["assessment_result"] = {"attempt_id": att["attempt_id"], "node_id": node_id, "item_id": att["item_id"],
                                      "outcome": result["outcome"], "passed": result["passed"], "total": result["total"],
                                      "by_category": result["by_category"], "checks": full["checks"],
                                      "misconception_feedback": full["misconception_feedback"],
                                      "evidence_id": ev_id, "finalized_at": att["finalized_at"],
                                      "independence_note_az": ("Cəhd zamanı tətbiqdə izah və ipucu bağlı idi. "
                                                               "Bu, kənar köməyin olmadığını sübut etmir.")}
        prof = state["learner_profile"]
        gaps = set(prof["gaps"])
        if result["outcome"] == "correct":
            gaps.discard(node_id)
        else:
            gaps.add(node_id)
        prof["gaps"] = sorted(gaps)
        prof["updated_at"] = now_iso()
        state["active_attempt_id"] = None
        state["attempt_status"] = "finalized"
        state["assessment_item_version"] = None
        cs.set_phase(state, "feedback", now_iso())
        state["resume_checkpoint"] = {"phase": "feedback", "route": f"#/result/{att['attempt_id']}", "node_id": node_id,
                                      "attempt_id": att["attempt_id"], "at": now_iso()}
        state["next_mission"] = self._mission_after_exam(d, state, att, full)

    def submit_and_finalize(self, sid, attempt_id: str, *, expected_revision=None, idempotency_key=None):
        """UI convenience: three durable steps; any failure leaves a resumable state."""
        steps = {"submit": self.submit_exam(sid, attempt_id, expected_revision=expected_revision,
                                            idempotency_key=idempotency_key)}
        steps["score"] = self.score_exam(sid, attempt_id)
        if steps["score"]["status"] == "scoring_failed":
            return {"status": "scoring_failed", "resumable": True, "steps": steps,
                    "reason": steps["score"].get("reason"), "revision": steps["score"]["revision"]}
        steps["finalize"] = self.finalize_exam(sid, attempt_id)
        return {"status": "finalized", "steps": steps, "assessment_result": steps["finalize"].get("assessment_result"),
                "next_mission": steps["finalize"].get("next_mission"), "revision": steps["finalize"]["revision"]}

    def abandon_exam(self, sid, attempt_id: str, reason: str | None = None, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] == "abandoned":
                return {"attempt_id": attempt_id, "status": "abandoned", "duplicate": True}, None, None
            cs.guard(state, "abandon_exam")
            if att["status"] not in {"active", "submitted", "scoring_failed"}:
                raise CourseStateError("phase_forbids_action", "a scored attempt must be finalized, not abandoned")
            att["status"] = "abandoned"
            att["abandoned_reason"] = (reason or "learner_exit")[:200]
            att["events"].append({"status": "abandoned", "at": now_iso()})
            led.append("observation", {"capability_id": att["node_id"], "attempt_id": attempt_id,
                                       "item_id": att["item_id"], "outcome": "unknown", "note": "exam_abandoned",
                                       "reason": att["abandoned_reason"], "correctness_checked": False},
                       {"source": "course_exam"})
            state["active_attempt_id"] = None
            state["attempt_status"] = "abandoned"
            state["assessment_item_version"] = None
            has_lesson = bool(self._lesson(d, att["node_id"]))
            cs.set_phase(state, "teaching" if has_lesson else "course_generation", now_iso())
            state["next_mission"] = self._mission_retry(d, state, att["node_id"], "abandoned")
            state["resume_checkpoint"] = {"phase": state["phase"],
                                          "route": f"#/learn/{att['node_id']}" if has_lesson else "#/",
                                          "node_id": att["node_id"], "attempt_id": None, "at": now_iso()}
            return ({"attempt_id": attempt_id, "status": "abandoned", "counted_as_success": False,
                     "next_mission": state["next_mission"]},
                    {"event_type": "course_exam_abandoned", "attempt_id": attempt_id}, None)
        return self._mutate(sid, "abandon_exam", fn, **kw)

    def advance(self, sid, **kw):
        def fn(d, led, state):
            cs.guard(state, "advance_to_mission")
            cs.set_phase(state, "next_mission", now_iso())
            state["resume_checkpoint"] = {"phase": "next_mission", "route": "#/mission", "node_id": state["active_node_id"],
                                          "attempt_id": None, "at": now_iso()}
            return {"next_mission": state["next_mission"]}, {"event_type": "course_next_mission_shown"}, None
        return self._mutate(sid, "advance", fn, **kw)

    # ----------------------------------------------------------- missions
    def _driver_hint(self, sid) -> dict | None:
        try:
            decision = self.sk._driver_decision(sid)
        except (ValueError, OSError, KeyError):
            return None
        return {k: decision.get(k) for k in ("action", "node", "reason") if decision.get(k) is not None}

    def _fresh_count(self, d, state, node_id) -> int:
        exposed = set(state["exposure"].get(node_id, []))
        return sum(1 for it in self._items(d, node_id) if it["item_id"] not in exposed)

    def _next_open_node(self, state, plan):
        for n in plan["nodes"]:
            if not self._passed(state, n["id"]) and self._available(state, n):
                return n
        return None

    def _initial_mission(self, d, state, plan) -> dict:
        n = self._next_open_node(state, plan)
        if n is None:
            return {"type": "course_review", "reason_az": "Bütün mərhələlər keçilib."}
        observed = [x for x in state["learner_profile"]["diagnostic"]
                    if x.get("node_id") == n["id"] and x["basis"] == "observed" and x["outcome"] == "correct"]
        mission = {"type": "start_node", "node_id": n["id"], "title": n["title"],
                   "reason_az": (f"“{n['title']}” yol xəritəsində ilk açıq mərhələdir"
                                 + (": ilkin şərti yoxdur." if not n.get("requires") else ": ilkin şərtləri keçilib.")),
                   "options_az": ["İzahdan başla (yeni başlayan üçün)",
                                  "Bunu bilirsənsə, birbaşa yeni tapşırıqla yoxla"],
                   "suggest_fast_track": bool(observed),
                   "uncertainty_az": "Başlanğıc profil təxminidir; ilk müstəqil nəticədən sonra yenilənəcək.",
                   "lesson_ready": bool(self._lesson(d, n["id"])), "fresh_items": self._fresh_count(d, state, n["id"])}
        if not mission["lesson_ready"]:
            mission["blocked_on"] = "lesson_content_missing"
        return mission

    def _mission_after_exam(self, d, state, att, full) -> dict:
        plan = self._plan(d)
        node_id, outcome = att["node_id"], full["outcome"]
        title = self._node(plan, node_id)["title"]
        hint = self._driver_hint(state["session_id"])
        by_cat = full["by_category"]
        tally = ", ".join(f"{k}: {v['passed']}/{v['total']}" for k, v in sorted(by_cat.items()))
        if outcome == "correct":
            nxt = self._next_open_node(state, plan)
            review_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
            mission = {"type": "next_node" if nxt else "course_review", "node_id": nxt["id"] if nxt else node_id,
                       "title": nxt["title"] if nxt else title,
                       "reason_az": (f"“{title}” üzrə görmədiyin tapşırığın bütün yoxlamaları keçdi ({tally})."
                                     + (f" Növbəti açıq mərhələ: “{nxt['title']}”." if nxt else "")),
                       "uncertainty_az": ("Bu, bir müstəqil nəticədir — ustalıq hələ təsdiqlənməyib. "
                                          "24 saatdan sonra qısa yeni tapşırıq nəticənin davamlı olduğunu yoxlayacaq."),
                       "review": {"node_id": node_id, "not_before": review_at}}
            if nxt:
                mission["lesson_ready"] = bool(self._lesson(d, nxt["id"]))
                if not mission["lesson_ready"]:
                    mission["blocked_on"] = "lesson_content_missing"
        else:
            mission = self._mission_retry(d, state, node_id, outcome, full=full, tally=tally)
        mission["driver_action"] = hint
        return mission

    def _mission_retry(self, d, state, node_id, outcome, *, full=None, tally=None) -> dict:
        plan = self._plan(d)
        title = self._node(plan, node_id)["title"]
        counts = state["misconception_counts"].get(node_id, {})
        fired = (full or {}).get("misconception_feedback", [])
        recurring = [m for m in fired if counts.get(m["id"], 0) >= 2]
        if outcome == "abandoned":
            mission = {"type": "retry_later", "node_id": node_id, "title": title,
                       "reason_az": "Cəhd dayandırıldı və uğur kimi sayılmadı. Hazır olanda yeni tapşırıqla yenidən yoxlanacaqsan."}
        elif outcome == "partial":
            failed = [c["name"] for c in full["checks"] if not c["passed"]]
            mission = {"type": "targeted_practice", "node_id": node_id, "title": title,
                       "reason_az": (f"Əsas hallar keçdi, amma bunlar keçmədi: {', '.join(failed)} ({tally}). "
                                     "Həmin hala uyğun qısa praktika, sonra görmədiyin yeni tapşırıq."),
                       "focus": failed}
        elif recurring:
            mission = {"type": "reteach_alternative", "node_id": node_id, "title": title,
                       "reason_az": (f"Eyni səhv təkrarlanır: {recurring[0]['name']}. Bu dəfə başqa izah və başqa nümunə ilə "
                                     "başlayırıq, sonra yeni tapşırıq."), "method_change": True}
        else:
            mission = {"type": "reteach", "node_id": node_id, "title": title,
                       "reason_az": (f"Əsas yoxlamalar keçmədi ({tally}). Qısa izah → nümunə → bələdçili praktika, "
                                     "sonra görmədiyin yeni tapşırıq.") if tally else "Qısa izah və praktika ilə davam et."}
        fresh = self._fresh_count(d, state, node_id)
        mission["fresh_items"] = fresh
        if fresh == 0:
            mission["blocked_on"] = "no_fresh_item"
        mission["uncertainty_az"] = "Bir cəhd səbəbi tam göstərmir; növbəti müstəqil nəticə bu qərarı yeniləyəcək."
        return mission

    # -------------------------------------------------------- commitments
    def _apply_commitments(self, state, c: dict):
        now = now_iso()
        new = []
        if c.get("weekly_active_minutes") is not None:
            v = c["weekly_active_minutes"]
            if type(v) is not int or not 15 <= v <= 1500:
                raise CourseStateError("phase_forbids_action", "weekly_active_minutes must be 15..1500")
            new.append({"id": new_id("cm"), "kind": "weekly_active_minutes", "target": v})
        if c.get("study_days_per_week") is not None:
            v = c["study_days_per_week"]
            if type(v) is not int or not 1 <= v <= 7:
                raise CourseStateError("phase_forbids_action", "study_days_per_week must be 1..7")
            new.append({"id": new_id("cm"), "kind": "study_days_per_week", "target": v})
        for cp in c.get("checkpoints", []) or []:
            due = cp.get("due") if isinstance(cp, dict) else None
            try:
                datetime.fromisoformat(due)
            except (TypeError, ValueError):
                raise CourseStateError("phase_forbids_action", "checkpoint due must be an ISO date")
            new.append({"id": new_id("cm"), "kind": "checkpoint", "node_id": cp.get("node_id"), "due": due,
                        "description": str(cp.get("description", ""))[:200]})
        if not new:
            raise CourseStateError("phase_forbids_action", "no commitment given")
        kinds = {x["kind"] for x in new}
        for old in state["learning_commitments"]:
            if old["status"] == "active" and old["kind"] in kinds and old["kind"] != "checkpoint":
                old["status"] = "superseded"
                old["superseded_at"] = now
        for x in new:
            x.update({"status": "active", "agreed_at": now, "agreed_by": "learner",
                      "agreed_text": str(c.get("agreed_text", ""))[:300]})
        state["learning_commitments"].extend(new)
        return new

    def record_commitments(self, sid, commitments: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "record_commitments")
            new = self._apply_commitments(state, commitments)
            return {"commitments": new}, {"event_type": "course_commitments_recorded", "count": len(new)}, None
        return self._mutate(sid, "record_commitments", fn, **kw)

    def respond_accountability(self, sid, blocker: str, note: str | None = None, **kw):
        if blocker not in BLOCKERS:
            raise CourseStateError("phase_forbids_action", f"blocker must be one of {BLOCKERS}")

        def fn(d, led, state):
            cs.guard(state, "accountability_response")
            acc = state["accountability"]
            if not acc["open"]:
                raise CourseStateError("phase_forbids_action", "no open discrepancy")
            item = acc["open"]
            item.update({"blocker": blocker, "note": (note or "")[:500], "responded_at": now_iso()})
            due = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
            checkpoint = {"id": new_id("cm"), "kind": "recovery_checkpoint", "node_id": item["recovery_task"].get("node_id"),
                          "due": due, "description": item["recovery_task"]["text_az"], "status": "active",
                          "agreed_at": now_iso(), "agreed_by": "learner", "agreed_text": "Bərpa tapşırığı"}
            state["learning_commitments"].append(checkpoint)
            acc["history"].append(item)
            acc["open"] = None
            return ({"recorded": True, "recovery_checkpoint": checkpoint},
                    {"event_type": "course_accountability_response", "blocker": blocker}, None)
        return self._mutate(sid, "respond_accountability", fn, **kw)

    # -------------------------------------------------------- engagement
    def _intervals(self, led) -> dict:
        latest = {}
        for r in led.verified_records():
            p = r["payload"]
            if r["record_type"] == "event" and p.get("event_type") == "engagement_interval":
                iv = p.get("interval") or {}
                cur = latest.get(iv.get("interval_id"))
                if cur is None or iv.get("seq", -1) > cur.get("seq", -1):
                    latest[iv["interval_id"]] = iv
        return latest

    def record_activity(self, sid, interval: dict) -> dict:
        iv = _validate_interval(interval)
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            current = self._intervals(led).get(iv["interval_id"])
            if current is not None:
                if iv["seq"] <= current["seq"]:
                    return {"status": "duplicate_ignored", "interval_id": iv["interval_id"]}
                if current.get("ended"):
                    return {"status": "interval_already_closed", "interval_id": iv["interval_id"]}
                for k in ("kind", "node_id", "attempt_id", "started_at"):
                    if current.get(k) != iv.get(k):
                        raise CourseStateError("phase_forbids_action", f"interval {k} is immutable")
                for k in ("active_seconds", "idle_seconds", "paused_seconds"):
                    if iv[k] < current[k]:
                        raise CourseStateError("phase_forbids_action", "interval counters must not decrease")
            if iv["kind"] == "exam" and state["phase"] not in cs.TEACHING_LOCKED_PHASES:
                iv["note"] = "exam_interval_outside_active_exam"
            led.append("event", {"event_type": "engagement_interval", "interval": iv}, {"source": "engagement"})
            return {"status": "recorded", "interval_id": iv["interval_id"], "seq": iv["seq"]}

    def engagement(self, led, now=None) -> dict:
        now = now or datetime.now(UTC)
        ivs = list(self._intervals(led).values())
        by_kind, by_node = {}, {}
        week = 0
        last_activity = None
        for iv in ivs:
            k = by_kind.setdefault(iv["kind"], {"active_seconds": 0, "idle_seconds": 0, "paused_seconds": 0, "intervals": 0})
            for f in ("active_seconds", "idle_seconds", "paused_seconds"):
                k[f] += iv[f]
            k["intervals"] += 1
            if iv.get("node_id"):
                by_node[iv["node_id"]] = by_node.get(iv["node_id"], 0) + iv["active_seconds"]
            last = _utc(iv["last_at"])
            if last and now - last <= timedelta(days=7):
                week += iv["active_seconds"]
            if last and iv["active_seconds"] > 0 and (last_activity is None or last > last_activity):
                last_activity = last
        open_ids = [iv["interval_id"] for iv in ivs if not iv.get("ended")
                    and (_utc(iv["last_at"]) or now) > now - timedelta(minutes=5)]
        return {"by_kind": by_kind, "active_seconds_by_node": by_node,
                "last_7_days_active_minutes": round(week / 60, 1),
                "total_active_minutes": round(sum(v["active_seconds"] for v in by_kind.values()) / 60, 1),
                "last_activity_at": last_activity.isoformat() if last_activity else None,
                "open_interval_ids": open_ids, "interval_count": len(ivs),
                "method_az": ("Aktiv vaxt: səhifə görünür, pəncərə fokusdadır və son 60 saniyədə klaviatura/siçan/scroll "
                              "hərəkəti olub. Qalan açıq vaxt 'boş' sayılır. IDE-də iş görünmür, ona görə rəqəm aşağı ola bilər."),
                "recent": sorted(ivs, key=lambda x: x["last_at"], reverse=True)[:20]}

    # -------------------------------------------------------- accountability
    def _findings(self, state, eng, now) -> list[dict]:
        out = []
        for c in state["learning_commitments"]:
            if c.get("status") != "active":
                continue
            agreed = _utc(c.get("agreed_at"))
            if c["kind"] == "weekly_active_minutes" and agreed and now - agreed >= timedelta(days=7):
                observed = eng["last_7_days_active_minutes"]
                if observed < c["target"]:
                    out.append({"commitment_id": c["id"], "kind": c["kind"],
                                "fact_az": (f"Razılaşma: həftədə {c['target']} dəqiqə aktiv iş. Son 7 gündə tətbiqdə "
                                            f"qeydə alınan aktiv iş: {observed:g} dəqiqə.")})
            elif c["kind"] in {"checkpoint", "recovery_checkpoint"}:
                try:
                    due = datetime.fromisoformat(c["due"])
                except (TypeError, ValueError):
                    continue
                due = (due if due.tzinfo else due.replace(tzinfo=UTC)) + timedelta(days=1)
                done = (c.get("node_id") and self._passed(state, c["node_id"])) or c.get("completed_at")
                if now > due and not done:
                    label = c.get("description") or c.get("node_id") or "yoxlama nöqtəsi"
                    out.append({"commitment_id": c["id"], "kind": c["kind"],
                                "fact_az": f"Razılaşdırılmış yoxlama nöqtəsi ({c['due']}): “{label}” hələ tamamlanmayıb."})
        last = _utc(eng.get("last_activity_at"))
        if any(c.get("status") == "active" for c in state["learning_commitments"]) and last and now - last > timedelta(days=7):
            out.append({"commitment_id": None, "kind": "no_recorded_activity",
                        "fact_az": f"Son qeydə alınan aktiv iş {last.date().isoformat()} tarixindədir (7 gündən çox əvvəl)."})
        return out

    def resume(self, sid, now: datetime | None = None) -> dict:
        """Session open: reconcile and surface a commitment discrepancy once (no background monitoring)."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            now = now or datetime.now(UTC)
            eng = self.engagement(led, now)
            findings = self._findings(state, eng, now)
            acc = state["accountability"]
            signature = (cs.digest("ac", sorted((f["kind"], f.get("commitment_id") or "") for f in findings))
                         if findings else None)
            seen = {h.get("signature") for h in acc["history"]}
            if findings and state["phase"] not in cs.TEACHING_LOCKED_PHASES and (
                    acc["open"] is None or acc["open"]["signature"] != signature) and signature not in seen:
                node = (state.get("next_mission") or {}).get("node_id") or state["active_node_id"]
                title = None
                if node:
                    try:
                        title = self._node(self._plan(d), node)["title"]
                    except CourseStateError:
                        title = node
                acc["open"] = {
                    "id": new_id("acc"), "signature": signature, "opened_at": now_iso(), "findings": findings,
                    "message_az": " ".join(f["fact_az"] for f in findings)
                                  + " Bu rəqəmlər yalnız bu tətbiqdə qeydə alınan işə əsaslanır.",
                    "question_az": ("Nə mane oldu? Vaxt, mövzunun çətinliyi, materialın aydın olmaması, motivasiya, "
                                    "şəxsi səbəb, alət problemi və ya başqa bir şey?"),
                    "recovery_task": {"node_id": node,
                                      "text_az": (f"Bugün 15 dəqiqə: “{title}” üzrə növbəti addımı et və Yoxla düyməsini bas."
                                                  if title else "Bugün 15 dəqiqə: yol xəritəsində növbəti addımı seç və başla.")},
                }
                self._commit(d, led, state, {"event_type": "course_accountability_opened", "signature": signature})
            return self._view(d, led, state, eng)

    # ------------------------------------------------------------ views
    def state(self, sid) -> dict:
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            return self._view(d, led, state)

    def turn(self, sid) -> dict:
        """Agent turn hydration: authoritative state + the only permitted actions."""
        view = self.resume(sid)
        view["agent_directive"] = AGENT_DIRECTIVES[view["phase"]]
        view["teaching_allowed"] = not view["teaching_locked"]
        return view

    def lesson_view(self, sid, node_id) -> dict:
        """Lesson overview for the UI (objective, criteria, practice brief). Locked during exams."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, _led, state = self._open(sid)
            if state["phase"] in cs.TEACHING_LOCKED_PHASES:
                raise CourseStateError("exam_active")
            if state["approved_roadmap_version"] != state["roadmap_version"]:
                raise CourseStateError("roadmap_not_approved")
            node = self._node(self._plan(d), node_id)
            lesson = self._lesson(d, node_id)
            summary = cc.public_lesson_summary(lesson)
            out = {"node": {k: node.get(k) for k in ("id", "title", "task", "outcomes", "skills", "practice",
                                                    "exam_domain", "requires")}, "lesson": summary,
                   "progress": state["node_progress"].get(node_id), "phase": state["phase"]}
            if lesson:
                out["practice"] = {"prompt_md": lesson["practice"]["prompt_md"], "entry": lesson["practice"]["entry"],
                                   "file": str(self._module(d, node_id) / "submission" / lesson["practice"]["entry"]),
                                   "hint_count": len(lesson["practice"]["hints"]),
                                   "checks": [{"id": c["id"], "name": c["name"]} for c in lesson["practice"]["checks"]]}
                out["self_explanation_prompt"] = lesson["self_explanation_prompt"]
            return out

    def _view(self, d, led, state, eng=None) -> dict:
        plan = None
        p = d / "workspace" / "roadmap.json"
        if p.exists():
            plan = read_json(p)
        nodes = []
        for n in (plan or {}).get("nodes", []):
            nid = n["id"]
            lesson = self._lesson(d, nid)
            passed = self._passed(state, nid)
            status = ("passed" if passed else "active" if nid == state["active_node_id"]
                      else "available" if self._available(state, n) else "locked")
            nodes.append({**{k: n.get(k) for k in ("id", "title", "task", "requires", "outcomes", "skills", "practice",
                                                   "source_refs", "exam_domain", "pitfalls", "stage", "estimated_minutes")},
                          "status": status, "lesson": cc.public_lesson_summary(lesson),
                          "fresh_items": self._fresh_count(d, state, nid),
                          "progress": state["node_progress"].get(nid)})
        att = state["attempts"].get(state["active_attempt_id"]) if state["active_attempt_id"] else None
        manifest = self._manifest(d)
        eng = eng or self.engagement(led)
        approved = bool(state["approved_roadmap_version"]) and state["approved_roadmap_version"] == state["roadmap_version"]
        return {
            "ui_schema_version": cs.UI_SCHEMA_VERSION, "state_schema_version": cs.SCHEMA_VERSION,
            "session_id": state["session_id"], "revision": state["revision"], "updated_at": state["updated_at"],
            "phase": state["phase"], "phase_label_az": PHASE_LABEL_AZ[state["phase"]],
            "phase_entered_at": state["phase_entered_at"],
            "goal": state["goal"], "learner_profile": state["learner_profile"],
            "roadmap": {"topic": (plan or {}).get("topic"), "goal": (plan or {}).get("goal"),
                        "status": (plan or {}).get("status"), "sources": (plan or {}).get("sources", []),
                        "version": state["roadmap_version"], "approved_version": state["approved_roadmap_version"],
                        "approved": approved, "approval_event": state["roadmap_approval_event"],
                        "review_required": state["review_required"], "nodes": nodes},
            "active_node_id": state["active_node_id"],
            "active_attempt": self._public_attempt(d, att) if att else None,
            "attempt_status": state["attempt_status"], "assessment_item_version": state["assessment_item_version"],
            "support_provenance": state["support_provenance"], "assessment_result": state["assessment_result"],
            "next_mission": state["next_mission"], "resume_checkpoint": state["resume_checkpoint"],
            "learning_commitments": state["learning_commitments"], "accountability": state["accountability"],
            "engagement": eng, "evidence_refs": state["evidence_refs"],
            "sources": {"manifest_version": state["source_manifest_version"],
                        "notebook_id": (manifest or {}).get("notebook_id"), "complete": (manifest or {}).get("complete"),
                        "summary": (manifest or {}).get("summary"), "resources": state["source_refs"],
                        "unexpected": (manifest or {}).get("unexpected_sources", [])},
            "course_version": state["course_version"], "content_versions": state["content_versions"],
            "permitted_actions": cs.permitted_actions(state),
            "teaching_locked": state["phase"] in cs.TEACHING_LOCKED_PHASES,
            "honest_boundaries_az": HONEST_BOUNDARIES_AZ,
        }

    def export(self, sid) -> dict:
        """Versioned, human-readable export of course truth (no vault content)."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            view = self._view(d, led, state)
            evidence = [{"seq": r["seq"], "timestamp": r["timestamp"], "record_id": r["record_id"],
                         **{k: r["payload"].get(k) for k in ("evidence_id", "capability_id", "outcome", "independence",
                                                             "strength", "scope", "verification", "attempt_id")},
                         "source": r["provenance"].get("source")}
                        for r in led.verified_records() if r["record_type"] == "evidence"]
            attempts = [{k: a.get(k) for k in ("attempt_id", "node_id", "item_id", "item_version", "status", "started_at",
                                               "submitted_at", "finalized_at", "fast_track", "result", "evidence_id",
                                               "events", "abandoned_reason")} for a in state["attempts"].values()]
            return {"export_schema_version": 1, "exported_at": now_iso(), "course": view,
                    "approval_history": state["approval_history"], "attempts": attempts, "evidence": evidence,
                    "ledger_integrity": led.integrity()}

    # --------------------------------------------------------- UI regeneration
    def regenerate_ui(self, sid) -> dict:
        """Refresh frontend code from the template; learner files, IDs and evidence untouched."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, led, state = self._open(sid)
            frontend = d / "workspace" / "frontend"
            template = Path(__file__).resolve().parents[1] / "templates" / "react"
            if not frontend.is_dir():
                raise CourseStateError("phase_forbids_action", "no frontend in this workspace")
            src = frontend / "src"
            data = src / "data" / "roadmap.json"
            snapshot = read_json(data) if data.exists() else None
            for f in (template / "src").rglob("*"):
                if f.is_file():
                    target = src / f.relative_to(template / "src")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
            shutil.copy2(template / "package.json", frontend / "package.json")
            shutil.copy2(template / "index.html.template", frontend / "index.html")
            if snapshot is not None:
                write_json(data, snapshot)
            files = sorted(p for p in (template / "src").rglob("*") if p.is_file())
            digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
            led.append("event", {"event_type": "ui_regenerated", "ui_schema_version": cs.UI_SCHEMA_VERSION,
                                 "template_sha256": digest, "course_revision": state["revision"]},
                       {"source": "course_ui"})
            return {"status": "regenerated", "ui_schema_version": cs.UI_SCHEMA_VERSION, "template_sha256": digest,
                    "revision": state["revision"], "note": "Rebuild with `npm run build` in the frontend directory."}


# ----------------------------------------------------------------- helpers
def _ref(result: dict) -> dict:
    keep = ("attempt_id", "status", "node_id", "kind", "roadmap_version", "content_version", "manifest_version")
    return {k: result[k] for k in keep if k in result and isinstance(result[k], (str, int, bool, type(None)))}


def _practice_feedback(results, fired, recurring) -> list[str]:
    lines = []
    for r in results:
        if r["passed"]:
            continue
        line = f"✗ {r['name']}: {r['feedback']}"
        if r.get("observed") is not None:
            line += f" Sənin nəticən: {r['observed']}."
            if r.get("expected"):
                line += f" Gözlənilən: {r['expected']}."
        if r.get("error"):
            line += f" ({r['error']})"
        lines.append(line)
    for m in fired:
        lines.append(f"Ehtimal olunan anlaşılmazlıq — {m['name']}: {m['feedback']}")
    if recurring:
        lines.append("Bu səhv ikinci dəfədir görünür. Başqa yolla izahı aç (Kömək → başqa izah) və sonra yenidən yoxla.")
    if not lines:
        lines.append("Bütün görünən yoxlamalar keçdi. Bu bələdçili praktikadır; müstəqil bacarığı yeni tapşırıq göstərəcək.")
    return lines


def _validate_interval(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise CourseStateError("phase_forbids_action", "interval must be an object")
    iv = {k: raw.get(k) for k in ("interval_id", "kind", "node_id", "attempt_id", "started_at", "last_at",
                                  "active_seconds", "idle_seconds", "paused_seconds", "seq", "ended")}
    if not isinstance(iv["interval_id"], str) or not _IVID.fullmatch(iv["interval_id"]):
        raise CourseStateError("phase_forbids_action", "interval_id must match iv_<hex>")
    if iv["kind"] not in ACTIVITY_KINDS:
        raise CourseStateError("phase_forbids_action", f"kind must be one of {ACTIVITY_KINDS}")
    if iv["node_id"] is not None and (not isinstance(iv["node_id"], str) or not _NODE.fullmatch(iv["node_id"])):
        raise CourseStateError("phase_forbids_action", "bad node_id")
    if iv["attempt_id"] is not None and (not isinstance(iv["attempt_id"], str) or not _ATTEMPT.fullmatch(iv["attempt_id"])):
        raise CourseStateError("phase_forbids_action", "bad attempt_id")
    for k in ("active_seconds", "idle_seconds", "paused_seconds", "seq"):
        iv[k] = iv[k] if iv[k] is not None else 0
        if type(iv[k]) is not int or iv[k] < 0:
            raise CourseStateError("phase_forbids_action", f"{k} must be a non-negative integer")
    iv["ended"] = bool(iv["ended"])
    start, last = _utc(iv["started_at"]), _utc(iv["last_at"])
    now = datetime.now(UTC)
    if not start or not last or last < start:
        raise CourseStateError("phase_forbids_action", "started_at/last_at must be ISO timestamps, last >= start")
    if last > now + timedelta(minutes=2) or start < now - timedelta(days=2):
        raise CourseStateError("phase_forbids_action", "interval timestamps outside the accepted window")
    elapsed = (last - start).total_seconds()
    if iv["active_seconds"] + iv["idle_seconds"] + iv["paused_seconds"] > elapsed + 30:
        raise CourseStateError("phase_forbids_action", "reported seconds exceed elapsed wall time")
    if iv["active_seconds"] > 4 * 3600:
        raise CourseStateError("phase_forbids_action", "single interval capped at 4 hours")
    iv["started_at"], iv["last_at"] = start.isoformat(), last.isoformat()
    return iv


def assert_no_active_exam(session_dir: Path, capability: str | None = None) -> None:
    """Guard used by legacy evidence paths: no teaching/evidence side-channel during an exam."""
    p = Path(session_dir) / STATE_FILE
    if not p.exists():
        return
    state = read_json(p)
    if state.get("phase") in cs.TEACHING_LOCKED_PHASES:
        raise CourseStateError("exam_active",
                               f"evidence/teaching for {capability or 'any node'} is locked until the exam is finalized")


def course_gate(session_dir: Path) -> dict | None:
    """Driver pre-check: course phases override the generic next-action loop."""
    p = Path(session_dir) / STATE_FILE
    if not p.exists():
        return None
    state = read_json(p)
    phase = state.get("phase")
    if phase in {"onboarding", "source_preparation"}:
        return {"action": "course_onboarding", "phase": phase, "reason": "goal_profile_and_sources_before_roadmap",
                "instruction": AGENT_DIRECTIVES[phase]}
    if phase == "roadmap_awaiting_approval" or state.get("approved_roadmap_version") != state.get("roadmap_version"):
        return {"action": "await_roadmap_approval", "phase": phase, "roadmap_version": state.get("roadmap_version"),
                "reason": "learner_must_approve_current_roadmap_version",
                "instruction": AGENT_DIRECTIVES["roadmap_awaiting_approval"]}
    if phase in cs.TEACHING_LOCKED_PHASES:
        return {"action": "exam_in_progress", "phase": phase, "attempt_id": state.get("active_attempt_id"),
                "reason": "assessed_attempt_withholds_teaching", "instruction": AGENT_DIRECTIVES[phase]}
    return None

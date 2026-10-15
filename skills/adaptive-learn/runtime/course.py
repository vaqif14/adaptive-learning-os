"""Course action layer: one guarded entry point for the agent, CLI and local UI.

Every action runs the same sequence under the session lock:
  recover journal -> verify ledger integrity -> load + migrate course-state ->
  validate invariants -> reconcile incomplete operations -> idempotency/revision
  check -> phase/approval guard -> effect -> commit (revision + 1).
The ledger stays the single authoritative store: course-state.json and
source-manifest.json are journaled through ``local_state.commit_state``,
evidence through ``EventLedger.append``, engagement intervals as ledger events.
Nothing here trusts chat memory or client-side state.

v2 (schema 2) binds every lesson, item, attempt, pass and review to the node's
capability version, identifies items by content (not id), re-verifies lesson
grounding on every use, and keeps a durable per-capability review queue driven
by an injectable clock. Nothing is deleted: superseded passes, items, lessons and
reviews stay in state, the vault/lesson archives and the ledger.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import course_content as cc
from . import course_sources as csrc
from . import course_state as cs
from . import course_journey as journey
from . import course_recovery as recovery_policy
from .course_roadmap import render_roadmap_html
from .course_state import CourseStateError
from .evidence_history import parse_time
from .listener import grade_explanation
from .local_state import commit_state
from .scheduling import half_life_days
from .session import SessionKernel
from .session_lock import session_lock
from .utils import new_id, private_dirs, read_json, write_bytes, write_json

STATE_FILE = "course-state.json"
MANIFEST_FILE = "source-manifest.json"
PRE_V2_ARCHIVE = "course-state.pre-v2.json"
PRE_V3_ARCHIVE = "course-state.pre-v3.json"
# A review counts as delayed evidence only if the LEDGER shows this much real spacing.
REVIEW_MIN_OBSERVED_HOURS = 20
RUBRIC_GRADERS = ("host_agent", "human")
RUBRIC_CONFIDENCE = ("low", "medium", "high")
_INTAKE_GATED = frozenset({"propose_roadmap", "approve_roadmap", "install_items", "install_lesson", "promote_build"})
_NODE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_ATTEMPT = re.compile(r"att_[0-9a-f]{16}")
_IVID = re.compile(r"iv_[0-9a-f]{16,32}")
_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,80}")
HELP_KINDS = ("explain", "why", "worked_example", "hint", "alternative", "walkthrough", "ladder")
REVIEW_FIRST_ACTIONS = frozenset({"start_mission", "help", "self_explanation", "begin_practice",
                                  "practice_check", "practice_answer", "practice_reveal", "practice_reset",
                                  "start_exam"})
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


class _FileJournal:
    """Before-images of the files one install may write, so a failed install can put the session tree back."""

    def __init__(self):
        self.before: dict[Path, tuple[bytes, int] | None] = {}

    @property
    def captured(self) -> bool:
        return bool(self.before)

    def capture(self, paths) -> None:
        for path in paths:
            if path in self.before:
                continue
            if path.is_file() and not path.is_symlink():
                self.before[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            else:
                self.before[path] = None

    def restore(self, parking: Path, root: Path) -> None:
        for path, image in self.before.items():
            if image is not None:
                if not path.is_file() or path.read_bytes() != image[0]:
                    write_bytes(path, image[0])
                    os.chmod(path, image[1])
            elif path.exists() or path.is_symlink():      # created by the failed install: parked, not deleted
                target = parking / path.relative_to(root)
                private_dirs(target.parent)
                os.rename(path, target)


class CourseKernel:
    def __init__(self, workspace: Path, *, clock=None):
        self.sk = SessionKernel(Path(workspace))
        # Injectable clock (tests, replay): state timestamps, due dates and windows use it.
        # Elapsed-time CLAIMS (spacing actually observed) use ledger timestamps instead.
        self.clock = clock or (lambda: datetime.now(UTC))

    def _dt(self) -> datetime:
        t = self.clock()
        return t if t.tzinfo else t.replace(tzinfo=UTC)

    def _now(self) -> str:
        return self._dt().isoformat()

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
        raw = read_json(path)
        state, applied = cs.migrate(raw)
        if applied:
            # The pre-migration state is archived in the same journaled transaction.
            archive = PRE_V2_ARCHIVE if (raw.get("schema_version", 0) if isinstance(raw, dict) else 0) < 2 else PRE_V3_ARCHIVE
            files = None if (d / archive).exists() else {archive: raw}
            self._commit(d, led, state, {"event_type": "course_state_migrated", "from_versions": applied}, files)
        errors = cs.validate(state)
        if errors:
            raise CourseStateError("invalid_state", ",".join(errors))
        if state["legacy_binding_pending"]:
            self._bind_legacy(d, led, state)
        self._reconcile(d, led, state)
        return d, led, state

    def _commit(self, d, led, state, event: dict, files: dict | None = None, workspace_install=None):
        state["revision"] += 1
        state["updated_at"] = self._now()
        errors = cs.validate(state)
        if errors:  # never journal an inconsistent state
            state["revision"] -= 1
            raise CourseStateError("invalid_state", ",".join(errors))
        event = dict(event)
        etype = event.pop("event_type")
        commit_state(d, led, {"event_type": etype, "course_revision": state["revision"], **event},
                     {STATE_FILE: state, **(files or {})}, workspace_install=workspace_install)

    def _mutate(self, sid, action, fn, *, expected_revision=None, idempotency_key=None, request=None, rehydrate=None):
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
                    if prev.get("request") not in (None, _request_digest(request)):
                        raise CourseStateError("phase_forbids_action", "idempotency key reused with different arguments")
                    replayed = prev["result"]
                    if rehydrate is not None:   # re-derive what the idempotency memory deliberately does not keep
                        replayed = rehydrate(d, state, dict(replayed))
                    return {"idempotent_replay": True, "action": action, "result": replayed,
                            "revision": state["revision"]}
            cs.check_revision(state, expected_revision)
            intake = self._intake_view(state)
            if action in _INTAKE_GATED and intake["required"] and not intake["ready_to_plan"]:
                raise CourseStateError("diagnostic_required")
            if action in _INTAKE_GATED and intake["required"]:
                gate_action = "install_content" if action in {"install_items", "install_lesson", "promote_build"} else action
                cs.guard(state, gate_action)
                readiness = self._generation_readiness(d, state, (request or {}).get("plan") if action == "propose_roadmap" else self._plan_safe(d),
                                                       check_generation_basis=action != "propose_roadmap")
                if not readiness["ready"]:
                    raise CourseStateError(readiness["blockers"][0])
            if action in REVIEW_FIRST_ACTIONS:
                # Existing exams still resume even when a review became due meanwhile.
                locked = state["phase"] in cs.TEACHING_LOCKED_PHASES
                if not (action == "start_exam" and locked):
                    cs.guard(state, action)  # preserve approval and exam-lock refusal precedence
                if not locked:
                    readiness = self._generation_readiness(d, state, self._plan_safe(d))
                    if readiness["required"] and not readiness["ready"]:
                        raise CourseStateError(readiness["blockers"][0])
                    self._require_reviews_first(d, state)
                    if action in {"start_mission", "start_exam"}:
                        node_id = (request or {}).get("node")
                        recovery = (state["node_progress"].get(node_id) or {}).get("recovery") or {}
                        if recovery.get("status") == "open" and recovery.get("capability_version") == self._cap_version(state, node_id):
                            raise CourseStateError("recovery_required", node_id)
            operation = fn(d, led, state)
            result, event, files = operation[:3]
            workspace_install = operation[3] if len(operation) == 4 else None
            if event is not None:
                cs.remember(state, idempotency_key, action, _ref(result), _request_digest(request))
                if workspace_install is None:
                    self._commit(d, led, state, event, files)
                else:
                    self._commit(d, led, state, event, files, workspace_install=workspace_install)
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

    def _lesson(self, d, node, state) -> dict | None:
        """Only the committed lesson version counts. It is read from the immutable
        per-version archive (pre-v2 lessons: the live file if its version still matches),
        so a file left by an interrupted install neither serves nor hides anything."""
        version = state["content_versions"].get(node)
        if not version:
            return None
        live = self._lesson_path(d, node)
        for path in (live.parent / "archive" / f"{version}.json", live):
            if path.is_file():
                try:
                    lesson = read_json(path)
                except (OSError, ValueError):
                    continue
                if lesson.get("content_version") == version:
                    return lesson
        return None

    def _committed_item(self, d, node, item_id, version) -> dict | None:
        vault = self._vault(d, node)
        for path in (vault / "archive" / f"{version}.json", vault / f"{item_id}.json"):
            if path.is_file():
                try:
                    item = read_json(path)
                except (OSError, ValueError):
                    continue
                if item.get("item_id") == item_id and item.get("item_version") == version:
                    return item
        return None

    def _items(self, d, node, state) -> list[dict]:
        """Committed item versions only (archive first, live file if it still matches)."""
        out = []
        for item_id, version in sorted((state["item_versions"].get(node) or {}).items()):
            item = self._committed_item(d, node, item_id, version)
            if item is not None:
                out.append(item)
        return out

    def _passed(self, state, node) -> bool:
        """Passed under the CURRENT capability version (a superseded pass is history)."""
        prog = state["node_progress"].get(node) or {}
        cap = state["capabilities"].get(node)
        if not prog.get("passed"):
            return False
        return not cap or prog.get("capability_version", cap.get("version")) == cap.get("version")

    def _cap_version(self, state, node_id):
        return (state["capabilities"].get(node_id) or {}).get("version")

    # ------------------------------------------------------------- readiness
    def _grounding(self, d, state, node_id, lesson, manifest=None) -> dict:
        """Re-verify every source quote of a committed lesson against the committed
        manifest AND the source hashes the lesson was bound to (fail closed)."""
        manifest = self._manifest(d) if manifest is None else manifest
        bound = (state["lesson_bindings"].get(node_id) or {}).get("sources") or {}
        entries = {r.get("source_id"): r for r in (manifest or {}).get("resources", [])}
        problems, seen, checked = [], set(), {}
        for part, part_refs in cc.lesson_ref_parts(lesson):
            for ref in part_refs:
                if ref.get("grounding") != "source":
                    continue
                sid = ref.get("source_id")
                key = (sid, ref.get("quote"))
                if key not in checked:
                    checked[key] = (csrc.find_quote(d / "sources", manifest, sid, ref.get("quote")) if manifest
                                    else {"grounded": False, "reason": "no_validated_source_manifest"})
                res = checked[key]
                if not res.get("grounded"):
                    reason = res.get("reason")
                elif bound.get(sid) != (entries.get(sid) or {}).get("content_sha256"):
                    reason = "source_changed_since_binding"
                else:
                    continue
                if (sid, reason) not in seen:
                    seen.add((sid, reason))
                    problems.append({"source_id": sid, "part": part, "reason": reason})
        return {"status": "invalid" if problems else "valid", "problems": problems,
                "rebindable": bool(problems) and all(p["reason"] == "source_changed_since_binding" for p in problems)}

    def _lesson_gate(self, d, state, node_id, lesson=None, manifest=None) -> dict:
        lesson = self._lesson(d, node_id, state) if lesson is None else lesson
        if not lesson:
            return {"status": "missing"}
        cap = self._cap_version(state, node_id)
        binding = state["lesson_bindings"].get(node_id) or {}
        if cap and binding.get("capability_version") != cap:
            return {"status": "capability_changed",
                    "label_az": "Tarixi nüsxə: bu dərs mərhələnin əvvəlki (dəyişmiş) məqsədi üçün yazılıb."}
        g = self._grounding(d, state, node_id, lesson, manifest)
        if g["status"] != "valid":
            return {"status": "grounding_invalid", "problems": g["problems"], "rebindable": g["rebindable"],
                    "label_az": "Tarixi nüsxə: mənbə dəyişib və ya etibarsızdır; bu material cari mənbə ilə təsdiqlənməyib."}
        return {"status": "ready"}

    def _ready_lesson(self, d, state, node_id) -> dict:
        """The committed lesson, only if it is bound to the current capability and its
        grounding still verifies; otherwise a stable refusal (fail closed)."""
        readiness = self._generation_readiness(d, state, self._plan_safe(d))
        if readiness["required"] and not readiness["ready"]:
            raise CourseStateError(readiness["blockers"][0])
        lesson = self._lesson(d, node_id, state)
        gate = self._lesson_gate(d, state, node_id, lesson)
        if gate["status"] == "missing":
            raise CourseStateError("lesson_missing", "lesson content for this node is not authored yet")
        if gate["status"] == "capability_changed":
            raise CourseStateError("capability_changed", "re-author the lesson for the approved capability")
        if gate["status"] == "grounding_invalid":
            raise CourseStateError("grounding_invalid", ", ".join(f"{p['source_id']}:{p['reason']}" for p in gate["problems"]))
        return lesson

    def _node_source_problems(self, state, node) -> list[str]:
        """Roadmap source keys whose committed manifest status is explicitly not usable."""
        refs = {r.get("key"): r for r in state["source_refs"]}
        return sorted(k for k in node.get("source_refs") or []
                      if k not in refs or refs[k].get("status") not in csrc._STATUSES_OK)

    # ------------------------------------------------------- item freshness
    def _exposures(self, state, node_id) -> list[dict]:
        # Diagnostics reveal prompts too. Treat matching/nearby prompts as uncertain
        # freshness even though diagnostic judgments never establish mastery.
        diagnostic = [{"item_id": "diagnostic-" + e["id"], "sigs": [], "prompt_norm": cc.canonical_text(e["prompt"]),
                       "surface": cc.canonical_text(e["prompt"]), "at": e.get("at")}
                      for e in state["learner_profile"]["diagnostic"]
                      if e.get("basis") == "observed" and e.get("node_id") == node_id]
        policy = state["learner_profile"].get("intake_policy") or {}
        probes = list(policy.get("probe_history") or []) + ([policy["active_probe"]] if policy.get("active_probe") else [])
        diagnostic += [{"item_id": "diagnostic-" + p["id"], "sigs": [], "prompt_norm": cc.canonical_text(p["prompt"]),
                        "surface": cc.canonical_text(p["prompt"]), "at": p.get("prepared_at")}
                       for p in probes if p.get("node_id") == node_id]
        return state["exposure_records"].get(node_id, []) + diagnostic

    def _item_status(self, state, node_id, item) -> dict:
        """Can this vault item give a FRESH unassisted attempt? Content identity, not id."""
        if state["item_versions"].get(node_id, {}).get(item["item_id"]) != item.get("item_version"):
            return {"status": "uncommitted"}
        binding = state["item_bindings"].get(node_id, {}).get(item["item_id"]) or {}
        cap = self._cap_version(state, node_id)
        if cap and binding.get("capability_version") != cap:
            return {"status": "capability_changed"}
        fresh = cc.classify_freshness(cc.fingerprint(item), self._exposures(state, node_id))
        if fresh["status"] == "needs_review":
            rv = state["freshness_reviews"].get(node_id, {}).get(item["item_id"]) or {}
            if rv.get("item_version") == item["item_version"] and rv.get("decision") == "distinct":
                return {**fresh, "status": "fresh", "basis": "author_review"}
        if fresh["status"] == "fresh":
            fresh["basis"] = "automatic"
        return fresh

    def _eligible(self, d, state, node_id) -> list[tuple[dict, dict]]:
        out = []
        for it in self._items(d, node_id, state):
            st = self._item_status(state, node_id, it)
            if st["status"] == "fresh":
                out.append((it, st))
        return out

    def _available(self, state, node) -> bool:
        caps = state.get("capabilities") or {}
        def prerequisite_ready(nid, trail):
            if nid in trail or not self._passed(state, nid):
                return False
            return all(prerequisite_ready(r, trail | {nid}) for r in (caps.get(nid) or {}).get("requires", []))
        return all(prerequisite_ready(r, {node["id"]}) for r in node.get("requires", []))

    def _progress(self, state, node_id) -> dict:
        return state["node_progress"].setdefault(node_id, {"passed": False, "last_outcome": None, "exam_attempts": [],
                                                           "practice": None, "help": {}, "method": "standard"})

    # ------------------------------------------------------------- reconcile
    def _evidence_exists(self, led, evidence_id: str) -> bool:
        return any(r["record_type"] == "evidence" and r["payload"].get("evidence_id") == evidence_id
                   for r in led.verified_records())

    def _reconcile(self, d, led, state):
        """Repair interrupted multi-step operations before any action runs.

        1. The installed roadmap must be the version the state knows about. A drift
           (crash between the workspace install and the course-state commit, or any
           out-of-band replacement) re-opens approval instead of teaching on it.
        2. A finalization whose evidence reached the ledger before a crash is completed.
        """
        plan_path = d / "workspace" / "roadmap.json"
        if state["roadmap_version"] and plan_path.exists():
            plan = read_json(plan_path)
            installed = cs.roadmap_version(plan) if plan.get("status") != "needs_curriculum_research" else None
            if installed and installed != state["roadmap_version"]:
                if state["phase"] in cs.TEACHING_LOCKED_PHASES:
                    raise CourseStateError("invalid_state", "roadmap changed during an exam; restore it or abandon the attempt")
                previous = state["approved_roadmap_version"]
                state["roadmap_version"] = installed
                state["review_required"] = {"reason": "roadmap_changed_without_review", "previous_version": previous,
                                            "new_version": installed, "changes": [{"change": "out_of_band_install"}],
                                            "at": self._now()}
                cs.set_phase(state, "roadmap_awaiting_approval", self._now())
                self._commit(d, led, state, {"event_type": "course_roadmap_drift_detected",
                                             "installed_version": installed, "approved_version": previous})
        att_id = state["active_attempt_id"]
        if not att_id:
            return
        att = state["attempts"][att_id]
        if att["status"] == "scored" and self._evidence_exists(led, f"ev_exam_{att_id}"):
            self._finalize_state(d, led, state, att, reconciled=True)
            self._commit(d, led, state, {"event_type": "course_exam_finalized", "attempt_id": att_id,
                                         "reconciled_after_interruption": True})

    def _bind_legacy(self, d, led, state):
        """Resolve v2 bindings for a migrated v1 course without erasing anything.

        * Approved roadmap installed: every node is bound to its current capability
          version (basis ``legacy_migration``); passes, lessons and items keep counting.
        * Approval pending with a recorded diff: nodes whose capability fields changed get
          an unknown previous version, so the coming approval supersedes them precisely;
          an out-of-band change of unknown content is bound as ``legacy_unverified``.
        * Exposure ids become content records from the vault (missing files are recorded
          as unverifiable). A passed node gets a scheduled review from its last pass.
        """
        now = self._now()
        plan = self._plan_safe(d)
        if plan.get("status") == "needs_curriculum_research":
            plan = {"nodes": []}
        snap = cs.capability_snapshot(plan)
        installed = cs.roadmap_version(plan) if plan.get("nodes") else None
        # A roadmap on disk that is not the approved version (drift, out-of-band install)
        # is never bound as the approved capability.
        approved_now = (bool(state["approved_roadmap_version"])
                        and state["approved_roadmap_version"] == state["roadmap_version"] == installed)
        changed = set()
        for ch in (state.get("review_required") or {}).get("changes") or []:
            if ch.get("change") == "node_changed" and set(ch.get("fields") or []) & set(cs.CAPABILITY_FIELDS):
                changed.add(ch.get("node"))
        summary = {"capabilities": 0, "lessons": 0, "items": 0, "exposures": 0, "unverifiable_exposures": 0,
                   "reviews": 0, "passes_kept": 0}
        if state["approved_roadmap_version"] and not state["capabilities"]:
            event_id = (state["roadmap_approval_event"] or {}).get("event_id")
            for nid, cur in snap.items():
                if not approved_now and nid in changed:
                    entry = {"version": None, "basis": "legacy_changed"}
                else:
                    entry = {"version": cur["version"], "basis": "legacy_migration" if approved_now else "legacy_unverified"}
                state["capabilities"][nid] = {**entry, "requires": cur["requires"], "since": now,
                                              "approval_event_id": event_id}
                summary["capabilities"] += 1
            if not approved_now:
                # Nodes with history but absent from the pending plan were removed by it: an
                # unknown version makes the coming approval supersede them (history kept).
                known = (set(state["node_progress"]) | set(state["content_versions"])
                         | {q.get("node_id") for q in state["review_queue"]})
                for nid in sorted(known - set(snap)):
                    state["capabilities"][nid] = {"version": None, "basis": "legacy_removed", "requires": [],
                                                  "since": now, "approval_event_id": event_id}
                    summary["capabilities"] += 1
        manifest = self._manifest(d) or {}
        shas = {r["source_id"]: r.get("content_sha256") for r in manifest.get("resources", []) if r.get("source_id")}
        for nid, version in state["content_versions"].items():
            lesson = self._lesson(d, nid, state)
            if lesson and nid not in state["lesson_bindings"]:
                state["lesson_bindings"][nid] = {
                    "content_version": version, "capability_version": self._cap_version(state, nid),
                    "sources": {sid: shas.get(sid) for sid in sorted(cc.cited_sources(lesson))},
                    "bound_at": now, "basis": "legacy_migration"}
                summary["lessons"] += 1
        for nid, versions in state["item_versions"].items():
            vault = {it["item_id"]: it for it in self._items(d, nid, state)}
            bindings = state["item_bindings"].setdefault(nid, {})
            for item_id, version in versions.items():
                it = vault.get(item_id)
                if item_id in bindings:
                    continue
                fp = cc.fingerprint(it) if it and it.get("item_version") == version else None
                bindings[item_id] = {"item_version": version, "capability_version": self._cap_version(state, nid),
                                     "content_fingerprint": fp and fp["content"], "behavior_fingerprint": fp and fp["behavior"],
                                     "kind": fp and fp["kind"], "bound_at": now, "basis": "legacy_migration"}
                summary["items"] += 1
        for nid, ids in state["exposure"].items():
            vault = {it["item_id"]: it for it in self._items(d, nid, state)}
            records = list(state["exposure_records"].get(nid) or [])
            recorded = [r.get("item_id") for r in records]
            for item_id in [i for i in ids if i not in recorded]:
                att = next((a for a in state["attempts"].values()
                            if a.get("node_id") == nid and a.get("item_id") == item_id), {})
                it = vault.get(item_id)
                if it and (not att or it.get("item_version") == att.get("item_version")):
                    records.append(cc.exposure_record(it, att.get("attempt_id"), att.get("started_at") or now))
                else:
                    records.append({"item_id": item_id, "unknown": True, "attempt_id": att.get("attempt_id"),
                                    "at": att.get("started_at") or now})
                    summary["unverifiable_exposures"] += 1
                summary["exposures"] += 1
            state["exposure_records"][nid] = records
        legacy_review = (state.get("next_mission") or {}).get("review") or {}
        for nid, prog in state["node_progress"].items():
            if not prog.get("passed"):
                continue
            prog.setdefault("capability_version", self._cap_version(state, nid))
            summary["passes_kept"] += 1
            if any(q["node_id"] == nid for q in state["review_queue"]):
                continue
            last = max((a for a in state["attempts"].values() if a.get("node_id") == nid and a.get("status") == "finalized"
                        and (a.get("result") or {}).get("outcome") == "correct"),
                       key=lambda a: a.get("finalized_at") or "", default=None)
            if last is None:
                continue
            due = legacy_review.get("not_before") if legacy_review.get("node_id") == nid else None
            if not due:
                anchor = _utc(last.get("finalized_at")) or self._dt()
                due = (anchor + timedelta(days=half_life_days(0, 0))).isoformat()
            self._enqueue_review(state, nid, source_attempt=last["attempt_id"], recalls=0, lapses=0,
                                 basis="legacy_migration", due_at=due)
            summary["reviews"] += 1
        state["legacy_binding_pending"] = False
        state["migration_log"].append({"at": now, "step": "legacy_bindings_resolved", **summary})
        self._commit(d, led, state, {"event_type": "course_legacy_bindings_resolved", **summary})

    # ------------------------------------------------------------------ init
    def init(self, sid: str, *, goal: str | None = None, require_intake: bool = False) -> dict:
        d = self.sk.dir(sid)
        with session_lock(d):
            self.sk._recover_state(sid)
            led = self.sk.ledger(sid)
            led.require_integrity()
            if (d / STATE_FILE).exists():
                d, led, state = self._open(sid)
                return {"status": "already_initialized", **self._view(d, led, state)}
            contract = read_json(d / "learning-contract.json")
            state = cs.new_state(sid, goal or contract.get("goal"), self._now())
            state["learner_profile"]["stated_background"] = contract.get("stated_background")
            cli_first = read_json(d / "session.json").get("workflow") == "cli_first"
            state["learner_profile"]["intake_policy"] = {"required": bool(require_intake or cli_first), "skip": None,
                                                         "active_probe": None, "probe_history": []}
            if (d / "workspace" / "roadmap.json").exists():
                plan = read_json(d / "workspace" / "roadmap.json")
                if plan.get("status") != "needs_curriculum_research":
                    # A pre-course (legacy) roadmap still needs explicit approval.
                    state["roadmap_version"] = cs.roadmap_version(plan)
                    state["review_required"] = {"reason": "legacy_roadmap_requires_approval",
                                                "new_version": state["roadmap_version"],
                                                "changes": cs.diff_roadmaps(None, plan)}
                    cs.set_phase(state, "roadmap_awaiting_approval", self._now())
            self._commit(d, led, state, {"event_type": "course_initialized", "schema_version": cs.SCHEMA_VERSION})
            return {"status": "initialized", **self._view(d, led, state)}

    # ------------------------------------------------------- onboarding/profile
    @staticmethod
    def _valid_exact_target(target):
        target = target or {}
        return (target.get("kind") == "certification" and isinstance(target.get("code"), str) and 1 <= len(target["code"].strip()) <= 100
                and isinstance(target.get("official_name"), str) and 1 <= len(target["official_name"].strip()) <= 300
                and isinstance(target.get("verification_url"), str) and target["verification_url"].startswith("https://")
                and isinstance(target.get("objectives"), list) and bool(target["objectives"])
                and all(isinstance(x, dict) and isinstance(x.get("domain"), str) and x["domain"].strip() for x in target["objectives"])
                and isinstance(target.get("verified_at"), str) and bool(target["verified_at"].strip()) and "version" in target
                and (target.get("version") is not None or isinstance(target.get("version_note"), str) and bool(target["version_note"].strip())))

    @staticmethod
    def _target_required(state):
        text = cc.canonical_text((state.get("goal") or {}).get("text") or "")
        target = (state.get("goal") or {}).get("target") or {}
        clarification = ((state.get("learner_profile") or {}).get("intake_policy") or {}).get("clarification") or {}
        return (target.get("kind") == "certification" or clarification.get("kind") == "certification"
                or bool(re.search(r"certificat|certifik|sertifik|\bexam\b|imtahan", text)))

    @staticmethod
    def _clarification_basis(state):
        profile = state["learner_profile"]
        skip = (profile.get("intake_policy") or {}).get("skip") or {}
        return cs.digest("clar", {"goal": state["goal"], "background": profile.get("stated_background"),
                                  "skip": {k: skip.get(k) for k in ("reason", "at", "learner_agreed")}})

    def _generation_basis(self, d, state):
        manifest = self._manifest(d) or {}
        return {"goal_confirmation": self._clarification_basis(state),
                "sources": {r.get("key"): {"source_id": r.get("source_id"),
                            "text_sha256": r.get("text_sha256") or r.get("content_sha256"),
                            "status": r.get("status")} for r in manifest.get("resources") or []}}

    def _generation_readiness(self, d, state, plan=None, *, check_generation_basis=True):
        """Read-only staged-generation gate; metadata alone never proves readable sources.

        API-created legacy courses retain their explicit legacy policy. New CLI courses
        and already strict intake sessions require current learner clarification.
        """
        policy = state["learner_profile"].get("intake_policy") or {}
        if not policy.get("required"):
            return {"ready": True, "required": False, "policy": "legacy_course",
                    "blockers": [], "clarification_required": False, "target_required": False,
                    "source_ready": None, "diagnostic_ready": None}
        blockers = []
        profile = state["learner_profile"]
        if not state["goal"].get("text"):
            blockers.append("goal_required")
        if not profile.get("stated_background"):
            blockers.append("background_required")
        skip = policy.get("skip") or {}
        clarification = policy.get("clarification") or {}
        current_confirmation = clarification.get("basis") == self._clarification_basis(state)
        qualified_skip = bool(skip.get("learner_agreed") and current_confirmation
                              and clarification.get("accept_provisional_skip") is True)
        diagnostic_ready = qualified_skip or (not skip and self._intake_view(state)["ready_to_plan"])
        if skip and not qualified_skip:
            blockers.append("provisional_skip_confirmation_required")
        elif not diagnostic_ready:
            blockers.append("diagnostic_required")
        target_required = self._target_required(state)
        target = state["goal"].get("target") or {}
        target_valid = self._valid_exact_target(target)
        if target_required and not target_valid:
            blockers.append("target_required")
        if not current_confirmation:
            blockers.append("goal_clarification_required")
        manifest = self._manifest(d)
        resources = (manifest or {}).get("resources") or []
        source_ready = bool(manifest and manifest.get("complete") is True and resources
                            and manifest.get("manifest_version") == state["source_manifest_version"])
        if not source_ready:
            blockers.append("sources_required")
        else:
            for resource in resources:
                try:
                    text, problem = csrc._cached_text(d / "sources", resource.get("source_id"), resource.get("content_sha256"))
                    readable = (resource.get("status") in csrc._STATUSES_OK and resource.get("stale") is not True
                                and not problem and isinstance(text, str) and len(text.strip()) >= csrc.MIN_CONTENT_CHARS)
                except (OSError, ValueError, TypeError, UnicodeError):
                    readable = False
                if not readable:
                    source_ready = False
                    blockers.append("sources_unreadable")
                    break
        if plan and plan.get("status") != "needs_curriculum_research":
            for node in plan.get("nodes") or []:
                if not node.get("source_refs"):
                    blockers.append("roadmap_source_refs_required")
                elif self._node_source_problems(state, node):
                    blockers.append("roadmap_sources_invalid")
            if check_generation_basis and state["roadmap_version"] and plan.get("generation_provenance") != self._generation_basis(d, state):
                blockers.append("roadmap_generation_stale")
        return {"required": True, "policy": "cli_first_staged_generation", "ready": not blockers,
                "blockers": list(dict.fromkeys(blockers)), "clarification_required": not current_confirmation,
                "target_required": target_required and not target_valid, "source_ready": source_ready,
                "diagnostic_ready": bool(diagnostic_ready), "diagnostic_skipped": bool(skip),
                "clarification": clarification or None}

    def confirm_goal(self, sid, payload, **kw):
        """Record actual learner clarification; the host must never fabricate assent."""
        if (not isinstance(payload, dict) or set(payload) - {"kind", "statement", "accept_provisional_skip"}
                or not isinstance(payload.get("kind"), str) or payload["kind"] not in {"learning", "certification"}
                or not isinstance(payload.get("statement"), str)
                or not 20 <= len(payload["statement"].strip()) <= 2000
                or ("accept_provisional_skip" in payload and type(payload["accept_provisional_skip"]) is not bool)):
            raise CourseStateError("phase_forbids_action", "clarification needs kind and an actual bounded learner confirmation")
        def fn(d, led, state):
            cs.guard(state, "record_goal")
            if not state["goal"].get("text") or not state["learner_profile"].get("stated_background"):
                raise CourseStateError("goal_clarification_required", "record goal and background first")
            required = self._target_required(state) or payload["kind"] == "certification"
            if required and payload["kind"] != "certification":
                raise CourseStateError("target_required", "a certification goal cannot be relabeled to bypass exact target clarification")
            if required and not self._valid_exact_target(state["goal"].get("target")):
                raise CourseStateError("target_required")
            policy = state["learner_profile"].setdefault("intake_policy", {"required": False})
            if policy.get("skip") and payload.get("accept_provisional_skip") is not True:
                raise CourseStateError("provisional_skip_confirmation_required")
            clarification = {"kind": payload["kind"], "statement": payload["statement"].strip(),
                             "accept_provisional_skip": payload.get("accept_provisional_skip", False),
                             "basis": self._clarification_basis(state), "at": self._now(), "confirmed_by": "learner"}
            policy["clarification"] = clarification
            return {"clarification": clarification, "generation_readiness": self._generation_readiness(d, state)}, {"event_type": "course_goal_clarified", "kind": payload["kind"]}, None
        return self._mutate(sid, "confirm_goal", fn, request=payload, **kw)

    def _intake_view(self, state):
        prof = state["learner_profile"]
        policy = prof.get("intake_policy") or {}
        out = journey.diagnostic_next(state["goal"], prof)
        out["required"] = policy.get("required", False)
        skip = policy.get("skip")
        ready = bool(state["goal"].get("text") and prof.get("stated_background")
                     and (skip or out["action"] == "ready_to_plan"))
        out["ready_to_plan"] = ready
        out["skipped"] = bool(skip)
        if skip:
            out.update(action="ready_to_plan", skip_reason=skip["reason"],
                       instruction_az="Diagnostic sənin razılığınla keçirildi; plan ilkin qalır və bacarıq sübutu yaranmayıb.")
        probe = policy.get("active_probe")
        if probe and not ready:
            out["probe"] = {k: probe.get(k) for k in ("id", "node_id", "prompt", "kind", "criteria", "status", "response")}
            out["status"] = probe["status"]
            out["action"] = "await_judgment" if probe["status"] == "awaiting_judgment" else "answer_probe"
        return out

    def record_intake(self, sid, payload, **kw):
        if not isinstance(payload, dict) or len(payload) != 1 or len(set(payload) & {"goal", "stated_background"}) != 1:
            raise CourseStateError("phase_forbids_action", "answer exactly one intake question")
        field = "goal" if "goal" in payload else "stated_background"
        value = payload[field]
        limit = 2000 if field == "goal" else 4000
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
            raise CourseStateError("phase_forbids_action", "intake answer must be bounded nonempty text")
        def fn(d, led, state):
            cs.guard(state, "record_goal")
            if state["approved_roadmap_version"]:
                raise CourseStateError("phase_forbids_action", "revise an approved goal through a new roadmap proposal")
            policy = state["learner_profile"].get("intake_policy") or {}
            if field == "goal" and state["goal"].get("text") != value.strip() and (state["roadmap_version"] or policy.get("active_probe") or state["learner_profile"]["diagnostic"]):
                raise CourseStateError("phase_forbids_action", "a changed goal needs a new or explicitly revised learning plan; preserve completed observations")
            if field == "stated_background" and not state["goal"].get("text"):
                raise CourseStateError("phase_forbids_action", "goal comes first")
            if field == "goal":
                state["goal"]["text"] = value.strip()
            else:
                state["learner_profile"][field] = value.strip()
            contract = read_json(d / "learning-contract.json")
            contract[field] = value.strip()
            return {"intake": self._intake_view(state)}, {"event_type": "course_intake_answered", "field": field}, {"learning-contract.json": contract}
        return self._mutate(sid, "record_intake", fn, request=payload, **kw)

    def install_diagnostic_probe(self, sid, payload, **kw):
        if (not isinstance(payload, dict) or not isinstance(payload.get("id"), str) or not _KEY.fullmatch(payload["id"])
                or not isinstance(payload.get("node_id"), str) or not _NODE.fullmatch(payload["node_id"])
                or not isinstance(payload.get("prompt"), str) or not 20 <= len(payload["prompt"].strip()) <= 4000
                or not isinstance(payload.get("kind"), str) or payload["kind"] not in {"explanation", "scenario", "artifact", "retrieval"}
                or not isinstance(payload.get("criteria"), list) or not 1 <= len(payload["criteria"]) <= 8
                or not all(isinstance(c, str) and 1 <= len(c) <= 500 for c in payload["criteria"])):
            raise CourseStateError("phase_forbids_action", "diagnostic probe needs a scoped prompt and learner-visible criteria, never an answer key")
        if set(payload) - {"id", "node_id", "prompt", "kind", "criteria"}:
            raise CourseStateError("phase_forbids_action", "only learner-visible diagnostic probe fields are accepted")
        def fn(d, led, state):
            cs.guard(state, "record_profile")
            intake = self._intake_view(state)
            if intake["ready_to_plan"] or not state["goal"].get("text") or not state["learner_profile"].get("stated_background"):
                raise CourseStateError("phase_forbids_action", "complete goal/background first; stop after the bounded diagnostic")
            policy = state["learner_profile"].setdefault("intake_policy", {"required": False, "skip": None, "active_probe": None, "probe_history": []})
            if policy.get("active_probe"):
                raise CourseStateError("phase_forbids_action", "finish the current diagnostic response before preparing another")
            if any(p["id"] == payload["id"] for p in policy["probe_history"]):
                raise CourseStateError("phase_forbids_action", "diagnostic probe id already used")
            prompts = [p["prompt"] for p in policy["probe_history"]]
            if cc.canonical_text(payload["prompt"]) in {cc.canonical_text(p) for p in prompts}:
                raise CourseStateError("phase_forbids_action", "prepare a different diagnostic task, not a renamed repeat")
            policy["active_probe"] = {**payload, "status": "awaiting_response", "prepared_at": self._now(),
                                      "probe_version": cs.digest("dp", {"goal": state["goal"]["text"], **payload})}
            return {"intake": self._intake_view(state)}, {"event_type": "course_diagnostic_probe_prepared", "probe_id": payload["id"]}, None
        return self._mutate(sid, "install_diagnostic_probe", fn, request=payload, **kw)

    def answer_diagnostic(self, sid, response, assistance=None, **kw):
        if not isinstance(response, str) or not 1 <= len(response.strip()) <= 8000:
            raise CourseStateError("phase_forbids_action", "diagnostic response must be 1-8000 characters")
        assistance = [] if assistance is None else assistance
        journey.diagnostic_attribution({"basis": "observed", "assistance": assistance})
        def fn(d, led, state):
            cs.guard(state, "record_profile")
            probe = (state["learner_profile"].get("intake_policy") or {}).get("active_probe")
            if not probe or probe["status"] != "awaiting_response":
                raise CourseStateError("phase_forbids_action", "no diagnostic prompt is awaiting a response")
            probe.update(status="awaiting_judgment", response=response.strip(), assistance=assistance, answered_at=self._now())
            return {"intake": self._intake_view(state)}, {"event_type": "course_diagnostic_response_saved", "probe_id": probe["id"]}, None
        return self._mutate(sid, "answer_diagnostic", fn, request={"response": response, "assistance": assistance}, **kw)

    def judge_diagnostic(self, sid, judgment, **kw):
        if (not isinstance(judgment, dict) or set(judgment) != {"outcome", "confidence", "assessor", "rationale"}
                or not isinstance(judgment.get("outcome"), str) or judgment["outcome"] not in {"correct", "incorrect", "partial", "unknown"}
                or not isinstance(judgment.get("confidence"), str) or judgment["confidence"] not in {"low", "medium", "high"}
                or not isinstance(judgment.get("assessor"), str) or judgment["assessor"] not in {"host_agent", "human"}
                or not isinstance(judgment.get("rationale"), str) or not 20 <= len(judgment["rationale"].strip()) <= 2000):
            raise CourseStateError("phase_forbids_action", "diagnostic judgment needs attributed outcome, confidence and a concrete rationale")
        def fn(d, led, state):
            cs.guard(state, "record_profile")
            prof = state["learner_profile"]
            policy = prof.get("intake_policy") or {}
            probe = policy.get("active_probe")
            if not probe or probe["status"] != "awaiting_judgment":
                raise CourseStateError("phase_forbids_action", "no captured diagnostic response awaits judgment")
            entry = {"id": probe["id"], "node_id": probe["node_id"], "prompt": probe["prompt"], "basis": "observed",
                     "task_kind": probe["kind"], "response": probe["response"], "assistance": probe["assistance"],
                     "independence": "assisted" if probe["assistance"] else "unassisted", **judgment}
            entry = {**entry, **journey.diagnostic_attribution(entry), "at": self._now(),
                     "capability_version": self._cap_version(state, probe["node_id"]), "probe_version": probe["probe_version"],
                     "response_sha256": hashlib.sha256(probe["response"].encode()).hexdigest()}
            prof["diagnostic"].append(entry)
            if entry["outcome"] in {"incorrect", "partial"} and entry["node_id"] not in prof["gaps"]:
                prof["gaps"].append(entry["node_id"])
            policy["probe_history"].append({**probe, "judgment": judgment, "finalized_at": self._now()})
            policy["active_probe"] = None
            prof["updated_at"] = self._now()
            if self._intake_view(state)["ready_to_plan"] and state["phase"] == "onboarding":
                cs.set_phase(state, "source_preparation", self._now())
            return {"intake": self._intake_view(state)}, {"event_type": "course_diagnostic_judged", "probe_id": probe["id"]}, None
        return self._mutate(sid, "judge_diagnostic", fn, request=judgment, **kw)

    def skip_diagnostic(self, sid, reason, **kw):
        if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 1000:
            raise CourseStateError("phase_forbids_action", "diagnostic skip needs an explicit 20-1000 character learner reason")
        def fn(d, led, state):
            cs.guard(state, "record_profile")
            prof = state["learner_profile"]
            if not state["goal"].get("text") or not prof.get("stated_background"):
                raise CourseStateError("phase_forbids_action", "goal and background come before an informed diagnostic skip")
            policy = prof.setdefault("intake_policy", {"required": False, "active_probe": None, "probe_history": []})
            if policy.get("active_probe"):
                probe = policy["active_probe"]
                if probe["status"] == "awaiting_judgment":
                    raise CourseStateError("phase_forbids_action", "finish the current captured response first; it must not be silently discarded")
                policy.setdefault("probe_history", []).append({**probe, "status": "skipped_by_learner", "reason": reason.strip()})
                policy["active_probe"] = None
            policy["skip"] = {"reason": reason.strip(), "learner_agreed": True, "at": self._now()}
            if state["phase"] == "onboarding":
                cs.set_phase(state, "source_preparation", self._now())
            return {"intake": self._intake_view(state)}, {"event_type": "course_diagnostic_skipped_by_learner"}, None
        return self._mutate(sid, "skip_diagnostic", fn, request={"reason": reason}, **kw)

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
        return self._mutate(sid, "record_target", fn, request={'target': target}, **kw)

    def record_diagnostic(self, sid, payload: dict, **kw):
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise CourseStateError("phase_forbids_action", "entries must be a list")
        for e in entries:
            if (not isinstance(e, dict) or e.get("basis") not in {"self_report", "observed"}
                    or not isinstance(e.get("prompt"), str) or not 1 <= len(e["prompt"].strip()) <= 4000):
                raise CourseStateError("phase_forbids_action", "each entry needs prompt and basis self_report|observed")
            if e.get("node_id") is not None and (not isinstance(e["node_id"], str) or not _NODE.fullmatch(e["node_id"])):
                raise CourseStateError("phase_forbids_action", "diagnostic node_id must be a safe capability slug")
            if e.get("outcome", "unknown") not in {"correct", "incorrect", "partial", "unknown"}:
                raise CourseStateError("phase_forbids_action", "outcome must be correct|incorrect|partial|unknown")
            if e["basis"] == "self_report" and e.get("outcome", "unknown") != "unknown":
                raise CourseStateError("phase_forbids_action", "self-report cannot carry a correctness outcome")
            journey.diagnostic_attribution(e)
        prefs = payload.get("explanation_preferences", [])
        if not isinstance(prefs, list) or not all(isinstance(p, str) for p in prefs):
            raise CourseStateError("phase_forbids_action", "explanation_preferences must be strings")

        def fn(d, led, state):
            cs.guard(state, "record_profile")
            prof = state["learner_profile"]
            if (prof.get("intake_policy") or {}).get("required") and any(e.get("basis") == "observed" for e in entries):
                raise CourseStateError("phase_forbids_action", "strict intake requires a prepared probe, captured learner response and separate attributed judgment")
            if payload.get("stated_background"):
                prof["stated_background"] = payload["stated_background"]
            if prefs:
                prof["explanation_preferences"] = prefs[:8]
            for e in entries:
                entry = {"id": e.get("id") or new_id("dx"), "prompt": e["prompt"],
                                           "response_summary": str(e.get("response_summary", ""))[:400],
                                           "basis": e["basis"], "outcome": e.get("outcome", "unknown"),
                         "node_id": e.get("node_id"), "capability_version": self._cap_version(state, e.get("node_id")),
                         "at": self._now(), **journey.diagnostic_attribution(e)}
                if not isinstance(entry["id"], str) or not _KEY.fullmatch(entry["id"]):
                    raise CourseStateError("phase_forbids_action", "diagnostic id must be 8-80 safe characters")
                previous = next((x for x in prof["diagnostic"] if x["id"] == entry["id"]), None)
                if previous:
                    if any(previous.get(k) != v for k, v in entry.items() if k != "at"):
                        raise CourseStateError("phase_forbids_action", "diagnostic id reused for a different observation")
                    continue
                prof["diagnostic"].append(entry)
            prof["gaps"] = sorted({x["node_id"] for x in prof["diagnostic"]
                                   if x["basis"] == "observed" and x["outcome"] in {"incorrect", "partial"} and x.get("node_id")})
            prof["updated_at"] = self._now()
            if state["phase"] == "onboarding" and state["goal"]["text"] and (not self._intake_view(state)["required"] or self._intake_view(state)["ready_to_plan"]):
                cs.set_phase(state, "source_preparation", self._now())
            return {"learner_profile": prof}, {"event_type": "course_diagnostic_recorded", "entries": len(entries)}, None
        return self._mutate(sid, "record_diagnostic", fn, request={'payload': payload}, **kw)

    def diagnostic_next(self, sid):
        """One bounded, goal-specific host-agent request; never a level/ability score."""
        state = self.state(sid)
        return state["intake"]

    def record_recovery(self, sid, node_id, plan, **kw):
        """Agree a concrete changed plan after repeated failures; no time-based blame."""
        plan = recovery_policy.validate_plan(plan)
        def fn(d, led, state):
            cs.guard(state, "record_recovery")
            node = self._node(self._plan(d), node_id)
            progress = self._progress(state, node_id)
            recovery = progress.get("recovery") or {}
            if recovery.get("status") != "open" or recovery.get("capability_version") != self._cap_version(state, node_id):
                raise CourseStateError("phase_forbids_action", "no current recovery decision is pending")
            prerequisite = plan.get("prerequisite_node_id")
            if prerequisite is not None and prerequisite not in node.get("requires", []):
                raise CourseStateError("phase_forbids_action", "recovery prerequisite must be an actual roadmap prerequisite")
            if recovery_policy.repeated_plan(progress.get("recovery_history", []), plan["plan"], recovery["capability_version"]):
                raise CourseStateError("recovery_required", "the previous recovery plan did not resolve the difficulty; choose a changed step")
            recovery.update(status="agreed", blocker=plan["blocker"], plan=plan["plan"].strip(),
                            prerequisite_node_id=prerequisite, agreed_at=self._now(),
                            cycle_restarted_after_attempt=recovery["triggering_attempt_id"], outcome="pending_observation")
            recovery["guidance"] = recovery_policy.guidance(plan["blocker"], sum(h.get("capability_version") == recovery["capability_version"] for h in progress.get("recovery_history", [])))
            progress.setdefault("recovery_history", []).append(dict(recovery))
            state["next_mission"] = {"type": "targeted_recovery", "node_id": node_id, "title": node["title"],
                                     "reason_az": recovery["plan"], "lesson_ready": self._lesson_gate(d, state, node_id)["status"] == "ready",
                                     "fresh_items": self._fresh_count(d, state, node_id),
                                     "uncertainty_az": "Bu plan ehtimal olunan səbəbi yoxlayır; bacarıq və ya diaqnoz təsdiqi deyil."}
            return {"recovery": recovery, "next_mission": state["next_mission"]}, {"event_type": "course_recovery_agreed", "node_id": node_id}, None
        return self._mutate(sid, "record_recovery", fn, request={"node": node_id, "plan": plan}, **kw)

    # ------------------------------------------------------------------ sources
    def record_sources(self, sid, manifest: dict, expected: list[dict], *, staged_cache: Path | None = None, **kw):
        """Commit a manifest. ``staged_cache`` (from validation) is promoted into the
        grounding cache only after the revision check and phase guard pass, so a refused
        commit can never change the text that lesson quotes are verified against."""
        if not isinstance(manifest, dict) or not isinstance(manifest.get("resources"), list):
            raise CourseStateError("phase_forbids_action", "manifest must come from course_sources.validate_resources")

        def fn(d, led, state):
            if state["phase"] == "onboarding":
                cs.guard(state, "begin_sources")
                cs.set_phase(state, "source_preparation", self._now())
            cs.guard(state, "record_sources")
            objects = {}
            if staged_cache is not None:
                staged = Path(staged_cache)
                if not staged.resolve().is_relative_to((d / "sources-staging").resolve()):
                    raise CourseStateError("phase_forbids_action", "staged cache must live in this session's staging area")
                # Immutable content-addressed objects; the journaled manifest is the only
                # pointer, so a crash before the commit changes nothing that is in use.
                objects = csrc.store_objects(staged, d / "sources")
            stored = {**manifest, "expected": expected}
            state["source_manifest_version"] = manifest.get("manifest_version")
            # A curated expected title beats NotebookLM's (PDFs are titled by their URL).
            state["source_refs"] = [{"key": r["key"], "source_id": r["source_id"],
                                     "title": (r.get("expected") or {}).get("title") or r.get("title"),
                                     "url": (r.get("expected") or {}).get("locator"), "status": r["status"],
                                     "content_sha256": r["content_sha256"], "stale": r.get("stale"),
                                     "issues": r.get("issues", [])} for r in manifest["resources"]]
            # Grounding is re-verified on every use; report which lessons this manifest blocks.
            impact = {}
            for node in sorted(state["content_versions"]):
                lesson = self._lesson(d, node, state)
                if lesson:
                    g = self._grounding(d, state, node, lesson, manifest=stored)
                    if g["status"] != "valid":
                        impact[node] = {"status": "grounding_invalid", "rebindable": g["rebindable"],
                                        "problems": g["problems"]}
            return ({"manifest_version": manifest.get("manifest_version"), "complete": manifest.get("complete"),
                     "summary": manifest.get("summary"), "grounding_impact": impact, "objects_stored": len(objects)},
                    {"event_type": "course_sources_recorded", "manifest_version": manifest.get("manifest_version"),
                     "grounding_blocked_nodes": sorted(impact)},
                    {MANIFEST_FILE: stored})
        return self._mutate(sid, "record_sources", fn, request={'manifest_version': manifest.get('manifest_version'), 'expected': expected, 'staged': str(staged_cache) if staged_cache else None}, **kw)

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
            strict = (state["learner_profile"].get("intake_policy") or {}).get("required", False)
            effective_plan = {**plan, "generation_provenance": self._generation_basis(d, state)} if strict else plan
            norm = normalize_plan(session["topic"], (state["goal"] or {}).get("text"), effective_plan)
            version = cs.roadmap_version(norm)
            if version == state["roadmap_version"] and old is not None:
                return {"roadmap_version": version, "changed": False}, None, None
            prepared = self.sk.set_roadmap(sid, effective_plan, _course_internal=True, roadmap_only=strict,
                                           prepare_only=strict)
            destination = d / prepared["workspace_install"] if strict else d / "workspace"
            installed = read_json(destination / "roadmap.json")
            for n in installed["nodes"]:             # lessons survive a roadmap revision
                lesson = self._lesson(d, n["id"], state)
                if lesson and not strict:
                    self._materialize_lesson_files(d, n, lesson)
            changes = cs.diff_roadmaps(old, installed)
            # Preview of what approving this version supersedes (shown before approval).
            impact = cs.capability_impact(state["capabilities"], installed)
            previous = state["approved_roadmap_version"]
            state["roadmap_version"] = cs.roadmap_version(installed)
            if previous and previous == state["roadmap_version"] and state["phase"] == "roadmap_awaiting_approval":
                # The pending change was reverted to exactly what the learner approved.
                state["review_required"] = None
                back = (state.get("resume_checkpoint") or {}).get("phase")
                cs.set_phase(state, back if back in {"course_generation", "teaching", "practice", "feedback",
                                                     "next_mission"} else "course_generation", self._now())
            if previous != state["roadmap_version"]:
                state["review_required"] = {"reason": "material_change" if previous else "initial_approval",
                                            "previous_version": previous, "new_version": state["roadmap_version"],
                                            "changes": changes, "impact": impact, "at": self._now()}
                cs.set_phase(state, "roadmap_awaiting_approval", self._now())
            self._write_roadmap_html(d, sid, installed, state, destination=destination)
            if strict:
                from .local_state import sync_tree
                sync_tree(destination)
            output = ({"roadmap_version": state["roadmap_version"], "changed": True, "changes": changes,
                     "capability_impact": impact,
                     "approval_invalidated": bool(previous and previous != state["roadmap_version"])},
                    {"event_type": "course_roadmap_proposed", "roadmap_version": state["roadmap_version"],
                     "previous_approved_version": previous}, prepared["files"] if strict else None)
            return (*output, prepared["workspace_install"]) if strict else output
        return self._mutate(sid, "propose_roadmap", fn, request={'plan': plan}, **kw)

    def _write_roadmap_html(self, d, sid, plan, state, destination=None):
        titles = {r["key"]: f"{r.get('title') or r.get('url')} — {r['status']}" for r in state["source_refs"]}
        cmd = (f"alearn --workspace {self.sk.workspace} course approve-roadmap --session {sid} "
               f"--roadmap-version {state['roadmap_version']}")
        html = render_roadmap_html(plan, roadmap_version=state["roadmap_version"],
                                   approved=state["approved_roadmap_version"] == state["roadmap_version"],
                                   approve_command=cmd, source_titles=titles)
        ((destination or d / "workspace") / "roadmap.html").write_text(html, encoding="utf-8")

    def approve_roadmap(self, sid, roadmap_version: str, *, channel: str, statement: str | None = None,
                        commitments: dict | None = None, **kw):
        if channel not in {"ui", "cli"}:
            raise CourseStateError("phase_forbids_action", "approval channel must be ui or cli")

        def fn(d, led, state):
            cs.guard(state, "approve_roadmap")
            if roadmap_version != state["roadmap_version"]:
                raise CourseStateError("roadmap_version_mismatch",
                                       f"approving {roadmap_version}, current {state['roadmap_version']}")
            event = {"event_id": new_id("apv"), "roadmap_version": roadmap_version, "approved_at": self._now(),
                     "approved_by": "learner", "channel": channel,
                     "statement": (statement or "Yol xəritəsini nəzərdən keçirdim və təsdiq edirəm.")[:300]}
            plan = self._plan(d)
            impact = cs.capability_impact(state["capabilities"], plan)
            superseded = self._apply_capability_impact(state, plan, impact, event)
            event["capability_impact"] = impact["summary"]
            state["approved_roadmap_version"] = roadmap_version
            state["roadmap_approval_event"] = event
            state["approval_history"].append(event)
            state["review_required"] = None
            prepared = None
            if (state["learner_profile"].get("intake_policy") or {}).get("required"):
                # Materialize the personalized teaching workspace only after the exact
                # version check and actual learner approval, preserving prior work.
                prepared = self.sk.set_roadmap(sid, self._plan(d), _course_internal=True,
                                                roadmap_only=False, prepare_only=True)
            if commitments:
                self._apply_commitments(state, commitments)
            cs.set_phase(state, "course_generation", self._now())
            destination = d / prepared["workspace_install"] if prepared else None
            self._write_roadmap_html(d, sid, plan, state, destination=destination)
            if prepared:
                from .local_state import sync_tree
                sync_tree(destination)
            state["next_mission"] = self._initial_mission(d, state, plan)
            output = ({"approval_event": event, "next_mission": state["next_mission"], "capability_impact": impact,
                    "superseded": superseded}, {
                "event_type": "course_roadmap_approved", "approval_event": event, "superseded": superseded}, prepared["files"] if prepared else None)
            return (*output, prepared["workspace_install"]) if prepared else output
        return self._mutate(sid, "approve_roadmap", fn, request={'roadmap_version': roadmap_version, 'channel': channel, 'commitments': commitments}, **kw)

    def _apply_capability_impact(self, state, plan, impact, event) -> dict:
        """Supersede current progress/readiness of changed capabilities; keep all history.

        A changed capability's pass becomes a ``superseded_passes`` entry (evidence stays
        in the ledger), its pending reviews are invalidated, and its old lesson/items stop
        being ready/eligible because they are bound to the previous version. Dependents
        keep their own passes (their hidden checks verified their own capability) but
        are flagged; unpassed dependents lock again until the prerequisite is re-shown.
        """
        now = self._now()
        out = {"passes": [], "reviews": [], "dependents_flagged": []}
        for nid, info in impact["nodes"].items():
            if info["change"] in {"capability_changed", "removed"}:
                prev = state["capabilities"].get(nid)
                if prev:
                    state["capability_history"].append({
                        "node_id": nid, "version": prev.get("version"), "since": prev.get("since"), "until": now,
                        "superseded_by": info["version"], "reason": info["change"], "approval_event_id": event["event_id"]})
                prog = state["node_progress"].get(nid)
                if prog and prog.get("passed"):
                    prog.setdefault("superseded_passes", []).append({
                        "capability_version": prog.get("capability_version"), "passed_at": prog.get("passed_at"),
                        "superseded_at": now, "reason": info["change"], "approval_event_id": event["event_id"]})
                    prog["passed"] = False
                    out["passes"].append(nid)
                for q in state["review_queue"]:
                    if q["node_id"] == nid and q["status"] in cs.PENDING_REVIEW:
                        q.update(status="invalidated", closed_at=now, reason=info["change"])
                        out["reviews"].append(q["review_id"])
                if state["active_node_id"] == nid:
                    state["active_node_id"] = None
                    state["support_provenance"] = cs.empty_support()
            if info.get("upstream_changed") and state["node_progress"].get(nid):
                state["node_progress"][nid]["upstream_changed"] = {"nodes": info["upstream_changed"], "at": now}
                out["dependents_flagged"].append(nid)
        caps = {}
        for nid, cur in cs.capability_snapshot(plan).items():
            kept = impact["nodes"][nid]["change"] in {"unchanged", "prerequisites_changed"}
            caps[nid] = {"version": cur["version"], "requires": cur["requires"], "basis": "approved",
                         "since": (state["capabilities"].get(nid) or {}).get("since") if kept else now,
                         "approval_event_id": event["event_id"]}
        state["capabilities"] = caps
        return out

    # ------------------------------------------------------------- authoring
    def install_items(self, sid, node_id: str, payload: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "install_content")
            node = self._node(self._plan(d), node_id)
            try:
                items = cc.validate_items(payload, node, self._lesson(d, node_id, state))
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", f"content_invalid: {e}")
            return self._apply_items(d, state, node_id, items)
        return self._mutate(sid, "install_items", fn, request={'node': node_id, 'payload': payload}, **kw)

    def _apply_items(self, d, state, node_id: str, items: list[dict]):
        """Write validated items into the vault and bind them (shared by install_items and promote_build)."""
        exposed = set(state["exposure"].get(node_id, [])) | {e.get("item_id") for e in self._exposures(state, node_id)}
        current = state["item_versions"].setdefault(node_id, {})
        for it in items:
            if it["item_id"] in exposed and current.get(it["item_id"]) not in (None, it["item_version"]):
                raise CourseStateError("phase_forbids_action",
                                       f"item {it['item_id']} was already shown; publish changes under a new item_id")
        now = self._now()
        cap = self._cap_version(state, node_id)
        bindings = state["item_bindings"].setdefault(node_id, {})
        freshness = {}
        for it in items:
            vault = self._vault(d, node_id)
            cc.write_private(vault / f"{it['item_id']}.json", it)
            archived = vault / "archive" / f"{it['item_version']}.json"
            if not archived.exists():           # every published version stays retrievable
                cc.write_private(archived, it)
            current[it["item_id"]] = it["item_version"]
            fp = cc.fingerprint(it)
            bindings[it["item_id"]] = {"item_version": it["item_version"], "capability_version": cap,
                                       "content_fingerprint": fp["content"], "behavior_fingerprint": fp["behavior"],
                                       "kind": fp["kind"], "bound_at": now, "basis": "installed"}
            freshness[it["item_id"]] = cc.classify_freshness(fp, self._exposures(state, node_id))
        state["course_version"] = self._course_version(state)
        return ({"node_id": node_id, "items": {i["item_id"]: i["item_version"] for i in items},
                 "freshness": freshness},
                {"event_type": "course_items_installed", "node_id": node_id,
                 "item_versions": {i["item_id"]: i["item_version"] for i in items},
                 "freshness": {k: v["status"] for k, v in freshness.items()}}, None)

    @staticmethod
    def _retire_items(state, node_id: str, item_ids, now: str) -> list[str]:
        """Take items that are live for ``node_id`` but not in the promoted build out of the exam pool. The vault files and
        the bindings stay (history; nothing is deleted): the binding is marked retired, ``item_versions`` no longer lists
        the item, so ``_items``/``_item_status`` never offer it again."""
        gone = []
        for item_id in sorted(item_ids):
            version = (state["item_versions"].get(node_id) or {}).pop(item_id, None)
            if version is None:
                continue
            binding = state["item_bindings"].setdefault(node_id, {}).setdefault(item_id, {"item_version": version})
            binding["retired"] = {"at": now, "reason": "not_in_promoted_build", "item_version": version}
            gone.append(item_id)
        return gone

    def install_lesson(self, sid, node_id: str, payload: dict, **kw):
        def fn(d, led, state):
            cs.guard(state, "install_content")
            node = self._node(self._plan(d), node_id)
            try:
                lesson = cc.validate_lesson(payload, node, self._ground(d), self._items(d, node_id, state))
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", f"content_invalid: {e}")
            return self._apply_lesson(d, state, node_id, lesson)
        return self._mutate(sid, "install_lesson", fn, request={'node': node_id, 'payload': payload}, **kw)

    def _apply_lesson(self, d, state, node_id: str, lesson: dict):
        """Write a validated lesson and bind it to the capability and source hashes (shared with promote_build)."""
        now = self._now()
        lesson["installed_at"] = now
        cc.write_private(self._lesson_path(d, node_id), lesson)
        archived = self._lesson_path(d, node_id).parent / "archive" / f"{lesson['content_version']}.json"
        if not archived.exists():               # superseded lessons stay readable as history
            cc.write_private(archived, lesson)
        self._materialize_lesson_files(d, self._node(self._plan(d), node_id), lesson)
        state["content_versions"][node_id] = lesson["content_version"]
        manifest = self._manifest(d) or {}
        shas = {r["source_id"]: r.get("content_sha256") for r in manifest.get("resources", []) if r.get("source_id")}
        previous = state["lesson_bindings"].get(node_id)
        state["lesson_bindings"][node_id] = {
            "content_version": lesson["content_version"], "capability_version": self._cap_version(state, node_id),
            "sources": {sid: shas.get(sid) for sid in sorted(cc.cited_sources(lesson))}, "bound_at": now,
            "basis": "installed",
            "history": ((previous or {}).get("history") or []) + ([{k: previous.get(k) for k in
                        ("content_version", "capability_version", "sources", "bound_at", "basis")}] if previous else [])}
        state["course_version"] = self._course_version(state)
        return ({"node_id": node_id, "content_version": lesson["content_version"],
                 "lesson": cc.public_lesson_summary(lesson, self._lesson_gate(d, state, node_id, lesson))},
                {"event_type": "course_lesson_installed", "node_id": node_id,
                 "content_version": lesson["content_version"]}, None)

    # --------------------------------------------------- studio promotion
    def promote_build(self, sid, bundle: dict, *, rehearsal: bool = False, **kw):
        """Install a validated Course Studio build through the normal admission path, all or nothing.

        ``bundle`` carries the job identity, the exact build digest and its inputs, the targets and one payload per
        unit. Every condition is re-checked here, under the session lock, from the ledger and the live course:
        the approved roadmap version equals the build's, the owner acceptance and the signed full rules receipt are
        in the ledger and bound to this digest and fresh, every unit validates against the live course (grounding,
        freshness, separation, capability version), and the capability versions and source hashes the build was
        authored against still hold. Nothing is written unless every node passes; the build adds no evidence.
        ``rehearsal`` (rules-check scratch copy only) waives the owner acceptance and receipt; it never applies to a
        real session because the CLI and StudioKernel never pass it."""
        from . import studio_plan as plan_mod
        from . import studio_receipt as receipts

        def fn(d, led, state):
            cs.guard(state, "install_content")
            digest = bundle.get("build_digest")
            if state["approved_roadmap_version"] != bundle.get("roadmap_version"):
                raise CourseStateError("roadmap_version_mismatch",
                                       f"approved {state['approved_roadmap_version']}, build {bundle.get('roadmap_version')}")
            plan = self._plan(d)
            if cs.roadmap_version(plan) != bundle.get("roadmap_version"):
                raise CourseStateError("roadmap_version_mismatch", "the installed roadmap differs from the build's")
            units = bundle.get("units") or []
            wanted = {f"{kind}:{n['id']}" for n in plan["nodes"] for kind in ("lesson", "items")}
            if {u.get("unit_id") for u in units} != wanted or len(units) != len(wanted):
                raise CourseStateError("promotion_build_incomplete", "the build does not cover every roadmap node exactly once")
            from .studio_jobs import build_digest_of
            unit_digests = {u["unit_id"]: receipts.payload_digest(u["payload"]) for u in units}
            if build_digest_of(unit_digests, bundle.get("roadmap_version"), bundle.get("sources") or {},
                               bundle.get("policy_versions") or {}) != digest:
                raise CourseStateError("promotion_digest_mismatch", "unit payloads do not hash to the build digest")
            manifest = self._manifest(d) or {}
            live_sources = {r.get("key"): r.get("content_sha256") for r in manifest.get("resources", [])}
            for key, sha in (bundle.get("sources") or {}).items():
                if live_sources.get(key) != sha:
                    raise CourseStateError("promotion_source_changed", f"source {key} changed since the build was authored")
            for nid, ver in (bundle.get("capabilities") or {}).items():
                if self._cap_version(state, nid) != ver:
                    raise CourseStateError("promotion_capability_changed", f"capability of {nid} changed since the build")
            acceptance_ref = None
            if not rehearsal:
                acceptance_ref = receipts.require_promotion_conditions(led, bundle)
            done = [b for b in state["studio"]["promoted_builds"] if b.get("build_digest") == digest]
            if done:            # idempotent: the same verified build is already installed
                return {"promoted": False, "already_promoted": True, "build_digest": digest,
                        "nodes": done[-1].get("nodes", [])}, None, None
            by_unit = {u["unit_id"]: u for u in units}
            order = plan_mod.prerequisite_order(plan["nodes"])
            validated = {}
            ground = self._ground(d)
            for node in order:
                nid = node["id"]
                lesson_payload = by_unit[f"lesson:{nid}"]["payload"]
                items_payload = by_unit[f"items:{nid}"]["payload"]
                try:
                    new_items = cc.validate_items(copy.deepcopy(items_payload), node, None)
                    ids = {i["item_id"] for i in new_items}
                    separation = [i for i in self._items(d, nid, state) if i["item_id"] not in ids] + new_items
                    lesson = cc.validate_lesson(copy.deepcopy(lesson_payload), node, ground, separation)
                    items = cc.validate_items(copy.deepcopy(items_payload), node, lesson)
                except _DRY_ERRORS as e:
                    raise CourseStateError("promotion_revalidation_failed", f"{nid}: {_dry_message(e)}")
                exposures = self._exposures(state, nid)
                exposed = set(state["exposure"].get(nid, [])) | {e.get("item_id") for e in exposures}
                current = state["item_versions"].get(nid, {})
                freshness, problems = {}, []
                for it in items:
                    if it["item_id"] in exposed and current.get(it["item_id"]) not in (None, it["item_version"]):
                        problems.append(f"item {it['item_id']} was already shown; publish changes under a new item_id")
                    freshness[it["item_id"]] = cc.classify_freshness(cc.fingerprint(it), exposures)
                verdict = plan_mod.evaluate_unit("items", items_payload, {"valid": not problems, "errors": problems,
                                                                          "freshness": freshness}, bundle["targets"])
                lesson_verdict = plan_mod.evaluate_unit("lesson", lesson_payload, {"valid": True}, bundle["targets"])
                if not verdict["complete"] or not lesson_verdict["complete"]:
                    raise CourseStateError("promotion_revalidation_failed",
                                           f"{nid}: " + "; ".join(verdict["problems"] + lesson_verdict["problems"])[:400])
                validated[nid] = (lesson, items)
            installed, retired, now = {}, {}, self._now()
            journal.capture(self._promotion_paths(d, order, validated))
            for node in order:               # every node validated above: now install, lesson then items
                nid = node["id"]
                lesson, items = validated[nid]
                live_before = set(state["item_versions"].get(nid) or {})
                self._apply_lesson(d, state, nid, lesson)
                self._apply_items(d, state, nid, items)
                installed[nid] = {"content_version": lesson["content_version"],
                                  "items": {i["item_id"]: i["item_version"] for i in items}}
                gone = self._retire_items(state, nid, live_before - {i["item_id"] for i in items}, now)
                if gone:
                    retired[nid] = gone
            record = {"promotion_id": new_id("prm"), "kind": "promotion", "job_id": bundle.get("job_id"),
                      "build_digest": digest, "roadmap_version": bundle.get("roadmap_version"), "promoted_at": now,
                      "nodes": [n["id"] for n in order], "installed": installed, "rehearsal": bool(rehearsal),
                      "units": unit_digests, "acceptance": acceptance_ref, "retired_items": retired,
                      "receipt_id": (bundle.get("receipt") or {}).get("receipt_id"), "evidence_records_added": 0}
            state["promotion_history"].append(record)
            state["studio"]["promoted_builds"].append({k: record[k] for k in ("job_id", "build_digest", "roadmap_version",
                                                                                "promoted_at", "nodes", "rehearsal")})
            state["studio"]["active_job"] = bundle.get("job_id")
            return ({"promoted": True, "build_digest": digest, "nodes": record["nodes"], "installed": installed,
                     "promotion_id": record["promotion_id"], "evidence_records_added": 0},
                    {"event_type": "course_build_promoted", "job_id": bundle.get("job_id"), "build_digest": digest,
                     "nodes": record["nodes"], "rehearsal": bool(rehearsal)}, None)
        journal = _FileJournal()
        committed = False
        try:
            result = self._mutate(sid, "promote_build", fn, request={"job": bundle.get("job_id"),
                                                                     "digest": bundle.get("build_digest"),
                                                                     "rehearsal": bool(rehearsal)}, **kw)
            committed = True
            return result
        finally:
            if not committed and journal.captured:
                self._rollback_promotion(sid, bundle.get("build_digest"), journal)

    def _promotion_paths(self, d, order, validated) -> list[Path]:
        """Every file the install loop of ``promote_build`` may write (lessons, archives, vault items, practice files)."""
        paths = []
        for node in order:
            nid = node["id"]
            lesson, items = validated[nid]
            mod, vault = self._module(d, nid), self._vault(d, nid)
            lp = self._lesson_path(d, nid)
            paths += [lp, lp.parent / "archive" / f"{lesson['content_version']}.json",
                      mod / "practice" / "README.md", mod / "checks" / "practice_checks.py"]
            if cc.item_kind(lesson["practice"]) == "python":
                paths.append(mod / "submission" / lesson["practice"]["entry"])
            for it in items:
                paths += [vault / f"{it['item_id']}.json", vault / "archive" / f"{it['item_version']}.json"]
        return paths

    def _rollback_promotion(self, sid, digest, journal) -> None:
        """A promotion that did not commit leaves the course as it was: files it overwrote get their old bytes back and
        files it created are moved into course/rollback/ (never deleted). A promotion whose state DID commit (a crash
        after the journal write; recovery completes it) is left alone."""
        d = self.sk.dir(sid)
        with session_lock(d):
            with contextlib.suppress(OSError, ValueError, CourseStateError):
                self.sk._recover_state(sid)
                state = read_json(d / STATE_FILE)
                if any(b.get("build_digest") == digest for b in (state.get("studio") or {}).get("promoted_builds", [])):
                    return
            journal.restore(d / "course" / "rollback" / new_id("rb"), d)

    def record_promotion_check(self, sid, build_digest: str, summary: dict, **kw):
        """Append the post-promotion rules-check outcome to ``promotion_history`` (a note, never evidence)."""
        def fn(d, led, state):
            entry = {"kind": "post_check", "build_digest": build_digest, "at": self._now(),
                     **{k: summary.get(k) for k in ("receipt_id", "status", "scope", "failing_rules")}}
            state["promotion_history"].append(entry)
            return {"recorded": True, "build_digest": build_digest}, {"event_type": "course_promotion_checked",
                                                                      "build_digest": build_digest,
                                                                      "status": summary.get("status")}, None
        return self._mutate(sid, "record_promotion_check", fn, request={"digest": build_digest, "summary": summary}, **kw)

    # ------------------------------------------------- dry runs (read-only)
    def _open_readonly(self, sid: str):
        """Read-only open: verify the ledger and load the committed state. Unlike ``_open`` it never
        recovers, migrates (in memory only) or reconciles, so it can never write a byte."""
        d = self.sk.dir(sid)
        led = self.sk.ledger(sid)
        led.require_integrity()
        path = d / STATE_FILE
        if not path.exists():
            raise CourseStateError("course_not_initialized", "run `alearn course init --session <id>` first")
        state, _ = cs.migrate(read_json(path))
        errors = cs.validate(state)
        if errors:
            raise CourseStateError("invalid_state", ",".join(errors))
        return d, led, state

    def _dry_context(self, sid: str, node_id: str):
        d, led, state = self._open_readonly(sid)
        if state["phase"] in cs.TEACHING_LOCKED_PHASES:
            raise CourseStateError("exam_active")
        if not state["roadmap_version"]:
            raise CourseStateError("roadmap_not_approved", "no roadmap proposed yet")
        plan = self._plan(d)
        if plan.get("status") == "needs_curriculum_research":
            raise CourseStateError("roadmap_not_approved", "no roadmap proposed yet")
        return d, state, self._node(plan, node_id)

    def validate_lesson_dry(self, sid, node_id: str, payload, *, staged_items=None) -> dict:
        """Run the install_lesson validators without installing anything (no state, file or ledger write).

        Separation is checked against the committed items plus ``staged_items`` (an items payload)."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, state, node = self._dry_context(sid, node_id)
            errors, items = [], self._items(d, node_id, state)
            if staged_items is not None:
                try:
                    staged = cc.validate_items(copy.deepcopy(staged_items), node, None)
                    ids = {i["item_id"] for i in staged}
                    items = [i for i in items if i["item_id"] not in ids] + staged
                except _DRY_ERRORS as e:
                    errors.append(f"staged_items: {e}")
            lesson = None
            try:
                lesson = cc.validate_lesson(copy.deepcopy(payload), node, self._ground(d), items)
            except _DRY_ERRORS as e:
                errors.append(_dry_message(e))
            out = {"valid": lesson is not None and not errors, "errors": errors, "node_id": node_id, "kind": "lesson",
                   "summary": {}, "content_version": None}
            if out["valid"]:
                shown = cc.public_lesson_summary(lesson)
                out["summary"] = {"objective": shown["objective"], "success_criteria": shown["success_criteria"],
                                  "practice_kind": shown["practice_kind"], "grounding": shown["grounding"],
                                  "practice_task_id": lesson["practice"]["task_id"],
                                  "walkthrough": (lesson.get("walkthrough") or {}).get("language"),
                                  "ladder": bool(lesson.get("ladder")), "hint_ceiling": lesson.get("hint_ceiling"),
                                  "cited_sources": sorted(s for s in cc.cited_sources(lesson) if s)}
                out["content_version"] = lesson["content_version"]
            return out

    def validate_items_dry(self, sid, node_id: str, payload, *, staged_lesson=None) -> dict:
        """Run the install_items validators + freshness classification without installing anything.

        Separation is checked against the committed lesson, or ``staged_lesson`` (a lesson payload)."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, state, node = self._dry_context(sid, node_id)
            errors, lesson = [], self._lesson(d, node_id, state)
            if staged_lesson is not None:
                try:
                    lesson = cc.validate_lesson(copy.deepcopy(staged_lesson), node, self._ground(d),
                                                self._items(d, node_id, state))
                except _DRY_ERRORS as e:
                    errors.append(f"staged_lesson: {e}")
            items = None
            try:
                items = cc.validate_items(copy.deepcopy(payload), node, lesson)
            except _DRY_ERRORS as e:
                errors.append(_dry_message(e))
            out = {"valid": False, "errors": errors, "node_id": node_id, "kind": "items", "summary": {},
                   "freshness": {}, "item_versions": {}}
            if items is None:
                return out
            exposures = self._exposures(state, node_id)
            exposed = set(state["exposure"].get(node_id, [])) | {e.get("item_id") for e in exposures}
            current = state["item_versions"].get(node_id, {})
            for it in items:
                if it["item_id"] in exposed and current.get(it["item_id"]) not in (None, it["item_version"]):
                    errors.append(f"item {it['item_id']} was already shown; publish changes under a new item_id")
                out["freshness"][it["item_id"]] = cc.classify_freshness(cc.fingerprint(it), exposures)
                out["item_versions"][it["item_id"]] = it["item_version"]
            tally = {}
            for f in out["freshness"].values():
                tally[f["status"]] = tally.get(f["status"], 0) + 1
            scopes = {}
            for it in items:
                scopes[it["scope"]] = scopes.get(it["scope"], 0) + 1
            out["summary"] = {"count": len(items), "scopes": scopes, "freshness": tally,
                              "items": [{"item_id": i["item_id"], "kind": cc.item_kind(i), "scope": i["scope"],
                                         "item_version": i["item_version"]} for i in items]}
            out["valid"] = not errors
            return out

    def studio_snapshot(self, sid) -> dict:
        """Read-only facts the Course Studio binds a job to (no recovery, migration or reconcile writes)."""
        d = self.sk.dir(sid)
        with session_lock(d):
            if not (d / STATE_FILE).exists():
                return {"initialized": False, "session_id": sid, "phase": None, "exam_locked": False,
                        "roadmap_proposed": False, "roadmap_version": None, "approved_roadmap_version": None,
                        "plan": None, "capabilities": {}, "generation_provenance": None, "manifest_version": None,
                        "resources": [], "ready": False, "blockers": ["course_not_initialized"],
                        "directive": "Run `alearn course init --session <id>`; the learner's goal and diagnostic come first."}
            d, led, state = self._open_readonly(sid)
            plan = None
            if state["roadmap_version"] and (d / "workspace" / "roadmap.json").exists():
                plan = self._plan(d)
                if plan.get("status") == "needs_curriculum_research":
                    plan = None
            manifest = self._manifest(d) or {}
            readiness = self._generation_readiness(d, state, plan)
            view = {"phase": state["phase"], "intake": self._intake_view(state), "generation_readiness": readiness,
                    "next_mission": state["next_mission"]}
            blockers = [] if plan else ["roadmap_not_proposed"]
            if readiness["required"] and not readiness["ready"]:
                blockers += readiness["blockers"]
            resources = [{"key": r.get("key"), "source_id": r.get("source_id"), "title": r.get("title"),
                          "kind": (r.get("expected") or {}).get("kind"), "status": r.get("status"),
                          "stale": r.get("stale"), "content_sha256": r.get("content_sha256")}
                         for r in manifest.get("resources") or []]
            return {"initialized": True, "session_id": sid, "course_revision": state["revision"],
                    "phase": state["phase"], "exam_locked": state["phase"] in cs.TEACHING_LOCKED_PHASES,
                    "roadmap_proposed": plan is not None, "roadmap_version": state["roadmap_version"],
                    "approved_roadmap_version": state["approved_roadmap_version"], "plan": plan,
                    "capabilities": cs.capability_snapshot(plan) if plan else {},
                    "generation_provenance": (plan or {}).get("generation_provenance"),
                    "manifest_version": state["source_manifest_version"], "resources": resources,
                    "ready": not blockers, "blockers": blockers, "directive": agent_directive(view),
                    "intake_required": bool(view["intake"]["required"])}

    def source_text(self, sid, source_id: str, content_sha256: str) -> str | None:
        """Cached validated source text (hash-verified), or None when it no longer verifies."""
        d = self.sk.dir(sid)
        with session_lock(d):
            text, problem = csrc.cached_text(d / "sources", source_id, content_sha256)
            return None if problem else text

    def _materialize_lesson_files(self, d, node, lesson):
        """Learner-facing practice files only: brief, starter (never overwritten), visible checks."""
        mod = self._module(d, node["id"])
        (mod / "practice").mkdir(parents=True, exist_ok=True)
        if cc.item_kind(lesson["practice"]) != "python":
            (mod / "practice" / "README.md").write_text(
                f"# Praktika — {node['title']}\n\n{lesson['practice']['prompt_md']}\n\n"
                "Cavabları tətbiqdə seç və **Yoxla** düyməsini bas (və ya agentə `course practice-answer` ilə ötür).\n",
                encoding="utf-8")
            return
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
            self._ready_lesson(d, state, node_id)
            if state["active_node_id"] != node_id:
                state["support_provenance"] = cs.empty_support()
            state["active_node_id"] = node_id
            self._progress(state, node_id)
            cs.set_phase(state, "teaching", self._now())
            state["resume_checkpoint"] = {"phase": "teaching", "route": f"#/learn/{node_id}", "node_id": node_id,
                                          "attempt_id": None, "at": self._now()}
            return {"node_id": node_id, "phase": "teaching"}, {"event_type": "course_mission_started", "node_id": node_id}, None
        return self._mutate(sid, "start_mission", fn, request={'node': node_id}, **kw)

    def help(self, sid, node_id: str, kind: str, rung: str | None = None, **kw):
        if kind not in HELP_KINDS:
            raise CourseStateError("phase_forbids_action", f"help kind must be one of {HELP_KINDS}")
        if kind == "ladder":
            rung = rung or "worked"
            if rung not in cc.LADDER_RUNGS:
                raise CourseStateError("phase_forbids_action", f"ladder rung must be one of {cc.LADDER_RUNGS}")
        elif rung is not None:
            raise CourseStateError("phase_forbids_action", "a rung applies to the ladder only")
        request = {'node': node_id, 'kind': kind}
        if kind == "ladder":
            request['rung'] = rung

        def fn(d, led, state):
            cs.guard(state, "help")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "help is for the active mission node")
            lesson = self._ready_lesson(d, state, node_id)
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
            elif kind == "walkthrough":
                # Watching earns no evidence: only provenance and a counter change.
                wt = lesson.get("walkthrough")
                if not wt:
                    raise CourseStateError("walkthrough_missing")
                sp["worked_example_shown"] = True
                content = {**cc.public_walkthrough(wt), "refs": refs(wt),
                           "note": "Bu nümayişdir; praktika və imtahan fərqli tapşırıqlardır. İzləmək sübut sayılmır."}
            elif kind == "ladder":
                ld = lesson.get("ladder")
                if not ld:
                    raise CourseStateError("ladder_missing")
                sp["worked_example_shown"] = True
                content = cc.public_ladder_rung(ld, rung)
                content["note"] = "Pilləli təlim öyrətmə materialıdır; sübut sayılmır. Üçüncü pillə praktikanın özüdür."
            else:  # hint ladder (practice only)
                if state["phase"] != "practice":
                    raise CourseStateError("phase_forbids_action", "hints belong to guided practice")
                used = int(prog["help"].get("hints", 0))
                hints = lesson["practice"]["hints"]
                if "hint_ceiling" in lesson and used >= lesson["hint_ceiling"]:
                    raise CourseStateError("hint_ceiling_reached")
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
        return self._mutate(sid, "help", fn, request=request, **kw)

    def self_explanation(self, sid, node_id: str, text: str, **kw):
        if not isinstance(text, str) or not 10 <= len(text.strip()) <= 4000:
            raise CourseStateError("phase_forbids_action", "self-explanation must be 10-4000 characters")

        def fn(d, led, state):
            cs.guard(state, "self_explanation")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "self-explanation is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            coverage = grade_explanation(text, " ".join(lesson["self_explanation_key_terms"] * 2), threshold=0.6)
            sub = self._module(d, node_id) / "submission" / "self-explanation.md"
            sub.write_text(text.strip() + "\n", encoding="utf-8")
            led.append("observation", {"capability_id": node_id, "verification": "self_explanation_coverage",
                                       "outcome": "unknown", "correctness_checked": False, "mastery_eligible": False,
                                       "coverage": coverage["coverage"], "missed": coverage["missed"][:10],
                                       "note": "formative_self_explanation; lexical coverage only"},
                       {"source": "course_self_explanation"})
            self._progress(state, node_id)["self_explanation"] = {"coverage": coverage["coverage"], "at": self._now()}
            return ({"coverage": coverage["coverage"], "covered": coverage["covered"], "missed": coverage["missed"][:10],
                     "note_az": "Bu yalnız açar anlayışların əhatəsidir, düzgünlük qiyməti deyil. Agent mənanı ayrıca yoxlayacaq."},
                    {"event_type": "course_self_explanation", "node_id": node_id}, None)
        return self._mutate(sid, "self_explanation", fn, request={'node': node_id, 'text': text}, **kw)

    def begin_practice(self, sid, node_id: str, **kw):
        def fn(d, led, state):
            cs.guard(state, "begin_practice")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "practice is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            prog = self._progress(state, node_id)
            current = prog.get("practice")
            if not current or current.get("revealed"):
                if current:      # a revealed attempt stays as history; a new attempt id is issued
                    prog.setdefault("practice_history", []).append(current)
                    prog["help"]["hints"] = 0       # hints are per attempt: the ceiling must not shut out the new one
                prog["practice"] = {"node_id": node_id, "practice_attempt_id": new_id("prc"), "checks_run": 0,
                                    "last_outcome": None}
            cs.set_phase(state, "practice", self._now())
            state["resume_checkpoint"] = {"phase": "practice", "route": f"#/practice/{node_id}", "node_id": node_id,
                                          "attempt_id": None, "at": self._now()}
            pr = lesson["practice"]
            out = {"node_id": node_id, "practice": prog["practice"], "prompt_md": pr["prompt_md"],
                   "kind": cc.item_kind(pr)}
            if out["kind"] == "python":
                out["file"] = str(self._module(d, node_id) / "submission" / pr["entry"])
            else:
                out["questions"] = cc.public_questions(pr["questions"])
                if pr.get("scenario_md"):
                    out["scenario_md"] = pr["scenario_md"]
            return out, {"event_type": "course_practice_started", "node_id": node_id}, None
        return self._mutate(sid, "begin_practice", fn, request={'node': node_id}, **kw)

    def practice_check(self, sid, node_id: str, **kw):
        def fn(d, led, state):
            cs.guard(state, "practice_check")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "practice check is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            self._refuse_revealed(state, node_id)
            if cc.item_kind(lesson["practice"]) != "python":
                raise CourseStateError("phase_forbids_action", "this practice is answered in the app: use practice-answer")
            entry = lesson["practice"]["entry"]
            path = self._module(d, node_id) / "submission" / entry
            code = path.read_text(encoding="utf-8") if path.is_file() and not path.is_symlink() else ""
            if not code.strip():
                raise CourseStateError("phase_forbids_action", f"submission/{entry} is empty")
            results = cc.run_checks(code, lesson["practice"]["checks"], reveal=True)
            passed = all(r["passed"] for r in results)
            fired = [] if passed else cc.detect_misconceptions(code, lesson["practice"]["misconceptions"])
            return self._record_practice(sid, led, state, node_id, results, fired, fmt="artifact_execution",
                                         strength="medium", fingerprint=hashlib.sha256(code.encode()).hexdigest())
        return self._mutate(sid, "practice_check", fn, request={'node': node_id}, **kw)

    def practice_answer(self, sid, node_id: str, answers: dict, **kw):
        """Concept/scenario practice: deterministic key grading, assisted/formative only."""
        def fn(d, led, state):
            cs.guard(state, "practice_answer")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "practice is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            self._refuse_revealed(state, node_id)
            pr = lesson["practice"]
            if cc.item_kind(pr) == "python":
                raise CourseStateError("phase_forbids_action", "this practice is code: use practice-check")
            try:
                given = cc.validate_answers(pr, answers)
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", str(e))
            results, fired = cc.grade_questions(pr["questions"], given, pr["misconceptions"])
            selected_only = all(q["type"] in ("single_choice", "multi_choice") for q in pr["questions"])
            canonical = json.dumps(given, sort_keys=True, ensure_ascii=False)
            return self._record_practice(sid, led, state, node_id, results, fired,
                                         fmt=cc.SELECTED_ONLY_FORMAT if selected_only else cc.KIND_FORMAT[cc.item_kind(pr)],
                                         strength="weak" if selected_only else "medium",
                                         fingerprint=hashlib.sha256(canonical.encode()).hexdigest())
        return self._mutate(sid, "practice_answer", fn, request={'node': node_id, 'answers': answers}, **kw)

    @staticmethod
    def _refuse_revealed(state, node_id):
        practice = (state["node_progress"].get(node_id) or {}).get("practice") or {}
        if practice.get("revealed"):
            raise CourseStateError("practice_revealed")

    def practice_reveal(self, sid, node_id: str, confirm: bool = False, **kw):
        """Explicit, confirmed reveal of the reference solution: the current practice attempt earns no credit.

        Appends an observation (never evidence), marks the attempt revealed (later evidence is assisted with
        ``ai_direct_answer_revealed``) and requires ``begin_practice`` to issue a new attempt id."""
        def fn(d, led, state):
            cs.guard(state, "practice_reveal")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "reveal is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            prog = self._progress(state, node_id)
            practice = prog.get("practice")
            if not practice:
                raise CourseStateError("phase_forbids_action", "begin the practice before asking for the solution")
            if confirm is not True:
                raise CourseStateError("reveal_confirmation_required")
            pr = lesson["practice"]
            if practice.get("revealed"):
                # Re-delivery (a lost response): the solution is returned again; no second observation, no event.
                return self._reveal_payload(pr, node_id, state), None, None
            if not self._reveal_observed(led, practice.get("practice_attempt_id")):
                led.append("observation", {"capability_id": node_id, "verification": "reveal", "outcome": "unknown",
                                           "correctness_checked": False, "mastery_eligible": False,
                                           "attempt_id": practice.get("practice_attempt_id"), "task_id": pr["task_id"],
                                           "note": "practice_solution_revealed; no credit for this practice attempt"},
                           {"source": "course_practice_reveal"})
            state["support_provenance"]["ai_direct_answer_revealed"] = True
            practice["revealed"] = True
            practice["revealed_at"] = self._now()
            prog["help"]["reveal"] = int(prog["help"].get("reveal", 0)) + 1
            self.sk.rebuild_projection(sid)
            out = self._reveal_payload(pr, node_id, state)
            return out, {"event_type": "course_practice_revealed", "node_id": node_id}, None
        def rehydrate(d, state, replayed):
            if self._ever_revealed(state, node_id):
                lesson = self._ready_lesson(d, state, node_id)
                replayed.update({k: v for k, v in self._reveal_payload(lesson["practice"], node_id, state).items()
                                 if k in ("reference_solution", "answer_key")})
            return replayed
        return self._mutate(sid, "practice_reveal", fn, request={'node': node_id, 'confirm': confirm is True},
                            rehydrate=rehydrate, **kw)

    @staticmethod
    def _ever_revealed(state, node_id) -> bool:
        prog = state["node_progress"].get(node_id) or {}
        return any((a or {}).get("revealed") for a in [prog.get("practice"), *(prog.get("practice_history") or [])])

    @staticmethod
    def _reveal_observed(led, attempt_id) -> bool:
        """The reveal observation is appended before the state commit; a retry after a failed commit must not
        record a second reveal for the same attempt."""
        return attempt_id is not None and any(
            r["record_type"] == "observation" and r["payload"].get("verification") == "reveal"
            and r["payload"].get("attempt_id") == attempt_id for r in led.verified_records())

    @staticmethod
    def _reveal_payload(pr, node_id, state) -> dict:
        out = {"node_id": node_id, "revealed": True, "kind": cc.item_kind(pr), "credit": 0,
               "support_provenance": dict(state["support_provenance"]),
               "note_az": ("Həll açıldı: bu praktika cəhdi üçün kredit verilmir. Davam etmək üçün yeni cəhd "
                           "başla (begin_practice).")}
        if cc.item_kind(pr) == "python":
            out["reference_solution"] = pr["reference_solution"]
        else:
            out["answer_key"] = cc.answer_key(pr["questions"])
        return out

    def practice_reset(self, sid, node_id: str, **kw):
        """Archive the learner's current submission file, then restore the starter. Nothing is deleted."""
        def fn(d, led, state):
            cs.guard(state, "practice_reset")
            if node_id != state["active_node_id"]:
                raise CourseStateError("phase_forbids_action", "reset is for the active node")
            lesson = self._ready_lesson(d, state, node_id)
            pr = lesson["practice"]
            if cc.item_kind(pr) != "python":
                raise CourseStateError("phase_forbids_action", "only a code practice has a file to reset")
            mod = self._module(d, node_id)
            path = mod / "submission" / pr["entry"]
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise CourseStateError("phase_forbids_action", f"submission/{pr['entry']} is not a regular file")
            archived = None
            starter = pr["starter_code"].encode("utf-8")
            if path.is_file() and path.read_bytes() != starter:   # a retry after a failed commit must not archive the starter
                stamp = self._dt().astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                target = mod / "archive" / f"{stamp}-{pr['entry']}"
                n = 1
                while target.exists():
                    n += 1
                    target = mod / "archive" / f"{stamp}-{n}-{pr['entry']}"
                write_bytes(target, path.read_bytes())
                archived = str(target)
            if not path.is_file() or path.read_bytes() != starter:
                write_bytes(path, starter)
            return ({"node_id": node_id, "reset": True, "archived": archived, "file": str(path),
                     "note_az": "Əvvəlki kodun arxivə köçürüldü, başlanğıc fayl bərpa olundu."},
                    {"event_type": "course_practice_reset", "node_id": node_id}, None)
        return self._mutate(sid, "practice_reset", fn, request={'node': node_id}, **kw)

    def _record_practice(self, sid, led, state, node_id, results, fired, *, fmt, strength, fingerprint):
        """Shared bookkeeping for code and in-app practice: formative, never mastery."""
        passed = all(r["passed"] for r in results)
        prog = self._progress(state, node_id)
        counts = state["misconception_counts"].setdefault(node_id, {})
        for m in fired:
            counts[m["id"]] = counts.get(m["id"], 0) + 1
        recurring = [m for m in fired if counts[m["id"]] >= 2]
        if recurring:
            prog["method"] = "alternative"
        sp = dict(state["support_provenance"])
        practice = prog["practice"] or {}
        if self._ever_revealed(state, node_id):   # sticky per node: a node switch resets the mission-level provenance,
            sp["ai_direct_answer_revealed"] = True    # but the learner has still seen the solution
        practice["checks_run"] = int(practice.get("checks_run", 0)) + 1
        practice["last_outcome"] = "correct" if passed else "incorrect"
        prog["practice"] = practice
        assisted = sp["hints_count"] > 0 or any(sp[k] for k in sp if k != "hints_count")
        ev_id = f"ev_prc_{practice.get('practice_attempt_id')}_{practice['checks_run']}"
        if not self._evidence_exists(led, ev_id):
            self._append_practice_evidence(led, ev_id, node_id, state, passed, assisted, sp, practice, fingerprint,
                                           fired, fmt, strength)
            self.sk.rebuild_projection(sid)
        feedback = _practice_feedback(results, fired, recurring)
        return ({"node_id": node_id, "passed": passed, "checks": results, "misconceptions": fired,
                 "recurring_misconceptions": [m["id"] for m in recurring], "feedback_az": feedback,
                 "method_change": bool(recurring), "support_provenance": sp},
                {"event_type": "course_practice_checked", "node_id": node_id, "passed": passed,
                 "misconceptions": [m["id"] for m in fired], "evidence_id": ev_id}, None)

    def _append_practice_evidence(self, led, ev_id, node_id, state, passed, assisted, sp, practice, fingerprint,
                                  fired, fmt, strength):
        led.append("evidence", {
            "evidence_id": ev_id, "capability_id": node_id,
            "outcome": "correct" if passed else "incorrect",
            "independence": "assisted" if assisted else "unknown",
            "strength": strength, "scope": "supported_completion", "evidence_format": fmt,
            "mastery_eligible": False, "correctness_checked": True,
            "support_provenance": {"hints_count": sp["hints_count"],
                                   "worked_example_shown": sp["worked_example_shown"],
                                   "ai_direct_answer_revealed": sp["ai_direct_answer_revealed"],
                                   "conceptual_scaffold": sp["explanation_shown"] or sp["alternative_explanation_shown"]},
            "attempt_id": practice.get("practice_attempt_id"),
            "task_fingerprint": fingerprint,
            "capability_version": self._cap_version(state, node_id),
            "misconceptions": [m["id"] for m in fired],
            "note": "guided_practice; formative, never independent mastery",
        }, {"source": "course_practice"})

    # ------------------------------------------------------------------- exam
    def start_exam(self, sid, node_id: str, *, fast_track: bool = False, **kw):
        def fn(d, led, state):
            resumed = self._resume_attempt(d, state, node_id)
            if resumed:
                return resumed, None, None
            cs.guard(state, "start_exam")
            return self._open_attempt(d, state, node_id, fast_track=fast_track, review=None)
        return self._mutate(sid, "start_exam", fn, request={'node': node_id, 'fast_track': bool(fast_track)}, **kw)

    def _resume_attempt(self, d, state, node_id):
        """Refresh / second tab / restart resumes the open attempt, never re-mints one."""
        att_id = state["active_attempt_id"]
        if att_id and state["phase"] in cs.TEACHING_LOCKED_PHASES:
            att = state["attempts"][att_id]
            if att["node_id"] == node_id:
                return {"resumed": True, "attempt_id": att_id, "attempt": self._public_attempt(d, att)}
            raise CourseStateError("exam_active", "another attempt is in progress")
        return None

    def _open_attempt(self, d, state, node_id, *, fast_track, review):
        plan = self._plan(d)
        node = self._node(plan, node_id)
        recovery = (state["node_progress"].get(node_id) or {}).get("recovery") or {}
        if recovery.get("status") == "open" and recovery.get("capability_version") == self._cap_version(state, node_id):
            raise CourseStateError("recovery_required", node_id)
        if not self._available(state, node):
            raise CourseStateError("phase_forbids_action", "prerequisites not yet demonstrated")
        lesson = self._lesson(d, node_id, state)
        if lesson is not None and self._lesson_gate(d, state, node_id, lesson)["status"] == "grounding_invalid":
            raise CourseStateError("grounding_invalid", "the lesson's sources must be re-validated before assessment")
        bad_sources = self._node_source_problems(state, node)
        if bad_sources:
            raise CourseStateError("grounding_invalid", "roadmap sources not validated: " + ", ".join(bad_sources))
        eligible = self._eligible(d, state, node_id)
        if not eligible:
            raise CourseStateError("no_fresh_item", "no unseen assessment item left for this node; author a new one")
        item, fresh = min(eligible, key=lambda p: (p[0].get("difficulty", 1), p[1]["basis"] != "automatic",
                                                   p[0]["item_id"]))
        now = self._now()
        kind = cc.item_kind(item)
        fp = cc.fingerprint(item)
        att_id = new_id("att")
        att = {"attempt_id": att_id, "node_id": node_id, "item_id": item["item_id"],
               "item_version": item["item_version"], "kind": kind,
               "entry": item["entry"] if kind == "python" else "answers.json", "status": "active",
               "started_at": now, "fast_track": bool(fast_track), "support": cs.empty_support(),
               "submission_sha256": None, "result": None, "evidence_id": None,
               "capability_version": self._cap_version(state, node_id), "content_fingerprint": fp["content"],
               "freshness_basis": fresh["basis"], "review_id": review["review_id"] if review else None,
               "events": [{"status": "active", "at": now}]}
        state["attempts"][att_id] = att
        state["exposure"].setdefault(node_id, []).append(item["item_id"])
        state["exposure_records"].setdefault(node_id, []).append(cc.exposure_record(item, att_id, now))
        state["active_node_id"] = node_id
        state["active_attempt_id"] = att_id
        state["attempt_status"] = "active"
        state["assessment_item_version"] = item["item_version"]
        self._progress(state, node_id)["exam_attempts"].append(att_id)
        cs.set_phase(state, "exam_active", now)
        state["resume_checkpoint"] = {"phase": "exam_active", "route": f"#/exam/{att_id}", "node_id": node_id,
                                      "attempt_id": att_id, "at": now}
        exam_dir = self._module(d, node_id) / "exam" / att_id
        exam_dir.mkdir(parents=True, exist_ok=True)
        where = ("Kodunu bu qovluqdakı fayla yaz." if kind == "python"
                 else "Cavablarını tətbiqdə ver (və ya answers.json faylını doldur).")
        (exam_dir / "TASK.md").write_text(f"# {item['title']}\n\n{item['prompt_md']}\n\n{where}\n"
                                          "Bu müstəqil yoxlamadır: tətbiqdə izah və ipucu bağlıdır.\n", encoding="utf-8")
        target = exam_dir / att["entry"]
        if not target.exists():
            if kind == "python":
                target.write_text(item["starter_code"], encoding="utf-8")
            else:
                write_json(target, {})
        return ({"resumed": False, "attempt_id": att_id, "attempt": self._public_attempt(d, att)},
                {"event_type": "course_exam_started", "attempt_id": att_id, "node_id": node_id, "kind": kind,
                 "item_id": item["item_id"], "item_version": item["item_version"], "fast_track": bool(fast_track),
                 "capability_version": att["capability_version"], "freshness_basis": fresh["basis"],
                 "review_id": att["review_id"]}, None)

    def _public_attempt(self, d, att) -> dict:
        item = self._frozen_item(d, att)
        public = cc.public_item(item) if item else {"item_id": att["item_id"], "integrity_status": "item_missing_or_changed"}
        exam_file = self._module(d, att["node_id"]) / "exam" / att["attempt_id"] / att["entry"]
        out = {"attempt_id": att["attempt_id"], "node_id": att["node_id"], "status": att["status"],
               "kind": att.get("kind", "python"), "started_at": att["started_at"], "fast_track": att["fast_track"],
               "item": public, "file": str(exam_file), "result": att.get("result"),
               "scoring_error": att.get("scoring_error"), "review_id": att.get("review_id"),
               "rubric_pending": att["status"] == "awaiting_rubric"}
        if out["kind"] != "python" and att["status"] == "active":
            try:
                saved = read_json(exam_file) if exam_file.is_file() and not exam_file.is_symlink() else {}
            except (OSError, ValueError):
                saved = {}
            out["saved_answers"] = saved if isinstance(saved, dict) else {}
        return out

    def _attempt(self, state, attempt_id):
        if not isinstance(attempt_id, str) or not _ATTEMPT.fullmatch(attempt_id) or attempt_id not in state["attempts"]:
            raise CourseStateError("phase_forbids_action", "unknown attempt")
        return state["attempts"][attempt_id]

    def _frozen_item(self, d, att) -> dict | None:
        """The vault item exactly as frozen at exam start, or None if missing/changed."""
        path = self._vault(d, att["node_id"]) / f"{att['item_id']}.json"
        if not path.exists():
            return None
        try:
            item = read_json(path)
            valid = isinstance(item, dict) and item.get("item_version") == att["item_version"] and cs.digest(
                "it", {k: v for k, v in item.items() if k != "item_version"}) == att["item_version"]
        except (OSError, ValueError, TypeError):
            return None
        if not valid:
            return None
        return item

    def save_answers(self, sid, attempt_id: str, answers: dict, **kw):
        """Non-code exams: persist the learner's own answers (no grading, no feedback)."""
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            cs.guard(state, "save_answers")
            if state["active_attempt_id"] != attempt_id or att["status"] != "active":
                raise CourseStateError("phase_forbids_action", "not the active attempt")
            if att.get("kind", "python") == "python":
                raise CourseStateError("phase_forbids_action", "code exams are answered in the exam file")
            item = self._frozen_item(d, att)
            if item is None:
                raise CourseStateError("invalid_state", "frozen item missing or changed")
            try:
                given = cc.validate_answers(item, answers)
            except cc.ContentError as e:
                raise CourseStateError("phase_forbids_action", str(e))
            write_json(self._module(d, att["node_id"]) / "exam" / attempt_id / att["entry"], given)
            return {"attempt_id": attempt_id, "saved": True, "answered": sorted(given)}, None, None
        return self._mutate(sid, "save_answers", fn, request={'attempt': attempt_id, 'answers': answers}, **kw)

    def submit_exam(self, sid, attempt_id: str, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] != "active":       # duplicate click / retry: no second snapshot
                return {"attempt_id": attempt_id, "status": att["status"], "duplicate": True}, None, None
            cs.guard(state, "submit_exam")
            if state["active_attempt_id"] != attempt_id:
                raise CourseStateError("phase_forbids_action", "not the active attempt")
            src = self._module(d, att["node_id"]) / "exam" / attempt_id / att["entry"]
            if att.get("kind", "python") == "python":
                code = src.read_text(encoding="utf-8") if src.is_file() and not src.is_symlink() else ""
                if not code.strip():
                    raise CourseStateError("phase_forbids_action", f"{att['entry']} is empty")
            else:
                item = self._frozen_item(d, att)
                if item is None:
                    raise CourseStateError("invalid_state", "frozen item missing or changed")
                try:
                    raw = read_json(src) if src.is_file() and not src.is_symlink() else {}
                    given = cc.validate_answers(item, raw)
                except (OSError, ValueError) as e:
                    raise CourseStateError("phase_forbids_action", f"answers are not valid: {e}")
                if not given:
                    raise CourseStateError("phase_forbids_action", "no answers saved yet")
                code = json.dumps(given, sort_keys=True, ensure_ascii=False)
            snap = self._attempt_dir(d, attempt_id) / "submitted"
            write_bytes(snap / att["entry"], code.encode("utf-8"))     # 0700 dirs (every parent) and a 0600 file, whatever the umask
            att["submission_sha256"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
            att["submitted_at"] = self._now()
            att["status"] = "submitted"
            att["events"].append({"status": "submitted", "at": self._now()})
            state["attempt_status"] = "submitted"
            cs.set_phase(state, "assessment_pending", self._now())
            state["resume_checkpoint"] = {"phase": "assessment_pending", "route": f"#/exam/{attempt_id}",
                                          "node_id": att["node_id"], "attempt_id": attempt_id, "at": self._now()}
            return ({"attempt_id": attempt_id, "status": "submitted", "submission_sha256": att["submission_sha256"]},
                    {"event_type": "course_exam_submitted", "attempt_id": attempt_id,
                     "submission_sha256": att["submission_sha256"]}, None)
        return self._mutate(sid, "submit_exam", fn, request={'attempt': attempt_id}, **kw)

    def _snapshot(self, d, att) -> str | None:
        snap = self._attempt_dir(d, att["attempt_id"]) / "submitted" / att["entry"]
        try:
            code = snap.read_text(encoding="utf-8") if snap.is_file() else None
        except (OSError, ValueError):
            return None
        if code is None or hashlib.sha256(code.encode("utf-8")).hexdigest() != att["submission_sha256"]:
            return None
        return code

    def score_exam(self, sid, attempt_id: str, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] in {"scored", "finalized", "awaiting_rubric"}:
                return {"attempt_id": attempt_id, "status": att["status"], "duplicate": True}, None, None
            cs.guard(state, "score_exam")
            if att["status"] not in {"submitted", "scoring_failed"}:
                raise CourseStateError("phase_forbids_action", f"cannot score a {att['status']} attempt")
            kind = att.get("kind", "python")
            failure = None
            item = self._frozen_item(d, att)
            code = self._snapshot(d, att)
            if item is None:
                failure = ("item_missing_from_vault" if not (self._vault(d, att["node_id"]) / f"{att['item_id']}.json").exists()
                           else "item_version_changed")
            elif code is None:
                failure = "submission_snapshot_changed"
            if failure is None and kind == "free_text":
                # Never keyword-scored: the attempt waits (exam lock kept) for a rubric judgment.
                att["status"] = "awaiting_rubric"
                att.pop("scoring_error", None)
                att["events"].append({"status": "awaiting_rubric", "at": self._now()})
                state["attempt_status"] = "awaiting_rubric"
                return ({"attempt_id": attempt_id, "status": "awaiting_rubric", "rubric_required": True},
                        {"event_type": "course_exam_awaiting_rubric", "attempt_id": attempt_id}, None)
            results = fired = None
            if failure is None:
                try:
                    if kind == "python":
                        results = cc.run_checks(code, item["checks"], reveal=False)
                        cc.validate_grading_result(results, [], item)
                        fired = ([] if all(r["passed"] for r in results)
                                 else cc.detect_misconceptions(code, item["misconceptions"]))
                    else:
                        results, fired = cc.grade_questions(item["questions"], json.loads(code), item["misconceptions"])
                    cc.validate_grading_result(results, fired, item)
                except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                    failure = f"verifier_unavailable:{type(e).__name__}"
            if failure:
                att["status"] = "scoring_failed"
                att["scoring_error"] = failure
                att["events"].append({"status": "scoring_failed", "at": self._now(), "reason": failure})
                state["attempt_status"] = "scoring_failed"
                return ({"attempt_id": attempt_id, "status": "scoring_failed", "reason": failure, "resumable": True},
                        {"event_type": "course_exam_scoring_failed", "attempt_id": attempt_id, "reason": failure}, None)
            return self._store_result(d, state, att, results, fired)
        return self._mutate(sid, "score_exam", fn, request={'attempt': attempt_id}, **kw)

    def _store_result(self, d, state, att, results, fired, extra=None):
        attempt_id = att["attempt_id"]
        by_cat = {}
        for r in results:
            c = by_cat.setdefault(r["category"], {"passed": 0, "total": 0})
            c["total"] += 1
            c["passed"] += int(r["passed"])
        core_ok = all(r["passed"] for r in results if r["category"] in {"core", "build"})
        outcome = "correct" if all(r["passed"] for r in results) else ("partial" if core_ok else "incorrect")
        if extra and extra.get("outcome"):
            outcome = extra["outcome"]
        result = {"outcome": outcome, "by_category": by_cat, "passed": sum(r["passed"] for r in results),
                  "total": len(results), "misconceptions": [m["id"] for m in fired],
                  "checks": [{"id": r["id"], "name": r["name"], "category": r["category"], "passed": r["passed"],
                              "feedback": r["feedback"], "error": r["error"]} for r in results],
                  "misconception_feedback": [{"id": m["id"], "name": m["name"], "feedback": m["feedback"]} for m in fired],
                  "scored_at": self._now(), "item_version": att["item_version"],
                  "submission_sha256": att["submission_sha256"], **(extra or {})}
        result_path = self._attempt_dir(d, attempt_id) / "result.json"
        cc.write_private(result_path, result)
        att["result"] = {k: result[k] for k in ("outcome", "by_category", "passed", "total", "misconceptions")}
        if extra and extra.get("rubric"):
            att["result"]["confidence"] = extra["rubric"]["confidence"]
            att["result"]["grader"] = extra["rubric"]["grader"]
        att["result_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
        att["status"] = "scored"
        att.pop("scoring_error", None)
        att["events"].append({"status": "scored", "at": self._now()})
        state["attempt_status"] = "scored"
        return ({"attempt_id": attempt_id, "status": "scored", "result": att["result"]},
                {"event_type": "course_exam_scored", "attempt_id": attempt_id, "outcome": outcome}, None)

    def record_rubric(self, sid, attempt_id: str, judgment: dict, **kw):
        """Explicit rubric judgment for an open response (host agent or human grader).

        Every criterion is decided; a criterion judged met must quote the learner's own
        response (>= 12 characters, verbatim modulo whitespace/case), so the decision is
        linked to evidence and can be audited. Nothing is inferred from keywords.
        """
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] in {"scored", "finalized"}:
                return {"attempt_id": attempt_id, "status": att["status"], "duplicate": True}, None, None
            cs.guard(state, "record_rubric")
            if att["status"] != "awaiting_rubric":
                raise CourseStateError("phase_forbids_action", f"cannot judge a {att['status']} attempt")
            item = self._frozen_item(d, att)
            code = self._snapshot(d, att)
            if item is None or code is None:
                raise CourseStateError("invalid_state", "frozen item or submission snapshot changed")
            j = _validate_judgment(judgment, item, json.loads(code)["response"])
            rows = [{"id": c["id"], "name": c["criterion"], "category": "core", "passed": j["criteria"][c["id"]]["met"],
                     "feedback": None if j["criteria"][c["id"]]["met"] else c["criterion"], "error": None,
                     "observed": None} for c in item["rubric"]]
            fired = [{"id": m["id"], "name": m["name"], "feedback": m["feedback"]}
                     for m in item["misconceptions"] if m["id"] in j["misconceptions"]]
            met = sum(r["passed"] for r in rows)
            outcome = "correct" if met == len(rows) else ("partial" if met else "incorrect")
            rubric = {"grader": j["grader"], "confidence": j["confidence"], "rationale": j["rationale"],
                      "criteria": j["criteria"], "judged_at": self._now(), "version": cs.digest("rb", item["rubric"])}
            att["rubric"] = {k: rubric[k] for k in ("grader", "confidence", "judged_at", "version")}
            result, event, files = self._store_result(d, state, att, rows, fired, {"outcome": outcome, "rubric": rubric})
            event = {**event, "event_type": "course_exam_rubric_recorded", "grader": j["grader"],
                     "confidence": j["confidence"], "rubric_version": rubric["version"]}
            return result, event, files
        return self._mutate(sid, "record_rubric", fn, request={'attempt': attempt_id, 'judgment': judgment}, **kw)

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
                item = self._frozen_item(d, att)
                if item is None or self._snapshot(d, att) is None:
                    raise CourseStateError("invalid_state", "frozen item or submitted response changed before finalization; no evidence written")
                led.append("evidence", self._exam_evidence(led, state, att, item, ev_id), {"source": "course_exam_verifier"})
                self.sk.rebuild_projection(sid)
            self._finalize_state(d, led, state, att)
            return ({"attempt_id": attempt_id, "status": "finalized", "assessment_result": state["assessment_result"],
                     "next_mission": state["next_mission"]},
                    {"event_type": "course_exam_finalized", "attempt_id": attempt_id, "evidence_id": ev_id}, None)
        return self._mutate(sid, "finalize_exam", fn, request={'attempt': attempt_id}, **kw)

    def _exam_evidence(self, led, state, att, item, ev_id) -> dict:
        """Evidence semantics per assessment kind (strength is earned, never asserted).

        python: hidden-check execution; strong only for a correct, automatically fresh item.
        concept/scenario: deterministic key; selected-response-only is recognition (weak),
        otherwise capped at medium. free_text: rubric judgment, not runtime-checked, medium.
        An item cleared as fresh only by author review is capped at medium.
        """
        result = att["result"]
        kind = att.get("kind", "python")
        correct = result["outcome"] == "correct"
        if kind == "python":
            fmt, verification = "artifact_execution", "hidden_checks"
            strength = "strong" if correct and att.get("freshness_basis", "automatic") == "automatic" else "medium"
        elif kind == "free_text":
            fmt, verification, strength = cc.KIND_FORMAT[kind], f"rubric_{result.get('grader', 'host_agent')}", "medium"
        else:
            selected_only = all(q["type"] in ("single_choice", "multi_choice") for q in item["questions"])
            fmt = cc.SELECTED_ONLY_FORMAT if selected_only else cc.KIND_FORMAT[kind]
            verification, strength = "answer_key", "weak" if selected_only else "medium"
        scope = item["scope"]
        review = self._review_evidence(led, state, att) if att.get("review_id") else None
        if review and review["counts_as_delayed"]:
            scope = "delayed_independent_performance"
        notes = {"python": "frozen unseen item, hidden + transfer checks",
                 "concept": "frozen unseen item, deterministic answer key",
                 "scenario": "frozen unseen scenario, deterministic answer key",
                 "free_text": "frozen unseen open question, rubric judgment linked to quoted response; not runtime-verified"}
        payload = {
            "evidence_id": ev_id, "capability_id": att["node_id"], "outcome": result["outcome"],
            "independence": "unassisted", "strength": strength, "scope": scope, "evidence_format": fmt,
            "mastery_eligible": True, "correctness_checked": kind != "free_text",
            "support_provenance": {k: att["support"][k] for k in
                                   ("hints_count", "worked_example_shown", "ai_direct_answer_revealed",
                                    "conceptual_scaffold")},
            "attempt_id": att["attempt_id"], "item_id": att["item_id"], "item_version": att["item_version"],
            "assessment_kind": kind, "capability_version": att.get("capability_version"),
            "content_fingerprint": att.get("content_fingerprint"), "freshness_basis": att.get("freshness_basis"),
            # Content identity, not item id: a renamed copy answered the same way collapses.
            "task_fingerprint": hashlib.sha256(((att.get("content_fingerprint") or att["item_version"])
                                                + att["submission_sha256"]).encode()).hexdigest(),
            "verification": verification, "fast_track": att["fast_track"],
            "note": (f"course exam: {notes[kind]}; no in-app teaching or hints were available during the attempt; "
                     "external assistance is not observable"),
        }
        if kind == "free_text":
            payload["rubric"] = {"grader": result.get("grader"), "confidence": result.get("confidence"),
                                 "uncertainty": "single_grader_judgment"}
        if review:
            payload["review"] = review
        return payload

    def _review_evidence(self, led, state, att) -> dict:
        """Spacing claims come from ledger timestamps (wall clock), never from the caller."""
        entry = next((q for q in state["review_queue"] if q["review_id"] == att["review_id"]), {})
        source = f"ev_exam_{entry.get('source_attempt_id')}"
        records = led.verified_records()
        src = next((r for r in records if r["record_type"] == "evidence"
                    and r["payload"].get("evidence_id") == source), None)
        started = next((r for r in records if r["record_type"] == "event"
                        and r["payload"].get("event_type") in {"course_review_started", "course_exam_started"}
                        and r["payload"].get("attempt_id") == att["attempt_id"]), None)
        src_t = _utc(src["timestamp"]) if src else None
        start_t = _utc(started["timestamp"]) if started else None
        # Retention spacing starts after the latest observed learning/retrieval of
        # this capability, not an older pass followed by recent re-teaching.
        exposures = {"course_help_delivered", "course_practice_started", "course_practice_checked",
                     "course_self_explanation", "course_exam_started"}
        last = src
        if src and started:
            for record in records:
                if not src["seq"] < record["seq"] < started["seq"]:
                    continue
                p = record["payload"]
                same_node = p.get("node_id", p.get("capability_id")) == att["node_id"]
                if same_node and (record["record_type"] == "evidence" or p.get("event_type") in exposures):
                    last = record
        last_t = _utc(last["timestamp"]) if last else None
        hours = (start_t - last_t).total_seconds() / 3600 if src_t and start_t and last_t else None
        observed = round(hours, 2) if hours is not None else None
        return {"review_id": att["review_id"], "scheduled_due_at": entry.get("due_at"),
                "interval_days": entry.get("interval_days"), "source_attempt_id": entry.get("source_attempt_id"),
                "observed_spacing_hours": observed, "spacing_basis": "ledger_timestamps",
                "last_learning_record_id": last.get("record_id") if last else None,
                "counts_as_delayed": hours is not None and hours >= REVIEW_MIN_OBSERVED_HOURS}

    def _free_text_corroborated(self, state, att) -> bool:
        """A single rubric judgment is never enough to pass a node (conservative progression)."""
        if (att.get("result") or {}).get("confidence") not in {"medium", "high"}:
            return False
        for other in state["attempts"].values():
            if other is att or other.get("node_id") != att["node_id"] or other.get("status") != "finalized":
                continue
            res = other.get("result") or {}
            if other.get("capability_version") != att.get("capability_version") or res.get("outcome") != "correct":
                continue
            if other.get("kind", "python") == "python":
                return True
            if (other.get("kind") == "free_text" and other.get("content_fingerprint") != att.get("content_fingerprint")
                    and res.get("confidence") in {"medium", "high"}
                    and "human" in {res.get("grader"), (att.get("result") or {}).get("grader")}):
                return True
        return False

    def _finalize_state(self, d, led, state, att, reconciled=False):
        ev_id = f"ev_exam_{att['attempt_id']}"
        result = att["result"]
        record = next(r for r in led.verified_records()
                      if r["record_type"] == "evidence" and r["payload"].get("evidence_id") == ev_id)
        att["status"] = "finalized"
        att["evidence_id"] = ev_id
        att["evidence_strength"] = record["payload"].get("strength")
        att["finalized_at"] = self._now()
        att["events"].append({"status": "finalized", "at": att["finalized_at"], "reconciled": reconciled})
        node_id = att["node_id"]
        prog = self._progress(state, node_id)
        prog["last_outcome"] = result["outcome"]
        provisional = False
        if result["outcome"] == "correct":
            if att.get("kind", "python") == "free_text" and not self._free_text_corroborated(state, att):
                provisional = True
                prog["provisional"] = {"attempt_id": att["attempt_id"], "at": att["finalized_at"],
                                       "reason": "single_rubric_judgment_needs_corroboration"}
            elif not att.get("review_id") or not self._passed(state, node_id):
                prog["passed"] = True
                prog["passed_at"] = att["finalized_at"]
                prog["capability_version"] = att.get("capability_version")
                prog.pop("provisional", None)
        counts = state["misconception_counts"].setdefault(node_id, {})
        for mid in result["misconceptions"]:
            counts[mid] = counts.get(mid, 0) + 1
        state["evidence_refs"].append({"evidence_id": ev_id, "record_id": record["record_id"], "seq": record["seq"],
                                       "attempt_id": att["attempt_id"], "node_id": node_id, "kind": "exam",
                                       "outcome": result["outcome"]})
        result_path = self._attempt_dir(d, att["attempt_id"]) / "result.json"
        try:
            ok = hashlib.sha256(result_path.read_bytes()).hexdigest() == att.get("result_sha256")
            full = read_json(result_path) if ok else None
        except (OSError, ValueError):
            full = None
        if full is None:   # never trust a changed result file; fall back to the committed summary
            full = {**result, "checks": [], "misconception_feedback": [], "result_file_untrusted": True}
        # Durable review queue: a review attempt completes its entry and schedules the next
        # one; a first independent pass schedules the first review. Ids are deterministic,
        # so a reconciled/replayed finalization cannot enqueue twice.
        if att.get("review_id"):
            self._complete_review(state, att, result["outcome"])
        elif result["outcome"] == "correct" and self._passed(state, node_id) and not any(
                q["node_id"] == node_id and q["status"] in cs.PENDING_REVIEW
                and q.get("capability_version") == self._cap_version(state, node_id) for q in state["review_queue"]):
            self._enqueue_review(state, node_id, source_attempt=att["attempt_id"], recalls=0, lapses=0)
        state["assessment_result"] = {"attempt_id": att["attempt_id"], "node_id": node_id, "item_id": att["item_id"],
                                      "kind": att.get("kind", "python"),
                                      "outcome": result["outcome"], "passed": result["passed"], "total": result["total"],
                                      "by_category": result["by_category"], "checks": full["checks"],
                                      "misconception_feedback": full["misconception_feedback"],
                                      "evidence_id": ev_id, "finalized_at": att["finalized_at"],
                                      "review_id": att.get("review_id"), "provisional": provisional,
                                      "grader_reliability": ("uncalibrated_ai" if result.get("grader") == "host_agent" else
                                                             "attributed_human" if result.get("grader") == "human" else "authored_checks"),
                                      "rubric": ({k: (full.get("rubric") or {}).get(k) for k in ("grader", "confidence", "version")}
                                                 if att.get("kind") == "free_text" else None),
                                      "independence_note_az": ("Cəhd zamanı tətbiqdə izah və ipucu bağlı idi. "
                                                               "Bu, kənar köməyin olmadığını sübut etmir.")}
        prof = state["learner_profile"]
        gaps = set(prof["gaps"])
        if result["outcome"] == "correct":
            gaps.discard(node_id)
        else:
            gaps.add(node_id)
        prof["gaps"] = sorted(gaps)
        prof["updated_at"] = self._now()
        state["active_attempt_id"] = None
        state["attempt_status"] = "finalized"
        state["assessment_item_version"] = None
        cs.set_phase(state, "feedback", self._now())
        state["resume_checkpoint"] = {"phase": "feedback", "route": f"#/result/{att['attempt_id']}", "node_id": node_id,
                                      "attempt_id": att["attempt_id"], "at": self._now()}
        state["next_mission"] = self._mission_after_exam(d, state, att, full, provisional=provisional)

    def submit_and_finalize(self, sid, attempt_id: str, *, expected_revision=None, idempotency_key=None):
        """UI convenience: durable steps; any failure leaves a resumable state."""
        steps = {"submit": self.submit_exam(sid, attempt_id, expected_revision=expected_revision,
                                            idempotency_key=idempotency_key)}
        steps["score"] = self.score_exam(sid, attempt_id)
        if steps["score"]["status"] == "scoring_failed":
            return {"status": "scoring_failed", "resumable": True, "steps": steps,
                    "reason": steps["score"].get("reason"), "revision": steps["score"]["revision"]}
        if steps["score"]["status"] == "awaiting_rubric":
            return {"status": "awaiting_rubric", "resumable": True, "steps": steps,
                    "note_az": "Açıq cavab rubrika ilə qiymətləndiriləcək; o vaxta qədər imtahan kilidi qalır.",
                    "revision": steps["score"]["revision"]}
        steps["finalize"] = self.finalize_exam(sid, attempt_id)
        return {"status": "finalized", "steps": steps, "assessment_result": steps["finalize"].get("assessment_result"),
                "next_mission": steps["finalize"].get("next_mission"), "revision": steps["finalize"]["revision"]}

    def judge_and_finalize(self, sid, attempt_id: str, judgment: dict, **kw):
        out = {"rubric": self.record_rubric(sid, attempt_id, judgment, **kw)}
        out["finalize"] = self.finalize_exam(sid, attempt_id)
        return {"status": "finalized", **out, "assessment_result": out["finalize"].get("assessment_result"),
                "next_mission": out["finalize"].get("next_mission"), "revision": out["finalize"]["revision"]}

    def abandon_exam(self, sid, attempt_id: str, reason: str | None = None, **kw):
        def fn(d, led, state):
            att = self._attempt(state, attempt_id)
            if att["status"] == "abandoned":
                return {"attempt_id": attempt_id, "status": "abandoned", "duplicate": True}, None, None
            cs.guard(state, "abandon_exam")
            if att["status"] not in {"active", "submitted", "scoring_failed", "awaiting_rubric"}:
                raise CourseStateError("phase_forbids_action", "a scored attempt must be finalized, not abandoned")
            # Everything that can fail is computed before the ledger append.
            has_lesson = bool(self._lesson(d, att["node_id"], state))
            mission = self._mission_retry(d, state, att["node_id"], "abandoned")
            att["status"] = "abandoned"
            att["abandoned_reason"] = (reason or "learner_exit")[:200]
            att["events"].append({"status": "abandoned", "at": self._now()})
            state["active_attempt_id"] = None
            state["attempt_status"] = "abandoned"
            state["assessment_item_version"] = None
            if att.get("review_id"):       # the review stays due; the exposed item is spent
                for q in state["review_queue"]:
                    if q["review_id"] == att["review_id"] and q["status"] == "in_progress":
                        q.update(status="scheduled", attempt_id=None, retries=int(q.get("retries", 0)) + 1)
            cs.set_phase(state, "teaching" if has_lesson else "course_generation", self._now())
            state["next_mission"] = mission
            led.append("observation", {"capability_id": att["node_id"], "attempt_id": attempt_id,
                                       "item_id": att["item_id"], "outcome": "unknown", "note": "exam_abandoned",
                                       "reason": att["abandoned_reason"], "correctness_checked": False},
                       {"source": "course_exam"})
            state["resume_checkpoint"] = {"phase": state["phase"],
                                          "route": f"#/learn/{att['node_id']}" if has_lesson else "#/",
                                          "node_id": att["node_id"], "attempt_id": None, "at": self._now()}
            return ({"attempt_id": attempt_id, "status": "abandoned", "counted_as_success": False,
                     "next_mission": state["next_mission"]},
                    {"event_type": "course_exam_abandoned", "attempt_id": attempt_id}, None)
        return self._mutate(sid, "abandon_exam", fn, request={'attempt': attempt_id}, **kw)

    def advance(self, sid, **kw):
        def fn(d, led, state):
            cs.guard(state, "advance_to_mission")
            cs.set_phase(state, "next_mission", self._now())
            state["resume_checkpoint"] = {"phase": "next_mission", "route": "#/mission", "node_id": state["active_node_id"],
                                          "attempt_id": None, "at": self._now()}
            return {"next_mission": state["next_mission"]}, {"event_type": "course_next_mission_shown"}, None
        return self._mutate(sid, "advance", fn, request={}, **kw)

    # ----------------------------------------------------------- missions
    def _fresh_count(self, d, state, node_id) -> int:
        return len(self._eligible(d, state, node_id))

    def _next_open_node(self, state, plan):
        for n in plan["nodes"]:
            if not self._passed(state, n["id"]) and self._available(state, n):
                return n
        return None

    def _initial_mission(self, d, state, plan) -> dict:
        n = self._next_open_node(state, plan)
        if n is None:
            return {"type": "course_review", "reason_az": "Bütün mərhələlər keçilib."}
        observed = journey.independent_diagnostic_successes([x for x in state["learner_profile"]["diagnostic"]
                    if x.get("node_id") == n["id"] and x.get("independent_observation") and x["outcome"] == "correct"
                    and x.get("confidence") in {"medium", "high"}
                    and (x.get("capability_version") == self._cap_version(state, n["id"])
                         or (x.get("capability_version") is None and not any(h.get("node_id") == n["id"] for h in state["capability_history"])))])
        mission = {"type": "start_node", "node_id": n["id"], "title": n["title"],
                   "reason_az": (f"“{n['title']}” yol xəritəsində ilk açıq mərhələdir"
                                 + (": ilkin şərti yoxdur." if not n.get("requires") else ": ilkin şərtləri keçilib.")),
                   "options_az": ["İzahdan başla (yeni başlayan üçün)",
                                  "Bunu bilirsənsə, birbaşa yeni tapşırıqla yoxla"],
                   "suggest_fast_track": bool(observed),
                   "uncertainty_az": "Başlanğıc profil təxminidir; ilk müstəqil nəticədən sonra yenilənəcək.",
                   "lesson_ready": self._lesson_gate(d, state, n["id"])["status"] == "ready",
                   "fresh_items": self._fresh_count(d, state, n["id"])}
        if not mission["lesson_ready"]:
            mission["blocked_on"] = _blocked_reason(self._lesson_gate(d, state, n["id"]))
        return mission

    def _plan_safe(self, d) -> dict:
        # Mission derivation runs after evidence is written (and inside reconcile),
        # so it must never raise; a missing roadmap degrades to an empty plan.
        p = d / "workspace" / "roadmap.json"
        try:
            return read_json(p) if p.exists() else {"nodes": []}
        except (OSError, ValueError):
            return {"nodes": []}

    @staticmethod
    def _title(plan, node_id) -> str:
        return next((n.get("title") or node_id for n in plan.get("nodes", []) if n.get("id") == node_id), node_id)

    def _mission_after_exam(self, d, state, att, full, *, provisional=False) -> dict:
        plan = self._plan_safe(d)
        node_id, outcome = att["node_id"], full["outcome"]
        title = self._title(plan, node_id)
        by_cat = full["by_category"]
        labels = {"core": "əsas", "edge": "kənar hal", "transfer": "transfer", "build": "icra"}
        tally = ", ".join(f"{labels.get(k, k)} {v['passed']}/{v['total']}" for k, v in sorted(by_cat.items()))
        review = next((q for q in state["review_queue"] if q["node_id"] == node_id and q["status"] == "scheduled"), None)
        when = _az_date(review["due_at"]) if review else None
        if outcome == "correct" and provisional:
            mission = {"type": "confirm_with_fresh_item", "node_id": node_id, "title": title,
                       "reason_az": (f"“{title}” üzrə açıq cavabın rubrikanın bütün meyarlarına cavab verdi. "
                                     "Mərhələni keçmiş saymaq üçün yeni tapşırıqda insan rəyi və ya müstəqil icra yoxlaması ilə təsdiq lazımdır."),
                       "uncertainty_az": "Eyni kalibrasiya olunmamış AI grader-in təkrar rəyləri müstəqil təsdiq sayılmır."}
        elif outcome == "correct":
            nxt = self._next_open_node(state, plan)
            if att.get("review_id"):
                lead = f"Təkrar yoxlama uğurlu oldu: “{title}” üzrə yeni tapşırığı yenə müstəqil həll etdin."
            else:
                lead = f"“{title}” üzrə görmədiyin tapşırığı müstəqil həll etdin ({tally})."
            mission = {"type": "next_node" if nxt else "course_review", "node_id": nxt["id"] if nxt else node_id,
                       "title": nxt["title"] if nxt else title,
                       "reason_az": lead + (f" İndi növbəti açıq mərhələyə keç: “{nxt['title']}”." if nxt
                                            else " Bütün mərhələlər keçilib; qalan iş təkrar yoxlamalardır."),
                       "uncertainty_az": ("Bu, bir müstəqil nəticədir — ustalıq hələ təsdiqlənməyib."
                                          + (f" {when} tarixindən sonra yeni bir tapşırıqla biliyin qalıb-qalmadığını "
                                             "yoxlayacağıq." if when else ""))}
            if review:
                mission["review"] = {"node_id": node_id, "review_id": review["review_id"], "due_at": review["due_at"]}
            if nxt:
                gate = self._lesson_gate(d, state, nxt["id"])
                mission["lesson_ready"] = gate["status"] == "ready"
                if not mission["lesson_ready"]:
                    mission["blocked_on"] = _blocked_reason(gate)
        else:
            mission = self._mission_retry(d, state, node_id, outcome, full=full, tally=tally)
            if att.get("review_id") and review:
                mission["reason_az"] += f" Təkrar yoxlama {when} tarixindən sonra yenidən açılacaq."
                mission["review"] = {"node_id": node_id, "review_id": review["review_id"], "due_at": review["due_at"]}
        return mission

    def _mission_retry(self, d, state, node_id, outcome, *, full=None, tally=None) -> dict:
        title = self._title(self._plan_safe(d), node_id)
        counts = state["misconception_counts"].get(node_id, {})
        fired = (full or {}).get("misconception_feedback", [])
        recurring = [m for m in fired if counts.get(m["id"], 0) >= 2]
        failures = journey.consecutive_failures(state, node_id, self._cap_version(state, node_id))
        if outcome in {"incorrect", "partial"} and len(failures) >= journey.RECOVERY_FAILURE_LIMIT:
            recovery = {"status": "open", "triggering_attempt_id": failures[-1], "failure_attempt_ids": failures,
                        "capability_version": self._cap_version(state, node_id), "opened_at": self._now(), "policy": "recovery-v1"}
            self._progress(state, node_id)["recovery"] = recovery
            return {"type": "recovery_check", "node_id": node_id, "title": title, "blocked_on": "recovery_required",
                    "reason_az": "Üç müstəqil cəhd keçmədi. Eyni dövrəni təkrarlamadan şərti, ilkin bacarığı, alət və iş yükünü yoxlayıb yeni plan razılaşdıraq.",
                    "uncertainty_az": "Bu, qabiliyyət və ya səylə bağlı hökm deyil. Üç cəhd həddi dəyişdirilə bilən məhsul qaydasıdır.",
                    "recovery": recovery}
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

    # ---------------------------------------------------------- review queue
    def _require_reviews_first(self, d, state):
        due = self._due_reviews(d, state)
        if due:
            raise CourseStateError("review_required", ", ".join(r["node_id"] for r in due))

    def _enqueue_review(self, state, node_id, *, source_attempt, recalls, lapses, basis="scheduled", due_at=None):
        """Schedule the next review of a capability (``scheduling.half_life_days`` policy).

        Deterministic id per (node, source attempt, capability version): replays and
        reconciled finalizations cannot duplicate an entry. One pending entry per node.
        """
        now_dt = self._dt()
        cap = self._cap_version(state, node_id)
        rid = cs.digest("rv", {"node": node_id, "source": source_attempt, "cap": cap})
        existing = next((q for q in state["review_queue"] if q["review_id"] == rid), None)
        if existing:
            return existing
        for q in state["review_queue"]:
            if q["node_id"] == node_id and q["status"] == "scheduled":
                q.update(status="superseded", closed_at=now_dt.isoformat())
        interval = half_life_days(recalls, lapses)
        entry = {"review_id": rid, "node_id": node_id, "capability_version": cap, "source_attempt_id": source_attempt,
                 "created_at": now_dt.isoformat(),
                 "due_at": due_at or (now_dt + timedelta(days=interval)).isoformat(),
                 "interval_days": interval, "recalls": recalls, "lapses": lapses, "status": "scheduled",
                 "attempt_id": None, "retries": 0, "basis": basis}
        state["review_queue"].append(entry)
        return entry

    def _complete_review(self, state, att, outcome):
        entry = next((q for q in state["review_queue"] if q["review_id"] == att["review_id"]), None)
        if entry is None or entry["status"] == "completed":
            return
        now = self._now()
        entry.update(status="completed", completed_at=now, outcome=outcome, attempt_id=att["attempt_id"])
        node_id = att["node_id"]
        if not self._passed(state, node_id):
            return
        prog = self._progress(state, node_id)
        if outcome == "correct" and ((att.get("result") or {}).get("confidence") == "low"
                                     or (att.get("kind") == "free_text" and (att.get("result") or {}).get("grader") == "host_agent")):
            # A low-confidence or uncalibrated AI judgment does not establish retention.
            prog["retention"] = {"status": "uncertain", "at": now, "review_id": entry["review_id"]}
            self._enqueue_review(state, node_id, source_attempt=att["attempt_id"], recalls=entry["recalls"],
                                 lapses=entry["lapses"], basis="retry_uncertain")
        elif outcome == "correct":
            prog["retention"] = {"status": "retained", "at": now, "review_id": entry["review_id"]}
            self._enqueue_review(state, node_id, source_attempt=att["attempt_id"],
                                 recalls=entry["recalls"] + 1, lapses=entry["lapses"])
        else:   # a lapse shortens the next interval; the pass itself stays (history), flagged
            prog["retention"] = {"status": "lapsed", "at": now, "review_id": entry["review_id"]}
            self._enqueue_review(state, node_id, source_attempt=att["attempt_id"],
                                 recalls=entry["recalls"], lapses=entry["lapses"] + 1, basis="retry_after_lapse")

    @staticmethod
    def _depths(plan) -> dict:
        """Prerequisite depth per node (longest ``requires`` chain); cycles are cut."""
        req = {n["id"]: n.get("requires") or [] for n in plan.get("nodes", [])}
        memo = {}

        def depth(nid, trail=()):
            if nid in memo:
                return memo[nid]
            if nid in trail:
                return 0
            memo[nid] = 1 + max((depth(r, trail + (nid,)) for r in req.get(nid, []) if r in req), default=-1)
            return memo[nid]
        return {nid: depth(nid) for nid in req}

    def _due_reviews(self, d, state, plan=None) -> list[dict]:
        """Due reviews for current capabilities, prerequisites first, then oldest due."""
        if state["phase"] in cs.TEACHING_LOCKED_PHASES:
            return []
        now = self._dt()
        plan = plan or self._plan_safe(d)
        depth = self._depths(plan)
        out = []
        for q in state["review_queue"]:
            if q["status"] != "scheduled":
                continue
            due = _utc(q.get("due_at"))
            cap = self._cap_version(state, q["node_id"])
            node = next((n for n in plan.get("nodes", []) if n.get("id") == q["node_id"]), None)
            if (due is None or due > now or node is None or (cap and q.get("capability_version") != cap)
                    or not self._passed(state, q["node_id"]) or not self._available(state, node)):
                continue   # kept scheduled; surfaced again once its prerequisites hold
            out.append({"review_id": q["review_id"], "node_id": q["node_id"], "title": self._title(plan, q["node_id"]),
                        "due_at": q["due_at"], "overdue_hours": round((now - due).total_seconds() / 3600, 1),
                        "interval_days": q["interval_days"], "fresh_items": self._fresh_count(d, state, q["node_id"])})
        out.sort(key=lambda r: (depth.get(r["node_id"], 0), r["due_at"]))
        return out

    def start_review(self, sid, node_id: str, **kw):
        """Start a due review: a fresh, unassisted item under the normal exam lock."""
        def fn(d, led, state):
            resumed = self._resume_attempt(d, state, node_id)
            if resumed:
                return resumed, None, None
            cs.guard(state, "start_exam")
            due = [r for r in self._due_reviews(d, state) if r["node_id"] == node_id]
            if not due:
                raise CourseStateError("review_not_due", f"no due review for {node_id}")
            entry = next(q for q in state["review_queue"] if q["review_id"] == due[0]["review_id"])
            result, event, files = self._open_attempt(d, state, node_id, fast_track=True, review=entry)
            entry.update(status="in_progress", attempt_id=result["attempt_id"], started_at=self._now())
            return result, {**event, "event_type": "course_review_started"}, files
        return self._mutate(sid, "start_review", fn, request={'node': node_id}, **kw)

    # ------------------------------------------------- grounding + freshness
    def reground(self, sid, node_id: str, **kw):
        """Rebind a lesson to changed-but-valid sources, only if every quote still verifies."""
        def fn(d, led, state):
            cs.guard(state, "reground")
            self._node(self._plan(d), node_id)
            lesson = self._lesson(d, node_id, state)
            if not lesson:
                raise CourseStateError("lesson_missing")
            gate = self._lesson_gate(d, state, node_id, lesson)
            if gate["status"] == "capability_changed":
                raise CourseStateError("capability_changed")
            if gate["status"] == "ready":
                return {"node_id": node_id, "status": "already_valid"}, None, None
            if not gate.get("rebindable"):
                return {"node_id": node_id, "status": "needs_reauthoring", "problems": gate["problems"]}, None, None
            now = self._now()
            manifest = self._manifest(d) or {}
            shas = {r["source_id"]: r.get("content_sha256") for r in manifest.get("resources", []) if r.get("source_id")}
            binding = state["lesson_bindings"][node_id]
            binding["history"] = (binding.get("history") or []) + [
                {k: binding.get(k) for k in ("sources", "bound_at", "basis")}]
            binding.update(sources={s_id: shas.get(s_id) for s_id in sorted(cc.cited_sources(lesson))},
                           bound_at=now, basis="regrounded")
            return ({"node_id": node_id, "status": "rebound", "sources": binding["sources"]},
                    {"event_type": "course_grounding_rebound", "node_id": node_id,
                     "manifest_version": state["source_manifest_version"]}, None)
        return self._mutate(sid, "reground", fn, request={'node': node_id}, **kw)

    def review_freshness(self, sid, node_id: str, item_id: str, decision: str, rationale: str | None = None, **kw):
        """Explicit decision on a near-duplicate item. ``distinct`` makes it eligible, but
        evidence from it stays capped at medium (freshness rests on a judgment)."""
        if decision not in {"distinct", "duplicate"}:
            raise CourseStateError("phase_forbids_action", "decision must be distinct or duplicate")
        if decision == "distinct" and (not isinstance(rationale, str) or len(rationale.strip()) < 30):
            raise CourseStateError("phase_forbids_action", "explain concretely what differs (>= 30 characters)")

        def fn(d, led, state):
            cs.guard(state, "review_freshness")
            item = next((it for it in self._items(d, node_id, state) if it["item_id"] == item_id), None)
            if item is None:
                raise CourseStateError("phase_forbids_action", "unknown item")
            raw = cc.classify_freshness(cc.fingerprint(item), self._exposures(state, node_id))
            if raw["status"] != "needs_review":
                raise CourseStateError("phase_forbids_action", f"item is {raw['status']}; only near-duplicates are reviewed")
            state["freshness_reviews"].setdefault(node_id, {})[item_id] = {
                "decision": decision, "rationale": (rationale or "").strip()[:600], "reviewer": "host_agent",
                "at": self._now(), "item_version": item["item_version"], "matches": raw["matches"],
                "policy": raw["policy"]}
            return ({"node_id": node_id, "item_id": item_id, "decision": decision},
                    {"event_type": "course_item_freshness_reviewed", "node_id": node_id, "item_id": item_id,
                     "decision": decision}, None)
        return self._mutate(sid, "review_freshness", fn, request={'node': node_id, 'item': item_id, 'decision': decision, 'rationale': rationale}, **kw)

    # -------------------------------------------------------- commitments
    def _apply_commitments(self, state, c: dict):
        if not isinstance(c, dict):
            raise CourseStateError("phase_forbids_action", "commitments must be an object")
        now = self._now()
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
        return self._mutate(sid, "record_commitments", fn, request={'commitments': commitments}, **kw)

    def respond_accountability(self, sid, blocker: str, note: str | None = None, **kw):
        if blocker not in BLOCKERS:
            raise CourseStateError("phase_forbids_action", f"blocker must be one of {BLOCKERS}")

        def fn(d, led, state):
            cs.guard(state, "accountability_response")
            acc = state["accountability"]
            if not acc["open"]:
                raise CourseStateError("phase_forbids_action", "no open discrepancy")
            item = acc["open"]
            item.update({"blocker": blocker, "note": (note or "")[:500], "responded_at": self._now()})
            due = (self._dt() + timedelta(days=2)).date().isoformat()
            checkpoint = {"id": new_id("cm"), "kind": "recovery_checkpoint", "node_id": item["recovery_task"].get("node_id"),
                          "due": due, "description": item["recovery_task"]["text_az"], "status": "active",
                          "agreed_at": self._now(), "agreed_by": "learner", "agreed_text": "Bərpa tapşırığı"}
            state["learning_commitments"].append(checkpoint)
            acc["history"].append(item)
            acc["open"] = None
            return ({"recorded": True, "recovery_checkpoint": checkpoint},
                    {"event_type": "course_accountability_response", "blocker": blocker}, None)
        return self._mutate(sid, "respond_accountability", fn, request={'blocker': blocker, 'note': note}, **kw)

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
        iv = _validate_interval(interval, self._dt())
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
        now = now or self._dt()
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
            now = now or self._dt()
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
                    "id": new_id("acc"), "signature": signature, "opened_at": self._now(), "findings": findings,
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
        promotion = self._studio_hook(sid, "try_auto_promote")
        view = self.resume(sid)
        view["agent_directive"] = agent_directive(view)
        view["teaching_allowed"] = bool(view["roadmap"]["approved"] and not view["teaching_locked"] and not view["due_reviews"]
                                        and view["generation_readiness"]["ready"]
                                        and (view.get("next_mission") or {}).get("blocked_on") != "recovery_required")
        receipt = self._studio_hook(sid, "turn_receipt")
        if receipt is not None:
            view["rules_receipt"] = receipt
            if receipt.get("blocks_teaching"):
                # A non-waivable rule fails for a PROMOTED build: the agent must see it before teaching on that build.
                view["teaching_allowed"] = False
                view["agent_directive"] = receipt["agent_directive"] + " " + view["agent_directive"]
        if promotion is not None and promotion.get("attempted"):
            view["studio_promotion"] = promotion
        return view

    def _studio_hook(self, sid, name: str):
        """Course Studio turn hooks (auto-promotion, rules receipt). A session without a studio is untouched and a
        hook failure never blocks a learner turn: it is reported instead."""
        d = self.sk.dir(sid)
        if not (d / "studio").is_dir():
            return None
        from .studio import StudioKernel
        from .studio_store import StudioError
        try:
            return getattr(StudioKernel(self.sk.workspace, clock=self.clock), name)(sid)
        except StudioError as e:
            return {"status": "studio_error", "error": e.code, "detail": e.detail, "attempted": name == "try_auto_promote"}
        except (OSError, ValueError, KeyError) as e:
            return {"status": "studio_error", "error": type(e).__name__, "detail": str(e)[:200],
                    "attempted": name == "try_auto_promote"}

    def lesson_view(self, sid, node_id) -> dict:
        """Lesson overview for the UI (objective, criteria, practice brief). Locked during exams."""
        d = self.sk.dir(sid)
        with session_lock(d):
            d, _led, state = self._open(sid)
            if state["phase"] in cs.TEACHING_LOCKED_PHASES:
                raise CourseStateError("exam_active")
            if state["approved_roadmap_version"] != state["roadmap_version"]:
                raise CourseStateError("roadmap_not_approved")
            readiness = self._generation_readiness(d, state, self._plan_safe(d))
            if readiness["required"] and not readiness["ready"]:
                raise CourseStateError(readiness["blockers"][0])
            self._require_reviews_first(d, state)
            node = self._node(self._plan(d), node_id)
            lesson = self._lesson(d, node_id, state)
            gate = self._lesson_gate(d, state, node_id, lesson) if lesson else None
            summary = cc.public_lesson_summary(lesson, gate)
            out = {"node": {k: node.get(k) for k in ("id", "title", "task", "outcomes", "skills", "practice",
                                                    "exam_domain", "requires")}, "lesson": summary,
                   "progress": state["node_progress"].get(node_id), "phase": state["phase"]}
            if lesson and gate["status"] == "ready":
                pr = lesson["practice"]
                if cc.item_kind(pr) == "python":
                    out["practice"] = {"kind": "python", "prompt_md": pr["prompt_md"], "entry": pr["entry"],
                                       "file": str(self._module(d, node_id) / "submission" / pr["entry"]),
                                       "hint_count": len(pr["hints"]),
                                       "checks": [{"id": c["id"], "name": c["name"]} for c in pr["checks"]]}
                else:
                    out["practice"] = {"kind": cc.item_kind(pr), "prompt_md": pr["prompt_md"],
                                       "scenario_md": pr.get("scenario_md"), "hint_count": len(pr["hints"]),
                                       "questions": cc.public_questions(pr["questions"])}
                out["self_explanation_prompt"] = lesson["self_explanation_prompt"]
                out["practice_first"] = {"walkthrough": bool(lesson.get("walkthrough")), "ladder": bool(lesson.get("ladder")),
                                         "hint_ceiling": lesson.get("hint_ceiling")}
            return out

    def _view(self, d, led, state, eng=None) -> dict:
        plan = None
        p = d / "workspace" / "roadmap.json"
        if p.exists():
            plan = read_json(p)
        manifest = self._manifest(d)
        locked = state["phase"] in cs.TEACHING_LOCKED_PHASES
        nodes = []
        display_plan = plan if state["roadmap_version"] else None
        for n in (display_plan or {}).get("nodes", []):
            nid = n["id"]
            lesson = self._lesson(d, nid, state)
            passed = self._passed(state, nid)
            status = ("passed" if passed else "active" if nid == state["active_node_id"]
                      else "available" if self._available(state, n) else "locked")
            summary = ({"status": "locked_during_exam"} if locked else
                       cc.public_lesson_summary(lesson, self._lesson_gate(d, state, nid, lesson, manifest) if lesson else None))
            nodes.append({**{k: n.get(k) for k in ("id", "title", "task", "requires", "outcomes", "skills", "practice",
                                                   "source_refs", "exam_domain", "pitfalls", "stage", "estimated_minutes")},
                          "status": status, "capability_version": self._cap_version(state, nid),
                          "skill_evidence": journey.skill_evidence(state, nid, self._cap_version(state, nid)) if not locked else None,
                          # During an exam no teaching material (criteria, feedback, help history) leaves the runtime.
                          "lesson": summary,
                          "fresh_items": self._fresh_count(d, state, nid),
                          "progress": None if locked else state["node_progress"].get(nid)})
            nodes[-1]["prerequisites_valid"] = self._available(state, n)
            nodes[-1]["effective_passed"] = passed and nodes[-1]["prerequisites_valid"]
            if passed and not nodes[-1]["prerequisites_valid"] and not locked:
                nodes[-1]["skill_evidence"]["status"] = "upstream_invalid"
                nodes[-1]["skill_evidence"]["label_az"] = "Əvvəlki öz nəticən tarixçədə qalır; ilkin şərt dəyişdiyi üçün downstream giriş və cari tamamlama qüvvədə deyil."
        att = state["attempts"].get(state["active_attempt_id"]) if state["active_attempt_id"] else None
        eng = eng or self.engagement(led)
        mission = dict(state["next_mission"]) if state["next_mission"] and not locked else None
        if mission and mission.get("node_id") and mission.get("type") != "course_review":
            # Readiness is recomputed from current content, never frozen at decision time.
            nid = mission["node_id"]
            gate = self._lesson_gate(d, state, nid, None, manifest)
            mission["lesson_ready"] = gate["status"] == "ready"
            mission["fresh_items"] = self._fresh_count(d, state, nid)
            if mission.get("type") != "recovery_check":
                mission.pop("blocked_on", None)
            if mission.get("type") == "recovery_check":
                mission["blocked_on"] = "recovery_required"
            elif not mission["lesson_ready"] and mission["type"] != "confirm_with_fresh_item":
                mission["blocked_on"] = _blocked_reason(gate)
            elif mission["type"] != "next_node" and mission["fresh_items"] == 0:
                mission["blocked_on"] = "no_fresh_item"
        due = self._due_reviews(d, state, plan or {"nodes": []})
        if due:
            for node in nodes:
                node["lesson"] = {"status": "review_required"}
        if due and mission is not None:
            # A due review outranks the stored mission at every decision point (durable queue).
            r = due[0]
            mission = {"type": "due_review", "node_id": r["node_id"], "title": r["title"], "review_id": r["review_id"],
                       "due_at": r["due_at"], "fresh_items": r["fresh_items"],
                       "reason_az": (f"“{r['title']}” üzrə təkrar yoxlamanın vaxtı çatıb ({_az_date(r['due_at'])}). "
                                     "Yeni, görmədiyin tapşırıqla bacarığın qalıb-qalmadığını yoxlayacağıq; "
                                     "əvvəlcədən izah verilmir."),
                       "uncertainty_az": ("Təkrar yoxlama bilikin qalıcılığını yoxlayır; bir nəticə tək başına "
                                          "uzunmüddətli yadda saxlamanı sübut etmir."),
                       "deferred": {k: mission.get(k) for k in ("type", "node_id", "title")}}
            if r["fresh_items"] == 0:
                mission["blocked_on"] = "no_fresh_item"
            recovery = (state["node_progress"].get(r["node_id"]) or {}).get("recovery") or {}
            if recovery.get("status") == "open" and recovery.get("capability_version") == self._cap_version(state, r["node_id"]):
                mission.update(type="recovery_check", blocked_on="recovery_required", recovery=recovery,
                               reason_az="Təkrar yoxlamalar keçmədi. Yeni müstəqil yoxlamadan əvvəl şərti və ilkin bacarıqları araşdırıb konkret dəyişmiş plan razılaşdıraq.")
        approved = bool(state["approved_roadmap_version"]) and state["approved_roadmap_version"] == state["roadmap_version"]
        readiness = self._generation_readiness(d, state, display_plan)
        permitted = [a for a in cs.permitted_actions(state) if not due or a not in REVIEW_FIRST_ACTIONS]
        if (mission or {}).get("type") == "recovery_check":
            permitted = [a for a in permitted if a not in {"start_mission", "start_exam"}]
        if self._intake_view(state)["required"] and not self._intake_view(state)["ready_to_plan"]:
            permitted = [a for a in permitted if a not in {"approve_roadmap", "install_content", "start_mission", "start_exam"}]
        if readiness["required"] and not readiness["ready"]:
            permitted = [a for a in permitted if a not in {"propose_roadmap", "approve_roadmap", "install_content", "start_mission", "start_exam"}]
        return {
            "ui_schema_version": cs.UI_SCHEMA_VERSION, "state_schema_version": cs.SCHEMA_VERSION,
            "session_id": state["session_id"], "revision": state["revision"], "updated_at": state["updated_at"],
            "phase": state["phase"], "phase_label_az": PHASE_LABEL_AZ[state["phase"]],
            "phase_entered_at": state["phase_entered_at"],
            "goal": state["goal"], "learner_profile": state["learner_profile"],
            "intake": self._intake_view(state),
            "generation_readiness": readiness,
            "roadmap": {"topic": (plan or {}).get("topic"), "goal": (plan or {}).get("goal"),
                        "status": (plan or {}).get("status"), "sources": (plan or {}).get("sources", []),
                        "version": state["roadmap_version"], "approved_version": state["approved_roadmap_version"],
                        "approved": approved, "approval_event": state["roadmap_approval_event"],
                        "review_required": state["review_required"], "nodes": nodes},
            "active_node_id": state["active_node_id"],
            "active_attempt": self._public_attempt(d, att) if att else None,
            "attempt_status": state["attempt_status"], "assessment_item_version": state["assessment_item_version"],
            "support_provenance": state["support_provenance"],
            "assessment_result": None if locked else state["assessment_result"],
            "next_mission": mission, "resume_checkpoint": state["resume_checkpoint"],
            "due_reviews": due,
            "review_queue": [{k: q.get(k) for k in ("review_id", "node_id", "status", "due_at", "interval_days", "recalls",
                                                    "lapses", "outcome", "completed_at", "attempt_id", "retries", "basis",
                                                    "reason")} for q in state["review_queue"]],
            "capability_history": state["capability_history"][-50:],
            "learning_commitments": state["learning_commitments"], "accountability": state["accountability"],
            "engagement": eng, "evidence_refs": state["evidence_refs"],
            "sources": {"manifest_version": state["source_manifest_version"],
                        "notebook_id": (manifest or {}).get("notebook_id"), "complete": (manifest or {}).get("complete"),
                        "summary": (manifest or {}).get("summary"), "resources": state["source_refs"],
                        "unexpected": (manifest or {}).get("unexpected_sources", [])},
            "course_version": state["course_version"], "content_versions": state["content_versions"],
            "permitted_actions": permitted,
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
            attempts = [{k: a.get(k) for k in ("attempt_id", "node_id", "item_id", "item_version", "kind", "status",
                                               "started_at", "submitted_at", "finalized_at", "fast_track", "result",
                                               "evidence_id", "events", "abandoned_reason", "capability_version",
                                               "content_fingerprint", "freshness_basis", "review_id", "rubric")}
                        for a in state["attempts"].values()]
            return {"export_schema_version": 2, "exported_at": self._now(), "course": view,
                    "approval_history": state["approval_history"], "attempts": attempts, "evidence": evidence,
                    "capabilities": state["capabilities"], "capability_history": state["capability_history"],
                    "review_queue": state["review_queue"], "freshness_reviews": state["freshness_reviews"],
                    "exposure_records": {n: [{k: e.get(k) for k in ("item_id", "kind", "content_fingerprint",
                                                                     "behavior_fingerprint", "attempt_id", "at", "unknown")}
                                             for e in recs] for n, recs in state["exposure_records"].items()},
                    "migration_log": state["migration_log"], "ledger_integrity": led.integrity()}

    # --------------------------------------------------------- UI regeneration
    def frontend_status(self, sid) -> dict:
        """Read-only: does the session's frontend source match the shipped template? A session built before a template
        change keeps serving its old bundle (``UI_SCHEMA_VERSION`` is unchanged), so a promoted studio build would show
        without the walkthrough, ladder and reveal controls until the owner regenerates and rebuilds the UI."""
        src = self.sk.dir(sid) / "workspace" / "frontend" / "src"
        if not src.is_dir():
            return {"present": False, "outdated": False}
        template = Path(__file__).resolve().parents[1] / "templates" / "react" / "src"
        outdated = []
        for f in sorted(p for p in template.rglob("*") if p.is_file()):
            mine = src / f.relative_to(template)
            try:
                same = mine.is_file() and mine.read_bytes() == f.read_bytes()
            except OSError:
                same = False
            if not same:
                outdated.append(f.relative_to(template).as_posix())
        return {"present": True, "outdated": bool(outdated), "files": outdated[:10],
                "next_step": (f"alearn course regenerate-ui --session {sid}, then `alearn course build-ui --session {sid}`: "
                              "the learner's app predates this build's walkthrough, hint ladder and reveal controls")
                if outdated else None}

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
_DRY_ERRORS = (cc.ContentError, AttributeError, TypeError, KeyError, ValueError, IndexError, RecursionError, OverflowError)


def _dry_message(e: Exception) -> str:
    return str(e) if isinstance(e, cc.ContentError) else f"malformed_payload: {type(e).__name__}: {e}"


def agent_directive(view: dict) -> str:
    """The one instruction the host agent should follow for this course view (pure)."""
    directive = AGENT_DIRECTIVES[view["phase"]]
    if view["intake"]["required"] and not view["intake"]["ready_to_plan"]:
        directive = ("Goal-first interviewer: " + view["intake"]["instruction_az"]
                     + " Prepare one scoped task via course diagnostic-probe, then judge the actual captured response via course diagnostic-judge; never fabricate learner work.")
    elif view["generation_readiness"]["required"] and not view["generation_readiness"]["ready"]:
        directive = ("CLI-first preparation: resolve " + ", ".join(view["generation_readiness"]["blockers"])
                     + ". Ask the learner for exact goal/target clarification and actual qualified skip consent when needed; verify readable resources before proposing personalized HTML. Do not fabricate assent, diagnostic work or sources.")
    elif (view.get("next_mission") or {}).get("type") == "recovery_check":
        directive = "Do not repeat the failed loop. Ask one neutral blocker question and agree a concrete changed recovery plan via course recovery; agreement is not completion evidence."
    return directive


def _ref(result: dict) -> dict:
    keep = ("attempt_id", "status", "node_id", "kind", "revealed", "roadmap_version", "content_version", "manifest_version")
    return {k: result[k] for k in keep if k in result and isinstance(result[k], (str, int, bool, type(None)))}


def _request_digest(request) -> str | None:
    return None if request is None else cs.digest("rq", request)


def _blocked_reason(gate: dict) -> str:
    return {"missing": "lesson_content_missing", "capability_changed": "capability_changed",
            "grounding_invalid": "grounding_invalid"}.get(gate["status"], gate["status"])


_AZ_MONTHS = ("yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avqust", "sentyabr", "oktyabr",
              "noyabr", "dekabr")


def _az_date(ts: str | None) -> str:
    t = _utc(ts)
    return f"{t.day} {_AZ_MONTHS[t.month - 1]}, {t:%H:%M} UTC" if t else "?"


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _validate_judgment(j, item, response: str) -> dict:
    """Shape + evidence rules for a rubric judgment (see ``CourseKernel.record_rubric``)."""
    def bad(detail):
        raise CourseStateError("rubric_required", detail)
    if not isinstance(j, dict):
        bad("judgment must be an object")
    if j.get("grader") not in RUBRIC_GRADERS:
        bad(f"grader must be one of {RUBRIC_GRADERS}")
    if j.get("confidence") not in RUBRIC_CONFIDENCE:
        bad(f"confidence must be one of {RUBRIC_CONFIDENCE}")
    rationale = j.get("rationale")
    if not isinstance(rationale, str) or not 20 <= len(rationale.strip()) <= 2000:
        bad("rationale must be 20-2000 characters")
    criteria = j.get("criteria")
    ids = [c["id"] for c in item["rubric"]]
    if not isinstance(criteria, dict) or not all(isinstance(k, str) for k in criteria) or set(criteria) != set(ids):
        bad(f"judge every rubric criterion exactly once: {ids}")
    resp = _ws(response)
    out = {}
    for cid in ids:
        v = criteria[cid]
        if not isinstance(v, dict) or not isinstance(v.get("met"), bool):
            bad(f"criterion {cid} needs met: true|false")
        quote = v.get("evidence_quote")
        if v["met"] and (not isinstance(quote, str) or len(quote.strip()) < 12 or _ws(quote) not in resp):
            bad(f"criterion {cid}: a met criterion must quote at least 12 characters of the learner's own response")
        out[cid] = {"met": v["met"], "evidence_quote": quote.strip()[:300] if isinstance(quote, str) else None}
    mis = j.get("misconceptions") or []
    known = {m["id"] for m in item["misconceptions"]}
    if not isinstance(mis, list) or not all(isinstance(m, str) for m in mis) or not set(mis) <= known:
        bad(f"misconceptions must be among {sorted(known)}")
    return {"grader": j["grader"], "confidence": j["confidence"], "rationale": rationale.strip(),
            "criteria": out, "misconceptions": sorted(set(mis))}


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


def _validate_interval(raw: dict, now: datetime) -> dict:
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
    if phase not in AGENT_DIRECTIVES:
        return None
    # The course's capability versions and review queue are the authority for a course
    # session; the generic evidence driver would schedule reviews from a second policy.
    now = datetime.now(UTC)
    progress = state.get("node_progress") or {}
    caps = state.get("capabilities") or {}
    due = sorted({q.get("node_id") for q in state.get("review_queue") or []
                  if q.get("status") == "scheduled" and (_utc(q.get("due_at")) or now + timedelta(days=1)) <= now
                  and (progress.get(q.get("node_id")) or {}).get("passed")
                  and q.get("capability_version") == (caps.get(q.get("node_id")) or {}).get("version", q.get("capability_version"))})
    if due:
        return {"action": "course_due_review", "phase": phase, "nodes": due, "reason": "spaced_review_due",
                "instruction": ("Offer the due review first (`alearn course review-start --node <id>`): a fresh, "
                                "unassisted item under the exam lock; do not re-teach before it.")}
    return {"action": "course_turn", "phase": phase, "reason": "course_state_is_authoritative",
            "instruction": AGENT_DIRECTIVES[phase] + " Hydrate with `alearn course turn`."}

"""Explicit, versioned course state machine (pure: no I/O).

The durable truth stays the session's hash-chained ledger. The action layer
(``course.py``) commits every transition through ``local_state.commit_state``,
which journals the complete ``course-state.json`` before materializing it, so
a crash replays to the last committed revision. This module only defines the
schema, migrations, permitted transitions, consistency checks and roadmap
versioning so the action layer, the HTTP bridge and the tests share one
definition instead of re-deriving rules from prose.
"""
from __future__ import annotations

import copy
import hashlib
import json

SCHEMA_VERSION = 2
UI_SCHEMA_VERSION = 2

PHASES = (
    "onboarding",                 # goal + brief provisional diagnostic
    "source_preparation",         # NotebookLM import + per-resource validation
    "roadmap_awaiting_approval",  # roadmap proposed; course generation blocked
    "course_generation",          # approved; lesson/item content being authored
    "teaching",                   # explanation, worked example, self-explanation
    "practice",                   # guided practice with hints + visible checks
    "exam_active",                # frozen item; no teaching or hints
    "assessment_pending",         # submitted; scoring/finalization resumable
    "feedback",                   # finalized result + specific feedback
    "next_mission",               # evidence-derived next mission shown
)
ATTEMPT_STATUSES = ("none", "active", "submitted", "scoring_failed", "awaiting_rubric", "scored", "finalized",
                    "abandoned")
OPEN_ATTEMPT_STATUSES = frozenset({"active", "submitted", "scoring_failed", "awaiting_rubric", "scored"})
REVIEW_STATUSES = ("scheduled", "in_progress", "completed", "superseded", "invalidated")
PENDING_REVIEW = frozenset({"scheduled", "in_progress"})
TEACHING_LOCKED_PHASES = frozenset({"exam_active", "assessment_pending"})
_AFTER_APPROVAL = frozenset(PHASES[3:])
_NOT_DURING_EXAM = frozenset(PHASES) - TEACHING_LOCKED_PHASES

# action -> phases it may start from. Anything absent is denied (fail closed).
ALLOWED: dict[str, frozenset] = {
    "record_goal": frozenset({"onboarding", "source_preparation", "roadmap_awaiting_approval"}),
    "confirm_goal": frozenset({"onboarding", "source_preparation", "roadmap_awaiting_approval"}),
    "record_profile": _NOT_DURING_EXAM,
    "record_recovery": _NOT_DURING_EXAM,
    "begin_sources": frozenset({"onboarding", "source_preparation"}),
    "record_sources": frozenset({"source_preparation", "roadmap_awaiting_approval", "course_generation",
                                 "teaching", "practice", "feedback", "next_mission"}),
    # Onboarding included: a blocked source step (login, missing book) must not block
    # independent roadmap work; unvalidated sources stay visible in the manifest.
    "propose_roadmap": frozenset({"onboarding", "source_preparation", "roadmap_awaiting_approval",
                                  "course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "approve_roadmap": frozenset({"roadmap_awaiting_approval"}),
    "install_content": frozenset({"course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "start_mission": frozenset({"course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "review_freshness": frozenset({"course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "reground": frozenset({"course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "help": frozenset({"teaching", "practice"}),
    "self_explanation": frozenset({"teaching", "practice"}),
    "begin_practice": frozenset({"teaching", "practice"}),
    "practice_check": frozenset({"practice"}),
    "practice_answer": frozenset({"practice"}),
    "start_exam": frozenset({"course_generation", "teaching", "practice", "feedback", "next_mission"}),
    "save_answers": frozenset({"exam_active"}),
    "submit_exam": frozenset({"exam_active"}),
    "score_exam": frozenset({"assessment_pending"}),
    "finalize_exam": frozenset({"assessment_pending"}),
    "record_rubric": frozenset({"assessment_pending"}),
    "abandon_exam": frozenset({"exam_active", "assessment_pending"}),
    "advance_to_mission": frozenset({"feedback"}),
    "record_commitments": _NOT_DURING_EXAM,
    "accountability_response": _NOT_DURING_EXAM,
}
REQUIRES_APPROVAL = frozenset({"install_content", "start_mission", "help", "self_explanation", "begin_practice",
                               "practice_check", "practice_answer", "start_exam", "review_freshness", "reground"})

# Learner-facing (Azerbaijani) refusal reasons; codes are stable for clients/tests.
REASONS_AZ = {
    "goal_required": "Əvvəl konkret məqsədi dəqiqləşdirək; fərdiləşdirilmiş HTML hələ yaradılmır.",
    "background_required": "Əvvəl bu işə aid təcrübəni dəqiqləşdirək.",
    "goal_clarification_required": "Məqsəd və planın çərçivəsi səninlə dəqiqləşdirilib təsdiqlənməlidir.",
    "target_required": "Sertifikatın dəqiq rəsmi adı, kodu və məqsədləri hələ dəqiqləşdirilməyib.",
    "provisional_skip_confirmation_required": "Diagnostic skip səbəbi saxlanıb. Yoxlamasız planın ilkin olduğunu anlayaraq davam etməyə ayrıca razılıq lazımdır; bacarıq nəticəsi yaranmır.",
    "sources_required": "Plan üçün mənbələr hələ tam yoxlanmayıb; fərdiləşdirilmiş roadmap HTML yaradılmır.",
    "sources_unreadable": "Mənbə mətni oxunaqlı, cari və hash-lə təsdiqlənmiş deyil; yenidən yoxlama lazımdır.",
    "roadmap_source_refs_required": "Hər roadmap mərhələsini uyğun yoxlanmış mənbə ilə bağla.",
    "roadmap_sources_invalid": "Roadmap-da göstərilən mənbə mövcud və yoxlanmış deyil.",
    "roadmap_generation_stale": "Roadmap əvvəlki məqsəd və ya mənbə versiyalarına əsaslanır; yenidən təklif edib yeni versiyanı nəzərdən keçirək.",
    "exam_active": "İmtahan davam edir: bu cəhd bitənə qədər dərs, ipucu və izah bağlıdır.",
    "roadmap_not_approved": "Yol xəritəsi hələ təsdiqlənməyib. Kurs yaradılması və tədris təsdiqdən sonra başlayır.",
    "roadmap_version_mismatch": "Təsdiq köhnə yol xəritəsi versiyasına aiddir. Yeni versiyanı nəzərdən keçir.",
    "phase_forbids_action": "Bu addım indiki mərhələdə icazəli deyil.",
    "revision_conflict": "Vəziyyət başqa pəncərədə və ya prosesdə dəyişib. Səhifə yeniləndi; yenidən cəhd et.",
    "stale_ui": "Bu interfeys köhnə versiyadır. Səhifəni yenilə.",
    "invalid_state": "Kurs vəziyyəti ziddiyyətlidir; yazılar dayandırıldı. Ehtiyat nüsxədən bərpa et.",
    "lesson_missing": "Bu mərhələnin dərsi hələ hazırlanmayıb.",
    "grounding_invalid": ("Bu dərsin mənbəsi dəyişib və ya artıq etibarlı deyil. Mənbə yenidən yoxlanana qədər "
                          "izah, ipucu, praktika və yoxlama bağlıdır."),
    "capability_changed": ("Bu mərhələnin məqsədi dəyişib. Köhnə dərs və tapşırıqlar tarixçədə qalır, amma yeni "
                           "məqsəd üçün dərs və yeni tapşırıq hazırlanmalıdır."),
    "no_fresh_item": "Bu mərhələ üçün görmədiyin tapşırıq qalmayıb. Yeni tapşırıq hazırlanmalıdır.",
    "review_not_due": "Bu mərhələnin təkrar yoxlaması hələ vaxtı çatmayıb.",
    "recovery_required": "Təkrar cəhdlər keçmədi. Davam etməzdən əvvəl səbəbi araşdırıb konkret dəyişmiş planı razılaşdıraq.",
    "diagnostic_required": "Əvvəl məqsəd və qısa performans diagnostic-i tamamla; yoxlamasız davam yalnız sənin açıq razılığınla ilkin plan yaradır.",
    "review_required": ("Təkrar yoxlamanın vaxtı çatıb. Əvvəl yeni tapşırıqla müstəqil yoxlama aparılır; "
                        "izah və praktika bundan sonra açılacaq."),
    "rubric_required": "Açıq cavab rubrika ilə qiymətləndirilməlidir; qərar cavabdan sitatla əsaslandırılmalıdır.",
}


class CourseStateError(ValueError):
    """A refused transition. ``code`` is stable; ``message`` is learner-facing."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail or REASONS_AZ.get(code, code)}")

    def to_dict(self) -> dict:
        return {"error": self.code, "message": REASONS_AZ.get(self.code, self.code), "detail": self.detail}


class StateVersionError(CourseStateError):
    pass


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def digest(prefix: str, obj) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical(obj)).hexdigest()[:16]}"


# The capability a node certifies: what is taught, practised, assessed and grounded.
# Title/stage/pitfalls are presentation; ``requires`` is ordering (its change is a
# separate dependency impact, not a different capability).
CAPABILITY_FIELDS = ("task", "outcomes", "skills", "practice", "source_refs", "exam_domain")

# Fields that change what the learner agreed to learn, practise or be assessed on.
# Presentation-only fields (stage label, pitfalls wording, inline lesson draft) are
# excluded so cosmetic edits do not force re-approval; teaching content has its own
# per-node content version.
MATERIAL_NODE_FIELDS = ("id", "title", "task", "requires", "outcomes", "skills", "practice",
                        "source_refs", "exam_domain")


def material_roadmap(plan: dict) -> dict:
    out = {
        "goal": plan.get("goal"),
        "sources": sorted(plan.get("sources") or []),
        "nodes": [{k: n.get(k) for k in MATERIAL_NODE_FIELDS} for n in plan.get("nodes") or []],
    }
    if "generation_provenance" in plan:
        out["generation_provenance"] = plan["generation_provenance"]
    return out


def roadmap_version(plan: dict) -> str:
    return digest("rm", material_roadmap(plan))


def capability_version(node: dict) -> str:
    return digest("cap", {k: node.get(k) for k in CAPABILITY_FIELDS})


def capability_snapshot(plan: dict) -> dict:
    return {n["id"]: {"version": capability_version(n), "requires": sorted(n.get("requires") or [])}
            for n in plan.get("nodes") or [] if isinstance(n, dict) and n.get("id")}


def _dependents(plan: dict, roots: set) -> dict:
    """Transitive dependents of ``roots`` over ``requires`` -> {dependent: sorted upstream roots}."""
    children = {}
    for n in plan.get("nodes") or []:
        for r in n.get("requires") or []:
            children.setdefault(r, set()).add(n["id"])
    out = {}
    for root in roots:
        stack, seen = [root], set()
        while stack:
            for child in children.get(stack.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
                    out.setdefault(child, set()).add(root)
    return {k: sorted(v) for k, v in out.items()}


def capability_impact(approved: dict, new_plan: dict) -> dict:
    """Per-node impact of approving ``new_plan`` against the approved capability snapshot.

    ``approved`` is ``state["capabilities"]`` ({node: {"version", "requires", ...}}).
    A version of ``None`` (legacy state whose old definition is known to differ) counts
    as changed. Nothing here deletes anything: callers supersede current progress and
    keep history.
    """
    new = capability_snapshot(new_plan)
    nodes = {}
    for nid, cur in new.items():
        prev = (approved or {}).get(nid)
        if prev is None:
            change = "added"
        elif prev.get("version") != cur["version"]:
            change = "capability_changed"
        elif sorted(prev.get("requires") or []) != cur["requires"]:
            change = "prerequisites_changed"
        else:
            change = "unchanged"
        nodes[nid] = {"change": change, "version": cur["version"],
                      "previous_version": (prev or {}).get("version")}
        if (prev or {}).get("basis") == "legacy_unverified":
            nodes[nid]["unverified"] = True    # migrated from an unknown out-of-band roadmap
    for nid in sorted(set(approved or {}) - set(new)):
        nodes[nid] = {"change": "removed", "version": None, "previous_version": approved[nid].get("version")}
    changed = {n for n, v in nodes.items() if v["change"] in {"capability_changed", "removed"}}
    for dep, roots in _dependents(new_plan, changed).items():
        if dep in nodes:
            nodes[dep]["upstream_changed"] = roots
    return {"initial": not approved, "nodes": nodes,
            "summary": {**{c: sorted(n for n, v in nodes.items() if v["change"] == c)
                           for c in ("capability_changed", "prerequisites_changed", "added", "removed")},
                        "unverified": sorted(n for n, v in nodes.items() if v.get("unverified"))}}


def diff_roadmaps(old: dict | None, new: dict) -> list[dict]:
    """Learner-reviewable list of material changes between two roadmaps."""
    if not old:
        return [{"change": "initial_roadmap", "nodes": [n.get("id") for n in new.get("nodes") or []]}]
    a, b = material_roadmap(old), material_roadmap(new)
    changes = []
    if a["goal"] != b["goal"]:
        changes.append({"change": "goal_changed"})
    if a["sources"] != b["sources"]:
        changes.append({"change": "sources_changed",
                        "added": sorted(set(b["sources"]) - set(a["sources"])),
                        "removed": sorted(set(a["sources"]) - set(b["sources"]))})
    old_nodes = {n["id"]: n for n in a["nodes"]}
    new_nodes = {n["id"]: n for n in b["nodes"]}
    for nid in sorted(new_nodes.keys() - old_nodes.keys()):
        changes.append({"change": "node_added", "node": nid})
    for nid in sorted(old_nodes.keys() - new_nodes.keys()):
        changes.append({"change": "node_removed", "node": nid})
    for nid in sorted(new_nodes.keys() & old_nodes.keys()):
        fields = [f for f in MATERIAL_NODE_FIELDS if old_nodes[nid].get(f) != new_nodes[nid].get(f)]
        if fields:
            changes.append({"change": "node_changed", "node": nid, "fields": fields})
    if [n["id"] for n in a["nodes"]] != [n["id"] for n in b["nodes"]] and not changes:
        changes.append({"change": "order_changed"})
    return changes


def empty_support() -> dict:
    return {"hints_count": 0, "explanation_shown": False, "worked_example_shown": False,
            "alternative_explanation_shown": False, "ai_direct_answer_revealed": False,
            "conceptual_scaffold": False}


def new_state(session_id: str, goal_text: str | None, now: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "phase_entered_at": now,
        "goal": {"text": goal_text, "target": None},
        "learner_profile": {"provisional": True, "stated_background": None, "explanation_preferences": [],
                            "diagnostic": [], "gaps": [], "updated_at": None,
                            "note": "Provisional; self-report is context, not demonstrated competence."},
        "source_refs": [],
        "source_manifest_version": None,
        "course_version": None,
        "roadmap_version": None,
        "approved_roadmap_version": None,
        "roadmap_approval_event": None,
        "approval_history": [],
        "review_required": None,
        "phase": "onboarding",
        "active_node_id": None,
        "active_attempt_id": None,
        "attempt_status": "none",
        "assessment_item_version": None,
        "support_provenance": empty_support(),
        "attempts": {},
        "evidence_refs": [],
        "assessment_result": None,
        "next_mission": None,
        "resume_checkpoint": {"phase": "onboarding", "route": "#/", "node_id": None, "attempt_id": None, "at": now},
        "learning_commitments": [],
        "accountability": {"open": None, "history": []},
        "engagement_intervals": {"store": "ledger:engagement_interval", "count": 0},
        "content_versions": {},
        "item_versions": {},
        "exposure": {},
        "misconception_counts": {},
        "node_progress": {},
        "idempotency": {},
        # v2: capability-version bindings, content-identity exposure, grounding, reviews.
        "capabilities": {},
        "capability_history": [],
        "lesson_bindings": {},
        "item_bindings": {},
        "exposure_records": {},
        "freshness_reviews": {},
        "review_queue": [],
        "legacy_binding_pending": False,
        "migration_log": [],
    }


# --- migrations ---------------------------------------------------------------
def _v0_to_v1(state: dict) -> dict:
    """v0 = pre-release prototype shape without revision/support bookkeeping."""
    goal = state.get("goal")
    goal_text = goal.get("text") if isinstance(goal, dict) else goal
    base = new_state(state.get("session_id"), goal_text, state.get("created_at") or state.get("updated_at") or "")
    for key, value in state.items():
        if key in base and key not in {"schema_version", "goal"}:
            base[key] = value
    base["schema_version"] = 1
    return base


def _v1_to_v2(state: dict) -> dict:
    """Add v2 bookkeeping without rebuilding: every v1 key (known or not) is preserved.

    Bindings that need files (roadmap, vault, manifest) are resolved by the action
    layer on first open (``legacy_binding_pending``) so this stays pure.
    """
    out = copy.deepcopy(state)
    # A state exported with down_v2_to_v1 carries its v2 data: restore it instead of
    # re-deriving (re-deriving would bind superseded lessons/items to the current
    # capability). Bindings for anything added under v1 are still resolved on open.
    archived = out.pop("v2_archive", None)
    if isinstance(archived, dict):
        for key in V2_ONLY_KEYS:
            if key in archived and key not in out:
                out[key] = archived[key]
    defaults = new_state(out.get("session_id"), None, out.get("updated_at") or "")
    for key, value in defaults.items():
        if key not in out:
            out[key] = value
    out["schema_version"] = 2
    out["legacy_binding_pending"] = True
    out["migration_log"] = list(out.get("migration_log") or []) + [
        {"from": 1, "to": 2, "at": out.get("updated_at"),
         "note": "v2 keys added; capability/lesson/item/exposure/review bindings resolved on first open"}]
    return out


_MIGRATIONS = {0: _v0_to_v1, 1: _v1_to_v2}
V2_ONLY_KEYS = ("capabilities", "capability_history", "lesson_bindings", "item_bindings", "exposure_records",
                "freshness_reviews", "review_queue", "legacy_binding_pending", "migration_log")


def down_v2_to_v1(state: dict) -> dict:
    """Reverse migration for export/rollback: v2-only keys are archived, never dropped."""
    if state.get("schema_version") != 2:
        raise StateVersionError("invalid_state", "down migration expects a v2 state")
    out = copy.deepcopy(state)
    out["v2_archive"] = {k: out.pop(k) for k in V2_ONLY_KEYS if k in out}
    out["schema_version"] = 1
    return out


def migrate(state: dict) -> tuple[dict, list[int]]:
    """Return (state at SCHEMA_VERSION, applied source versions). Never downgrades."""
    if not isinstance(state, dict):
        raise StateVersionError("invalid_state", "course state must be an object")
    state = copy.deepcopy(state)
    version = state.get("schema_version", 0)
    if type(version) is not int or version < 0:
        raise StateVersionError("invalid_state", "schema_version must be a non-negative integer")
    if version > SCHEMA_VERSION:
        raise StateVersionError("newer_state_schema",
                                f"state schema {version} is newer than this runtime ({SCHEMA_VERSION}); upgrade the runtime")
    applied = []
    while version < SCHEMA_VERSION:
        state = _MIGRATIONS[version](state)
        applied.append(version)
        version = state["schema_version"]
    return state, applied


# --- consistency ---------------------------------------------------------------
REQUIRED_KEYS = tuple(new_state("sess_0000000000000000", None, "").keys())


def validate(state: dict) -> list[str]:
    """Cross-field invariants. Any error makes the action layer fail closed."""
    errors = [f"missing:{k}" for k in REQUIRED_KEYS if k not in state]
    if errors:
        return errors
    if state["phase"] not in PHASES:
        errors.append("unknown_phase")
    if state["attempt_status"] not in ATTEMPT_STATUSES:
        errors.append("unknown_attempt_status")
    if type(state["revision"]) is not int or state["revision"] < 0:
        errors.append("bad_revision")
    phase = state["phase"]
    approved = state["approved_roadmap_version"]
    if phase in _AFTER_APPROVAL and (not approved or approved != state["roadmap_version"]):
        errors.append("phase_requires_current_approval")
    if approved and not state["roadmap_approval_event"]:
        errors.append("approval_without_event")
    att_id = state["active_attempt_id"]
    att = state["attempts"].get(att_id) if att_id else None
    if phase == "exam_active" and (not att or att.get("status") != "active" or state["attempt_status"] != "active"):
        errors.append("exam_active_without_active_attempt")
    if phase == "assessment_pending" and (not att or att.get("status") not in
                                          {"submitted", "scoring_failed", "awaiting_rubric", "scored"}):
        errors.append("assessment_pending_without_submitted_attempt")
    if att and att.get("item_version") != state["assessment_item_version"]:
        errors.append("frozen_item_version_mismatch")
    actives = [a for a in state["attempts"].values() if a.get("status") in OPEN_ATTEMPT_STATUSES]
    if len(actives) > 1:
        errors.append("multiple_open_attempts")
    if actives and actives[0].get("attempt_id") != att_id:
        errors.append("open_attempt_not_active")
    queue = state["review_queue"]
    if not isinstance(queue, list) or any(not isinstance(q, dict) or q.get("status") not in REVIEW_STATUSES
                                          for q in queue):
        errors.append("bad_review_queue")
    else:
        pending = [q.get("node_id") for q in queue if q.get("status") in PENDING_REVIEW]
        if len(pending) != len(set(pending)):
            errors.append("duplicate_pending_review")
        if len({q.get("review_id") for q in queue}) != len(queue):
            errors.append("duplicate_review_id")
    if not isinstance(state["capabilities"], dict):
        errors.append("bad_capabilities")
    return errors


def guard(state: dict, action: str) -> None:
    """Raise unless ``action`` is permitted from the persisted state."""
    errors = validate(state)
    if errors:
        raise CourseStateError("invalid_state", ",".join(errors))
    if action not in ALLOWED:
        raise CourseStateError("phase_forbids_action", f"unknown action {action}")
    phase = state["phase"]
    if phase not in ALLOWED[action]:
        if phase in TEACHING_LOCKED_PHASES:
            raise CourseStateError("exam_active", f"{action} refused during {phase}")
        if phase in {"onboarding", "source_preparation", "roadmap_awaiting_approval"} and action in REQUIRES_APPROVAL:
            raise CourseStateError("roadmap_not_approved", f"{action} refused during {phase}")
        raise CourseStateError("phase_forbids_action", f"{action} refused during {phase}")
    if action in REQUIRES_APPROVAL and state["approved_roadmap_version"] != state["roadmap_version"]:
        raise CourseStateError("roadmap_version_mismatch")


def permitted_actions(state: dict) -> list[str]:
    out = []
    for action in ALLOWED:
        try:
            guard(state, action)
        except CourseStateError:
            continue
        out.append(action)
    return out


def set_phase(state: dict, phase: str, now: str) -> None:
    if phase not in PHASES:
        raise CourseStateError("invalid_state", f"unknown phase {phase}")
    if state["phase"] != phase:
        state["phase"] = phase
        state["phase_entered_at"] = now


def check_revision(state: dict, expected_revision) -> None:
    if expected_revision is None:
        return
    if type(expected_revision) is not int or expected_revision != state["revision"]:
        raise CourseStateError("revision_conflict",
                               f"expected revision {expected_revision}, current {state['revision']}")


IDEMPOTENCY_LIMIT = 256


def remembered(state: dict, key: str | None):
    if key is None:
        return None
    return state["idempotency"].get(key)


def remember(state: dict, key: str | None, action: str, result, request: str | None = None) -> None:
    """Remember a key's first use; ``request`` (a digest of the arguments) binds the key
    to that exact request so a reused key with different arguments is refused."""
    if key is None:
        return
    store = state["idempotency"]
    store[key] = {"action": action, "result": result, "revision": state["revision"] + 1, "request": request}
    while len(store) > IDEMPOTENCY_LIMIT:
        store.pop(next(iter(store)))

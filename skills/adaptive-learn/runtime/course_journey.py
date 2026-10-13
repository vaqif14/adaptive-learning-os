"""Transparent, bounded journey heuristics. No calibrated probabilities or level labels."""
from __future__ import annotations

from datetime import datetime
import hashlib

from .course_content import canonical_text
from .course_state import CourseStateError

DIAGNOSTIC_OBSERVATION_LIMIT = 2
RECOVERY_FAILURE_LIMIT = 3
RECOVERY_BLOCKERS = {"prerequisite", "task_interpretation", "workload", "accessibility", "tooling", "other"}


def diagnostic_attribution(entry):
    independence = entry.get("independence", "unknown")
    assistance = entry.get("assistance", [])
    assessor = entry.get("assessor", "host_agent")
    confidence = entry.get("confidence", "unknown")
    kind = entry.get("task_kind", "unknown")
    if not isinstance(independence, str) or independence not in {"unassisted", "assisted", "unknown"}:
        raise CourseStateError("phase_forbids_action", "diagnostic independence must be unassisted|assisted|unknown")
    if not isinstance(assistance, list) or len(assistance) > 20 or not all(isinstance(x, str) and 1 <= len(x) <= 300 for x in assistance):
        raise CourseStateError("phase_forbids_action", "diagnostic assistance must be a bounded list of descriptions")
    if (not isinstance(assessor, str) or assessor not in {"host_agent", "human"}
            or not isinstance(confidence, str) or confidence not in {"unknown", "low", "medium", "high"}):
        raise CourseStateError("phase_forbids_action", "diagnostics are attributed human/host judgments, not auto verification")
    if not isinstance(kind, str) or kind not in {"unknown", "explanation", "scenario", "artifact", "retrieval"}:
        raise CourseStateError("phase_forbids_action", "invalid diagnostic task kind")
    response, rationale = entry.get("response", ""), entry.get("rationale", "")
    if not isinstance(response, str) or len(response) > 8000 or not isinstance(rationale, str) or len(rationale) > 2000:
        raise CourseStateError("phase_forbids_action", "diagnostic response/rationale must be bounded text")
    if assistance:
        independence = "assisted"
    attributed = entry["basis"] == "observed" and bool(response.strip()) and len(rationale.strip()) >= 20 and kind != "unknown"
    return {"response": response, "rationale": rationale, "task_kind": kind, "independence": independence,
            "assistance": assistance, "assessor": assessor, "confidence": confidence,
            "attributed_observation": attributed,
            "independent_observation": attributed and independence == "unassisted",
            "verification": "attributed_judgment", "calibrated": False}


def diagnostic_tasks(entries):
    """Keep one cold observation per prompt, retaining support from every retry.

    This detects canonical duplicates, not semantic equivalence. The host must
    still inspect whether a changed prompt tests transfer or repeats an answer.
    IDs, responses and assessor labels cannot turn a seen prompt into a new task.
    """
    tasks, repeated, unscoped = {}, [], []
    for entry in entries:
        if not entry.get("attributed_observation"):
            continue
        if not entry.get("node_id"):
            unscoped.append(entry["id"])
            continue
        key = hashlib.sha256(canonical_text(entry.get("prompt", "")).encode()).hexdigest()
        if key not in tasks:
            tasks[key] = {**entry, "task_fingerprint": key, "entry_ids": [entry["id"]]}
        else:
            first = tasks[key]
            first["entry_ids"].append(entry["id"])
            repeated.append(entry["id"])
            # A new id or omitted help does not erase help on the same task.
            if entry.get("assistance") or entry.get("independence") == "assisted":
                first["independent_observation"] = False
                first["independence"] = "assisted"
                first["assistance"] = list(dict.fromkeys(first.get("assistance", []) + entry.get("assistance", [])))
    return list(tasks.values()), repeated, unscoped


def independent_diagnostic_successes(entries):
    """Scoped initial successes, never mastery or a calibrated competence score."""
    tasks, _, _ = diagnostic_tasks(entries)
    return [e for e in tasks if e.get("independent_observation") and e.get("outcome") == "correct"
            and e.get("confidence") in {"medium", "high"}]


def diagnostic_next(goal, profile):
    observed, repeated, unscoped = diagnostic_tasks(profile.get("diagnostic", []))
    bounded = observed[:DIAGNOSTIC_OBSERVATION_LIMIT]
    info = {"provisional": True, "observations": len(bounded), "observation_limit": DIAGNOSTIC_OBSERVATION_LIMIT,
            "total_distinct_observations": len(observed),
            "policy": "intake-v2", "calibrated_probability": None,
            "evidence_entry_ids": [e["id"] for e in bounded], "repeated_task_entry_ids": repeated,
            "unscoped_entry_ids": unscoped,
            "scope_limit_az": "Bu qısa müşahidə yalnız yoxlanmış tapşırıqlara aiddir; ümumi səviyyə, learning style və qabiliyyət ölçmür.",
            "freshness_limit_az": "Məzmuncə eyni sual təkrar sayılır. Məna və transfer fərqini host agent ayrıca yoxlamalıdır."}
    if not goal.get("text"):
        action, text = "ask_goal", "Nəyi müstəqil edə bilmək istəyirsən?"
    elif not profile.get("stated_background"):
        action, text = "ask_background", "Bu işə aid indiyə qədər nə etmisən? Bir konkret nümunə de."
    elif not observed:
        action, text = "performance_probe", "Məqsədə uyğun bir kiçik tapşırıq ver: learner qərarını, izahını və ya hazırladığı işi göstərsin. Özünü qiymətləndirməsini istəmə."
    elif len(observed) >= DIAGNOSTIC_OBSERVATION_LIMIT:
        action, text = "ready_to_plan", "Qısa diagnostic-i burada saxla. Yalnız müşahidə edilmiş bacarıqlar üçün ilkin plan qur; sonrakı yeni cəhdlər təxmini yeniləsin."
    else:
        last = observed[-1]
        info["node_id"] = last.get("node_id")
        info["basis_entry_id"] = last["id"]
        if last["outcome"] in {"incorrect", "partial", "unknown"}:
            action, text = "prerequisite_or_interpretation_probe", "Bir kiçik sualla ayırd et: şərt aydın olmadı, yoxsa ilkin bacarıqda boşluq var? Səbəbi əvvəlcədən elan etmə."
        elif not last.get("independent_observation") or last.get("confidence") in {"unknown", "low"}:
            action, text = "smaller_independent_probe", "Daha kiçik, fərqli tapşırığı köməksiz sına. Əvvəlki köməkli və ya qeyri-müəyyən nəticə müstəqil bacarıq sayılmır."
        else:
            action, text = "fresh_transfer_probe", "Eyni bacarığı fərqli kontekstdə bir yeni tapşırıqla köməksiz yoxla; əvvəlki cavabı təkrar soruşma."
    return {**info, "action": action, "instruction_az": text}


def skill_evidence(state, node_id, capability_version):
    changed = any(h.get("node_id") == node_id for h in state.get("capability_history", []))
    diagnostic = [e for e in state["learner_profile"].get("diagnostic", []) if e.get("node_id") == node_id
                  and (e.get("capability_version") == capability_version or (not changed and e.get("capability_version") is None))]
    tasks, repeated, _ = diagnostic_tasks(diagnostic)
    successes = independent_diagnostic_successes(diagnostic)
    attempts = [a for a in state["attempts"].values() if a.get("node_id") == node_id and a.get("status") == "finalized"
                and a.get("capability_version") == capability_version]
    # JSON object insertion order is not a chronology (writers may sort keys).
    def finalized_time(attempt):
        stamp = attempt.get("finalized_at")
        if not isinstance(stamp, str):
            return 0
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            return 0
    attempts.sort(key=finalized_time)
    current = [a for a in attempts if (a.get("result") or {}).get("outcome") == "correct"]
    def unassisted(attempt):
        support = attempt.get("support")
        return (isinstance(support, dict) and support.get("hints_count") == 0
                and all(support.get(k) is False for k in
                        ("worked_example_shown", "ai_direct_answer_revealed", "conceptual_scaffold")))
    independent = [a for a in current if unassisted(a)]
    executable = [a for a in independent if a.get("kind", "python") == "python"
                  and a.get("evidence_strength") == "strong"]
    diagnostic_difficulties = [e for e in tasks if e.get("independent_observation")
                              and e.get("outcome") in {"incorrect", "partial"}]
    if ((current and attempts and (attempts[-1].get("result") or {}).get("outcome") in {"incorrect", "partial"})
            or (not attempts and successes and tasks and tasks[-1].get("outcome") in {"incorrect", "partial"})):
        status, label = "mixed_evidence", "Əvvəl uğur var, amma son cəhddə çətinlik göründü; cari bacarıq yenidən yoxlanmalıdır."
    elif executable:
        status, label = "independent_performance", "Yeni tapşırıq üzrə icra yoxlaması var; ümumi ustalıq və transfer hələ təsdiqlənməyib."
    elif independent and all(a.get("evidence_strength") == "weak" for a in independent):
        status, label = "recognition_only", "Seçim suallarında tanıma sübutu var; özün izah etmək və tətbiq etmək ayrıca yoxlanmalıdır."
    elif independent or successes:
        status, label = "provisional_observation", "İlkin müşahidə və ya rəy var; bu, kalibrasiya olunmuş skill ehtimalı deyil."
    elif diagnostic_difficulties or (attempts and not current):
        status, label = "needs_more_evidence", "Bu bacarıq üzrə çətinlik görünür; səbəbi və hazırkı bacarığı yeni müşahidə ilə ayırd etmək lazımdır."
    elif current or tasks:
        status, label = "assisted_or_uncertain", "Köməkli və ya qeyri-müəyyən müşahidə var; müstəqil nəticə lazımdır."
    else:
        status, label = "not_yet_observed", "Bu bacarıq üçün hələ kifayət qədər müşahidə yoxdur."
    return {"status": status, "label_az": label, "calibrated_probability": None, "mastery_claim": False,
            "diagnostic_entry_ids": [e["id"] for e in diagnostic], "attempt_ids": [a["attempt_id"] for a in attempts],
            "repeated_diagnostic_entry_ids": repeated,
            "evidence_counts": {"independent_diagnostic_successes": len(successes),
                                "independent_executable_successes": len(executable),
                                "finalized_attempts": len(attempts)},
            "independence_limit_az": "Tətbiqdə qeydə alınmış kömək izlənir; şəxsi kompüterdə xarici kömək müşahidə olunmur.",
            "policy": "skill-evidence-v2"}


def consecutive_failures(state, node_id, capability_version):
    progress = state["node_progress"].get(node_id) or {}
    recovery = progress.get("recovery") or {}
    resolved = recovery.get("cycle_restarted_after_attempt", recovery.get("resolved_after_attempt"))
    failures = []
    for aid in progress.get("exam_attempts", []):
        att = state["attempts"].get(aid) or {}
        if att.get("status") != "finalized" or att.get("capability_version") != capability_version:
            continue
        outcome = (att.get("result") or {}).get("outcome")
        if outcome == "correct" or aid == resolved:
            failures = []
        elif outcome in {"incorrect", "partial"}:
            failures.append(aid)
    return failures

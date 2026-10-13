"""Bounded recovery plan validation; no automatic diagnosis or efficacy claims.

A plan is a learner-agreed change to try, not evidence that the change worked.
Text normalization rejects cosmetic resubmissions; it cannot prove that two
differently worded plans are semantically different. The host reviews that.
"""
from __future__ import annotations

import re
import unicodedata

from .course_state import CourseStateError

RECOVERY_BLOCKERS = frozenset({"prerequisite", "task_interpretation", "workload", "accessibility", "tooling", "other"})


def plan_signature(text: str) -> str:
    """Comparable tokens across case, Unicode presentation and punctuation."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def validate_plan(plan: dict) -> dict:
    """Return a bounded copy and reject malformed client values consistently."""
    if not isinstance(plan, dict):
        raise CourseStateError("recovery_required", "recovery needs a learner-agreed plan object")
    blocker, text = plan.get("blocker"), plan.get("plan")
    if (plan.get("learner_agreed") is not True or not isinstance(blocker, str)
            or blocker not in RECOVERY_BLOCKERS or not isinstance(text, str)
            or not 20 <= len(text.strip()) <= 2000 or len(plan_signature(text)) < 20):
        raise CourseStateError("recovery_required", "needs learner agreement, a blocker and a concrete 20-2000 character plan")
    prerequisite = plan.get("prerequisite_node_id")
    if prerequisite is not None and (not isinstance(prerequisite, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", prerequisite)):
        raise CourseStateError("phase_forbids_action", "recovery prerequisite must be a safe roadmap node id")
    return {"blocker": blocker, "plan": text.strip(), "learner_agreed": True,
            "prerequisite_node_id": prerequisite}


def repeated_plan(history: list[dict], text: str, capability_version: str) -> bool:
    signature = plan_signature(text)
    return any(h.get("capability_version") == capability_version
               and plan_signature(h.get("plan", "")) == signature for h in history)


def guidance(blocker: str, previous_plans: int = 0) -> dict:
    """Concrete discriminating next steps, explicitly provisional and bounded."""
    steps = {
        "prerequisite": "Bir ilkin bacarığı kiçik tapşırıqla yoxla; boşluq görünərsə əvvəl onu bərpa et.",
        "task_interpretation": "Şərti learner öz sözləri ilə desin; bir nümunədə gözlənən nəticəni ayırd et.",
        "workload": "Tapşırığı kiçilt və razılaşdırılmış qısa iş bloku seç; vaxtı bacarıq hökmü kimi istifadə etmə.",
        "accessibility": "Learner-in seçdiyi giriş formasını və əlçatanlıq dəstəyini dəyiş; hədəf bacarığı saxla.",
        "tooling": "Alətin ən kiçik işləyən nümunəsini yoxla; alət xətasını bilik çatışmazlığı kimi sayma.",
        "other": "Bir müşahidə edilən maneəni seç və onu ayırd edən kiçik yoxlama razılaşdır.",
    }
    return {"instruction_az": steps[blocker], "previous_plans": previous_plans,
            "review_recommended": previous_plans >= 1,
            "review_reason_az": ("Dəyişmiş plan da çətinliyi həll etmədi. Şərti, prerequisite-i, mənbəni və məqsədin həcmini host ilə yenidən nəzərdən keçir."
                                 if previous_plans >= 1 else None),
            "completion_evidence_required": True,
            "uncertainty_az": "Razılaşdırılmış plan irəliləyiş və ya səbəbin təsdiqi deyil; yeni learner işi yoxlanmalıdır.",
            "policy": "recovery-v1", "calibrated": False}

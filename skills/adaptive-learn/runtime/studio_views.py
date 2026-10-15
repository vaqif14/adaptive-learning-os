"""Owner HTTP views (stage E): read-only projections of the studio for the owner UI, plus the thin control wrappers.

Nothing here accepts authored content or writes course/learner state. A node preview is built from the validated
staged payloads but reduced to what a learner would see (the same ``course_content.public_*`` functions the learner
routes use); the vault (reference solutions, hidden check source, answer keys, rubrics, exemplars) never leaves, and a
final scan refuses a preview in which any vault text survived. The gate is never run here: this module is imported by
a threaded server, and the gate swaps process-wide state (see ``rules_check.scratch_state_env``).
"""
from __future__ import annotations

from . import course_content as cc
from . import studio_worker as sw
from .studio_store import StudioError, studio_dir

EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 500
NOTIFICATION_LIMIT = 20
MIN_VAULT_TEXT = 15          # shorter strings (a one-letter answer key) cannot be scanned for without false positives


# ----------------------------------------------------------------------- status
def _nodes(st: dict, titles: dict) -> list[dict]:
    """Per node: the lesson and items unit with their status and attempts, in the job's unit order."""
    rows: dict[str, dict] = {}
    for unit in (st.get("units") or {}).values():
        row = rows.setdefault(unit["node_id"], {"node": unit["node_id"], "title": titles.get(unit["node_id"]),
                                                "lesson": None, "items": None})
        row[unit["kind"] if unit["kind"] in ("lesson", "items") else "lesson"] = {
            "status": unit["status"], "attempts": unit["attempts"], "reason": unit.get("reason"),
            "last_errors": [str(e)[:300] for e in (unit.get("last_errors") or [])[:3]]}
    return list(rows.values())


def status_view(k, sid: str) -> dict:
    """Job summary, per-node unit progress, worker heartbeat, runners (names and hashes only, never values), notifications."""
    root = studio_dir(k.ck.sk.dir(sid))
    st = k.status(sid)
    notes = sw.notifications(root)
    notes["notifications"] = notes["notifications"][-NOTIFICATION_LIMIT:]
    if st.get("status") == "no_job":
        return {**st, "nodes": [], "worker": sw.worker_status(root, k._dt()), "runners": k.runners_show(sid)["runners"],
                "notifications": notes}
    # a superseded/archived job still reports; the titles come from the live plan
    return {**st, "nodes": _nodes(st, k.node_titles(sid)), "runners": k.runners_show(sid)["runners"], "notifications": notes}


def events_view(k, sid: str, limit: int | None = None) -> dict:
    limit = EVENT_LIMIT if limit is None else max(1, min(int(limit), MAX_EVENT_LIMIT))
    out = k.events(sid)
    events = out["events"]
    return {"job_id": out["job_id"], "total": len(events), "limit": limit, "events": events[-limit:], "verify": out["verify"]}


def handover_view(k, sid: str) -> dict:
    """HANDOVER.json and the Markdown of the CURRENT build digest. Read-only: a stale or missing receipt is reported
    (``receipt_fresh`` false), never recomputed here; the owner runs ``alearn rules-check`` / ``studio handover``."""
    return k.handover(sid, None, refresh=False)


# ---------------------------------------------------------------------- preview
def _public_practice(pr: dict) -> tuple[dict, int]:
    kind = cc.item_kind(pr)
    out = {"kind": kind, "task_id": pr["task_id"], "prompt_md": pr["prompt_md"], "hint_count": len(pr["hints"])}
    if kind == "python":
        out.update(entry=pr["entry"], starter_code=pr["starter_code"],
                   visible_checks=[{"id": c["id"], "name": c["name"]} for c in pr["checks"]])
        return out, len(pr["checks"])
    out.update(scenario_md=pr.get("scenario_md"), questions=cc.public_questions(pr["questions"]))
    return out, len(pr["questions"])


def _public_lesson(lesson: dict) -> tuple[dict, int]:
    practice, hidden = _public_practice(lesson["practice"])
    out = {"objective": lesson["objective"], "success_criteria": list(lesson["success_criteria"]),
           "explanation": {"short": lesson["explanation"]["short"], "body": lesson["explanation"]["body"]},
           "why": lesson["why"]["text"], "practice": practice, "hint_ceiling": lesson.get("hint_ceiling"),
           "walkthrough": cc.public_walkthrough(lesson["walkthrough"]) if lesson.get("walkthrough") else None,
           "ladder": None}
    if lesson.get("ladder"):
        out["ladder"] = {"worked": cc.public_ladder_rung(lesson["ladder"], "worked"),
                         "completion": cc.public_ladder_rung(lesson["ladder"], "completion"),
                         "independent": "practice"}
    return out, hidden


def _vault_texts(payloads: dict) -> list[str]:
    """Strings a learner must never see: reference solutions, hidden check/detector source, exemplars."""
    found: list[str] = []

    def take(value):
        if isinstance(value, str) and len(value.strip()) >= MIN_VAULT_TEXT:
            found.append(value.strip())

    def scan(unit):
        take(unit.get("reference_solution"))
        take(unit.get("exemplar"))
        for check in unit.get("checks") or []:
            take(check.get("code"))
        for probe in unit.get("misconceptions") or []:
            if isinstance(probe, dict):
                take(probe.get("detector"))
    lesson = payloads.get("lesson")
    if lesson:
        scan(lesson["practice"])
    for item in (payloads.get("items") or {}).get("items") or []:
        scan(item)
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)


def node_preview(k, sid: str, node: str) -> dict:
    """What a learner would see for one staged node, plus counts of the hidden checks. Never the vault."""
    view = k.staged_view(sid, node)
    payloads = view["payloads"]
    try:
        lesson, hidden_practice = _public_lesson(payloads["lesson"]) if payloads.get("lesson") else (None, 0)
        items = [cc.public_item(i) for i in (payloads.get("items") or {}).get("items") or []]
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise StudioError("studio_preview_malformed", f"the staged payload of {node} could not be previewed ({type(e).__name__})")
    out = {"job_id": view["job_id"], "status": view["status"], "build_digest": view["build_digest"], "node": node,
           "title": view["title"], "units": view["units"], "lesson": lesson, "items": items,
           "hidden": {"practice_checks": hidden_practice,
                      "item_checks": {i["item_id"]: i.get("check_count", 0) for i in items},
                      "total": hidden_practice + sum(i.get("check_count", 0) for i in items)},
           "note_az": "Bu, öyrənənin görəcəyi görünüşdür. Gizli yoxlamalar, istinad həlləri və cavab açarları burada göstərilmir; "
                      "yalnız sayları verilir."}
    shown = [" ".join(text.split()) for text in _strings(out)]      # compared on the values, whitespace-collapsed
    for secret in _vault_texts(payloads):
        needle = " ".join(secret.split())
        if any(needle in text for text in shown):
            raise StudioError("studio_preview_leak", f"the preview of {node} contained vault text and was refused")
    return out

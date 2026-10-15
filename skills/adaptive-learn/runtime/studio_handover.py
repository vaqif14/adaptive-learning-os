"""The handover dossier: what was built (counts derived from the staged artifacts), the R1-R10 receipt table in plain
Azerbaijani, what is absent and what is NOT verified. The current HANDOVER.md + HANDOVER.json live at
``studio/handover/<job>/`` with a copy per build digest at ``studio/handover/<job>/<digest>/``; a superseded dossier is
renamed into ``archive/`` (nothing is overwritten or removed).

Azerbaijani wording below is natural plain language written for the owner; a native speaker should review the
phrasing of the rule labels and the caveats before it is shown to learners (the dossier is owner-facing).
"""
from __future__ import annotations

from collections import Counter

from . import course_content as cc
from .rules_probes.base import FIX_ONLY_RULES, HARD_RULES, RECEIPT_CLAIM, WAIVABLE_RULES
import os

from .studio_store import child, read_json, secure_mkdir, utc_stamp, write_atomic_bytes, write_atomic_json
from .utils import fsync_directory

HANDOVER_SCHEMA = 1
RULE_LABELS_AZ = {
    "R1": "Saxta ustalıq yoxdur: izləmək, ipucu və ya açılmış həll sübut sayılmır",
    "R2": "Sübut səviyyəsi: heç bir tapşırıq növü cədvəldə icazə verilən gücdən çox sübut yarada bilmir",
    "R3": "Keçid qaydası: ilkin mərhələ keçilməmiş mərhələ açılmır, eyni tapşırığı təkrarlamaqla keçmək olmur",
    "R4": "Jurnalın bütövlüyü: qeydlər dəyişdirilməyib, yalnız əlavə olunub, imzalar yoxlanılıb",
    "R5": "Transfer əhatəsi: hər mərhələdə əsas və yeni vəziyyət tapşırıqları var, təcrübə ustalıq sayılmır",
    "R6": "Yol xəritəsi səlahiyyəti: təsdiq yalnız öyrənənin təsdiq etdiyi dəqiq versiyaya və dəqiq yığmaya aiddir",
    "R7": "Mənbə əsaslandırması: sitatlar yoxlanmış mənbə mətnində tapılır",
    "R8": "Cavabın sızmaması: həll və gizli yoxlamalar öyrənənə göstərilmir, imtahanda kömək bağlıdır",
    "R9": "Dürüst ölçmə: şişirdilmiş iddia yoxdur, sayılar yığmadan götürülüb, çatışmazlıqlar açıq yazılıb",
    "R10": "Məxfilik və təhlükəsizlik: fayl icazələri, şəbəkəsiz kod, sirr və şəxsi məlumat yoxdur",
}
STATUS_AZ = {"pass": "keçdi", "fail": "KEÇMƏDİ", "error": "yoxlama xətası", "not_applicable": "tətbiq olunmur",
             "skipped": "yoxlanılmadı (tez rejim)"}
NOT_VERIFIED = [
    {"id": "lexical_grounding", "text_az": "Mənbə əsaslandırması yalnız mətn uyğunluğudur: sitatın mənbədə olması yoxlanılır, "
                                           "mənanın düzgün başa düşülməsi yoxlanılmır."},
    {"id": "fresh_v1_uncalibrated", "text_az": "Tapşırığın 'yeni' sayılması (fresh-v1) hələ real öyrənən məlumatı ilə "
                                                "kalibrlənməyib; eşiklər ilkin qaydalardır."},
    {"id": "free_text_provisional", "text_az": "Açıq cavablar rubrika ilə agent tərəfindən qiymətləndirilir; bu qiymət ilkindir "
                                                "və tək başına mərhələni keçirmir."},
    {"id": "no_efficacy_evidence", "text_az": "Kursun öyrənməyə real təsiri ölçülməyib; effektivlik barədə heç bir sübut yoxdur."},
    {"id": "language_quality", "text_az": "Azərbaycan dilindəki mətnlərin təbiiliyi və dəqiqliyi yerli dil bilicisi tərəfindən "
                                           "yoxlanılmayıb."},
    {"id": "difficulty_calibration", "text_az": "Tapşırıqların çətinliyi real öyrənənlərlə yoxlanılmayıb."},
    {"id": "owner_acceptance_unauthenticated", "text_az": "Sahibin təsdiqi əmr sətri və ya sahib tokeni ilə verilən mətn bəyanatıdır; onu real insanın "
                                                          "verdiyini kriptoqrafik olaraq sübut etmir (eyni hesabda işləyən agent də "
                                                          "eyni əmri və ya tokeni istifadə edə bilər). Təsdiqin hansı kanaldan "
                                                          "(terminal və ya sahib interfeysi) verildiyi qeyd olunur."},
    # (Azerbaijani text: native review advisable.)
    {"id": "sandbox_not_a_boundary", "text_az": "Kodu yoxlayan prosesdaxili qatlar (AST süzgəci, icra zamanı audit qadağaları) "
                                                 "təhlükəsizlik sərhədi deyil. Sərhəd əməliyyat sisteminin sandbox-udur (macOS-da Seatbelt, "
                                                 "Linux-da bubblewrap): hər yoxlamada R10 os_sandbox_contains onu kanareyka faylı və şəbəkə "
                                                 "cəhdi ilə sınayır. Belə sandbox olmayan platformada runner-lər işə salınmır, host və "
                                                 "öyrənən kodu isə yalnız prosesdaxili qatlarla qorunur. Sandbox-un özünün (Apple/bubblewrap) "
                                                 "boşluqsuz olduğu sübut edilməyib."},
    {"id": "behavioural_equivalence_bounded", "text_az": "Həll cavabının sızması və yeni tapşırığın praktikadan fərqli olması davranış "
                                                         "yoxlaması ilə (eyni imzalı funksiyalar üzrə) yoxlanılır; fərqli imzalı və ya tam "
                                                         "başqa quruluşlu surətlər tutulmaya bilər. Keçid miqyası etiketləri müəllif tərəfindən "
                                                         "bəyan edilir."},
    {"id": "claim_lint_lexical", "text_az": "İddia süzgəci söz siyahısına əsaslanır: Azərbaycan dilində bütün ifadə formalarını tutmur və "
                                              "yerli dil bilicisi tərəfindən yoxlanılmayıb."},
    {"id": "walkthrough_prose", "text_az": "Nümayişin izahat mətni insan tərəfindən oxunmayıb; yalnız kodun çıxışı sandbox-da "
                                           "yoxlanılıb."},
]
ABSENT = [
    {"id": "no_video", "text_az": "Video, səs və ya animasiya yoxdur."},
    {"id": "no_real_expert", "text_az": "Heç bir real mütəxəssisin adı, sözü və ya nümayişi yoxdur; nümayişlər agent tərəfindən "
                                         "yazılmış, mənbəyə əsaslanan nümayişdir."},
    {"id": "no_learner_data", "text_az": "Bu yığma heç bir öyrənən sübutu, cəhdi və ya müşahidəsi əlavə etməyib."},
    {"id": "no_hosted_ai", "text_az": "İş vaxtı heç bir xarici model çağırılmır; mətnlər yalnız host agent tərəfindən yazılıb."},
    {"id": "no_certificate", "text_az": "Sertifikat, xal, seriya və ya digər oyunlaşdırma yoxdur."},
]


# ------------------------------------------------------------------- derivation
def _walkthrough_counts(wt) -> dict | None:
    if not isinstance(wt, dict):
        return None
    steps = [s for s in wt.get("steps") or [] if isinstance(s, dict)]
    return {"language": wt.get("language"), "steps": len(steps),
            "checkpoints": sum(1 for s in steps if s.get("checkpoint")),
            "predict_prompts": sum(1 for s in steps if s.get("predict")),
            "deliberate_mistake": bool((wt.get("mistake") or {}).get("symptom"))}


def _ladder_counts(ld) -> dict | None:
    if not isinstance(ld, dict):
        return None
    return {"rungs": 3, "blanks": len((ld.get("completion") or {}).get("blanks") or [])}


def _ref_counts(lesson: dict) -> dict:
    source = supplementary = 0
    for part in ("explanation", "why", "worked_example", "alternative_explanation", "walkthrough"):
        for ref in ((lesson.get(part) or {}).get("refs") or []) if isinstance(lesson.get(part), dict) else []:
            if isinstance(ref, dict) and ref.get("supplementary") is True:
                supplementary += 1
            elif isinstance(ref, dict):
                source += 1
    return {"source": source, "supplementary": supplementary}


def derive_nodes(subjects: list[dict]) -> dict:
    """Per node counts DERIVED from the staged artifacts (never declared by the author or the job)."""
    out = {}
    for s in subjects:
        lesson, items = s.get("lesson"), s.get("items")
        row = {"lesson": None, "items": None}
        if isinstance(lesson, dict):
            practice = lesson.get("practice") or {}
            row["lesson"] = {"practice_kind": cc.item_kind(practice), "hints": len(practice.get("hints") or []),
                             "hint_ceiling": lesson.get("hint_ceiling"),
                             "practice_checks": len(practice.get("checks") or []) + len(practice.get("questions") or []),
                             "walkthrough": _walkthrough_counts(lesson.get("walkthrough")),
                             "ladder": _ladder_counts(lesson.get("ladder")), "refs": _ref_counts(lesson),
                             "success_criteria": len(lesson.get("success_criteria") or [])}
        if isinstance(items, list):
            real = [i for i in items if isinstance(i, dict)]
            row["items"] = {"count": len(real), "by_kind": dict(sorted(Counter(cc.item_kind(i) for i in real).items())),
                            "by_scope": dict(sorted(Counter(i.get("scope") for i in real).items())),
                            "checks": sum(len(i.get("checks") or []) + len(i.get("questions") or []) for i in real),
                            "free_text_provisional": sum(1 for i in real if cc.item_kind(i) == "free_text")}
        out[s["node"]] = row
    return out


def _totals(nodes: dict) -> dict:
    t = {"nodes": len(nodes), "lessons": 0, "walkthrough_steps": 0, "ladders": 0, "items": 0, "free_text_items": 0}
    for row in nodes.values():
        if row["lesson"]:
            t["lessons"] += 1
            t["walkthrough_steps"] += (row["lesson"]["walkthrough"] or {}).get("steps", 0)
            t["ladders"] += 1 if row["lesson"]["ladder"] else 0
        if row["items"]:
            t["items"] += row["items"]["count"]
            t["free_text_items"] += row["items"]["free_text_provisional"]
    return t


# --------------------------------------------------------------------- building
def _reasons(details: list) -> str:
    """Distinct not-applicable reasons, whitespace-collapsed; a cut reason ends with an ellipsis, never mid-word silence.
    The probe text is free-form (mostly English): it is shown as written, not translated."""
    out = set()
    for d in details:
        text = " ".join(str(d or "səbəb göstərilməyib").split())
        out.add(text if len(text) <= 90 else text[:89].rstrip() + "…")
    return "; ".join(sorted(out))


def probe_summary_az(probes: list[dict]) -> str:
    """Per-rule probe counts in words, e.g. '2 keçdi · 1 tətbiq olunmur (səbəb)'. A bare 'passed/total' would hide a probe that
    did not apply (it is not a pass) and read as a partial failure; every non-pass is named with its own count and reason."""
    count = {k: sum(1 for p in probes if p["status"] == k) for k in ("pass", "fail", "error", "not_applicable", "skipped")}
    parts = []
    if count["pass"]:
        parts.append(f"{count['pass']} keçdi")
    if count["fail"]:
        parts.append(f"{count['fail']} KEÇMƏDİ")
    if count["error"]:
        parts.append(f"{count['error']} yoxlama xətası")
    if count["not_applicable"]:
        parts.append(f"{count['not_applicable']} tətbiq olunmur ({_reasons([p.get('detail') for p in probes if p['status'] == 'not_applicable'])})")
    if count["skipped"]:
        parts.append(f"{count['skipped']} yoxlanılmadı (tez rejim)")
    return " · ".join(parts) or "yoxlama yoxdur"


def legacy_probe_summary_az(row: dict) -> str:
    """Summary for a rule row of a dossier written before ``probe_summary_az`` existed, from the counts it did keep
    (a not-applicable probe is named with its reason, never folded into 'N keçdi')."""
    total, passed = int(row.get("probes") or 0), int(row.get("probes_passed") or 0)
    na, failing = row.get("probes_not_applicable") or [], row.get("failing") or []
    parts = []
    if passed:
        parts.append(f"{passed} keçdi")
    if failing:
        parts.append(f"{len(failing)} KEÇMƏDİ")
    if na:
        parts.append(f"{len(na)} tətbiq olunmur ({_reasons([x.get('reason') for x in na])})")
    other = total - passed - len(failing) - len(na)
    if other > 0:
        parts.append(f"{other} yoxlanılmadı")
    return " · ".join(parts) or "yoxlama yoxdur"


def with_probe_summaries(doc: dict | None) -> dict | None:
    """A copy of a dossier whose rule rows all carry ``probe_summary_az`` (old files are read, never rewritten)."""
    if not doc or not isinstance(doc.get("rules"), list):
        return doc
    rules = [r if r.get("probe_summary_az") else {**r, "probe_summary_az": legacy_probe_summary_az(r)} for r in doc["rules"]]
    return {**doc, "rules": rules}


def _rule_rows(receipt: dict) -> list[dict]:
    rows = []
    for r in receipt["results"]:
        failing = [p for p in r["probes"] if p["status"] in {"fail", "error"}]
        rows.append({"rule": r["rule"], "label_az": RULE_LABELS_AZ[r["rule"]], "status": r["status"],
                     "status_az": STATUS_AZ.get(r["status"], r["status"]), "hard": r["rule"] in HARD_RULES,
                     "probes": len(r["probes"]),
                     "probes_passed": sum(1 for p in r["probes"] if p["status"] == "pass"),
                     "probes_not_applicable": [{"id": p["id"], "reason": str(p.get("detail") or "")[:300]}
                                               for p in r["probes"] if p["status"] == "not_applicable"],
                     "probe_summary_az": probe_summary_az(r["probes"]),
                     "failing": [{"id": p["id"], "detail": p["detail"][:300]} for p in failing]})
    return rows


def build_handover(job: dict, receipt: dict, subjects: list[dict], titles: dict, *, now: str) -> dict:
    nodes = derive_nodes(subjects)
    blocked = receipt["status"] != "pass" or receipt["scope"] != "full"
    findings = [{"rule": r["rule"], **f} for r in _rule_rows(receipt) for f in r["failing"]]
    return {
        "handover_schema": HANDOVER_SCHEMA, "job_id": job["job_id"], "session_id": job["session_id"],
        "build_digest": job.get("build_digest"), "roadmap_version": job["binding"].get("roadmap_version"),
        "created_at": now, "policy": job["policy"]["id"], "blocked": blocked, "blocking_findings": findings,
        "receipt": {k: receipt.get(k) for k in ("receipt_id", "scope", "status", "exit_code", "ledger_seq_after",
                                                "created_at", "probe_set_hash", "code_tree_hash")},
        "receipt_claim": RECEIPT_CLAIM["id"], "receipt_statement_az": RECEIPT_CLAIM["text_az"],
        "rules": _rule_rows(receipt), "waivable_rules": list(WAIVABLE_RULES), "non_waivable_rules": list(HARD_RULES),
        "fix_only_rules": list(FIX_ONLY_RULES), "titles": {n: titles.get(n) for n in nodes}, "nodes": nodes,
        "totals": _totals(nodes), "absent": ABSENT, "not_verified": NOT_VERIFIED,
        "owner_actions": {
            "accept": f"alearn studio accept --session {job['session_id']} --job {job['job_id']} "
                      f"--digest {job.get('build_digest')} --statement \"<yoxladım və qəbul edirəm>\"",
            "reject": f"alearn studio reject --session {job['session_id']} --job {job['job_id']} --node <node> --note \"<səbəb>\"",
            "refresh": f"alearn rules-check --session {job['session_id']} --job {job['job_id']} --scope full"},
    }


def _node_md(nid: str, title, row: dict) -> list[str]:
    lines = [f"### {title or nid} (`{nid}`)", ""]
    lesson, items = row["lesson"], row["items"]
    if lesson:
        wt, ld = lesson["walkthrough"], lesson["ladder"]
        lines.append(f"- Dərs: təcrübə növü `{lesson['practice_kind']}`, {lesson['hints']} ipucu"
                     f"{', ipucu limiti ' + str(lesson['hint_ceiling']) if lesson['hint_ceiling'] is not None else ''}, "
                     f"{lesson['success_criteria']} uğur meyarı, mənbəli istinad {lesson['refs']['source']}, "
                     f"mənbəsiz (əlavə) istinad {lesson['refs']['supplementary']}.")
        lines.append("- Nümayiş: " + (f"{wt['steps']} addım, {wt['checkpoints']} yoxlama nöqtəsi, "
                                       f"{wt['predict_prompts']} proqnoz sualı, planlı səhv "
                                       f"{'var' if wt['deliberate_mistake'] else 'yoxdur'} (dil: {wt['language']})."
                                       if wt else "yoxdur."))
        lines.append("- Pilləli təlim: " + (f"{ld['rungs']} pillə, {ld['blanks']} boşluq." if ld else "yoxdur."))
    else:
        lines.append("- Dərs: yığmada yoxdur.")
    if items:
        kinds = ", ".join(f"{k}: {v}" for k, v in items["by_kind"].items())
        scopes = ", ".join(f"{k}: {v}" for k, v in items["by_scope"].items())
        lines.append(f"- Qiymətləndirmə tapşırıqları: {items['count']} (növlər: {kinds}; əhatə: {scopes}); "
                     f"{items['checks']} yoxlama/sual; ilkin (provisional) açıq cavab: {items['free_text_provisional']}.")
    else:
        lines.append("- Qiymətləndirmə tapşırıqları: yığmada yoxdur.")
    return lines + [""]


def render_md(doc: dict) -> str:
    lines = []
    if doc["blocked"]:
        lines += ["> **BLOKLANIB**: bu yığma qəbul edilə bilməz. Səbəblər aşağıdakı cədvəldə və 'Tapılan problemlər' "
                  "bölməsindədir.", ""]
    lines += [f"# Təhvil dossieri: {doc['job_id']}", "",
              f"- Yığma izi (build digest): `{doc['build_digest']}`",
              f"- Yol xəritəsi versiyası: `{doc['roadmap_version']}`",
              f"- Qəbz: `{doc['receipt']['receipt_id']}` ({doc['receipt']['scope']} rejim, nəticə: {doc['receipt']['status']})",
              f"- Yaradılıb: {doc['created_at']}", "", doc["receipt_statement_az"], "",
              "## Hər mərhələdə nə hazırlanıb (saylar yığma fayllarından götürülüb)", ""]
    for nid, row in doc["nodes"].items():
        lines += _node_md(nid, doc["titles"].get(nid), row)
    t = doc["totals"]
    lines += [f"Cəmi: {t['nodes']} mərhələ, {t['lessons']} dərs, {t['walkthrough_steps']} nümayiş addımı, "
              f"{t['ladders']} pilləli təlim, {t['items']} qiymətləndirmə tapşırığı.", "",
              "## R1-R10 qəbz cədvəli", "", "| Qayda | Nəticə | Yoxlamalar | Təsvir |", "|---|---|---|---|"]
    for r in doc["rules"]:
        hard = " (güzəştsiz)" if r["hard"] else ""
        lines.append(f"| {r['rule']}{hard} | {r['status_az']} | {r['probe_summary_az'].replace('|', '/')} | {r['label_az']} |")
    if doc["blocking_findings"]:
        lines += ["", "## Tapılan problemlər", ""]
        lines += [f"- {f['rule']} / `{f['id']}`: {f['detail']}" for f in doc["blocking_findings"]]
    lines += ["", "## Nələr yoxdur", ""] + [f"- {x['text_az']}" for x in doc["absent"]]
    lines += ["", "## Nələr yoxlanılmayıb", ""] + [f"- {x['text_az']}" for x in doc["not_verified"]]
    lines += ["", "## Sahibin addımları", "",
              f"- Qəbul: `{doc['owner_actions']['accept']}`", f"- Rədd: `{doc['owner_actions']['reject']}`",
              f"- Qəbzi təzələ: `{doc['owner_actions']['refresh']}`", ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------- storage
def handover_dir(root, job_id: str):
    return child(root / "handover", job_id)


def _supersede(folder, names: tuple[str, ...]) -> None:
    """Rename the current dossier files into ``<folder>/archive/`` (they are superseded, never overwritten or removed)."""
    current = [folder / n for n in names if (folder / n).is_file()]
    if not current:
        return
    try:
        old = read_json(folder / "HANDOVER.json")
        tag = f"{(old.get('build_digest') or 'nodigest')[:12]}-{(old.get('receipt') or {}).get('receipt_id', 'noreceipt')}"
    except (OSError, ValueError):
        tag = "unreadable"
    archive = secure_mkdir(folder / "archive", folder)
    stamp = utc_stamp()
    for path in current:
        target, n = archive / f"{stamp}-{tag}-{path.name}", 0
        while target.exists():
            n += 1
            target = archive / f"{stamp}-{tag}-{n}-{path.name}"
        os.rename(path, target)
    fsync_directory(archive)
    fsync_directory(folder)


def _install(folder, doc: dict, md: str, root) -> None:
    _supersede(folder, ("HANDOVER.json", "HANDOVER.md"))
    write_atomic_json(folder / "HANDOVER.json", doc, root=root)
    write_atomic_bytes(folder / "HANDOVER.md", md.encode("utf-8"), root=root)


def write_handover(root, doc: dict) -> dict:
    """Current dossier at ``handover/<job>/HANDOVER.{md,json}`` plus the copy bound to its build digest at
    ``handover/<job>/<digest>/HANDOVER.{md,json}``; a previous dossier is renamed into an ``archive/`` folder first."""
    md = render_md(doc)
    folder = handover_dir(root, doc["job_id"])
    _install(folder, doc, md, root)
    _install(child(folder, doc["build_digest"]), doc, md, root)
    rel = folder.relative_to(root).as_posix()      # relative to studio/: events and job documents never carry absolute paths
    return {"dir": rel, "json": rel + "/HANDOVER.json", "md": rel + "/HANDOVER.md",
            "digest_dir": f"{rel}/{doc['build_digest']}"}


def _read_pair(folder) -> dict | None:
    path, md_path = folder / "HANDOVER.json", folder / "HANDOVER.md"
    if not (path.is_file() and md_path.is_file()):
        return None
    try:
        return {"json": read_json(path), "md": md_path.read_text(encoding="utf-8")}
    except (OSError, ValueError):
        return None


def load_latest(root, job_id: str, digest: str) -> dict | None:
    """The newest dossier for ``digest``: the job's current one when it is for that digest, else the digest's own copy."""
    folder = handover_dir(root, job_id)
    current = _read_pair(folder)
    if current is not None and current["json"].get("build_digest") == digest:
        return current
    return _read_pair(child(folder, digest)) if (folder / digest).is_dir() else None

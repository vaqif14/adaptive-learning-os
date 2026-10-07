"""Deterministic, offline learning workspace. Plans are input, never mastery."""
from __future__ import annotations

from html import escape
from pathlib import Path
import re
import shutil

from .utils import write_json


def normalize_plan(topic: str, goal: str | None, plan: dict | None) -> dict:
    if plan is None:
        plan = {"sources": [], "status": "needs_curriculum_research", "nodes": [
            {"id": "first-attempt", "title": "İlk praktik cəhd", "task":
             f"Məqsəd: {goal or topic}\n\nAgent məqsədə uyğun bir qısa tapşırıq hazırlamalıdır. "
             "Tapşırıq hazır olana qədər bu mərhələ başlanğıc çərçivəsidir.", "requires": []}]}
    if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
        raise ValueError("roadmap must contain a non-empty nodes array")
    sources = plan.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(s, str) or not s.startswith(("https://", "http://")) for s in sources):
        raise ValueError("roadmap sources must be HTTP(S) URLs")
    nodes, seen = [], set()
    for node in plan["nodes"]:
        if not isinstance(node, dict):
            raise ValueError("roadmap node must be an object")
        nid = node.get("id")
        if not isinstance(nid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", nid) or nid in seen:
            raise ValueError("roadmap node IDs must be unique safe slugs")
        for field in ("title", "task"):
            if not isinstance(node.get(field), str) or not node[field].strip():
                raise ValueError(f"roadmap node requires {field}")
        requires = node.get("requires", [])
        if not isinstance(requires, list) or any(not isinstance(r, str) or r not in seen for r in requires):
            raise ValueError("prerequisites must reference earlier nodes (acyclic order)")
        if not isinstance(node.get("lesson", ""), str):
            raise ValueError("lesson must be text")
        if not isinstance(node.get("stage", ""), str):
            raise ValueError("stage must be text")
        pitfalls = node.get("pitfalls", [])
        if not isinstance(pitfalls, list) or any(not isinstance(x, str) or not x.strip() for x in pitfalls):
            raise ValueError("pitfalls must be a list of non-empty strings (where people get stuck)")
        nodes.append({"stage": node.get("stage", ""), "lesson": node.get("lesson", ""), "id": nid, "title": node["title"], "task": node["task"], "requires": requires, "pitfalls": pitfalls, "status": "not_assessed"})
        seen.add(nid)
    return {"topic": topic, "goal": goal, "status": plan.get("status", "proposed"), "sources": sources, "nodes": nodes}


def page(title: str, content: str) -> str:
    return f'''<!doctype html>
<html lang="az"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title><style>
:root{{font-family:system-ui,sans-serif;color:#e8edf6;background:#0c1321}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1000px;margin:auto;padding:48px 24px}}a{{color:#99c8ff}}h1{{font-size:clamp(28px,5vw,48px);letter-spacing:-1px}}p{{line-height:1.7;color:#b3c0d4}}.tag{{color:#eac769;font-size:13px;text-transform:uppercase;letter-spacing:2px}}.path{{max-width:650px;margin:40px auto}}.node{{position:relative;border:1px solid #53617a;border-radius:12px;background:#172339;padding:24px;margin:0 0 32px}}.node:not(:last-child)::after{{content:"";position:absolute;height:33px;width:2px;background:#eac769;bottom:-33px;left:50%}}.node a{{display:block;font-weight:650;font-size:20px;text-decoration:none}}.node small{{display:block;margin-top:12px;color:#b3c0d4}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#172339;padding:24px;border-radius:12px;line-height:1.7}}.links{{display:flex;gap:20px;flex-wrap:wrap}}footer{{border-top:1px solid #344259;margin-top:40px;padding-top:20px;color:#b3c0d4}}
</style><main>{content}</main></html>'''


def create_workspace(root: Path, plan: dict, session_id: str) -> dict:
    # Called once inside the transactional session directory; no learner files overwritten.
    root.mkdir(exist_ok=False)
    write_json(root / "roadmap.json", plan)
    (root / "resources").mkdir()
    (root / "resources" / "SOURCES.md").write_text("# Mənbələr\n\n" + "\n".join(plan["sources"]) + "\n", encoding="utf-8")
    cards = []
    for i, node in enumerate(plan["nodes"], 1):
        relative = f'modules/{node["id"]}'
        folder = root / relative
        folder.mkdir(parents=True)
        for name in ("submission", "checks", "feedback"):
            (folder / name).mkdir()
            (folder / name / ".gitkeep").touch()
        pit = ("\n## Harada ilişirlər (diqqət)\n" + "\n".join(f"- {x}" for x in node["pitfalls"]) + "\n") if node["pitfalls"] else ""
        (folder / "TASK.md").write_text(f'# {node["title"]}\n\n{node["task"]}\n{pit}\n'
            'İşini `submission/` daxilində yaz. Sonra agentə “yoxla” de.\n'
            'Yoxlama meyarları `checks/`, yoxlama nəticəsi `feedback/` daxilində saxlanır.\n', encoding="utf-8")
        pit_html = ("<h2>Harada ilişirlər</h2><ul>" + "".join(f"<li>{escape(x)}</li>" for x in node["pitfalls"]) + "</ul>") if node["pitfalls"] else ""
        (folder / "index.html").write_text(page(node["title"],
            f'<a href="../../index.html">← Yol xəritəsi</a><h1>{escape(node["title"])}</h1>'
            f'<pre>{escape(node["task"])}</pre>' + pit_html + '<p>İşini bu mərhələnin submission/ qovluğunda yaz və agentə “yoxla” de.</p>'
            '<div class="links"><a href="TASK.md">Tapşırıq faylı</a></div>'
            '<footer>Hələ qiymətləndirilməyib. Səhifəni açmaq tamamlanma sübutu deyil.</footer>'), encoding="utf-8")
        deps = ", ".join(node["requires"]) or "Başlanğıc"
        cards.append(f'<article class="node"><span class="tag">Mərhələ {i:02}</span>'
                     f'<a href="{relative}/index.html">{escape(node["title"])}</a>'
                     f'<small>Əvvəl: {escape(deps)} · Hələ qiymətləndirilməyib</small></article>')
    sources = "".join(f'<p><a href="{escape(s, quote=True)}" rel="noreferrer">{escape(s)}</a></p>' for s in plan["sources"])
    (root / "index.html").write_text(page(plan["topic"],
        f'<span class="tag">Adaptive Learning · Praktik yol xəritəsi</span><h1>{escape(plan["topic"])}</h1>'
        f'<p>{escape(plan["goal"] or "Məqsəd hələ dəqiqləşdirilməyib")}</p>'
        '<p>Tapşırığı aç → qovluqda işini yaz → agentə yoxlat → rəyi tətbiq et.</p>'
        f'<p>Plan: {escape(str(plan["status"]))}</p><section class="path">{"".join(cards)}</section>'
        f'<h2>Mənbələr</h2>{sources}<footer>Proqres yoxlanmış nəticələr əsasında saxlanır. Bu səhifə başlanğıc planının görünüşüdür.</footer>'), encoding="utf-8")
    (root / "README.md").write_text(f'# {plan["topic"]}\n\nHTML yol xəritəsi: `index.html`\n'
        f'Runtime session: `{session_id}`\n\n'
        'Hər modul: TASK.md, index.html, submission/, checks/, feedback/.\n'
        'Agent tapşırığı və meyarları hazırlayır; öyrənən submission/ daxilində işləyir.\n'
        'Agent faylları oxuyur, uyğun verifier ilə yoxlayır, rəyi saxlayır və runtime ledger-ə sübut əlavə edir.\n'
        'Hazır cavablar öyrənənin yerinə yazılmır. Git/GitHub bağlantısı məcburi deyil.\n', encoding="utf-8")
    frontend = root / "frontend"
    shutil.copytree(Path(__file__).resolve().parents[1] / "templates" / "react", frontend)
    (frontend / "index.html.template").rename(frontend / "index.html")
    write_json(frontend / "src" / "data" / "roadmap.json", {**plan, "workspace_directory": str(root.resolve())})
    (frontend / ".gitignore").write_text("node_modules/\ndist/\n", encoding="utf-8")
    with (root / "README.md").open("a", encoding="utf-8") as f:
        f.write("\nReact interfeysi: frontend/ (npm install, npm run dev). Dərslər və tapşırıq şərtləri burada; kod practice-i IDE-də submission/ daxilində.\n")
    return {"frontend_directory": str(frontend.resolve()), "directory": str(root.resolve()), "index_html": str((root / "index.html").resolve()), "roadmap_json": str((root / "roadmap.json").resolve())}

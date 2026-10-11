"""Offline interactive roadmap (roadmap.sh-inspired) for learner review.

A single self-contained HTML file: no network, no external assets, inline
script pinned by a CSP sha256 hash, roadmap data embedded as inert JSON. It
renders the dependency graph, opens node details (skills, outcomes,
prerequisites, practice, sources) by click or keyboard, and collapses to a
stacked list on small screens. A file:// page cannot write learner state, so
approval happens in the served course app or through the CLI; the page shows
the exact version being approved.
"""
from __future__ import annotations

import base64
import hashlib
import json
from html import escape

_SCRIPT = r"""
(function () {
  var data = JSON.parse(document.getElementById('roadmap-data').textContent);
  var nodes = data.nodes, byId = {};
  nodes.forEach(function (n) { byId[n.id] = n; });
  var level = {}, rows = [];
  nodes.forEach(function (n) {
    var l = 0; (n.requires || []).forEach(function (r) { l = Math.max(l, (level[r] || 0) + 1); });
    level[n.id] = l; (rows[l] = rows[l] || []).push(n);
  });
  var W = 230, H = 132, width = Math.max(320, Math.max.apply(null, rows.map(function (r) { return r.length; })) * W);
  var height = rows.length * H + 20, pos = {};
  rows.forEach(function (row, l) { row.forEach(function (n, i) { pos[n.id] = { x: width * (i + 0.5) / row.length, y: l * H + 10 }; }); });
  var canvas = document.getElementById('canvas');
  canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
  var svg = document.getElementById('edges');
  svg.setAttribute('width', width); svg.setAttribute('height', height);
  nodes.forEach(function (n) {
    (n.requires || []).forEach(function (r) {
      var a = pos[r], b = pos[n.id]; if (!a) return;
      var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      var y1 = a.y + 84, mid = (y1 + b.y) / 2;
      p.setAttribute('d', 'M ' + a.x + ' ' + y1 + ' V ' + mid + ' H ' + b.x + ' V ' + b.y);
      svg.appendChild(p);
    });
  });
  var list = document.getElementById('nodes'), buttons = {};
  nodes.forEach(function (n, i) {
    var li = document.createElement('li');
    li.style.left = (pos[n.id].x - 100) + 'px'; li.style.top = pos[n.id].y + 'px';
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'node'; b.dataset.id = n.id;
    b.setAttribute('aria-describedby', 'hint');
    var num = document.createElement('span'); num.className = 'num'; num.textContent = 'MƏRHƏLƏ ' + String(i + 1).padStart(2, '0');
    var t = document.createElement('strong'); t.textContent = n.title;
    var d = document.createElement('small'); d.textContent = (n.requires || []).length ? 'Əvvəl: ' + n.requires.map(function (r) { return byId[r] ? byId[r].title : r; }).join(', ') : 'Başlanğıc mərhələ';
    b.appendChild(num); b.appendChild(t); b.appendChild(d);
    b.addEventListener('click', function () { show(n.id); });
    b.addEventListener('keydown', function (e) { nav(e, n); });
    li.appendChild(b); list.appendChild(li); buttons[n.id] = b;
  });
  function nav(e, n) {
    var row = rows[level[n.id]], idx = row.indexOf(n), target = null;
    if (e.key === 'ArrowRight') target = row[idx + 1];
    else if (e.key === 'ArrowLeft') target = row[idx - 1];
    else if (e.key === 'ArrowUp') target = byId[(n.requires || [])[0]];
    else if (e.key === 'ArrowDown') target = nodes.filter(function (m) { return (m.requires || []).indexOf(n.id) >= 0; })[0];
    if (target) { e.preventDefault(); buttons[target.id].focus(); }
  }
  function add(parent, tag, text) { var el = document.createElement(tag); el.textContent = text; parent.appendChild(el); return el; }
  function section(parent, title, items) {
    if (!items || !items.length) return;
    add(parent, 'h3', title); var ul = add(parent, 'ul', '');
    items.forEach(function (x) { add(ul, 'li', x); });
  }
  function show(id) {
    var n = byId[id], panel = document.getElementById('detail');
    Object.keys(buttons).forEach(function (k) { buttons[k].setAttribute('aria-pressed', k === id ? 'true' : 'false'); });
    panel.textContent = '';
    add(panel, 'p', n.exam_domain || 'Mərhələ').className = 'eyebrow';
    add(panel, 'h2', n.title);
    add(panel, 'p', n.task);
    section(panel, 'Bacarıqlar', n.skills);
    section(panel, 'Nəticədə edə biləcəksən', n.outcomes);
    section(panel, 'İlkin şərtlər', (n.requires || []).map(function (r) { return byId[r] ? byId[r].title : r; }));
    if (n.practice) { add(panel, 'h3', 'Gözlənilən praktika'); add(panel, 'p', n.practice); }
    section(panel, 'Mənbə əsası', (n.source_refs || []).map(function (k) { var s = data.source_titles[k]; return s ? s : k + ' (mənbə manifestində yoxdur)'; }));
    section(panel, 'Harada ilişirlər', n.pitfalls);
    panel.hidden = false; panel.focus();
  }
})();
"""


def render_roadmap_html(plan: dict, *, roadmap_version: str, approved: bool, approve_command: str,
                        source_titles: dict[str, str]) -> str:
    data = {"nodes": [{k: n.get(k) for k in ("id", "title", "task", "requires", "skills", "outcomes", "practice",
                                              "source_refs", "pitfalls", "exam_domain")} for n in plan["nodes"]],
            "source_titles": source_titles}
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    sha = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode()
    csp = (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{sha}'; "
           "base-uri 'none'; form-action 'none'")
    status = ("Təsdiqlənib" if approved else
              "Təsdiq gözləyir — kurs yaradılması və dərslər təsdiqdən sonra başlayır")
    return f"""<!doctype html>
<html lang="az"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">
<title>{escape(plan['topic'])} — yol xəritəsi</title>
<style>
:root{{color-scheme:dark;--bg:#0b1120;--panel:#121c30;--line:#d6b55e;--text:#e8edf6;--muted:#aab8ce;--focus:#79b8ff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text)}}
a{{color:#99c8ff}}main{{max-width:1240px;margin:auto;padding:32px 16px}}
.skip{{position:absolute;left:-999px}}.skip:focus{{left:16px;top:8px;background:#fff;color:#000;padding:8px}}
.eyebrow{{font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#e8bf64}}
h1{{font-size:clamp(26px,4vw,44px);margin:8px 0}}p{{line-height:1.6;color:var(--muted)}}
.status{{border:1px solid #37435b;border-radius:10px;padding:12px 16px;background:var(--panel)}}
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:24px;margin-top:24px}}
.viewport{{overflow:auto;border:1px solid #263047;border-radius:12px;max-height:72vh}}
#canvas{{position:relative;margin:auto}}#edges{{position:absolute;inset:0}}#edges path{{fill:none;stroke:var(--line);stroke-width:2}}
#nodes{{list-style:none;margin:0;padding:0}}#nodes li{{position:absolute;width:200px}}
.node{{width:200px;min-height:84px;text-align:left;padding:10px 12px;border:1px solid #37435b;border-radius:10px;background:var(--panel);color:var(--text);font:inherit;cursor:pointer;display:grid;gap:4px}}
.node:focus-visible{{outline:3px solid var(--focus);outline-offset:3px}}.node[aria-pressed=true]{{border-color:#e8bf64;background:#202b40}}
.num{{font-size:10px;letter-spacing:1px;color:#e8bf64}}.node small{{color:var(--muted);font-size:11px}}
#detail{{background:var(--panel);border:1px solid #37435b;border-radius:12px;padding:20px;align-self:start}}#detail:focus{{outline:3px solid var(--focus)}}
code{{background:#1b2840;padding:2px 6px;border-radius:6px;overflow-wrap:anywhere}}
@media (max-width:820px){{.layout{{grid-template-columns:1fr}}.viewport{{max-height:none;overflow:visible;border:0}}
#canvas{{width:auto!important;height:auto!important}}#edges{{display:none}}#nodes li{{position:static;width:auto;margin-bottom:12px}}.node{{width:100%}}}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}
</style></head><body>
<a class="skip" href="#detail">Mərhələ təfərrüatına keç</a>
<main>
<p class="eyebrow">Yol xəritəsi · nəzərdən keçirmək üçün</p>
<h1>{escape(plan['topic'])}</h1>
<p>{escape(plan.get('goal') or '')}</p>
<div class="status" role="status"><strong>{escape(status)}</strong><br>
Versiya: <code>{escape(roadmap_version)}</code><br>
Bu fayl yalnız baxış üçündür və vəziyyəti dəyişə bilmir. Təsdiq kurs tətbiqində edilir və ya öyrənən özü bu əmri işlədir:<br>
<code>{escape(approve_command)}</code></div>
<p id="hint">Mərhələni açmaq üçün klik et və ya Enter bas. Ox düymələri: sağ/sol — eyni səviyyə, yuxarı — ilkin şərt, aşağı — növbəti mərhələ.</p>
<div class="layout">
<div class="viewport" tabindex="0" aria-label="Asılılıq diaqramı"><div id="canvas"><svg id="edges" aria-hidden="true"></svg><ol id="nodes" aria-label="Mərhələlər asılılıq sırası ilə"></ol></div></div>
<aside id="detail" tabindex="-1" aria-live="polite"><p>Təfərrüat üçün mərhələ seç.</p></aside>
</div>
</main>
<script type="application/json" id="roadmap-data">{blob}</script>
<script>{_SCRIPT}</script>
</body></html>
"""

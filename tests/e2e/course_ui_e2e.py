"""Browser E2E for the local course app on a SYNTHETIC fixture course (CI-safe).

Optional tool, not collected by pytest and not a package dependency: it needs
`pip install playwright` + `playwright install chromium` and the frontend's own
npm dependencies (or `--node-modules` pointing at an existing install).

Everything runs against the real built UI and the real local bridge
(`runtime.server.make_server`) on loopback; the course content is the labelled
TEST FIXTURE from tests/course_fixture.py. The script acts as the host agent only
where the agent acts in production (authoring content, re-validating sources,
proposing a roadmap change) and as the learner through the browser everywhere else.

Every log entry is a real assertion: there are no unconditional passes. Telemetry
(console messages, request failures, timings) is recorded separately and never
counted as a check. Each screenshot is verified on disk (PNG signature, size,
sha256) and a Playwright trace is retained.

Exit code: 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from course_fixture import (CONCEPT_ITEMS, CONCEPT_LESSON, CONCEPT_NODE, EXAM_CORRECT, NEAREST_REF,  # noqa: E402
                            PRACTICE_CORRECT, PRACTICE_WRONG_ADD, ROADMAP, IsolatedState, items, lesson,
                            manifest_for)
from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402

FREE_TEXT_ANSWER = ("TEST FIXTURE cavabı: dot product uzun vektorlarda böyüyür; uzunluqlara böləndə yalnız "
                    "istiqamət qalır və nəticə müqayisə olunur.")
SELF_EXPLANATION = ("TEST FIXTURE cavabı: dot product-u iki vektorun uzunluğuna bölürük ki, uzunluq təsir etməsin "
                    "və yalnız istiqamət müqayisə olunsun.")
EXPECTED = [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}]


def e2e_roadmap():
    plan = copy.deepcopy(ROADMAP)
    plan["nodes"].insert(1, copy.deepcopy(CONCEPT_NODE))      # vec-sim, vec-ideas, vec-search
    return plan


class Clock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t


class Run:
    def __init__(self, out: Path):
        self.out = out
        (out / "screenshots").mkdir(parents=True, exist_ok=True)
        self.checks, self.telemetry, self.shots = [], [], []
        self.started = time.time()

    def check(self, name, ok, detail=None):
        if not isinstance(ok, bool):
            raise TypeError(f"check {name!r} must be given a computed bool, got {type(ok).__name__}")
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail is not None else ""), flush=True)
        return ok

    def note(self, name, value):
        self.telemetry.append({"telemetry": name, "value": value})

    def shot(self, page, name, full=True):
        path = self.out / "screenshots" / f"{len(self.shots) + 1:02d}-{name}.png"
        page.screenshot(path=str(path), full_page=full)
        data = path.read_bytes() if path.is_file() else b""
        ok = data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 4000
        self.shots.append({"file": str(path.relative_to(self.out)), "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest(), "valid_png": ok})
        self.check(f"screenshot saved and non-trivial: {path.name}", ok, len(data))

    def finish(self):
        failed = [c for c in self.checks if not c["ok"]]
        report = {"summary": {"checks": len(self.checks), "passed": len(self.checks) - len(failed),
                              "failed": len(failed), "screenshots": len(self.shots),
                              "seconds": round(time.time() - self.started, 1)},
                  "checks": self.checks, "screenshots": self.shots, "telemetry": self.telemetry}
        (self.out / "e2e-log.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(report["summary"]))
        return 1 if failed or not self.checks else 0


class Server:
    def __init__(self, ws, sid, dist, port):
        self.ws, self.sid, self.dist, self.port, self.srv = ws, sid, dist, port, None

    def start(self):
        self.srv = make_server(ServerConfig(host="127.0.0.1", port=self.port, allow_anon=True, workspace=self.ws,
                                            ui_dir=self.dist, course_session=self.sid))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def new_course(ws, *, clock=None):
    sk = SessionKernel(ws)
    sid = sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığını hesablamaq və izah etmək",
                                                 stated_background="Python əsasları (TEST FIXTURE)"))["session_id"]
    k = CourseKernel(ws, clock=clock)
    d = sk.dir(sid)
    k.init(sid)
    k.record_sources(sid, manifest_for(d / "sources"), EXPECTED)
    version = k.propose_roadmap(sid, e2e_roadmap())["roadmap_version"]
    return sk, k, sid, d, version


def author(k, sid):
    """Host-agent authoring step (validated content; synthetic fixture)."""
    k.install_items(sid, "vec-sim", items())
    k.install_lesson(sid, "vec-sim", lesson())
    concept = copy.deepcopy(CONCEPT_ITEMS)
    concept["items"] = [concept["items"][0]]
    k.install_items(sid, "vec-ideas", concept)
    k.install_lesson(sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))


def build_frontend(frontend: Path, node_modules: str | None, run: Run) -> Path:
    nm = frontend / "node_modules"
    if node_modules and not nm.exists():
        nm.symlink_to(Path(node_modules).resolve(), target_is_directory=True)
    if not nm.exists():
        raise RuntimeError("Frontend dependencies unavailable: pass --node-modules with an existing install; this QA tool never installs software.")
    t = time.time()
    proc = subprocess.run([str(nm / ".bin" / "vite"), "build"], cwd=frontend, capture_output=True, text=True, timeout=600)
    (run.out / "vite-build.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    run.note("vite_build_seconds", round(time.time() - t, 1))
    run.check("frontend production build succeeds", proc.returncode == 0, proc.returncode)
    dist = frontend / "dist"
    run.check("built index.html exists", (dist / "index.html").is_file())
    return dist


def api(page, path, body=None):
    return page.evaluate("""async ([path, body]) => {
        const init = body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {};
        const r = await fetch(path, init); let data = null; try { data = await r.json(); } catch (e) {}
        return {status: r.status, data};
    }""", [path, body])


def state(page):
    return api(page, "/api/course/state")["data"]


def nodes(st):
    return {n["id"]: n for n in st["roadmap"]["nodes"]}


def wait_hash(page, prefix, timeout=10000):
    page.wait_for_function("p => location.hash.startsWith(p)", arg=prefix, timeout=timeout)
    return page.evaluate("() => location.hash")


def eventually(page, predicate_js, arg, timeout=8000):
    """True once the predicate holds in the page (re-hydration is asynchronous)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        page.wait_for_function(predicate_js, arg=arg, timeout=timeout)
        return True
    except PlaywrightTimeout:
        return False


def toolbar(page, name):
    # A phase can render before its mutation's follow-up refresh finishes.
    # Wait for the visible global busy indicator instead of racing that request.
    refresh = page.get_by_role("button", name="Vəziyyəti yenilə", exact=True)
    if refresh.count():
        page.wait_for_function("() => [...document.querySelectorAll('button')].some(b => b.innerText === 'Vəziyyəti yenilə' && !b.disabled)")
    return page.get_by_role("toolbar").get_by_role("button", name=name, exact=True)


def heading_clear_of_header(page):
    return page.evaluate("""() => {
        const h = document.querySelector('main h1'); const head = document.querySelector('header');
        if (!h || !head) return false;
        return h.getBoundingClientRect().top >= head.getBoundingClientRect().bottom - 1 || getComputedStyle(head).position !== 'sticky';
    }""")


# --------------------------------------------------------------------------- phases
def phase_approve(page, srv, run, k, sid):
    page.goto(srv.base + "/#/learn/vec-sim")
    h = wait_hash(page, "#/roadmap")
    run.check("direct lesson route before approval redirects to the roadmap", h == "#/roadmap", h)
    page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
    run.check("roadmap renders every node", page.locator("button.node").count() == 3, page.locator("button.node").count())
    run.shot(page, "roadmap-desktop")
    page.locator("button.node").first.focus()
    page.keyboard.press("Enter")
    title = page.locator("aside.detail h2").inner_text()
    run.check("keyboard Enter opens node detail", title == "Vektor oxşarlığı", title)
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    run.check("mobile roadmap has no horizontal page scroll", overflow <= 1, overflow)
    run.shot(page, "roadmap-mobile")
    page.set_viewport_size({"width": 1366, "height": 900})
    page.reload()
    button = page.get_by_role("button", name="Təsdiq edirəm", exact=True)
    run.check("approve is disabled until the learner confirms review", button.is_disabled())
    page.get_by_label("Mərhələləri, ilkin şərtləri və mənbələri nəzərdən keçirdim.").check()
    button.click()
    page.get_by_text("Növbəti missiya").first.wait_for()
    st = state(page)
    run.check("approval persisted through the UI channel", st["roadmap"]["approval_event"]["channel"] == "ui")
    run.check("teaching stays blocked until content exists",
              (st["next_mission"] or {}).get("blocked_on") == "lesson_content_missing", st["next_mission"])
    run.shot(page, "mission-after-approval")
    author(k, sid)


def phase_python(page, srv, run, sk, sid):
    page.goto(srv.base + "/#/")
    page.reload()                         # agent-side authoring is picked up on load/focus, not pushed
    page.locator("article.mission").get_by_role("button", name="İndi et", exact=True).click()
    page.get_by_text("Bu mövzu sənə nə qədər tanışdır?").first.wait_for()
    page.get_by_role("button", name="Bilmirəm — qısa izah et", exact=True).click()
    page.get_by_role("heading", name="Qısa izah", exact=True).wait_for()
    run.check("explanation carries a verified source quote",
              page.get_by_text("Mənbədən (yoxlanılıb)").first.is_visible())
    run.check("explanation shows the exact quoted source sentence",
              "dot product divided by the product of their lengths" in page.inner_text("main"))
    run.shot(page, "lesson-explanation")
    toolbar(page, "İndi et").click()
    page.get_by_role("heading", name="Əl ilə nümunə", exact=True).wait_for()
    run.check("worked example is labelled as different from the exam",
              page.get_by_text("imtahan tapşırığı fərqli olacaq").is_visible())
    page.get_by_label("Öz sözlərinlə izah et").fill(SELF_EXPLANATION)
    page.get_by_role("button", name="Göndər", exact=True).click()
    page.get_by_text("Açar anlayışların əhatəsi").first.wait_for()
    run.check("self-explanation feedback says it is coverage, not a grade",
              "düzgünlük qiyməti deyil" in page.inner_text("main"))
    toolbar(page, "Sonrakı").click()
    page.get_by_text("Bələdçili praktika").first.wait_for()
    practice_file = Path(api(page, "/api/course/lesson?node=vec-sim")["data"]["practice"]["file"])
    practice_file.write_text("# TEST FIXTURE answer with a misconception\n" + PRACTICE_WRONG_ADD, encoding="utf-8")
    toolbar(page, "Yoxla").click()
    page.get_by_text("Ehtimal olunan anlaşılmazlıq").first.wait_for()
    body = page.inner_text("main")
    run.check("practice feedback names the specific misconception", "Vurmaq əvəzinə toplamaq" in body)
    run.check("practice feedback quotes the learner's actual value", "Sənin nəticən: 10" in body)
    run.shot(page, "practice-misconception")
    toolbar(page, "Kömək").click()
    page.get_by_role("heading", name="İpucu 1/2", exact=True).wait_for()
    run.check("hint ladder starts at level 1", page.get_by_role("heading", name="İpucu 1/2", exact=True).is_visible())
    practice_file.write_text("# TEST FIXTURE answer after feedback\n" + PRACTICE_CORRECT, encoding="utf-8")
    toolbar(page, "Yoxla").click()
    go_exam = page.get_by_role("button", name="Müstəqil yoxlamaya keç", exact=True)
    go_exam.wait_for()
    go_exam.focus()
    go_exam.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    run.check("the page behind the dialog is inert",
              page.evaluate("() => document.querySelector('.app-body').inert === true"))
    run.check("confirm dialog focuses its primary action",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Başla")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    run.check("Tab stays inside the confirm dialog",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Başla")
    page.keyboard.press("Escape")
    run.check("Escape closes the confirm dialog", page.get_by_role("dialog").count() == 0)
    run.check("focus returns to the opener",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Müstəqil yoxlamaya keç")
    go_exam.click()
    page.get_by_role("button", name="Başla", exact=True).click()
    h = wait_hash(page, "#/exam/att_")
    att_id = h.split("/")[-1]
    run.check("exam route opened for a new attempt", att_id.startswith("att_"), h)
    page.get_by_text("Müstəqil yoxlama.").first.wait_for()
    run.check("exam heading is not hidden behind the sticky header", heading_clear_of_header(page))
    why = toolbar(page, "Niyə")
    why.focus()
    run.check("locked help stays focusable with aria-disabled and announces why",
              why.get_attribute("aria-disabled") == "true" and why.evaluate("b => b.disabled") is False
              and why.evaluate("b => b === document.activeElement")
              and "bağlıdır" in page.locator("#hint-why").inner_text())
    run.check("teaching API is locked during the exam",
              api(page, "/api/course/help", {"ui_schema": 2, "node": "vec-sim", "kind": "explain",
                                             "expected_revision": state(page)["revision"]})["status"] == 423)
    run.shot(page, "exam-locked")
    page.get_by_role("button", name="Cəhdi dayandır", exact=True).click()
    run.check("abandon confirmation focuses the safe choice",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Xeyr, imtahana qayıt")
    page.keyboard.press("Escape")
    run.check("Escape keeps the exam and returns focus to the trigger",
              page.get_by_role("alertdialog").count() == 0
              and page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Cəhdi dayandır")
    page.goto(srv.base + "/#/learn/vec-sim")
    run.check("direct lesson route during exam returns to the same attempt", wait_hash(page, "#/exam/") == h)
    page.go_back()
    page.wait_for_timeout(400)
    run.check("Back keeps the learner in the exam", page.evaluate("() => location.hash") == h)
    page.go_forward()
    tab = page.context.new_page()
    tab.goto(srv.base + "/#/")
    run.check("a second tab is routed to the same attempt", wait_hash(tab, "#/exam/") == h)
    tab.close()
    srv.stop()
    srv.start()
    page.reload()
    page.get_by_text("Müstəqil yoxlama.").first.wait_for()
    run.check("after a server restart the same attempt resumes", page.evaluate("() => location.hash") == h)
    att = state(page)["active_attempt"]
    Path(att["file"]).write_text("# TEST FIXTURE exam answer\n" + EXAM_CORRECT, encoding="utf-8")
    toolbar(page, "İndi et").click()
    wait_hash(page, "#/result/")
    page.get_by_role("heading", name="Keçdi: 4/4 yoxlama", exact=True).wait_for()
    run.check("result shows all hidden checks passed",
              page.get_by_role("heading", name="Keçdi: 4/4 yoxlama", exact=True).is_visible())
    run.shot(page, "exam-result")
    rev = state(page)["revision"]
    again = api(page, "/api/course/exam/submit", {"ui_schema": 2, "attempt_id": att_id, "expected_revision": rev})
    run.check("a repeated submit is a no-op replay", again["status"] == 200 and again["data"]["status"] == "finalized",
              again["status"])
    ev = [e for e in api(page, "/api/course/export")["data"]["evidence"] if e["attempt_id"] == att_id]
    run.check("exactly one evidence record exists for the attempt", len(ev) == 1, len(ev))
    stale = api(page, "/api/course/next", {"ui_schema": 2, "expected_revision": rev - 1})
    run.check("a stale revision is refused with the current state", stale["status"] == 409
              and stale["data"]["error"] == "revision_conflict" and "state" in stale["data"], stale["status"])
    review = state(page)["review_queue"]
    run.check("a first independent pass schedules a durable review",
              [(q["node_id"], q["status"]) for q in review] == [("vec-sim", "scheduled")], review)
    toolbar(page, "Sonrakı").click()
    page.get_by_text("Növbəti missiya").first.wait_for()
    run.check("next mission is the concept node", page.locator("#mission-title").inner_text() == "Oxşarlıq anlayışları",
              page.locator("#mission-title").inner_text())


def phase_concept(page, srv, run):
    page.locator("article.mission").get_by_role("button", name="İndi et", exact=True).click()
    page.get_by_role("button", name="Bilmirəm — qısa izah et", exact=True).click()
    page.get_by_role("heading", name="Qısa izah", exact=True).wait_for()
    toolbar(page, "Niyə").click()
    page.get_by_text("Əlavə izah — mənbədə yoxdur").first.wait_for()
    run.check("supplementary material is labelled as not from the source",
              page.get_by_text("Əlavə izah — mənbədə yoxdur").first.is_visible())
    toolbar(page, "Sonrakı").click()
    page.get_by_text("Bələdçili praktika · anlayış").first.wait_for()
    page.get_by_label("0", exact=True).check()
    page.get_by_role("button", name="Yoxla", exact=True).first.click()
    page.get_by_text("Ehtimal olunan anlaşılmazlıq").first.wait_for()
    run.check("concept practice diagnoses the chosen distractor",
              "Eyni istiqaməti perpendikulyarla qarışdırmaq" in page.inner_text("main"))
    run.shot(page, "concept-practice-feedback")
    page.get_by_label("1", exact=True).check()
    page.get_by_role("button", name="Yoxla", exact=True).first.click()
    page.get_by_role("button", name="Müstəqil yoxlamaya keç", exact=True).wait_for()
    run.check("correct concept practice unlocks the exam",
              page.get_by_role("button", name="Müstəqil yoxlamaya keç", exact=True).is_visible())
    page.get_by_role("button", name="Müstəqil yoxlamaya keç", exact=True).click()
    page.get_by_role("button", name="Başla", exact=True).click()
    wait_hash(page, "#/exam/att_")
    page.locator("fieldset.question").first.wait_for()
    run.check("concept exam renders every question", page.locator("fieldset.question").count() == 3)
    run.check("no answer key reaches the browser", '"answer"' not in json.dumps(state(page)))
    page.get_by_label("Vektorların istiqamət yaxınlığını", exact=True).check()
    page.get_by_label("Eyni istiqamətli vektorların kosinusu 1-dir", exact=True).check()
    page.get_by_label("Perpendikulyar vektorların kosinusu 0-dır", exact=True).check()
    page.get_by_role("button", name="Hər sənədlə kosinusu hesabla — aşağı", exact=True).click()
    page.get_by_role("button", name="Cavabları yadda saxla", exact=True).click()
    page.get_by_text("Yadda saxlanıb").first.wait_for()
    page.reload()
    page.locator("fieldset.question").first.wait_for()
    run.check("saved answers survive a refresh",
              page.get_by_label("Vektorların istiqamət yaxınlığını", exact=True).is_checked()
              and page.get_by_label("Perpendikulyar vektorların kosinusu 0-dır", exact=True).is_checked())
    run.shot(page, "concept-exam")
    toolbar(page, "İndi et").click()
    wait_hash(page, "#/result/")
    page.get_by_role("heading", name="Keçdi: 3/3 sual", exact=True).wait_for()
    run.check("concept exam is graded deterministically",
              page.get_by_role("heading", name="Keçdi: 3/3 sual", exact=True).is_visible())
    att = state(page)["assessment_result"]["attempt_id"]
    ev = [e for e in api(page, "/api/course/export")["data"]["evidence"] if e["attempt_id"] == att]
    run.check("selected/ordered responses are never strong evidence", len(ev) == 1 and ev[0]["strength"] != "strong",
              ev and ev[0]["strength"])
    run.shot(page, "concept-result")
    toolbar(page, "Sonrakı").click()


def phase_free_text(page, srv, run, k, sid):
    payload = copy.deepcopy(CONCEPT_ITEMS)
    payload["items"] = [payload["items"][1]]
    k.install_items(sid, "vec-ideas", payload)                       # agent authors an open question
    att = k.start_exam(sid, "vec-ideas", fast_track=True)["attempt"]
    page.goto("about:blank")
    page.goto(srv.base + f"/#/exam/{att['attempt_id']}")
    page.get_by_label("Cavabın", exact=True).wait_for()
    page.get_by_label("Cavabın", exact=True).fill("Qısa cavab")
    run.check("an answer under the minimum length cannot be submitted",
              toolbar(page, "İndi et").get_attribute("aria-disabled") == "true")
    page.get_by_label("Cavabın", exact=True).fill(FREE_TEXT_ANSWER)
    toolbar(page, "İndi et").click()
    page.get_by_role("heading", name="Cavabın rubrika ilə qiymətləndirilməsini gözləyir", exact=True).wait_for()
    run.check("an open answer waits for a rubric judgment under the exam lock",
              state(page)["teaching_locked"] and toolbar(page, "İndi et").get_attribute("aria-disabled") == "true")
    run.shot(page, "free-text-awaiting-rubric")
    k.judge_and_finalize(sid, att["attempt_id"], {
        "grader": "host_agent", "confidence": "medium",
        "rationale": "Cavab normallaşdırmanın uzunluğu aradan qaldırdığını və istiqaməti saxladığını deyir.",
        "criteria": {"r-normalize": {"met": True, "evidence_quote": "uzunluqlara böləndə"},
                     "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}}})
    page.goto("about:blank")
    page.goto(srv.base + f"/#/result/{att['attempt_id']}")
    page.get_by_role("heading", name="Keçdi: 2/2 meyar", exact=True).wait_for()
    run.check("a judged open answer shows who judged it and that it is an opinion",
              "Bu, runtime yoxlaması deyil, rəydir." in page.inner_text("main"))
    run.shot(page, "free-text-result")
    toolbar(page, "Sonrakı").click()


def phase_integrity(page, srv, run, k, sid, d):
    renamed = items()
    copy_item = copy.deepcopy(renamed["items"][0])
    copy_item.update(item_id="cosine-a-renamed", title="Yenidən adlandırılmış surət",
                     checks=list(reversed(copy_item["checks"])))
    out = k.install_items(sid, "vec-sim", {"node_id": "vec-sim", "items": [copy_item]})
    run.check("a renamed copy of a seen item is classified as a duplicate",
              out["freshness"]["cosine-a-renamed"]["status"] == "duplicate", out["freshness"]["cosine-a-renamed"]["status"])
    run.check("the duplicate adds no fresh item", nodes(state(page))["vec-sim"]["fresh_items"] == 1,
              nodes(state(page))["vec-sim"]["fresh_items"])
    stage = d / "sources-staging" / "stg_e2e_bad"
    bad = manifest_for(stage)
    bad["resources"][0]["status"] = "unreadable"
    bad["manifest_version"] = "sm-e2e-bad"
    k.record_sources(sid, bad, EXPECTED, staged_cache=stage)
    page.goto(srv.base + "/#/roadmap")
    page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
    run.check("roadmap labels the lesson whose source became invalid",
              eventually(page, "t => (document.querySelector('button.node') || {}).innerText?.includes(t)",
                         "mənbə yenidən yoxlanmalıdır"))
    page.goto(srv.base + "/#/learn/vec-sim")
    page.get_by_text("mənbəsi dəyişib").first.wait_for()
    run.check("invalid grounding blocks the lesson with a labelled historical snapshot",
              "Tarixi nüsxə" in page.inner_text("main"))
    run.shot(page, "grounding-invalid")
    good = manifest_for(d / "sources-staging" / "stg_e2e_good")
    good["manifest_version"] = "sm-e2e-good"
    k.record_sources(sid, good, EXPECTED, staged_cache=d / "sources-staging" / "stg_e2e_good")
    run.check("re-validated identical source restores readiness",
              nodes(state(page))["vec-sim"]["lesson"]["status"] == "ready")
    changed = e2e_roadmap()
    changed["nodes"][0]["outcomes"] = ["Kosinus və Evklid məsafəsini müqayisə edə bilir"]
    k.propose_roadmap(sid, changed)
    page.goto(srv.base + "/#/")
    page.get_by_role("heading", name="Yol xəritəsi hazırdır", exact=True).wait_for()
    page.goto(srv.base + "/#/roadmap")
    page.get_by_text("Təsdiqin proqresinə təsiri").first.wait_for()
    run.check("approval shows what a capability change supersedes before approving",
              "məqsəd dəyişir" in page.inner_text("main"))
    run.shot(page, "capability-impact")
    page.get_by_label("Mərhələləri, ilkin şərtləri və mənbələri nəzərdən keçirdim.").check()
    page.get_by_role("button", name="Təsdiq edirəm", exact=True).click()
    page.get_by_text("Növbəti missiya").first.wait_for()
    st = state(page)
    n = nodes(st)
    run.check("reapproval supersedes the changed capability's pass", n["vec-sim"]["status"] != "passed", n["vec-sim"]["status"])
    run.check("the old lesson is not presented as ready", n["vec-sim"]["lesson"]["status"] == "capability_changed")
    run.check("the superseded pass stays in history", len(n["vec-sim"]["progress"]["superseded_passes"]) == 1)
    run.check("the pending review of the changed capability is invalidated",
              [q["status"] for q in st["review_queue"] if q["node_id"] == "vec-sim"][:1] == ["invalidated"])
    run.check("unchanged concept progress is preserved", n["vec-ideas"]["status"] == "passed", n["vec-ideas"]["status"])


def phase_review(page, run, ws, dist, port):
    clock = Clock(datetime.now(UTC) - timedelta(days=2))
    sk, k, sid, d, version = new_course(ws, clock=clock)
    k.approve_roadmap(sid, version, channel="cli")
    author(k, sid)
    att = k.start_exam(sid, "vec-sim", fast_track=True)["attempt"]
    Path(att["file"]).write_text("# TEST FIXTURE\n" + EXAM_CORRECT, encoding="utf-8")
    k.submit_and_finalize(sid, att["attempt_id"])
    k.advance(sid)
    run.check("the agent driver dispatches the due review", sk.next_action(sid)["action"] == "course_due_review")
    srv = Server(ws, sid, dist, port).start()
    try:
        page.goto(srv.base + "/#/")
        page.get_by_text("Təkrar yoxlamanın vaxtıdır").first.wait_for()
        run.check("a due review outranks the stored mission after a day",
                  page.get_by_role("button", name="Təkrar yoxlamaya başla", exact=True).is_visible())
        for route in ("learn", "practice"):
            page.goto(srv.base + f"/#/{route}/vec-sim")
            wait_hash(page, "#/mission")
            run.check(f"due review guards direct {route} navigation",
                      page.get_by_role("button", name="Təkrar yoxlamaya başla", exact=True).is_visible())
        for endpoint in ("mission/start", "exam/start"):
            response = api(page, f"/api/course/{endpoint}",
                           {"ui_schema": 2, "expected_revision": state(page)["revision"],
                            "node": "vec-sim", "fast_track": True})
            run.check(f"due review refuses direct bridge {endpoint}",
                      response["status"] == 409 and response["data"].get("error") == "review_required",
                      response["status"])
        response = api(page, "/api/course/lesson?node=vec-sim")
        run.check("due review refuses direct lesson retrieval",
                  response["status"] == 409 and response["data"].get("error") == "review_required")
        run.shot(page, "due-review-mission")
        page.get_by_role("button", name="Təkrar yoxlamaya başla", exact=True).click()
        wait_hash(page, "#/exam/att_")
        page.get_by_text("Təkrar yoxlama ·").first.wait_for()
        cur = state(page)["active_attempt"]
        run.check("the review uses a fresh item, not the one already passed", cur["item"]["item_id"] == "nearest-b",
                  cur["item"]["item_id"])
        Path(cur["file"]).write_text("# TEST FIXTURE\n" + NEAREST_REF, encoding="utf-8")
        toolbar(page, "İndi et").click()
        wait_hash(page, "#/result/")
        page.get_by_text("Təkrar yoxlamanın nəticəsi").first.wait_for()
        q = state(page)["review_queue"]
        run.check("the review completes and the next one is scheduled further out",
                  [x["status"] for x in q] == ["completed", "scheduled"] and q[1]["interval_days"] == 2.0,
                  [(x["status"], x["interval_days"]) for x in q])
        ev = [e for e in api(page, "/api/course/export")["data"]["evidence"] if e["attempt_id"] == cur["attempt_id"]]
        run.check("review evidence does not claim spacing the ledger did not observe",
                  len(ev) == 1 and ev[0]["scope"] != "delayed_independent_performance", ev and ev[0]["scope"])
        page.goto(srv.base + "/#/progress")
        page.get_by_role("heading", name="Təkrar yoxlamalar", exact=True).wait_for()
        run.check("progress lists completed and scheduled reviews",
                  "tamamlanıb" in page.inner_text("main") and "planlaşdırılıb" in page.inner_text("main"))
        run.shot(page, "progress-reviews")
    finally:
        srv.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="e2e-artifacts")
    ap.add_argument("--node-modules", help="reuse an existing node_modules (no install)")
    ap.add_argument("--workspace", help="keep the fixture workspace here (default: inside --out)")
    args = ap.parse_args(argv)
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    out = Path(args.out).resolve()
    run = Run(out)
    ws = Path(args.workspace).resolve() if args.workspace else out / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    with IsolatedState():
        sk, k, sid, d, _ = new_course(ws)
        dist = build_frontend(d / "workspace" / "frontend", args.node_modules, run)
        port = free_port()
        srv = Server(ws, sid, dist, port).start()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="az-AZ")
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.on("console", lambda m: run.note("console", f"{m.type}: {m.text[:200]}") if m.type in {"error", "warning"} else None)
            page.set_default_timeout(15000)
            try:
                phase_approve(page, srv, run, k, sid)
                phase_python(page, srv, run, sk, sid)
                phase_concept(page, srv, run)
                phase_free_text(page, srv, run, k, sid)
                phase_integrity(page, srv, run, k, sid, d)
                srv.stop()
                srv = None
                phase_review(page, run, ws, dist, free_port())
            except (PlaywrightError, AssertionError, KeyError, ValueError, OSError) as e:
                run.check("all phases completed without an exception", False, f"{type(e).__name__}: {str(e)[:400]}")
                try:
                    run.shot(page, "failure")
                except PlaywrightError:
                    pass
            finally:
                run.check("no uncaught page errors", not errors, errors[:3])
                context.tracing.stop(path=str(out / "trace.zip"))
                browser.close()
                if srv is not None:
                    srv.stop()
        run.check("trace retained", (out / "trace.zip").is_file() and (out / "trace.zip").stat().st_size > 1000)
    return run.finish()


if __name__ == "__main__":
    sys.exit(main())

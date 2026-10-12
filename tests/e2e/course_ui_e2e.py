"""Browser E2E for the local course app (optional tool; needs `pip install playwright`
and `playwright install chromium`; not collected by pytest and not a package dependency).

Drives the real built UI against a running `alearn course serve` and records
screenshots + a JSON assertion log. Learner answers written here are PREPARED DEMO
ANSWERS (labelled in each file); they are not real learner work.

Phases (run in order; restart the server between `learn` and `resume` to test restart):
  approve  roadmap review, keyboard/mobile checks, guarded direct route, approval
  learn    beginner path: explain, worked example, self-explanation, practice with a
           prepared misconception, hint, method change, fresh exam, exam-lock checks
  resume   after a server restart: same attempt resumes, duplicate submit, result,
           next mission, refresh persistence, export evidence count
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PRACTICE_WRONG = ("# DEMO: prepared learner answer with a typical misconception (raw dot product)\n"
                  "def cosine_similarity(a, b):\n    return sum(x * y for x, y in zip(a, b))\n")
PRACTICE_RIGHT = ("# DEMO: prepared learner answer after feedback\nimport math\n\n\n"
                  "def cosine_similarity(a, b):\n    dot = sum(x * y for x, y in zip(a, b))\n"
                  "    na = math.sqrt(sum(x * x for x in a))\n    nb = math.sqrt(sum(y * y for y in b))\n"
                  "    if na == 0 or nb == 0:\n        return 0.0\n    return dot / (na * nb)\n")
EXAM_RIGHT = ("# DEMO: prepared learner answer for the unseen exam item\nimport math\n\n\n"
              "def _cos(a, b):\n    dot = sum(x * y for x, y in zip(a, b))\n"
              "    na = math.sqrt(sum(x * x for x in a))\n    nb = math.sqrt(sum(y * y for y in b))\n"
              "    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)\n\n\n"
              "def top_k(query, docs, k):\n    return sorted(range(len(docs)), key=lambda i: (-_cos(query, docs[i]), i))[:k]\n")
SELF_EXPLANATION = ("DEMO hazır cavab: Xam dot product uzun vektorlara üstünlük verir, çünki ədədlər böyüdükcə nəticə də böyüyür. "
                    "Kosinus dot product-u iki vektorun uzunluğuna bölür, yəni normallaşdırır, ona görə yalnız istiqamət qalır.")


class Run:
    def __init__(self, out: Path):
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = []

    def check(self, name, ok, detail=None):
        self.log.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail is not None else ""))

    def shot(self, page, name, full=True):
        path = self.out / f"{name}.png"
        page.screenshot(path=str(path), full_page=full)
        return str(path)

    def finish(self, phase):
        (self.out / f"{phase}-log.json").write_text(json.dumps(self.log, ensure_ascii=False, indent=1), encoding="utf-8")
        failed = [c for c in self.log if not c["ok"]]
        print(f"{phase}: {len(self.log) - len(failed)} passed, {len(failed)} failed")
        return 1 if failed else 0


def api(page, path, body=None):
    return page.evaluate("""async ([path, body]) => {
        const init = body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {};
        const r = await fetch(path, init); let data = null; try { data = await r.json(); } catch {}
        return {status: r.status, data};
    }""", [path, body])


def wait_hash(page, prefix, timeout=8000):
    page.wait_for_function("p => location.hash.startsWith(p)", arg=prefix, timeout=timeout)
    return page.evaluate("() => location.hash")


def phase_approve(page, base, run):
    page.goto(base + "/#/learn/embeddings-similarity")
    h = wait_hash(page, "#/roadmap")
    run.check("direct lesson route before approval redirects to roadmap", h == "#/roadmap", h)
    page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
    run.shot(page, "01-roadmap-desktop")
    first = page.locator("button.node").first
    first.focus()
    page.keyboard.press("Enter")
    title = page.locator("aside.detail h2").inner_text()
    run.check("keyboard Enter opens node detail", "kosinus" in title.lower(), title)
    page.keyboard.press("ArrowDown")
    focused = page.evaluate("() => document.activeElement && document.activeElement.innerText")
    run.check("ArrowDown moves focus to a dependent node", "Transformer" in (focused or "") or "RAG" in (focused or ""), focused)
    run.shot(page, "02-roadmap-detail-keyboard", full=False)
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    run.check("mobile roadmap has no horizontal page scroll", overflow <= 1, overflow)
    run.shot(page, "03-roadmap-mobile")
    page.set_viewport_size({"width": 1366, "height": 900})
    page.reload()
    button = page.get_by_role("button", name="Təsdiq edirəm", exact=True)
    run.check("approve button disabled until review is confirmed", button.is_disabled())
    page.get_by_label("Mərhələləri, ilkin şərtləri və mənbələri nəzərdən keçirdim.").check()
    button.click()
    page.get_by_text("Növbəti missiya").first.wait_for()
    state = api(page, "/api/course/state")["data"]
    run.check("approval persisted with ui channel", state["roadmap"]["approval_event"]["channel"] == "ui",
              state["roadmap"]["approval_event"]["roadmap_version"])
    run.check("phase is course_generation after approval", state["phase"] == "course_generation", state["phase"])
    run.shot(page, "04-mission-after-approval")


def phase_learn(page, base, run, out):
    page.goto(base + "/#/")
    page.get_by_text("Növbəti missiya").first.wait_for()
    page.locator("article.mission").get_by_role("button", name="İndi et", exact=True).click()
    page.get_by_text("Bu mövzu sənə nə qədər tanışdır?").first.wait_for()
    run.shot(page, "05-lesson-entry")
    page.get_by_role("button", name="Bilmirəm — qısa izah et", exact=True).click()
    page.get_by_role("heading", name="Qısa izah", exact=True).wait_for()
    run.check("explanation shows source-grounded quote", page.get_by_text("Mənbədən").first.is_visible())
    run.shot(page, "06-explanation")
    page.get_by_role("toolbar").get_by_role("button", name="İndi et", exact=True).click()
    page.get_by_role("heading", name="Əl ilə nümunə: istiqamət eyni, uzunluq fərqli", exact=True).wait_for()
    run.check("worked example is separate and labelled as not the exam",
              page.get_by_text("imtahan tapşırığı fərqli olacaq").is_visible())
    run.shot(page, "07-worked-example")
    page.get_by_label("Öz sözlərinlə izah et").fill(SELF_EXPLANATION)
    page.get_by_role("button", name="Göndər", exact=True).click()
    page.get_by_text("Açar anlayışların əhatəsi").first.wait_for()
    run.shot(page, "08-self-explanation")
    page.get_by_role("toolbar").get_by_role("button", name="Sonrakı", exact=True).click()
    page.get_by_text("Bələdçili praktika").first.wait_for()
    run.shot(page, "09-practice")
    lesson = api(page, "/api/course/lesson?node=embeddings-similarity")["data"]
    practice_file = Path(lesson["practice"]["file"])
    practice_file.write_text(PRACTICE_WRONG, encoding="utf-8")
    page.get_by_role("toolbar").get_by_role("button", name="Yoxla", exact=True).click()
    page.get_by_text("Ehtimal olunan anlaşılmazlıq").first.wait_for()
    body = page.inner_text("main")
    run.check("feedback quotes the learner's actual value", "Sənin nəticən: 10" in body)
    run.check("misconception named specifically", "Normallaşdırmadan xam dot product" in body)
    run.shot(page, "10-practice-misconception")
    page.get_by_role("toolbar").get_by_role("button", name="Kömək", exact=True).click()
    page.get_by_role("heading", name="İpucu 1/3", exact=True).wait_for()
    page.get_by_role("toolbar").get_by_role("button", name="Yoxla", exact=True).click()
    page.get_by_role("button", name="Başqa yolla izah et", exact=True).wait_for()
    page.get_by_role("button", name="Başqa yolla izah et", exact=True).click()
    page.get_by_role("heading", name="Başqa yolla izah", exact=True).wait_for()
    run.check("recurring error switches method (alternative explanation)", True)
    run.shot(page, "11-method-change")
    practice_file.write_text(PRACTICE_RIGHT, encoding="utf-8")
    page.get_by_role("toolbar").get_by_role("button", name="Yoxla", exact=True).click()
    page.get_by_text("4/4 keçdi").first.wait_for()
    run.shot(page, "12-practice-pass")
    page.get_by_role("button", name="Müstəqil yoxlamaya keç", exact=True).click()
    page.get_by_role("button", name="Başla", exact=True).click()
    h = wait_hash(page, "#/exam/")
    attempt = h.split("/")[-1]
    page.get_by_text("Müstəqil yoxlama.").first.wait_for()
    run.shot(page, "13-exam")
    run.check("help actions disabled in exam",
              page.get_by_role("toolbar").get_by_role("button", name="Kömək", exact=True).is_disabled()
              and page.get_by_role("toolbar").get_by_role("button", name="Niyə", exact=True).is_disabled())
    page.goto(base + "/#/learn/embeddings-similarity")
    h2 = wait_hash(page, "#/exam/")
    run.check("direct lesson route during exam returns to same attempt", h2.endswith(attempt), h2)
    page.go_back()
    time.sleep(0.5)
    run.check("Back keeps the exam route", page.evaluate("() => location.hash").endswith(attempt),
              page.evaluate("() => location.hash"))
    page.reload()
    page.get_by_text("Müstəqil yoxlama.").first.wait_for()
    run.check("refresh resumes the same attempt", page.evaluate("() => location.hash").endswith(attempt))
    second = page.context.new_page()
    second.goto(base + "/#/roadmap")
    h3 = wait_hash(second, "#/exam/")
    run.check("second tab is routed to the same active attempt", h3.endswith(attempt), h3)
    run.shot(second, "14-exam-second-tab")
    lesson_api = api(second, "/api/course/lesson?node=embeddings-similarity")
    run.check("lesson API locked (423) during exam", lesson_api["status"] == 423, lesson_api["status"])
    state = api(second, "/api/course/state")["data"]
    help_api = api(second, "/api/course/help", {"ui_schema": 1, "node": "embeddings-similarity", "kind": "explain",
                                               "expected_revision": state["revision"]})
    run.check("help API locked (423) during exam", help_api["status"] == 423, help_api["status"])
    restart = api(second, "/api/course/exam/start", {"ui_schema": 1, "node": "embeddings-similarity",
                                                     "expected_revision": state["revision"]})
    run.check("start-exam again resumes, never mints a new attempt",
              restart["status"] == 200 and restart["data"]["resumed"] and restart["data"]["attempt"]["attempt_id"] == attempt)
    second.close()
    (out / "attempt.txt").write_text(attempt, encoding="utf-8")


def phase_resume(page, base, run, out):
    attempt = (out / "attempt.txt").read_text(encoding="utf-8").strip()
    page.goto(base + "/#/")
    h = wait_hash(page, "#/exam/")
    run.check("after server restart the same attempt is still active", h.endswith(attempt), h)
    run.shot(page, "15-exam-after-restart")
    state = api(page, "/api/course/state")["data"]
    Path(state["active_attempt"]["file"]).write_text(EXAM_RIGHT, encoding="utf-8")
    # Duplicate click: two synchronous clicks on the submit action.
    page.evaluate("() => { const b = [...document.querySelectorAll('[role=toolbar] button')]"
                  ".find(x => x.textContent.startsWith('İndi et')); b.click(); b.click(); }")
    wait_hash(page, "#/result/", timeout=60000)
    page.get_by_role("heading", name="Gizli yoxlamalar", exact=False).first.wait_for()
    head = page.locator("h1").inner_text()
    run.check("fresh unassisted exam finalized as passed", head.startswith("Keçdi"), head)
    run.shot(page, "16-result")
    page.reload()
    page.get_by_text("Gizli yoxlamalar").first.wait_for()
    run.check("finalized result survives reload", page.locator("h1").inner_text().startswith("Keçdi"))
    export = api(page, "/api/course/export")["data"]
    exam_ev = [e for e in export["evidence"] if e.get("attempt_id") == attempt and e.get("verification") == "hidden_checks"]
    run.check("duplicate submit produced exactly one exam evidence record", len(exam_ev) == 1, len(exam_ev))
    run.check("exam evidence is strong + unassisted",
              bool(exam_ev) and exam_ev[0]["strength"] == "strong" and exam_ev[0]["independence"] == "unassisted")
    practice_ev = [e for e in export["evidence"] if e.get("source") == "course_practice"]
    run.check("guided practice stayed assisted/supported (never strong)",
              bool(practice_ev) and all(e["strength"] == "medium" and e["scope"] == "supported_completion" for e in practice_ev),
              len(practice_ev))
    mission = export["course"]["next_mission"]
    run.check("next mission is evidence-derived with a reason", bool(mission and mission.get("reason_az")),
              mission and mission.get("type"))
    page.get_by_role("toolbar").get_by_role("button", name="Sonrakı", exact=True).click()
    page.get_by_text("Növbəti missiya").first.wait_for()
    run.shot(page, "17-next-mission")
    page.goto(base + "/#/progress")
    page.get_by_role("heading", name="Proqres və sübut", exact=True).wait_for()
    run.shot(page, "18-progress")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base + "/#/")
    page.get_by_text("Növbəti missiya").first.wait_for()
    overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    run.check("mobile mission view has no horizontal scroll", overflow <= 1, overflow)
    run.shot(page, "19-mission-mobile")
    (out / "export.json").write_text(json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["approve", "learn", "resume"])
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
    out = Path(args.out)
    run = Run(out)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="az-AZ")
        page = context.new_page()
        errors, http_errors = [], []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        def on_response(resp):
            if resp.status >= 400:
                try:
                    body = resp.text()[:300]
                except PlaywrightError:
                    body = None
                http_errors.append({"status": resp.status, "method": resp.request.method, "url": resp.url, "body": body})
        page.on("response", on_response)
        phases = {"approve": lambda: phase_approve(page, args.base, run),
                  "learn": lambda: phase_learn(page, args.base, run, out),
                  "resume": lambda: phase_resume(page, args.base, run, out)}
        try:
            phases[args.phase]()
        except (PlaywrightError, AssertionError, KeyError, TypeError, OSError) as exc:
            run.check(f"phase {args.phase} completed without exception", False, repr(exc)[:300])
            run.shot(page, f"error-{args.phase}")
        expected_locks = [e for e in http_errors if e["status"] == 423]   # exam-lock probes in `learn`
        unexpected = [e for e in http_errors if e["status"] != 423]
        run.check("no unexpected HTTP errors", not unexpected, unexpected[:3])
        run.check("no browser console errors (423 lock probes excluded)",
                  len([e for e in errors if "423" not in e]) == 0, errors[:3])
        run.log.append({"check": "http_423_lock_responses_observed", "ok": True, "detail": len(expected_locks)})
        browser.close()
    sys.exit(run.finish(args.phase))


if __name__ == "__main__":
    main()

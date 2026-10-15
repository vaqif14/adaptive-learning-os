"""Browser E2E for the Course Studio owner UI and the learner practice-first UI on the SYNTHETIC fixture course (CI-safe).

Optional tool, not collected by pytest and not a package dependency: it needs `pip install playwright` +
`playwright install chromium` and the frontend's own npm dependencies (or `--node-modules` pointing at an existing
install; this QA tool never installs software).

What runs for real: the built UI, the real owner server (`ServerConfig(owner_session=...)`, own token) and the real
learner course server, a real detached worker process started from the owner UI, and `alearn studio promote` in its own
process. The course content is the labelled TEST FIXTURE of tests/studio_fixture.py, authored through the studio loop.

Every log entry is a real assertion: there are no unconditional passes. Telemetry (console messages, timings) is recorded
separately and never counted as a check. Each screenshot is verified on disk and a Playwright trace is retained.

Exit code: 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests/e2e"))

from course_ui_e2e import Run, api, free_port, state, toolbar, wait_hash  # noqa: E402
from rules_fixture import RulesWorld  # noqa: E402
from course_fixture import IsolatedState  # noqa: E402
from runtime import ledger_trust  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402

OWNER_TOKEN = "e2e-owner-token-0123456789abcdef"
LEARNER_TOKEN = "e2e-learner-token-0123456789abcd"
STATEMENT = "Dossieri yoxladım və qəbul edirəm (TEST FIXTURE)."
TEMPLATE = ROOT / "skills/adaptive-learn/templates/react"


class Serving:
    def __init__(self, **cfg):
        self.srv = make_server(ServerConfig(host="127.0.0.1", port=0, **cfg))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"


def build_dist(node_modules: str | None, out: Path, run: Run) -> Path:
    """Build the committed template exactly as the runtime materialises it (index.html.template -> index.html)."""
    work = out / "frontend"
    if work.exists():
        raise RuntimeError(f"{work} exists already: pass a fresh --out")
    work.mkdir(parents=True)
    shutil.copytree(TEMPLATE / "src", work / "src")
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(TEMPLATE / name, work / name)
    shutil.copy2(TEMPLATE / "index.html.template", work / "index.html")
    if not node_modules:
        raise RuntimeError("pass --node-modules with an existing install; this QA tool never installs software")
    (work / "node_modules").symlink_to(Path(node_modules).resolve(), target_is_directory=True)
    t = time.time()
    proc = subprocess.run([str(work / "node_modules/.bin/vite"), "build"], cwd=work, capture_output=True, text=True, timeout=600)
    (out / "vite-build.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    run.note("vite_build_seconds", round(time.time() - t, 1))
    run.check("frontend production build succeeds", proc.returncode == 0, proc.returncode)
    dist = work / "dist"
    run.check("built index.html exists", (dist / "index.html").is_file())
    return dist


def tree_hashes(path: Path) -> dict:
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}


def cli(ws: Path, *argv) -> dict:
    """The owner's terminal: a separate process (the gate must not run inside a threaded server)."""
    env = {**os.environ, "ALEARN_LEDGER_KEY": ledger_trust.ledger_key().hex()}
    env.pop("ALEARN_OWNER_TOKEN", None)
    proc = subprocess.run([sys.executable, str(ROOT / "skills/adaptive-learn/scripts/alearn.py"), "--workspace", str(ws), *argv],
                          capture_output=True, text=True, timeout=300, env=env)
    try:
        return {"code": proc.returncode, **json.loads(proc.stdout)}
    except ValueError:
        return {"code": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}


def horizontal_overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")


# ------------------------------------------------------------------ owner phases
def phase_owner(page, srv, run, w, job_id, digest, requests):
    page.goto(srv.base + "/")
    page.get_by_role("heading", name="Sahib girişi", exact=True).wait_for()
    run.check("the owner server shows the token form first, no studio data before it",
              page.get_by_label("Sahib tokeni").is_visible() and page.get_by_role("heading", name="Studio", exact=True).count() == 0)
    run.check("the token field is a password input", page.get_by_label("Sahib tokeni").get_attribute("type") == "password")
    run.shot(page, "owner-token-form")
    page.get_by_label("Sahib tokeni").fill("x" * 40)
    page.get_by_role("button", name="Daxil ol", exact=True).click()
    page.get_by_role("alert").filter(has_text="Token qəbul edilmədi.").wait_for()
    run.check("a wrong token is refused with a plain message", True)
    run.check("the learner token is refused as well",
              api_with_token(page, "/api/studio", LEARNER_TOKEN)["status"] == 401)
    page.get_by_label("Sahib tokeni").fill(OWNER_TOKEN)
    page.get_by_role("button", name="Daxil ol", exact=True).click()
    page.get_by_role("heading", name="Studio", exact=True).wait_for()
    run.check("the owner token opens the Studio", page.get_by_role("heading", name="Studio", exact=True).is_visible())
    stored = page.evaluate("() => ({session: window.sessionStorage.getItem('alearn-owner-token'), local: Object.keys(window.localStorage), "
                           "cookie: document.cookie, url: location.href})")
    run.check("the token is kept in sessionStorage only (not localStorage, cookies or the URL)",
              stored["session"] == OWNER_TOKEN and stored["local"] == [] and stored["cookie"] == "" and OWNER_TOKEN not in stored["url"], stored["url"])
    run.check("no request URL ever carried the token", not any(OWNER_TOKEN in u for u in requests), len(requests))

    # Studio: progress, per-node units, worker, events, notifications
    page.locator("#unit-progress").wait_for()
    run.check("Studio shows the build progress", page.locator("#unit-progress").inner_text() == "4 / 4 vahid hazırdır",
              page.locator("#unit-progress").inner_text())
    run.check("Studio shows the job status in plain Azerbaijani", page.locator(".job .chip").first.inner_text() == "təhvilə hazırdır")
    rows = page.locator("table tbody tr")
    run.check("Studio lists every node with its lesson and item units",
              rows.count() == 2 and all("hazırdır" in rows.nth(i).inner_text() for i in range(2)), rows.count())
    run.check("Studio shows the worker heartbeat state (not running yet)", "İşləmir" in page.locator("[data-worker]").inner_text())
    body = page.inner_text("main")
    run.check("Studio shows the event chain as intact and a handover notification", "bütövdür" in body and "handover_ready" in body)
    run.check("runners are listed without secrets", "qoşulmayıb" in body and OWNER_TOKEN not in page.content())
    run.shot(page, "owner-studio")
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    page.get_by_role("heading", name="Studio", exact=True).wait_for()
    run.check("mobile Studio has no horizontal page scroll", horizontal_overflow(page) <= 1, horizontal_overflow(page))
    run.shot(page, "owner-studio-mobile")
    page.set_viewport_size({"width": 1366, "height": 900})
    page.reload()
    page.get_by_role("heading", name="Studio", exact=True).wait_for()

    # start the detached worker from the UI: a real separate process that finds the build ready and finishes
    page.get_by_role("button", name="Fon işçisini başlat", exact=True).click()
    page.get_by_role("status").filter(has_text="Fon işçisi").first.wait_for()
    run.check("start launches the detached worker and says so",
              re.search(r"Fon işçisi başladıldı \(pid \d+\)|artıq işləyir|işini dərhal tamamladı",
                        page.get_by_role("status").filter(has_text="Fon işçisi").first.inner_text()) is not None)
    deadline = time.time() + 40
    done = False
    while time.time() < deadline and not done:
        page.wait_for_timeout(500)
        done = "worker_finished" in page.inner_text("main")
    run.check("the worker finishes and its notification appears in the Studio", done)
    beat = json.loads((w.root / "worker" / "heartbeat.json").read_text(encoding="utf-8")) if (w.root / "worker" / "heartbeat.json").is_file() else {}
    run.check("the worker left a heartbeat file", bool(beat.get("pid")), beat.get("state"))

    # the cancel confirmation makes the whole page behind it inert (header, nav, controls); Escape cancels without a state change
    status_before = w.job(job_id)["status"]
    page.get_by_role("button", name="Ləğv et", exact=True).click()
    page.get_by_role("dialog").wait_for()
    # the header goes inert one effect after the dialog mounts, so wait for it instead of reading it in the same tick
    page.wait_for_function("() => document.querySelector('.app.owner > header').inert")
    run.check("the owner cancel dialog inerts the header and the Studio body behind it",
              page.evaluate("() => [document.querySelector('.app.owner > header').inert, document.querySelector('.app.owner > footer').inert, "
                            "document.querySelector('#studio-title').parentElement.querySelector(':scope > div').inert]") == [True, True, True])
    page.keyboard.press("Escape")
    page.get_by_role("dialog").wait_for(state="detached")
    page.wait_for_function("() => !document.querySelector('.app.owner > header').inert")
    run.check("Escape closes it, the page is live again and the job is unchanged",
              not page.evaluate("() => document.querySelector('.app.owner > header').inert") and w.job(job_id)["status"] == status_before)

    # Handover
    page.get_by_role("link", name="Təhvil", exact=True).click()
    page.get_by_role("heading", name="Təhvil dossieri", exact=True).wait_for()
    page.locator("#rules-table").wait_for()
    run.check("Handover shows the exact build digest the kernel holds", page.locator("#build-digest").inner_text() == digest)
    rows = page.locator("#rules-table tbody tr")
    labels = [rows.nth(i).locator("th").inner_text().split()[0] for i in range(rows.count())]
    run.check("the receipt table has R1 to R10 in order", labels == [f"R{i}" for i in range(1, 11)], labels)
    table_text = page.locator("#rules-table").inner_text()
    run.check("probe counts are words, never a bare 'passed/total' fraction", re.search(r"\b\d+/\d+\b", table_text) is None, table_text[:200])
    run.check("probe counts say 'keçdi' and name any 'tətbiq olunmur' with its reason",
              "keçdi" in table_text and all("(" in c for c in
                                            [rows.nth(i).locator("td").nth(1).inner_text() for i in range(rows.count())
                                             if "tətbiq olunmur" in rows.nth(i).locator("td").nth(1).inner_text()]))
    run.check("the table explains that 'tətbiq olunmur' is not a pass", "keçid sayılmır" in page.locator("#rules-legend").inner_text())
    run.check("the BLOCKED banner is absent for a passing build", page.get_by_text("BLOKLANIB").count() == 0)
    run.check("the NOT-verified list is shown", page.locator("#not-verified li").count() >= 5)
    run.shot(page, "owner-handover")

    # per-node preview: the public learner view, never the vault
    page.get_by_role("button", name=re.compile("Vektor oxşarlığı.*önizlə")).click()
    page.get_by_role("heading", name=re.compile("Önizləmə")).wait_for()
    preview = page.locator(".preview").inner_text()
    run.check("the preview shows the walkthrough with its agent-authored label",
              "Agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş" in preview)
    run.check("the preview shows only counts of hidden checks", "Gizli yoxlama sayı:" in preview)
    staged = w.staged(job_id, "lesson:vec-sim")
    vault = [staged["practice"]["reference_solution"].splitlines()[3].strip(), staged["practice"]["checks"][0]["code"]]
    run.check("no reference solution line or hidden check source appears in the preview",
              not any(v in page.locator(".preview").inner_html() for v in vault), vault)
    run.shot(page, "owner-preview")
    page.get_by_role("button", name="Önizləməni bağla").click()
    # a mistake step that also has a prediction stays closed until the prediction is revealed (reasoning node, mistake at step 2)
    page.get_by_role("button", name=re.compile("önizlə")).nth(1).click()
    page.get_by_role("heading", name=re.compile("Önizləmə")).wait_for()
    pwt = page.locator(".preview .walkthrough")
    pnav = pwt.locator(".wt-nav button")
    pnav.nth(0).focus()
    page.keyboard.press("ArrowRight")
    run.check("ArrowRight moves focus to the new current stepper button",
              page.evaluate("() => document.activeElement.getAttribute('aria-current')") == "step" and "Addım 2" in pwt.inner_text(), pwt.inner_text()[:80])
    page.keyboard.press("ArrowDown")
    run.check("ArrowDown is not taken by the stepper", "Addım 2" in pwt.inner_text())
    run.check("the mistake symptom and fix stay hidden before the prediction is revealed",
              pwt.locator(".mistake").count() == 0 and "Simptom" not in pwt.inner_text()
              and "proqnozdan sonra açılır" in pwt.inner_text(), pwt.inner_text()[:300])
    pwt.get_by_role("button", name="Cavabı göstər", exact=True).click()
    run.check("after the reveal the mistake block appears", pwt.locator(".mistake").count() == 1 and "Simptom:" in pwt.inner_text())
    pwt.get_by_role("button", name="Növbəti addım", exact=True).click()
    run.check("Next moves focus to the new step title", page.evaluate("() => document.activeElement.tagName") == "H4"
              and "Addım 3" in pwt.inner_text())
    run.check("at the last step Next is aria-disabled but keeps being focusable",
              pwt.get_by_role("button", name="Növbəti addım", exact=True).get_attribute("aria-disabled") == "true"
              and pwt.get_by_role("button", name="Növbəti addım", exact=True).evaluate("b => b.disabled") is False)
    page.get_by_role("button", name="Önizləməni bağla").click()

    # accept: digest shown, typed statement required
    accept = page.get_by_role("button", name="Qəbul edirəm", exact=True)
    run.check("accept is disabled until the owner types a statement", accept.is_disabled())
    page.get_by_label("Qəbul bəyanatı (özün yaz)").fill(STATEMENT)
    run.check("accept shows the build digest it is bound to", digest[:16] in page.locator(".accept").inner_text())
    run.check("accept becomes available with a statement", not accept.is_disabled())
    accept.click()
    page.locator("#accept-result").wait_for()
    run.check("acceptance is confirmed and promotion is named as a separate step",
              "alearn studio promote" in page.locator("#accept-result").inner_text(), page.locator("#accept-result").inner_text())
    job = w.job(job_id)
    run.check("the job is accepted through the owner UI channel with the typed statement",
              (job["status"], job["acceptance"]["channel"], job["acceptance"]["statement"]) == ("accepted", "owner_ui", STATEMENT), job["acceptance"])
    page.get_by_text("Qəbul yalnız “təhvilə hazırdır” vəziyyətində mümkündür.").wait_for()
    run.check("a second acceptance is no longer offered", page.get_by_role("button", name="Qəbul edirəm", exact=True).is_disabled())
    run.shot(page, "owner-accepted")
    page.get_by_role("button", name="Tokeni sil və çıx").click()
    page.get_by_role("heading", name="Sahib girişi", exact=True).wait_for()
    run.check("forgetting the token returns to the form and clears sessionStorage",
              page.evaluate("() => window.sessionStorage.getItem('alearn-owner-token')") is None)


def api_with_token(page, path, token):
    return page.evaluate("""async ([path, token]) => {
        const r = await fetch(path, {headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'});
        return {status: r.status};
    }""", [path, token])


# ---------------------------------------------------------------- learner phases
def phase_learner(page, srv, run, w, job_id):
    page.goto(srv.base + "/#/")
    page.reload()
    page.locator("article.mission").get_by_role("button", name="İndi et", exact=True).click()
    page.get_by_text("Bu mövzu sənə nə qədər tanışdır?").first.wait_for()
    run.check("the learner course app is served (not the owner UI) by the course server",
              page.get_by_role("heading", name="Sahib girişi", exact=True).count() == 0)
    run.check("the learner server never mounts /api/studio",
              api(page, "/api/studio")["status"] == 404 and api(page, "/api/studio/handover")["status"] == 404)
    page.get_by_role("button", name="Bilmirəm — qısa izah et", exact=True).click()
    page.get_by_role("heading", name="Qısa izah", exact=True).wait_for()

    # walkthrough stepper
    page.get_by_role("button", name="Nümayişi izlə", exact=True).click()
    wt = page.locator(".walkthrough")
    wt.wait_for()
    run.check("the walkthrough carries the agent-authored, source-grounded label",
              wt.locator(".wt-banner").inner_text() == "Agent tərəfindən yazılmış, mənbəyə əsaslanan nümayiş")
    run.check("the stepper starts at step 1 with explanation and code",
              "Addım 1 / 4" in wt.inner_text() and wt.locator("pre.code").count() == 1 and "scores = [4, 8, 6]" in wt.inner_text())
    run.shot(page, "learner-walkthrough-step1")
    nav = wt.locator(".wt-nav button")
    run.check("the stepper has one labelled button per step and marks the current one",
              nav.count() == 4 and nav.nth(0).get_attribute("aria-current") == "step" and "qəsdən edilmiş səhv" in nav.nth(2).get_attribute("aria-label"))
    nav.nth(0).focus()
    page.keyboard.press("ArrowRight")
    run.check("ArrowRight moves the stepper by keyboard", "Addım 2 / 4" in wt.inner_text(), wt.inner_text()[:120])
    run.check("predict-then-reveal hides the expected output until the learner reveals",
              "Gözlənilən çıxış proqnozdan sonra açılır." in wt.inner_text() and wt.locator("pre.code.out").count() == 0)
    wt.get_by_label(re.compile("proqnoz ver")).fill("20")
    wt.get_by_role("button", name="Cavabı göstər", exact=True).click()
    run.check("revealing shows the answer and the expected checkpoint output",
              "4 + 8 + 6 = 18" in wt.inner_text() and wt.locator("pre.code.out").inner_text().strip() == "18")
    wt.get_by_role("button", name="Növbəti addım", exact=True).click()
    run.check("Next moves focus to the step title", page.evaluate("() => document.activeElement.tagName") == "H4")
    wt.get_by_role("button", name="Növbəti addım", exact=True).click()
    run.check("the stepper reaches step 4 and then disables Next", "Addım 4 / 4" in wt.inner_text()
              and wt.get_by_role("button", name="Növbəti addım", exact=True).get_attribute("aria-disabled") == "true")
    run.check("the stepper shows a visible marker and legend for the mistake step",
              "⚠" in nav.nth(2).inner_text() and "qəsdən edilmiş səhv olan addım" in wt.inner_text())
    wt.get_by_role("button", name="Əvvəlki addım", exact=True).click()
    mistake = wt.locator(".mistake")
    run.check("the deliberate mistake step is highlighted with symptom, fix and the wrong code",
              mistake.count() == 1 and "Qəsdən edilmiş səhv" in mistake.inner_text() and "Simptom:" in mistake.inner_text()
              and "Düzəliş:" in mistake.inner_text() and "average = total / scores" in mistake.inner_text(), wt.inner_text()[:200])
    run.check("the mistake step is also marked in the stepper by more than color",
              nav.nth(2).get_attribute("class").find("has-mistake") >= 0 and "qəsdən edilmiş səhv" in nav.nth(2).get_attribute("aria-label"))
    run.check("watching is stated to earn no evidence", "izləmək sübut sayılmır" in page.inner_text("main").lower()
              or "İzləmək sübut sayılmır" in page.inner_text("main"))
    run.shot(page, "learner-walkthrough-mistake")
    wt.scroll_into_view_if_needed()
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(200)
    run.check("mobile lesson with the walkthrough has no horizontal page scroll", horizontal_overflow(page) <= 1, horizontal_overflow(page))
    run.shot(page, "learner-walkthrough-mobile")
    page.set_viewport_size({"width": 1366, "height": 900})

    # faded ladder
    ladder = page.locator(".ladder")
    ladder.get_by_role("button", name="Həll olunmuş nümunəni aç", exact=True).click()
    ladder.locator(".ladder-rung").first.wait_for()
    run.check("rung 1 shows the worked solution and an explanation",
              "def mean(values)" in ladder.inner_text() and "həll olunmuş nümunədir" in ladder.inner_text())
    run.check("the opened rung is announced: aria-expanded, marked opened, heading focused",
              ladder.get_by_role("button", name="Həll olunmuş nümunəni aç", exact=True).get_attribute("aria-expanded") == "true"
              and "açıldı" in ladder.inner_text() and page.evaluate("() => document.activeElement.id") == "rung-worked")
    helps = []
    page.on("request", lambda r: helps.append(r.url) if r.url.endswith("/api/course/help") else None)
    ladder.get_by_role("button", name="Həll olunmuş nümunəni aç", exact=True).click()
    page.wait_for_timeout(300)
    run.check("opening the same rung again does not refetch it (no extra help event)", helps == [] and ladder.locator(".ladder-rung").count() == 1, helps)
    ladder.get_by_role("button", name="Boşluqları doldur", exact=True).click()
    ladder.locator(".ladder-rung").nth(1).wait_for()
    run.check("rung 2 shows the completion template with visible ___ blanks and never the blank answers",
              (ladder.text_content() or "").count("___") >= 3 and "x * y" not in (ladder.text_content() or ""),
              [(ladder.text_content() or "").count("___"), "x * y" in (ladder.text_content() or "")])
    run.check("rung 3 is the practice itself", ladder.get_by_role("button", name="Müstəqil: praktikaya keç", exact=True).is_visible())
    run.shot(page, "learner-ladder")

    ladder.get_by_role("button", name="Müstəqil: praktikaya keç", exact=True).click()
    wait_hash(page, "#/practice/vec-sim")
    page.get_by_text("Bələdçili praktika").first.wait_for()
    info = api(page, "/api/course/lesson?node=vec-sim")["data"]
    practice_file = Path(info["practice"]["file"])
    starter = w.staged(job_id, "lesson:vec-sim")["practice"]["starter_code"]

    # hint ceiling
    toolbar(page, "Kömək").click()
    page.get_by_role("heading", name="İpucu 1/2", exact=True).wait_for()
    page.locator("#ceiling-note").wait_for()
    run.check("the hint ceiling message appears once the limit is reached",
              "İpucu limitinə çatdın (1 ipucu)" in page.locator("#ceiling-note").inner_text(), page.locator("#ceiling-note").inner_text())
    run.check("the hint action is locked but still focusable and announces why",
              toolbar(page, "Kömək").get_attribute("aria-disabled") == "true" and toolbar(page, "Kömək").evaluate("b => b.disabled") is False
              and "limitinə" in page.locator("#hint-help").inner_text())
    run.shot(page, "learner-hint-ceiling")

    # reset: confirmation, archive, never delete
    practice_file.write_text("# learner work in progress\nprint('mine')\n", encoding="utf-8")
    page.get_by_role("button", name="Faylı başlanğıca qaytar", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    run.check("reset asks first and says the current code is archived, not deleted",
              "silinmir" in dialog.inner_text() and "arxiv" in dialog.inner_text())
    run.check("the reset dialog focuses the safe choice and inerts the page behind",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Xeyr"
              and page.evaluate("() => document.querySelector('.app-body').inert === true"))
    page.keyboard.press("Escape")
    run.check("Escape cancels the reset and leaves the file untouched",
              page.get_by_role("dialog").count() == 0 and "learner work" in practice_file.read_text(encoding="utf-8"))
    page.get_by_role("button", name="Faylı başlanğıca qaytar", exact=True).click()
    page.get_by_role("button", name="Bəli, qaytar", exact=True).click()
    page.get_by_text("Arxiv:").wait_for()
    archive = list((practice_file.parent.parent / "archive").glob("*"))
    run.check("reset archives the learner's file (nothing deleted) and restores the starter",
              len(archive) == 1 and "learner work" in archive[0].read_text(encoding="utf-8")
              and practice_file.read_text(encoding="utf-8") == starter, [a.name for a in archive])

    # reveal: the cost is stated and confirmed
    page.get_by_role("button", name="Həlli göstər", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    text = dialog.inner_text()
    run.check("reveal states the cost: no credit for this task and a new attempt (same task) is required",
              "kredit verilmir" in text and "yeni cəhd" in text and "eyni tapşırıq" in text)
    run.check("the reveal dialog focuses the safe choice, not the costly one",
              page.evaluate("() => document.activeElement && document.activeElement.innerText") == "Xeyr, özüm davam edirəm")
    run.shot(page, "learner-reveal-confirm")
    page.keyboard.press("Escape")
    prog = api(page, "/api/course/lesson?node=vec-sim")["data"]["progress"]["practice"]
    run.check("cancelling the reveal changes nothing on the server", not prog.get("revealed"), prog)
    page.get_by_role("button", name="Həlli göstər", exact=True).click()
    page.get_by_role("button", name="Bəli, həlli aç (kredit yoxdur)", exact=True).click()
    page.get_by_role("heading", name="Həll açıldı: bu cəhd üçün kredit yoxdur", exact=True).wait_for()
    run.check("after the confirmed reveal the solution is shown with the zero-credit note",
              "math.sqrt(dot(a, a))" in page.locator(".reveal-result").inner_text())
    prog = api(page, "/api/course/lesson?node=vec-sim")["data"]["progress"]["practice"]
    run.check("the server recorded the reveal (practice attempt is revealed)", prog.get("revealed") is True, prog)
    run.check("checking is locked after a reveal until a new attempt starts",
              toolbar(page, "Yoxla").get_attribute("aria-disabled") == "true")
    run.check("focus moves to the reveal result instead of falling to the locked opener",
              page.evaluate("() => document.activeElement.id") == "reveal-title")
    run.shot(page, "learner-reveal-result")
    page.get_by_role("button", name="Yeni cəhd başla", exact=True).click()
    page.get_by_role("heading", name="Həll açıldı: bu cəhd üçün kredit yoxdur", exact=True).wait_for(state="detached")
    prog = api(page, "/api/course/lesson?node=vec-sim")["data"]["progress"]["practice"]
    run.check("a new attempt clears the revealed state", not prog.get("revealed"), prog)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="e2e-studio")
    ap.add_argument("--node-modules", help="reuse an existing node_modules (no install)")
    ap.add_argument("--check-dist", action="store_true", help="also assert the committed template dist equals a fresh build of src")
    args = ap.parse_args(argv)
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    out = Path(args.out).resolve()
    run = Run(out)
    with IsolatedState():
        os.environ["ALEARN_LEDGER_KEY"] = ledger_trust.ledger_key().hex()      # the detached worker is another process
        dist = build_dist(args.node_modules, out, run)
        if args.check_dist:
            committed, fresh = tree_hashes(TEMPLATE / "dist"), tree_hashes(dist)
            run.check("the committed template dist is reproducible from src", committed == fresh,
                      None if committed == fresh else (sorted(set(committed) ^ set(fresh)) or "same files, different bytes"))
        w = RulesWorld(gate=True)
        owner = learner = None
        try:
            job_id, last = w.build()
            run.check("the studio loop built a complete, gate-passing build", last["state"] == "done" and w.job(job_id)["status"] == "ready_for_review", last)
            digest = w.job(job_id)["build_digest"]
            os.environ["ALEARN_OWNER_TOKEN"] = OWNER_TOKEN
            os.environ["ADAPTIVE_API_TOKEN"] = LEARNER_TOKEN
            owner = Serving(workspace=w.ws, owner_session=w.sid, owner_token=OWNER_TOKEN, ui_dir=dist)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="az-AZ")
                context.tracing.start(screenshots=True, snapshots=True)
                page = context.new_page()
                errors, requests = [], []
                page.on("pageerror", lambda e: errors.append(str(e)[:300]))
                page.on("request", lambda r: requests.append(r.url))
                page.on("console", lambda m: run.note("console", f"{m.type}: {m.text[:200]}") if m.type in {"error", "warning"} else None)
                page.set_default_timeout(15000)
                try:
                    phase_owner(page, owner, run, w, job_id, digest, requests)
                    owner.stop()
                    owner = None
                    # the learner approves the exact roadmap, then the owner promotes in a separate process
                    w.approve()
                    promoted = cli(w.ws, "studio", "promote", "--session", w.sid, "--job", job_id)
                    run.check("`alearn studio promote` (own process) promotes the accepted build",
                              promoted.get("status") == "promoted" and promoted.get("promotion", {}).get("promoted") is True, promoted)
                    os.environ.pop("ALEARN_OWNER_TOKEN", None)
                    learner = Serving(workspace=w.ws, allow_anon=True, course_session=w.sid, ui_dir=dist)
                    phase_learner(page, learner, run, w, job_id)
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
            run.check("trace retained", (out / "trace.zip").is_file() and (out / "trace.zip").stat().st_size > 1000)
        finally:
            for srv in (owner, learner):
                if srv is not None:
                    srv.stop()
            w.close()
    print(f"artifacts: {out}")
    return run.finish()


if __name__ == "__main__":
    sys.exit(main())

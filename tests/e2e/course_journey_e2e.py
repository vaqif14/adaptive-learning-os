"""Additional SYNTHETIC fixture browser checks for the learner journey.

Uses the real local bridge, persisted runtime and production React build. Host
authoring/judgment is explicit fixture code, never a claim of live AI grading or
NotebookLM ingestion. No installation, external service or user progress access.
Run the original course_ui_e2e.py as well: its 88 assertions remain independent.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import course_ui_e2e as base
from course_fixture import CONCEPT_ITEMS, CONCEPT_LESSON, IsolatedState, items, manifest_for
from runtime.contracts import LearningContract
from runtime.course import CourseKernel
from runtime.session import SessionKernel


def post(page, endpoint, **body):
    return base.api(page, endpoint, {"ui_schema": 2, "expected_revision": base.state(page)["revision"], **body})


def fresh_course(ws, dist, *, strict=False):
    sk = SessionKernel(ws)
    sid = sk.start("Vektorlar · SYNTHETIC QA", LearningContract() if strict else
                   LearningContract(goal="Vektorların istiqamətini müstəqil müqayisə et", stated_background="TEST FIXTURE"),
                   defer_workspace=strict)["session_id"]
    k = CourseKernel(ws)
    k.init(sid, require_intake=True) if strict else k.init(sid)
    d = sk.dir(sid)
    return sk, k, sid, d, base.Server(ws, sid, dist, base.free_port()).start()


def phase_cli_staging(page, ws, dist, run):
    cli = base.ROOT / "skills/adaptive-learn/scripts/alearn.py"
    cli_ws = ws / "actual-cli-start"
    cli_ws.mkdir(parents=True, exist_ok=True)
    prefix = [sys.executable, str(cli), "--workspace", str(cli_ws)]
    command = prefix + ["start", "--topic", "SYNTHETIC CLI QA", "--without-notebooklm"]
    started = subprocess.run(command, capture_output=True, text=True, timeout=30)
    run.note("actual_cli_start", {"command": command, "exit_code": started.returncode})
    run.check("actual CLI start succeeds without external notebook calls", started.returncode == 0, started.stderr[-300:])
    sid = json.loads(started.stdout)["session_id"]
    d = SessionKernel(cli_ws).dir(sid)
    run.check("actual CLI start creates state without course HTML or frontend", (d / "session.json").is_file()
              and not (d / "workspace").exists() and not list(d.rglob("*.html")))
    initialized = subprocess.run(prefix + ["course", "init", "--session", sid], capture_output=True, text=True, timeout=30)
    run.note("actual_cli_init_exit_code", initialized.returncode)
    run.check("actual CLI init preserves the staged no-course state", initialized.returncode == 0 and not (d / "workspace").exists())
    command = prefix + ["course", "serve", "--session", sid, "--host", "127.0.0.1", "--port", str(base.free_port()), "--allow-anon"]
    refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
    run.note("actual_preapproval_serve", {"command": command, "exit_code": refused.returncode})
    run.check("actual CLI serve refuses a course interface before approval", refused.returncode != 0
              and "--intake" in refused.stderr and "waits for roadmap approval" in refused.stderr)
    port = base.free_port()
    command = prefix + ["course", "serve", "--session", sid, "--host", "127.0.0.1", "--port", str(port), "--allow-anon", "--intake"]
    log_path = run.out / "actual-cli-intake-server.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            ready = False
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=.3) as response:
                        ready = response.status == 200
                    break
                except (OSError, TimeoutError):
                    time.sleep(.05)
            run.check("explicit CLI intake preview serves the bundled shell", ready, log_path.read_text()[-300:])
            if not ready:
                raise RuntimeError("actual CLI --intake did not start")
            page.goto(f"http://127.0.0.1:{port}/#/roadmap")
            base.wait_hash(page, "#/intake")
            page.get_by_role("heading", name="Tanışlıq", exact=True).wait_for()
            run.check("CLI preview has no course navigation or placeholder roadmap", page.get_by_role("navigation", name="Bölmələr").count() == 0
                      and page.locator("button.node").count() == 0 and not (d / "workspace").exists())
            run.shot(page, "actual-cli-intake-shell")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            run.note("own_cli_intake_server_stopped", {"command": command, "exit_code_after_intentional_stop": proc.returncode})


def phase_target_waiting(page, ws, dist, run):
    sk, k, sid, d, srv = fresh_course(ws, dist, strict=True)
    try:
        page.goto(srv.base + "/#/intake")
        post(page, "/api/course/intake", goal="TEST FIXTURE: NVIDIA sertifikasiya imtahanına hazırlaşmaq istəyirəm.")
        post(page, "/api/course/intake", stated_background="İlk dəfədir; dəqiq hansı sertifikata aid olduğunu agentlə danışacağam.")
        page.reload()
        page.get_by_text("Bu format mənə uyğun deyil", exact=True).click()
        page.get_by_label("Səbəb", exact=True).fill("TEST FIXTURE learner: indi diagnostic keçmək istəyirəm; nəticəsiz planın ilkin qaldığını bilirəm.")
        page.get_by_role("button", name="İlkin planla davam etməyə razıyam", exact=True).click()
        run.check("NVIDIA-like goal plus skip still waits for exact target", base.eventually(page,
                  "async () => {const s=await(await fetch('/api/course/state')).json();return s.generation_readiness?.target_required && s.intake?.skipped;}", None))
        st = base.state(page)
        run.check("unqualified skip is separately visible and cannot generate a roadmap", not st["generation_readiness"]["ready"]
                  and "provisional_skip_confirmation_required" in st["generation_readiness"]["blockers"]
                  and st["roadmap"]["version"] is None and not (d / "workspace").exists())
        run.check("waiting screen states target clarification and provisional skip limits", "Dəqiq hədəf və rəsmi tələblər yoxlanmalıdır" in page.inner_text("main")
                  and "Əvvəlki diagnostic skip-i saxlanıb" in page.inner_text("main") and page.get_by_role("navigation", name="Bölmələr").count() == 0)
        run.shot(page, "certification-target-and-skip-waiting")
    finally:
        srv.stop()


def phase_intake(page, ws, dist, run):
    sk, k, sid, d, srv = fresh_course(ws, dist, strict=True)
    try:
        page.goto(srv.base + "/#/")
        page.get_by_role("heading", name="Məqsədin nədir?", exact=True).wait_for()
        run.check("fresh intake has no placeholder HTML workspace", not (d / "workspace").exists() and not list(d.rglob("*.html")))
        run.check("fresh intake uses Tanışlıq identity without course navigation", page.get_by_role("heading", name="Tanışlıq", exact=True).is_visible()
                  and page.get_by_role("link", name="Tanışlıq", exact=True).is_visible() and page.get_by_role("navigation", name="Bölmələr").count() == 0)
        for route in ("roadmap", "learn/vec-sim", "practice/vec-sim", "progress", "result/att_fixture"):
            page.goto(srv.base + f"/#/{route}")
            base.wait_hash(page, "#/intake")
            run.check(f"no-course bookmark {route} returns to intake without stale content", page.get_by_role("heading", name="Tanışlıq", exact=True).is_visible()
                      and page.locator("button.node").count() == 0 and page.get_by_role("navigation", name="Bölmələr").count() == 0)
        run.check("a blank learner starts with goal alone", page.get_by_label("Sonda nəyi müstəqil edə bilmək istəyirsən?").is_visible()
                  and page.get_by_label("Öz cavabın", exact=True).count() == 0)
        page.get_by_label("Sonda nəyi müstəqil edə bilmək istəyirsən?").fill("Yeni axtarış ssenarisində vektor istiqamətini müqayisə edib seçimi izah etmək.")
        page.get_by_role("button", name="Cavabı qeyd et", exact=True).click()
        page.get_by_role("heading", name="Başlanğıc təcrübən", exact=True).wait_for()
        run.check("goal persists before background is requested", base.state(page)["goal"]["text"].startswith("Yeni axtarış"))
        page.get_by_label("Bu işə aid indiyə qədər nə etmisən? Bir konkret nümunə yaz.").fill("Python siyahılarını istifadə etmişəm, vektor oxşarlığını hələ hesablamamışam.")
        page.get_by_role("button", name="Cavabı qeyd et", exact=True).click()
        page.get_by_role("heading", name="Qısa başlanğıc yoxlaması", exact=True).wait_for()
        run.check("self-report does not create competence evidence", base.state(page)["intake"]["observations"] == 0
                  and not base.state(page)["evidence_refs"])
        result = post(page, "/api/course/approve", roadmap_version="rp-not-ready", statement="Təsdiq edirəm")
        run.check("strict intake cannot be bypassed through approval", result["status"] == 409
                  and result["data"].get("error") == "diagnostic_required", result)
        k.install_diagnostic_probe(sid, {"id": "dp-browser-one", "node_id": "vec-sim", "kind": "explanation",
                                      "prompt": "İki axtarış sənədinin vektorları eyni tərəfə baxır, amma uzunluqları fərqlidir. Oxşarlığı necə müqayisə edərdin?",
                                      "criteria": ["Uzunluq ilə istiqaməti ayırır", "Seçimin səbəbini izah edir"]})
        page.goto(srv.base + "/#/")
        page.reload()
        page.get_by_label("Öz cavabın", exact=True).wait_for()
        run.check("host-authored diagnostic exposes prompt and criteria without a key", "uzunluqları fərqlidir" in page.inner_text("main")
                  and "answer_key" not in json.dumps(base.state(page)))
        answer = "Uzunluğu yox, istiqaməti müqayisə edərdim. Uzunluqlara bölmə miqyasın təsirini aradan qaldırır."
        page.get_by_label("Öz cavabın", exact=True).fill(answer)
        page.get_by_role("button", name="Cavabı göndər", exact=True).click()
        page.get_by_text("Cavabın saxlanıb. Agent onu tapşırığın meyarları ilə yoxlayır", exact=False).wait_for()
        run.check("saved diagnostic waits for attributed judgment", base.state(page)["intake"]["probe"]["response"] == answer
                  and base.state(page)["intake"]["observations"] == 0)
        repeat = post(page, "/api/course/diagnostic/answer", response="A replacement answer after submission")
        run.check("repeated answer cannot silently overwrite captured diagnostic work", repeat["status"] == 409
                  and base.state(page)["intake"]["probe"]["response"] == answer)
        spoof = post(page, "/api/course/diagnostic/judge", outcome="correct", confidence="high", assessor="human")
        run.check("learner bridge cannot submit a host diagnostic grade", spoof["status"] == 404
                  and base.state(page)["intake"]["observations"] == 0)
        srv.stop()
        srv.start()
        page.reload()
        page.get_by_text("Cavabın saxlanıb. Agent onu tapşırığın meyarları ilə yoxlayır", exact=False).wait_for()
        run.check("an interrupted diagnostic resumes its saved answer", base.state(page)["intake"]["probe"]["response"] == answer)
        k.judge_diagnostic(sid, {"outcome": "correct", "confidence": "medium", "assessor": "host_agent",
                               "rationale": "Fixture rəyi: cavab uzunluq təsirini istiqamət müqayisəsindən ayırır."})
        run.check("one observed success requests fresh transfer", k.diagnostic_next(sid)["action"] == "fresh_transfer_probe")
        k.install_diagnostic_probe(sid, {"id": "dp-browser-two", "node_id": "vec-sim", "kind": "scenario",
                                      "prompt": "Axtarışda uzun amma başqa istiqamətə baxan sənəd və qısa amma eyni istiqamətli sənəd var. Hansını seçərdin və niyə?",
                                      "criteria": ["Yeni kontekstdə seçimi izah edir"]})
        page.reload()
        page.get_by_label("Öz cavabın", exact=True).wait_for()
        page.get_by_label("Öz cavabın", exact=True).fill("Eyni istiqamətli qısa sənədi seçərdim: burada istiqamət mənaya uyğunluğu göstərir.")
        page.get_by_role("button", name="Cavabı göndər", exact=True).click()
        page.get_by_text("Cavabın saxlanıb. Agent onu tapşırığın meyarları ilə yoxlayır", exact=False).wait_for()
        k.judge_diagnostic(sid, {"outcome": "correct", "confidence": "medium", "assessor": "host_agent",
                               "rationale": "Fixture rəyi: yeni ssenaridə istiqamətlə bağlı səbəb verilmişdir."})
        step = k.diagnostic_next(sid)
        run.check("two fresh observations end the brief diagnostic provisionally", step["action"] == "ready_to_plan"
                  and step["observations"] == 2 and step["calibrated_probability"] is None)
        run.check("diagnostic completion alone cannot generate a course", not k.state(sid)["generation_readiness"]["ready"]
                  and "goal_clarification_required" in k.state(sid)["generation_readiness"]["blockers"])
        k.confirm_goal(sid, {"kind": "learning", "statement": "TEST FIXTURE learner confirmation: məqsədim yeni ssenaridə vektor istiqamətini müstəqil müqayisə edib səbəbi izah etməkdir."})
        missing_stage = d / "sources-staging" / "stg_journey_missing"
        missing = manifest_for(missing_stage)
        missing.update(complete=False, manifest_version="sm-journey-missing")
        missing["resources"][0].update(status="missing", issues=["SYNTHETIC FIXTURE: requested resource absent"])
        k.record_sources(sid, missing, base.EXPECTED, staged_cache=missing_stage)
        page.reload()
        page.get_by_text("1 mənbə hələ hazır deyil.", exact=False).wait_for()
        run.check("missing material is exposed instead of silently treated as grounding", base.state(page)["sources"]["resources"][0]["status"] == "missing"
                  and "əsaslandırılmış dərs hazır sayılmır" in page.inner_text("main"))
        run.check("NotebookLM is honestly described as an external CLI service", "NotebookLM xarici xidmətdir" in page.inner_text("main"))
        run.shot(page, "intake-missing-material")
        k.record_sources(sid, manifest_for(d / "sources"), base.EXPECTED)
        version = k.propose_roadmap(sid, base.e2e_roadmap())["roadmap_version"]
        run.check("a proposed roadmap stages only preview before approval", (d / "workspace/roadmap.html").is_file()
                  and not (d / "workspace/modules").exists() and not (d / "workspace/frontend").exists())
        # Host-side proposal is picked up through hydration/polling. First wait
        # for that committed state, then use the actual learner preview link.
        page.reload()
        page.get_by_role("heading", name="Yol xəritəsi hazırdır", exact=True).wait_for()
        page.get_by_role("link", name="Yol xəritəsini aç", exact=True).click()
        page.get_by_role("heading", name="Öyrənmə yol xəritəsi", exact=True).wait_for()
        run.check("roadmap retains all dependencies after intake", page.locator("button.node").count() == 3)
        page.get_by_label("Mərhələləri, ilkin şərtləri və mənbələri nəzərdən keçirdim.").check()
        page.get_by_role("button", name="Təsdiq edirəm", exact=True).click()
        page.get_by_text("Növbəti missiya").first.wait_for()
        run.check("only learner approval materializes teaching assets", (d / "workspace/modules/vec-sim/submission").is_dir()
                  and (d / "workspace/frontend/src/App.jsx").is_file())
        base.author(k, sid)
        page.goto(srv.base + "/#/progress")
        page.get_by_role("heading", name="Proqres və sübut", exact=True).wait_for()
        evidence = base.nodes(base.state(page))["vec-sim"]["skill_evidence"]
        run.check("skill evidence exposes scope without inventing probability or mastery", evidence["mastery_claim"] is False
                  and evidence["calibrated_probability"] is None and len(evidence["diagnostic_entry_ids"]) == 2)
        run.check("learner progress states calibration and general mastery limits", "Kalibrasiya olunmuş bacarıq ehtimalı hesablanmır" in page.inner_text("main"))
        run.shot(page, "intake-provisional-evidence")
        # Continue the very same fresh learner/session through explanation,
        # production practice, new exam, lock/restart and evidence-based next step.
        # These reuse assertion-bearing UI actions from the original suite.
        base.phase_python(page, srv, run, sk, sid)
        base.phase_concept(page, srv, run)
        check_readiness_invalidation(page, srv, k, sid, d, run)
    finally:
        srv.stop()


def check_readiness_invalidation(page, srv, k, sid, d, run):
    cache = d / "sources/src-fixture-001.txt"
    sha = json.loads((d / "source-manifest.json").read_text())["resources"][0]["content_sha256"]
    # Valid immutable objects can legitimately survive damage to a legacy alias.
    # Invalidate every copy the grounding reader can verify, then restore each.
    originals = {p: p.read_bytes() for p in (cache, d / f"sources/objects/{sha}.txt") if p.is_file()}
    def corrupt(text):
        for path in originals:
            path.write_text(text, encoding="utf-8")
    def restore():
        for path, data in originals.items():
            path.write_bytes(data)
    revision = k.state(sid)["revision"]
    requests = []
    listener = lambda request: requests.append(request.url) if "/api/course/lesson?" in request.url else None
    page.on("request", listener)
    try:
        corrupt("SYNTHETIC FIXTURE: cache readability/hash invalidated")
        st = k.state(sid)
        run.check("source cache changes readiness even without a state revision change", st["revision"] == revision
                  and not st["generation_readiness"]["ready"] and "sources_unreadable" in st["generation_readiness"]["blockers"])
        page.goto(srv.base + "/#/roadmap")
        page.get_by_role("button", name="Vəziyyəti yenilə", exact=True).click()
        page.get_by_role("heading", name="Tarixi yol xəritəsi", exact=True).wait_for()
        run.check("invalidated approved map is historical with teaching warning", "Hazırkı dərs və praktika bağlıdır" in page.inner_text("main")
                  and page.get_by_role("navigation", name="Bölmələr").count() == 0)
        for route in ("learn", "practice"):
            page.goto(srv.base + f"/#/{route}/vec-sim")
            base.wait_hash(page, "#/roadmap")
            run.check(f"changed readiness guards approved {route} route", page.get_by_role("heading", name="Tarixi yol xəritəsi", exact=True).is_visible())
        run.check("blocked teaching routes do not fetch lesson content", not requests, requests)
        blocked = base.api(page, "/api/course/lesson?node=vec-sim")
        run.check("strict lesson GET refuses unreadable source grounding", blocked["status"] == 409, blocked["status"])
        run.shot(page, "historical-map-invalid-readiness")
        restore()
        att = k.start_exam(sid, "vec-sim", fast_track=True)["attempt"]
        page.goto("about:blank")
        page.goto(srv.base + f"/#/exam/{att['attempt_id']}")
        page.get_by_text("Müstəqil yoxlama.").first.wait_for()
        corrupt("SYNTHETIC FIXTURE: readiness changed during active exam")
        page.goto(srv.base + "/#/learn/vec-sim")
        base.wait_hash(page, f"#/exam/{att['attempt_id']}")
        run.check("active exam route owns the app despite changed generation readiness", base.state(page)["teaching_locked"]
                  and base.state(page)["active_attempt"]["attempt_id"] == att["attempt_id"])
        restore()
        k.abandon_exam(sid, att["attempt_id"], reason="Synthetic QA complete; intentional abandon")
    finally:
        restore()
        page.remove_listener("request", listener)


def phase_accessible_intake(page, ws, dist, run):
    sk, k, sid, d, srv = fresh_course(ws, dist, strict=True)
    try:
        page.goto(srv.base + "/#/")
        post(page, "/api/course/intake", goal="Vektor axtarışında seçimin səbəbini müstəqil izah et")
        post(page, "/api/course/intake", stated_background="İlk dəfədir; konkret nümunəyə ehtiyacım var.")
        k.install_diagnostic_probe(sid, {"id": "dp-browser-help", "node_id": "vec-sim", "kind": "explanation",
                                      "prompt": "Uzunluğu fərqli, eyni istiqamətli iki vektoru bir axtarış tapşırığında necə müqayisə edərdin?",
                                      "criteria": ["İstiqamət və uzunluğu ayırır"]})
        page.reload()
        page.get_by_label("Öz cavabın", exact=True).wait_for()
        page.get_by_label("Öz cavabın", exact=True).fill("Nümunədən sonra istiqaməti müqayisə etməyin lazım olduğunu başa düşdüm.")
        page.get_by_label("Bu cavabda izah, ipucu və ya hazır nümunədən istifadə etdim.").check()
        page.get_by_label("Aldığın kömək (qısa)").fill("Worked example istifadə etdim.")
        page.get_by_role("button", name="Cavabı göndər", exact=True).click()
        page.get_by_text("Cavabın saxlanıb. Agent onu tapşırığın meyarları ilə yoxlayır", exact=False).wait_for()
        k.judge_diagnostic(sid, {"outcome": "correct", "confidence": "high", "assessor": "host_agent",
                               "rationale": "Synthetic rəy: learner cavabda məqsədi göstərir, amma nümunə köməyi qeyd olunub."})
        run.check("assisted diagnostic success asks for smaller independent work", k.diagnostic_next(sid)["action"] == "smaller_independent_probe")
        page.reload()
        page.get_by_text("Bu format mənə uyğun deyil", exact=True).click()
        button = page.get_by_role("button", name="İlkin planla davam etməyə razıyam", exact=True)
        run.check("accessible diagnostic alternative needs explicit reason", button.is_disabled())
        reason = "Hazırda bu mətn formatı əlçatan deyil; agentlə uyğun alternativi sonra yoxlamaq istəyirəm."
        page.get_by_label("Səbəb", exact=True).fill(reason)
        button.click()
        run.check("explicit skip produces provisional planning without competence evidence",
                  base.eventually(page, "async () => { const r=await fetch('/api/course/state');const s=await r.json();return s.intake?.skipped===true; }", None))
        st = base.state(page)
        run.check("skip preserves actual observed assistance and no mastery", st["intake"]["skipped"] and st["intake"]["skip_reason"] == reason
                  and not st["evidence_refs"] and st["intake"]["calibrated_probability"] is None)
        run.check("skip alone keeps generation blocked pending explicit provisional confirmation",
                  not st["generation_readiness"]["ready"] and "provisional_skip_confirmation_required" in st["generation_readiness"]["blockers"])
        k.confirm_goal(sid, {"kind": "learning", "statement": "TEST FIXTURE learner confirmation: diagnostic-i keçdiyim üçün planın ilkin qaldığını və bunun bacarıq sübutu olmadığını qəbul edirəm.",
                             "accept_provisional_skip": True})
        srv.stop()
        srv.start()
        page.reload()
        run.check("accessible fallback agreement survives restart", base.state(page)["intake"]["skipped"])
        run.check("qualified skip still waits for readable resources", base.state(page)["generation_readiness"]["diagnostic_ready"]
                  and not base.state(page)["generation_readiness"]["source_ready"] and not (d / "workspace").exists())
        run.shot(page, "intake-accessible-provisional")
    finally:
        srv.stop()


def phase_grader(page, ws, dist, run):
    sk, k, sid, d, srv = fresh_course(ws, dist)
    try:
        k.record_sources(sid, manifest_for(d / "sources"), base.EXPECTED)
        version = k.propose_roadmap(sid, base.e2e_roadmap())["roadmap_version"]
        k.approve_roadmap(sid, version, channel="cli")
        k.install_lesson(sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        attempt_ids = []
        for number in (1, 2):
            item = copy.deepcopy(CONCEPT_ITEMS["items"][1])
            item["item_id"] = f"open-browser-{number}"
            item["prompt_md"] = ("Yeni ssenari: sənədin uzunluğunun kosinus nəticəsinə təsirini 2-4 cümlə ilə izah et." if number == 1 else
                                 "Fərqli ssenari: bir sənədin bütün vektor elementlərini ikiqat etsək, istiqamət oxşarlığı niyə dəyişməz? 2-4 cümlə ilə əsaslandır.")
            result = k.install_items(sid, "vec-ideas", {"node_id": "vec-ideas", "items": [item]})
            if result["freshness"][item["item_id"]]["status"] == "needs_review":
                k.review_freshness(sid, "vec-ideas", item["item_id"], "distinct", "Fixture: fərqli ssenari və miqyas dəyişməsi soruşulur.")
            att = k.start_exam(sid, "vec-ideas", fast_track=True)["attempt"]
            attempt_ids.append(att["attempt_id"])
            page.goto(srv.base + f"/#/exam/{att['attempt_id']}")
            page.get_by_label("Cavabın", exact=True).wait_for()
            page.get_by_label("Cavabın", exact=True).fill(base.FREE_TEXT_ANSWER)
            base.toolbar(page, "İndi et").click()
            page.get_by_role("heading", name="Cavabın rubrika ilə qiymətləndirilməsini gözləyir", exact=True).wait_for()
            k.judge_and_finalize(sid, att["attempt_id"], {
                "grader": "host_agent", "confidence": "high", "rationale": "Synthetic host-agent rəyidir: normallaşdırma və istiqamət cavabda var.",
                "criteria": {"r-normalize": {"met": True, "evidence_quote": "uzunluqlara böləndə"},
                             "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}}})
            base.wait_hash(page, "#/result/")
            run.check(f"host judgment {number} arrives automatically in the waiting UI", page.evaluate("() => location.hash") == f"#/result/{att['attempt_id']}")
            # Also recover a bookmark/reload of the old waiting exam URL.
            page.goto(srv.base + f"/#/exam/{att['attempt_id']}")
            base.wait_hash(page, "#/result/")
            run.check(f"finalized exam bookmark {number} recovers to its result", page.evaluate("() => location.hash") == f"#/result/{att['attempt_id']}")
            page.get_by_text("AI grader-in düzgünlüyü real cavablar üzərində kalibrasiya edilməyib.", exact=False).wait_for()
            st = base.state(page)
            run.check(f"AI judgment {number} remains provisional with honest warning", st["assessment_result"]["provisional"]
                      and st["assessment_result"]["grader_reliability"] == "uncalibrated_ai"
                      and base.nodes(st)["vec-ideas"]["status"] != "passed")
            run.check(f"AI judgment {number} does not create a review of an unpassed node", not st["review_queue"])
            k.advance(sid)
        run.check("two same-agent judgments require independent corroboration", k.state(sid)["next_mission"]["type"] == "confirm_with_fresh_item")
        page.goto(srv.base + f"/#/result/{attempt_ids[0]}")
        base.wait_hash(page, f"#/result/{attempt_ids[-1]}")
        run.check("an old result bookmark cannot mislabel the latest committed result", page.evaluate("() => location.hash") == f"#/result/{base.state(page)['assessment_result']['attempt_id']}")
        run.shot(page, "ai-grader-provisional")
    finally:
        srv.stop()


def fail_fixture(k, sid, number):
    item = copy.deepcopy(items()["items"][0])
    item["item_id"] = f"failure-browser-{number}"
    item["prompt_md"] = f"Fərqli fixture ssenarisi {number}: `cosine(a,b)` ilə yeni miqyası hesabla."
    for index, check in enumerate(item["checks"]):
        check["code"] = f"assert cosine([{number + index + 1},0], [1,0]) == 1.0"
    out = k.install_items(sid, "vec-sim", {"node_id": "vec-sim", "items": [item]})
    if out["freshness"][item["item_id"]]["status"] == "needs_review":
        k.review_freshness(sid, "vec-sim", item["item_id"], "distinct", "Synthetic fixture: qiymətlər və ssenari əvvəlki cəhddən fərqlidir.")
    att = k.start_exam(sid, "vec-sim", fast_track=True)["attempt"]
    Path(att["file"]).write_text("def cosine(a,b): return -999\n", encoding="utf-8")
    k.submit_and_finalize(sid, att["attempt_id"])
    k.advance(sid)


def phase_recovery(page, ws, dist, run):
    sk, k, sid, d, srv = fresh_course(ws, dist)
    try:
        k.record_sources(sid, manifest_for(d / "sources"), base.EXPECTED)
        version = k.propose_roadmap(sid, base.e2e_roadmap())["roadmap_version"]
        k.approve_roadmap(sid, version, channel="cli")
        base.author(k, sid)
        for number in (1, 2, 3):
            fail_fixture(k, sid, number)
        page.goto(srv.base + "/#/mission")
        page.get_by_role("heading", name="Dəyişmiş planı razılaşdır", exact=True).wait_for()
        st = base.state(page)
        run.check("three failed attempts trigger a bounded recovery decision", st["next_mission"]["type"] == "recovery_check")
        for endpoint in ("mission/start", "exam/start"):
            out = post(page, f"/api/course/{endpoint}", node="vec-sim", fast_track=True)
            run.check(f"pending recovery blocks {endpoint}", out["status"] == 409 and out["data"].get("error") == "recovery_required", out["status"])
        srv.stop()
        srv.start()
        page.reload()
        page.get_by_role("heading", name="Dəyişmiş planı razılaşdır", exact=True).wait_for()
        run.check("recovery stays open across restart", base.state(page)["next_mission"]["type"] == "recovery_check")
        button = page.get_by_role("button", name="Planı qəbul et", exact=True)
        run.check("recovery cannot proceed without learner agreement", button.is_disabled())
        plan = "Əvvəl tapşırığın şərtini öz sözlərimlə izah edəcəyəm, sonra kiçik vektor nümunəsinin nəticəsini təxmin edəcəyəm."
        page.get_by_label("Konkret dəyişmiş addım (20–2000 simvol)").fill(plan)
        run.check("a concrete plan alone is insufficient", button.is_disabled())
        page.get_by_label("Bu planla davam etməyə razıyam.").check()
        button.click()
        page.get_by_text(plan, exact=True).wait_for()
        run.check("agreed changed plan is persisted and dispatched", base.state(page)["next_mission"]["type"] == "targeted_recovery")
        page.reload()
        page.get_by_text(plan, exact=True).wait_for()
        run.check("accepted recovery survives refresh", base.state(page)["next_mission"]["reason_az"] == plan)
        run.shot(page, "recovery-agreed")

        # Exercise the real browser tracker as well as the exact API-counter
        # fixture below. This is engagement telemetry, never learning evidence.
        page.get_by_role("button", name="Fasilə", exact=True).click()
        page.wait_for_timeout(2200)
        page.get_by_role("button", name="Davam et", exact=True).click()
        page.goto(srv.base + "/#/progress")
        page.get_by_role("heading", name="Proqres və sübut", exact=True).wait_for()
        run.check("real browser pause records paused engagement separately", base.eventually(page,
                  "async () => {const r=await fetch('/api/course/state'); const s=await r.json();return (s.engagement.by_kind.other?.paused_seconds||0)>=1;}", None))

        now = datetime.now(UTC)
        interval = {"interval_id": "iv_0123456789abcdef01234567", "kind": "teaching", "node_id": "vec-sim", "attempt_id": None,
                    "started_at": (now - timedelta(seconds=120)).isoformat(), "last_at": now.isoformat(),
                    "active_seconds": 60, "idle_seconds": 30, "paused_seconds": 10, "seq": 1, "ended": False}
        out = post(page, "/api/course/activity", interval=interval)
        run.check("labelled fixture activity records active and idle separately", out["status"] == 200 and out["data"]["status"] == "recorded")
        duplicate = post(page, "/api/course/activity", interval=interval)
        run.check("activity retry cannot count the same interval twice", duplicate["status"] == 200 and duplicate["data"]["status"] == "duplicate_ignored")
        invalid = {**interval, "seq": 2, "active_seconds": 59}
        out = post(page, "/api/course/activity", interval=invalid)
        run.check("time counters cannot decrease on retry", out["status"] == 409)
        srv.stop()
        srv.start()
        page.reload()
        # the app loads in two steps (mode probe, then the learner chunk): wait until it rendered before the server is stopped again
        page.get_by_role("button", name="Vəziyyəti yenilə", exact=True).wait_for()
        eng = base.state(page)["engagement"]["by_kind"]["teaching"]
        run.check("activity totals survive server restart without doubling", eng["active_seconds"] == 60 and eng["idle_seconds"] == 30 and eng["paused_seconds"] == 10, eng)
        run.check("time does not create independent mastery evidence", len(base.state(page)["evidence_refs"]) == 3
                  and base.nodes(base.state(page))["vec-sim"]["status"] != "passed")
        srv.stop()
        page.get_by_role("button", name="Vəziyyəti yenilə", exact=True).click()
        page.get_by_text("Lokal server cavab vermir.", exact=False).wait_for()
        run.check("server outage is exposed instead of claiming writes were saved", "Dəyişikliklər saxlanmır" in page.inner_text("body"))
        srv.start()
        page.get_by_role("button", name="Vəziyyəti yenilə", exact=True).click()
        run.check("manual retry reconnects after interruption", base.eventually(page,
                  "() => !document.body.innerText.includes('Lokal server cavab vermir.')", None))
        run.check("reconnection preserves the accepted mission", base.state(page)["next_mission"]["reason_az"] == plan)
    finally:
        srv.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--node-modules", required=True, help="reuse an existing install; no automatic installation")
    ap.add_argument("--phases", nargs="+", choices=("cli", "target", "intake", "accessible", "grader", "recovery"), default=["cli", "target", "intake", "accessible", "grader", "recovery"])
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    out = Path(args.out).resolve()
    run = base.Run(out)
    run.note("workflow_evidence", "Synthetic fixture, actual runtime/browser; no live model grading, remote import or learning efficacy evidence.")
    run.note("requested_phases", args.phases)
    ws = out / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    with IsolatedState():
        sk, k, sid, d, version = base.new_course(ws)
        dist = base.build_frontend(d / "workspace/frontend", args.node_modules, run)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="az-AZ")
            context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.set_default_timeout(15000)
            try:
                phases = {"cli": phase_cli_staging, "target": phase_target_waiting, "intake": phase_intake, "accessible": phase_accessible_intake, "grader": phase_grader, "recovery": phase_recovery}
                for name in args.phases:
                    run.note("phase_started", name)
                    phases[name](page, ws, dist, run)
            except Exception as exc:
                run.check("requested phases completed without exception", False, f"{type(exc).__name__}: {str(exc)[:600]}")
                try:
                    run.shot(page, "failure")
                except Exception:
                    pass
            finally:
                run.check("no uncaught page errors", not errors, errors[:3])
                context.tracing.stop(path=str(out / "trace.zip"))
                browser.close()
        run.check("trace retained", (out / "trace.zip").is_file() and (out / "trace.zip").stat().st_size > 1000)
    return run.finish()


if __name__ == "__main__":
    sys.exit(main())

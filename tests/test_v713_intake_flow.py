"""Actual local intake state flow. Synthetic observations are not efficacy data."""
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract
from runtime.course import CourseKernel
from runtime.course_state import CourseStateError
from runtime.server import ServerConfig, make_server
from runtime.utils import read_json, write_json
from course_fixture import roadmap, items, lesson
from test_v700_course_flow import CourseCase
from test_v700_course_server import call
from test_v712_journey import observation


def probe(**changes):
    return {"id": "probe-one1", "node_id": "vec-sim", "kind": "explanation",
            "prompt": "Vektorların uzunluqları fərqlidirsə, istiqamət yaxınlığını necə müqayisə edərdin?",
            "criteria": ["Ölçünün təsirini izah edir.", "Qərarını konkret nümunə ilə əsaslandırır."], **changes}


def judgment(**changes):
    return {"outcome": "correct", "confidence": "medium", "assessor": "host_agent",
            "rationale": "Learner ölçünün təsirini ayırır və qərarını öz nümunəsində əsaslandırır.", **changes}


class StrictIntakeFlowTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.sid = self.sk.start("Learner intake", LearningContract())["session_id"]
        self.d = self.sk.dir(self.sid)
        self.k.init(self.sid, require_intake=True)

    def background(self):
        self.k.record_intake(self.sid, {"goal": "Vektor oxşarlığını müstəqil müqayisə etmək"})
        self.k.record_intake(self.sid, {"stated_background": "Sadə cədvəllərlə işlədim, vektor müqayisəsi etməmişəm."})

    def complete_one(self, **changes):
        self.k.install_diagnostic_probe(self.sid, probe(**changes))
        self.k.answer_diagnostic(self.sid, "Uzunluq təsirini çıxarıb istiqaməti müqayisə edərdim.")
        return self.k.judge_diagnostic(self.sid, judgment())

    def test_goal_first_exactly_one_question_and_contract_persistence(self):
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "ask_goal")
        for payload in ({"stated_background": "Python"}, {"goal": "X", "stated_background": "Y"},
                        {}, {"goal": []}, {"goal": " "}):
            with self.subTest(payload=payload), self.assertRaises(CourseStateError):
                self.k.record_intake(self.sid, payload)
        self.k.record_intake(self.sid, {"goal": "Vektor müqayisəsini müstəqil etmək"})
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "ask_background")
        self.k.record_intake(self.sid, {"stated_background": "Bir əvvəlki tapşırıqda sadə cədvəl hazırladım."})
        self.assertEqual(self.k.diagnostic_next(self.sid)["action"], "performance_probe")
        contract = read_json(self.d / "learning-contract.json")
        self.assertEqual(contract["goal"], "Vektor müqayisəsini müstəqil etmək")
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])

    def test_no_probe_before_intake_and_no_response_or_judgment_before_probe(self):
        for operation in (lambda: self.k.install_diagnostic_probe(self.sid, probe()),
                          lambda: self.k.answer_diagnostic(self.sid, "A real response"),
                          lambda: self.k.judge_diagnostic(self.sid, judgment()),
                          lambda: self.k.skip_diagnostic(self.sid, "Bu gün ilkin diagnostic-i keçmək istəyirəm.")):
            with self.assertRaises(CourseStateError):
                operation()
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        for operation in (lambda: self.k.install_diagnostic_probe(self.sid, probe(id="probe-two2")),
                          lambda: self.k.judge_diagnostic(self.sid, judgment())):
            with self.assertRaises(CourseStateError):
                operation()
        self.k.answer_diagnostic(self.sid, "Learner-in saxlanmış cavabını itirmək olmaz.")
        with self.assertRaises(CourseStateError):
            self.k.skip_diagnostic(self.sid, "Hazırda saxlanmış cavabımı silib keçmək istəyirəm.")

    def test_required_diagnostic_blocks_approval_and_lesson_generation(self):
        self.background()
        for operation in (lambda: self.k.propose_roadmap(self.sid, roadmap()),
                          lambda: self.k.approve_roadmap(self.sid, "rm-not-ready", channel="cli"),
                          lambda: self.k.install_items(self.sid, "vec-sim", items()),
                          lambda: self.k.install_lesson(self.sid, "vec-sim", lesson())):
            with self.assertRaises(CourseStateError) as ctx:
                operation()
            self.assertEqual(ctx.exception.code, "diagnostic_required")

    def test_actual_response_waits_for_judgment_survives_restart_and_replay(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        response = "Learner-in öz cavabı: istiqamət üçün uzunluğu nəzərə alıb müqayisə edərdim."
        out = self.k.answer_diagnostic(self.sid, response, assistance=["A separate worked example"],
                                       idempotency_key="probe-answer1")
        self.assertEqual(out["intake"]["status"], "awaiting_judgment")
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])
        self.k = CourseKernel(self.ws)
        self.assertEqual(self.k.diagnostic_next(self.sid)["probe"]["response"], response)
        replay = self.k.answer_diagnostic(self.sid, response, assistance=["A separate worked example"],
                                          idempotency_key="probe-answer1")
        self.assertTrue(replay["idempotent_replay"])
        with self.assertRaises(CourseStateError):
            self.k.answer_diagnostic(self.sid, "A changed response", idempotency_key="probe-answer1")
        self.k.judge_diagnostic(self.sid, judgment(), idempotency_key="probe-judgment1")
        self.k.judge_diagnostic(self.sid, judgment(), idempotency_key="probe-judgment1")
        entry = self.k.state(self.sid)["learner_profile"]["diagnostic"][0]
        self.assertEqual(entry["response"], response)
        self.assertEqual(entry["independence"], "assisted")
        self.assertEqual(len(self.k.state(self.sid)["learner_profile"]["diagnostic"]), 1)

    def test_two_distinct_scoped_probes_stop_provisionally_without_mastery(self):
        self.background()
        self.complete_one()
        self.assertFalse(self.k.diagnostic_next(self.sid)["ready_to_plan"])
        with self.assertRaises(CourseStateError):
            self.k.install_diagnostic_probe(self.sid, probe(id="probe-copy1"))
        self.complete_one(id="probe-two2", kind="scenario",
                          prompt="Mətn axtarışında uzun sənədin daha çox sözü varsa, müqayisə üçün nəyi yoxlayardın?")
        state = self.k.state(self.sid)
        self.assertEqual(state["phase"], "source_preparation")
        self.assertTrue(state["intake"]["ready_to_plan"])
        self.assertEqual(state["intake"]["observations"], 2)
        self.assertEqual(state["intake"]["calibrated_probability"], None)
        with self.assertRaises(CourseStateError):
            self.k.install_diagnostic_probe(self.sid, probe(id="probe-three3", prompt="Üçüncü başqa tapşırığı həll et və izah et."))
        self.assertEqual(self.strong(), 0)

    def test_explicit_skip_keeps_profile_provisional_and_creates_no_evidence(self):
        self.background()
        self.k.skip_diagnostic(self.sid, "İndi qısa diagnostic-i keçib ilkin planla başlamağı seçirəm.")
        state = self.k.state(self.sid)
        self.assertTrue(state["intake"]["skipped"])
        self.assertTrue(state["intake"]["ready_to_plan"])
        self.assertTrue(state["intake"]["provisional"])
        self.assertEqual(state["learner_profile"]["diagnostic"], [])
        self.assertEqual(self.strong(), 0)

    def test_unattempted_probe_can_be_explicitly_skipped_and_is_preserved_in_history(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        self.k.skip_diagnostic(self.sid, "Bu tapşırığı indi cavablandırmadan ilkin planla başlamağı seçirəm.")
        state = self.k.state(self.sid)
        self.assertTrue(state["intake"]["skipped"])
        history = state["learner_profile"]["intake_policy"]["probe_history"]
        self.assertEqual(history[0]["status"], "skipped_by_learner")
        self.assertEqual(state["learner_profile"]["diagnostic"], [])

    def test_goal_revision_cannot_relabel_an_active_or_finalized_probe(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        with self.assertRaises(CourseStateError):
            self.k.record_intake(self.sid, {"goal": "Tamamilə fərqli bir məqsəd"})
        self.k.answer_diagnostic(self.sid, "Learner-in əsl cavabı.")
        self.k.judge_diagnostic(self.sid, judgment())
        with self.assertRaises(CourseStateError):
            self.k.record_intake(self.sid, {"goal": "Tamamilə fərqli bir məqsəd"})

    def test_malformed_probe_response_and_judgment_never_record_observations(self):
        self.background()
        revision = self.k.state(self.sid)["revision"]
        for change in ({"kind": []}, {"criteria": "one"}, {"criteria": []}, {"node_id": []},
                       {"prompt": "short"}, {"answer_key": "Never display this"}):
            with self.subTest(change=change), self.assertRaises(CourseStateError):
                self.k.install_diagnostic_probe(self.sid, probe(**change))
        self.assertEqual(self.k.state(self.sid)["revision"], revision)
        self.k.install_diagnostic_probe(self.sid, probe())
        for response in (None, [], "", " " * 2, "X" * 8001):
            with self.subTest(response=str(response)[:40]), self.assertRaises(CourseStateError):
                self.k.answer_diagnostic(self.sid, response)
        self.k.answer_diagnostic(self.sid, "Bu, learner-in həqiqi saxlanmış cavabıdır.")
        for changes in ({"outcome": []}, {"confidence": []}, {"assessor": []}, {"rationale": "short"}):
            with self.subTest(changes=changes), self.assertRaises(CourseStateError):
                self.k.judge_diagnostic(self.sid, judgment(**changes))
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])

    def test_judgment_cannot_overwrite_captured_learner_response_or_task(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        self.k.answer_diagnostic(self.sid, "Əsl saxlanmış learner cavabı.")
        for field, value in (("response", "Forged correct response"), ("node_id", "forged-node"),
                             ("prompt", "A different task prompt"), ("independence", "unassisted")):
            with self.subTest(field=field), self.assertRaises(CourseStateError):
                self.k.judge_diagnostic(self.sid, judgment(**{field: value}))
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])

    def test_snapshot_tamper_does_not_change_captured_response_and_resubmit_is_refused(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        original = "Original captured response before interruption."
        self.k.answer_diagnostic(self.sid, original)
        path = self.d / "course-state.json"
        altered = read_json(path)
        altered["learner_profile"]["intake_policy"]["active_probe"]["response"] = "Tampered snapshot response"
        write_json(path, altered)
        self.k = CourseKernel(self.ws)
        self.assertEqual(self.k.diagnostic_next(self.sid)["probe"]["response"], original)
        with self.assertRaises(CourseStateError):
            self.k.answer_diagnostic(self.sid, "A second response without the same replay key")
        self.k.judge_diagnostic(self.sid, judgment())
        self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"][0]["response"], original)

    def test_legacy_observation_submission_cannot_bypass_strict_capture_flow(self):
        self.background()
        with self.assertRaises(CourseStateError):
            self.k.record_diagnostic(self.sid, {"entries": [observation(), observation(id="dx-second",
                prompt="Başqa bir vector müqayisəsində qərarını izah et.")]})
        self.assertFalse(self.k.diagnostic_next(self.sid)["ready_to_plan"])

    def test_http_cannot_supply_grade_or_access_a_judgment_route(self):
        self.background()
        self.k.install_diagnostic_probe(self.sid, probe())
        ui = self.ws / "ui-intake"
        ui.mkdir()
        (ui / "index.html").write_text("<!doctype html><title>Intake fixture</title>")
        server = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True,
                            workspace=self.ws, ui_dir=ui, course_session=self.sid))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        port = server.server_address[1]
        try:
            revision = self.k.state(self.sid)["revision"]
            code, _, _ = call(port, "/api/course/diagnostic/judge", {"ui_schema": 2,
                               "expected_revision": revision, **judgment()})
            self.assertEqual(code, 404)
            code, _, _ = call(port, "/api/course/diagnostic/answer", {"ui_schema": 2,
                               "expected_revision": revision, "response": "The learner's actual response.",
                               "outcome": "correct", "confidence": "high", "assessor": "human"})
            self.assertIn(code, (200, 400, 409))
            self.assertEqual(self.k.state(self.sid)["learner_profile"]["diagnostic"], [])
            self.assertFalse(self.k.diagnostic_next(self.sid)["ready_to_plan"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

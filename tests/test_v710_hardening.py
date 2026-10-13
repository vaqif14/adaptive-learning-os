"""Hardening for the v7.1 remediation (beyond the red-first audit regressions).

Freshness review, capability impact/dependents, v1->v2 migration and down export,
grounding failure modes across restart/reapproval, non-code and rubric paths,
review-queue retry/abandon/invalidation/idempotence, bridge routes and CLI.
All data is the synthetic TEST FIXTURE course.
"""
import copy
import hashlib
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import course_state as cs  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.course_state import CourseStateError  # noqa: E402
from runtime.local_state import commit_state  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from course_fixture import (CONCEPT_ITEMS, CONCEPT_LESSON, COSINE_REF, EXAM_NO_NORMALIZE, NEAREST_REF,  # noqa: E402
                            QUOTE, SOURCE_TEXT, concept_roadmap, items, lesson, manifest_for, roadmap)
from test_v700_course_flow import CourseCase  # noqa: E402
from test_v700_course_server import call  # noqa: E402
from test_v710_audit_regressions import Clock, pass_exam  # noqa: E402

NEAR_RATIONALE = "Miqyas halı yeni ədədlərlə yoxlanır; tapşırığın əsas halları eynidir, transfer fərqlidir."


def evidence_for(case, attempt_id):
    return next(r["payload"] for r in case.sk.ledger(case.sid).verified_records()
                if r["payload"].get("evidence_id") == f"ev_exam_{attempt_id}")


def nodes_of(state):
    return {n["id"]: n for n in state["roadmap"]["nodes"]}


def near_copy():
    payload = items()
    it = copy.deepcopy(payload["items"][0])
    it["item_id"] = "cosine-a-near"
    it["checks"] = list(reversed(it["checks"]))
    it["checks"][0]["code"] = ("assert abs(cosine([2,0],[7,0]) - 1) < 1e-9 "
                               "and abs(cosine([0,1],[0,-3]) + 1) < 1e-9")
    it["prompt_md"] = ("Yeni tapşırıq: kosinus funksiyasını `cosine(a, b)` adı ilə yaz; "
                       "sıfır uzunluqlu vektorda nəticə 0.0 olsun.")
    return {"node_id": "vec-sim", "items": [it]}


def search_content():
    """Synthetic content for the dependent node ``vec-search``."""
    it = copy.deepcopy(items()["items"][1])
    it["item_id"] = "search-nearest"
    les = lesson()
    les["node_id"] = "vec-search"
    return {"node_id": "vec-search", "items": [it]}, les


class FreshnessReviewTests(CourseCase):
    def test_distinct_decision_makes_item_eligible_but_caps_evidence(self):
        self.approve()
        self.author()
        pass_exam(self)
        pass_exam(self, code=NEAREST_REF)
        out = self.k.install_items(self.sid, "vec-sim", near_copy())
        self.assertEqual(out["freshness"]["cosine-a-near"]["status"], "needs_review")
        with self.assertRaises(CourseStateError):
            self.k.review_freshness(self.sid, "vec-sim", "cosine-a-near", "distinct", "fərqlidir")
        with self.assertRaises(CourseStateError):   # exact duplicates can never be reviewed into freshness
            self.k.review_freshness(self.sid, "vec-sim", "cosine-a", "distinct", NEAR_RATIONALE)
        self.k.review_freshness(self.sid, "vec-sim", "cosine-a-near", "distinct", NEAR_RATIONALE)
        att = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertEqual(att["item"]["item_id"], "cosine-a-near")
        Path(att["file"]).write_text(COSINE_REF, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        ev = evidence_for(self, att["attempt_id"])
        self.assertEqual((ev["outcome"], ev["strength"], ev["freshness_basis"]), ("correct", "medium", "author_review"))

    def test_copies_inside_one_payload_are_rejected(self):
        self.approve()
        payload = items()
        twin = copy.deepcopy(payload["items"][0])
        twin["item_id"] = "cosine-twin"
        twin["title"] = "Başqa ad"
        payload["items"].append(twin)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.install_items(self.sid, "vec-sim", payload)
        self.assertIn("same thing", str(ctx.exception))

    def test_task_fingerprint_is_content_identity_not_item_id(self):
        self.approve()
        self.author()
        att, _ = pass_exam(self)
        ev = evidence_for(self, att["attempt_id"])
        expected = hashlib.sha256((ev["content_fingerprint"] + hashlib.sha256(COSINE_REF.encode()).hexdigest())
                                  .encode()).hexdigest()
        self.assertEqual(ev["task_fingerprint"], expected)
        self.assertTrue(ev["capability_version"].startswith("cap-"))


class CapabilityImpactTests(CourseCase):
    def _pass_both(self):
        self.approve()
        self.author()
        pass_exam(self)
        its, les = search_content()
        self.k.install_items(self.sid, "vec-search", its)
        self.k.install_lesson(self.sid, "vec-search", les)
        pass_exam(self, node="vec-search", code=NEAREST_REF)

    def test_preview_supersede_and_dependents_keep_their_own_pass(self):
        self._pass_both()
        changed = roadmap()
        changed["nodes"][0]["outcomes"] = ["Kosinus və Evklid məsafəsini müqayisə edə bilir"]
        proposed = self.k.propose_roadmap(self.sid, changed)
        impact = proposed["capability_impact"]["nodes"]
        self.assertEqual(impact["vec-sim"]["change"], "capability_changed")
        self.assertEqual(impact["vec-search"]["upstream_changed"], ["vec-sim"])
        review = self.k.state(self.sid)["roadmap"]["review_required"]
        self.assertEqual(review["impact"]["summary"]["capability_changed"], ["vec-sim"])
        out = self.k.approve_roadmap(self.sid, proposed["roadmap_version"], channel="cli")
        self.assertEqual(out["superseded"]["passes"], ["vec-sim"])
        st = self.k.state(self.sid)
        nodes = nodes_of(st)
        self.assertEqual(nodes["vec-sim"]["status"], "available")
        self.assertEqual(nodes["vec-search"]["status"], "passed")
        self.assertEqual(nodes["vec-search"]["progress"]["upstream_changed"]["nodes"], ["vec-sim"])
        sup = nodes["vec-sim"]["progress"]["superseded_passes"]
        self.assertEqual(len(sup), 1)
        self.assertTrue(sup[0]["passed_at"])
        self.assertEqual(st["capability_history"][-1]["node_id"], "vec-sim")
        self.assertEqual(nodes["vec-sim"]["lesson"]["status"], "capability_changed")
        self.assertIn("Tarixi nüsxə", nodes["vec-sim"]["lesson"]["historical_snapshot"]["label_az"])

    def test_reauthoring_restores_readiness_but_needs_fresh_evidence(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"][0]["task"] = "İki vektorun oxşarlığını kosinus və miqyasla izah et."
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "no_fresh_item")      # old items are bound to the old capability
        self.author()                                                # re-authored for the approved capability
        self.assertEqual(nodes_of(self.k.state(self.sid))["vec-sim"]["lesson"]["status"], "ready")
        att = self.k.start_exam(self.sid, "vec-sim")["attempt"]
        self.assertEqual(att["item"]["item_id"], "nearest-b")        # the exposed item stays spent

    def test_prerequisite_change_and_added_node_keep_capability_progress(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"].insert(0, {"id": "vec-basics", "title": "Vektor əsasları", "task": "Vektoru tanı.",
                                    "requires": [], "outcomes": ["Vektoru siyahı kimi yazır"], "skills": ["list"],
                                    "practice": "kiçik nümunə", "source_refs": ["r1"]})
        changed["nodes"][1]["requires"] = ["vec-basics"]
        p = self.k.propose_roadmap(self.sid, changed)
        self.assertEqual(p["capability_impact"]["nodes"]["vec-sim"]["change"], "prerequisites_changed")
        self.assertEqual(p["capability_impact"]["nodes"]["vec-basics"]["change"], "added")
        self.k.approve_roadmap(self.sid, p["roadmap_version"], channel="cli")
        self.assertEqual(nodes_of(self.k.state(self.sid))["vec-sim"]["status"], "passed")

    def test_roadmap_revision_keeps_the_built_app_servable(self):
        self.approve()
        dist = self.d / "workspace" / "frontend" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<!doctype html><title>built</title>", encoding="utf-8")
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın k sənədi tap."
        self.k.propose_roadmap(self.sid, changed)
        self.assertEqual((self.d / "workspace" / "frontend" / "dist" / "index.html").read_text(encoding="utf-8"),
                         "<!doctype html><title>built</title>")

    def test_removed_node_keeps_its_history(self):
        self.approve()
        changed = roadmap()
        changed["nodes"] = changed["nodes"][:1]
        p = self.k.propose_roadmap(self.sid, changed)
        self.assertEqual(p["capability_impact"]["nodes"]["vec-search"]["change"], "removed")
        self.k.approve_roadmap(self.sid, p["roadmap_version"], channel="cli")
        self.assertEqual([h["node_id"] for h in self.k.state(self.sid)["capability_history"]], ["vec-search"])


class MigrationTests(CourseCase):
    def _journal_as_v1(self, extra=None):
        v1 = cs.down_v2_to_v1(read_json(self.d / "course-state.json"))
        v1.pop("v2_archive")
        v1.update(extra or {})
        commit_state(self.d, self.sk.ledger(self.sid), {"event_type": "test_simulated_v1_state"},
                     {"course-state.json": v1})
        return v1

    def test_v1_course_migrates_keeping_progress_exposure_and_unknown_keys(self):
        self.approve()
        self.author()
        pass_exam(self)
        v1 = self._journal_as_v1({"custom_extension": {"keep": True}})
        st = self.k.state(self.sid)
        raw = read_json(self.d / "course-state.json")
        self.assertEqual(raw["schema_version"], 2)
        self.assertEqual(raw["custom_extension"], {"keep": True})
        self.assertEqual(read_json(self.d / "course-state.pre-v2.json"), v1)
        self.assertEqual(raw["capabilities"]["vec-sim"]["basis"], "legacy_migration")
        self.assertEqual(raw["lesson_bindings"]["vec-sim"]["basis"], "legacy_migration")
        self.assertTrue(raw["exposure_records"]["vec-sim"][0]["content_fingerprint"])
        self.assertEqual([(q["node_id"], q["basis"]) for q in raw["review_queue"]], [("vec-sim", "legacy_migration")])
        self.assertFalse(raw["legacy_binding_pending"])
        nodes = nodes_of(st)
        self.assertEqual((nodes["vec-sim"]["status"], nodes["vec-sim"]["lesson"]["status"]), ("passed", "ready"))
        events = [r["payload"].get("event_type") for r in self.sk.ledger(self.sid).verified_records()
                  if r["record_type"] == "event"]
        self.assertIn("course_state_migrated", events)
        self.assertIn("course_legacy_bindings_resolved", events)
        self.assertEqual(self.k.start_exam(self.sid, "vec-sim")["attempt"]["item"]["item_id"], "nearest-b")

    def test_pending_legacy_change_is_superseded_precisely_on_approval(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"][0]["outcomes"] = ["Fərqli nəticə"]
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        self._journal_as_v1()
        self.assertEqual(read_json(self.d / "course-state.json")["schema_version"], 1)
        self.k.state(self.sid)
        raw = read_json(self.d / "course-state.json")
        self.assertIsNone(raw["capabilities"]["vec-sim"]["version"])
        self.assertEqual(raw["capabilities"]["vec-search"]["basis"], "legacy_unverified")
        out = self.k.approve_roadmap(self.sid, v, channel="cli")
        self.assertEqual(out["superseded"]["passes"], ["vec-sim"])

    def test_down_migration_archives_and_cli_export_never_touches_live_state(self):
        import alearn
        self.approve()
        down = cs.down_v2_to_v1(read_json(self.d / "course-state.json"))
        self.assertEqual(down["schema_version"], 1)
        self.assertEqual(set(down["v2_archive"]), set(cs.V2_ONLY_KEYS))
        self.assertFalse(set(cs.V2_ONLY_KEYS) & set(down))
        with self.assertRaises(cs.StateVersionError):
            cs.down_v2_to_v1(down)
        out = self.ws / "down.json"
        common = ["--workspace", str(self.ws), "course", "schema-down", "--session", self.sid, "--output", str(out)]
        self.assertEqual(alearn.main(common), 0)
        self.assertEqual(read_json(out)["schema_version"], 1)
        self.assertEqual(read_json(self.d / "course-state.json")["schema_version"], 2)
        self.assertEqual(alearn.main(common), 1)                                # never overwrites


class GroundingFailureModeTests(CourseCase):
    def _record(self, mutate):
        staging = self.d / "sources-staging"
        stage = staging / f"stg_{len(list(staging.glob('*'))) if staging.exists() else 0}"
        m = manifest_for(stage)
        mutate(m, stage)
        m["manifest_version"] = "sm-" + stage.name
        return self.k.record_sources(self.sid, m, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}],
                                     staged_cache=stage)

    @staticmethod
    def _rewrite(text):
        def mutate(m, stage):
            (stage / "src-fixture-001.txt").write_text(text, encoding="utf-8")
            m["resources"][0]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        return mutate

    def test_each_invalid_status_blocks_and_a_valid_manifest_restores(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")

        def status(value):
            return lambda m, stage: m["resources"][0].update(status=value)

        def missing(m, stage):
            m["resources"] = []
        for label, mutate in (("unreadable", status("unreadable")), ("identity_mismatch", status("identity_mismatch")),
                              ("partial", status("partial")), ("missing", missing)):
            with self.subTest(label):
                out = self._record(mutate)
                self.assertIn("vec-sim", out["grounding_impact"])
                with self.assertRaises(CourseStateError) as ctx:
                    self.k.help(self.sid, "vec-sim", "explain")
                self.assertEqual(ctx.exception.code, "grounding_invalid")
                with self.assertRaises(CourseStateError) as ctx:
                    CourseKernel(self.ws).help(self.sid, "vec-sim", "why")          # survives restart
                self.assertEqual(ctx.exception.code, "grounding_invalid")
        self._record(lambda m, stage: None)                                     # the original text again
        self.assertEqual(self.k.help(self.sid, "vec-sim", "explain")["content"]["refs"][0]["grounding"], "source")

    def test_changed_text_without_the_quote_needs_reauthoring_and_blocks_exam(self):
        self.approve()
        self.author()
        out = self._record(self._rewrite(SOURCE_TEXT.replace(QUOTE, "a different formulation of the idea")))
        self.assertFalse(out["grounding_impact"]["vec-sim"]["rebindable"])
        self.assertEqual(self.k.reground(self.sid, "vec-sim")["status"], "needs_reauthoring")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_exam(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "grounding_invalid")
        st = self.k.state(self.sid)
        node = nodes_of(st)["vec-sim"]
        self.assertEqual(node["lesson"]["grounding"], "invalid")
        self.assertIn("Tarixi nüsxə", node["lesson"]["historical_snapshot"]["label_az"])
        self.assertEqual(st["next_mission"]["blocked_on"], "grounding_invalid")
        self.assertNotIn("practice", self.k.lesson_view(self.sid, "vec-sim"))     # no current teaching material

    def test_block_survives_cosmetic_reapproval(self):
        self.approve()
        self.author()
        self._record(lambda m, stage: m["resources"][0].update(status="unreadable"))
        cosmetic = roadmap()
        cosmetic["nodes"][0]["pitfalls"] = ["Başqa ifadə"]
        cosmetic["nodes"][0]["title"] = "Vektor oxşarlığı (kosinus)"
        v = self.k.propose_roadmap(self.sid, cosmetic)["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_mission(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "grounding_invalid")

    def test_missing_cached_object_fails_closed(self):
        self.approve()
        self.author()
        self.k.start_mission(self.sid, "vec-sim")
        self._record(self._rewrite(SOURCE_TEXT + " Appended."))
        self.assertEqual(self.k.reground(self.sid, "vec-sim")["status"], "rebound")
        self.k.help(self.sid, "vec-sim", "explain")
        sha = read_json(self.d / "source-manifest.json")["resources"][0]["content_sha256"]
        (self.d / "sources" / "objects" / f"{sha}.txt").unlink()               # simulated disk loss (test only)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.help(self.sid, "vec-sim", "explain")
        self.assertEqual(ctx.exception.code, "grounding_invalid")


class NonCodeHardeningTests(CourseCase):
    RESPONSE = ("Dot product uzun vektorlarda böyüyür; uzunluqlara böləndə yalnız istiqamət qalır "
                "və nəticə müqayisə olunur.")

    def setUp(self):
        super().setUp()
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        v = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")

    def _install(self, which):
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][i] for i in which]
        self.k.install_items(self.sid, "vec-ideas", payload)

    @staticmethod
    def _judgment(**over):
        j = {"grader": "host_agent", "confidence": "medium",
             "rationale": "Cavab normallaşdırmanın uzunluğu aradan qaldırdığını və istiqaməti saxladığını deyir.",
             "criteria": {"r-normalize": {"met": True, "evidence_quote": "uzunluqlara böləndə"},
                          "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}}}
        j.update(over)
        return j

    def test_single_rubric_judgment_is_provisional_until_corroborated(self):
        self._install([1])
        self.k.install_lesson(self.sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.assertEqual(att["kind"], "free_text")
        self.assertNotIn("rubric", json.dumps(att["item"]))                     # criteria hidden until judged
        self.k.save_answers(self.sid, att["attempt_id"], {"response": self.RESPONSE})
        self.assertEqual(self.k.submit_and_finalize(self.sid, att["attempt_id"])["status"], "awaiting_rubric")
        fabricated = self._judgment(criteria={"r-normalize": {"met": True, "evidence_quote": "normalization is explained"},
                                              "r-direction": {"met": True, "evidence_quote": "yalnız istiqamət qalır"}})
        with self.assertRaises(CourseStateError) as ctx:
            self.k.record_rubric(self.sid, att["attempt_id"], fabricated)
        self.assertEqual(ctx.exception.code, "rubric_required")
        with self.assertRaises(CourseStateError) as ctx:                       # every criterion must be judged
            self.k.record_rubric(self.sid, att["attempt_id"], self._judgment(criteria={"r-normalize": {"met": False}}))
        self.assertEqual(ctx.exception.code, "rubric_required")
        done = self.k.judge_and_finalize(self.sid, att["attempt_id"], self._judgment())
        self.assertEqual(done["assessment_result"]["outcome"], "correct")
        self.assertTrue(done["assessment_result"]["provisional"])
        self.assertEqual(done["next_mission"]["type"], "confirm_with_fresh_item")
        ev = evidence_for(self, att["attempt_id"])
        self.assertEqual((ev["strength"], ev["correctness_checked"], ev["verification"]),
                         ("medium", False, "rubric_host_agent"))
        self.assertNotEqual(nodes_of(self.k.state(self.sid))["vec-ideas"]["status"], "passed")
        self.k.advance(self.sid)
        self._install([0])
        exam = self.k.start_exam(self.sid, "vec-ideas")["attempt"]
        self.k.save_answers(self.sid, exam["attempt_id"], {"q1": "c", "q2": ["a", "c"], "q3": ["s2", "s1", "s3"]})
        self.k.submit_and_finalize(self.sid, exam["attempt_id"])
        self.assertEqual(nodes_of(self.k.state(self.sid))["vec-ideas"]["status"], "passed")   # corroborated

    def test_answer_shapes_key_privacy_and_resume(self):
        self._install([0])
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        for bad in ({"q1": ["c"]}, {"zz": "a"}, {"q3": "s1"}, "not-an-object"):
            with self.subTest(bad=bad), self.assertRaises(CourseStateError):
                self.k.save_answers(self.sid, att["attempt_id"], bad)
        self.k.save_answers(self.sid, att["attempt_id"], {"q1": "a"})
        dumped = json.dumps(self.k.state(self.sid), ensure_ascii=False)
        self.assertNotIn('"answer"', dumped)
        self.assertNotIn("length-not-direction", dumped)
        again = CourseKernel(self.ws).start_exam(self.sid, "vec-ideas")
        self.assertTrue(again["resumed"])
        self.assertEqual(again["attempt"]["saved_answers"], {"q1": "a"})
        res = self.k.submit_and_finalize(self.sid, att["attempt_id"])["assessment_result"]
        self.assertEqual(res["outcome"], "incorrect")
        self.assertEqual([m["id"] for m in res["misconception_feedback"]], ["length-not-direction"])
        self.assertIn("Cavab verilməyib.", [c["error"] for c in res["checks"]])

    def test_practice_kinds_are_not_interchangeable(self):
        self._install([0])
        self.k.install_lesson(self.sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        self.k.start_mission(self.sid, "vec-ideas")
        began = self.k.begin_practice(self.sid, "vec-ideas")
        self.assertEqual(began["kind"], "concept")
        self.assertNotIn("answer", json.dumps(began["questions"]))
        with self.assertRaises(CourseStateError):
            self.k.practice_check(self.sid, "vec-ideas")
        self.assertEqual(self.k.lesson_view(self.sid, "vec-ideas")["practice"]["kind"], "concept")

    def test_invalid_question_designs_are_rejected(self):
        def variant(mutate):
            payload = copy.deepcopy(CONCEPT_ITEMS)
            payload["items"] = [payload["items"][0]]
            mutate(payload["items"][0])
            return payload
        cases = {
            "ordering_in_answer_order": lambda it: it["questions"][2].update(answer=["s1", "s2", "s3"]),
            "answer_not_an_option": lambda it: it["questions"][0].update(answer="z"),
            "multi_all_options": lambda it: it["questions"][1].update(answer=["a", "b", "c"]),
            "no_transfer": lambda it: it["questions"].pop(2),
            "distractor_maps_correct_option": lambda it: it["questions"][0].update(misconceptions={"c": "always-positive"}),
            "duplicate_option_text": lambda it: it["questions"][0]["options"][1].update(text="Vektorların uzunluq fərqini"),
        }
        for label, mutate in cases.items():
            with self.subTest(label), self.assertRaises(CourseStateError):
                self.k.install_items(self.sid, "vec-ideas", variant(mutate))

    def test_scenario_partial_result_targets_the_failed_transfer(self):
        scenario = {"item_id": "ideas-scenario-c", "kind": "scenario", "title": "Axtarış ssenarisi",
                    "scope": "far_transfer", "difficulty": 1,
                    "prompt_md": "Yeni tapşırıq: ssenarini oxu və komandanın qərarını qiymətləndir.",
                    "scenario_md": ("Komanda sənədləri dot product ilə sıralayır. Uzun sənədlər həmişə ilk yerə çıxır, "
                                    "qısa amma mövzuya uyğun sənədlər isə geridə qalır."),
                    "questions": [
                        {"id": "s-cause", "type": "single_choice", "category": "core",
                         "prompt": "Problemin səbəbi nədir?",
                         "options": [{"id": "a", "text": "Normallaşdırma yoxdur"}, {"id": "b", "text": "Sənədlər azdır"}],
                         "answer": "a", "fail_feedback": "Dot product uzunluqla böyüyür; normallaşdırma lazımdır.",
                         "misconceptions": {"b": "volume-confusion"}},
                        {"id": "s-cos", "type": "numeric", "category": "transfer",
                         "prompt": "[3,4] və [6,8] vektorlarının kosinusu neçədir?", "answer": 1, "tolerance": 0.01,
                         "fail_feedback": "Eyni istiqamətli vektorların kosinusu 1-dir."}],
                    "misconceptions": [{"id": "volume-confusion", "name": "Həcmi səbəb saymaq",
                                        "feedback": "Sənəd sayı yox, uzunluq sıralamanı pozur."}]}
        self.k.install_items(self.sid, "vec-ideas", {"node_id": "vec-ideas", "items": [scenario]})
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.assertIn("Uzun sənədlər", att["item"]["scenario_md"])
        self.k.save_answers(self.sid, att["attempt_id"], {"s-cause": "a", "s-cos": "0,5"})
        out = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        self.assertEqual(out["assessment_result"]["outcome"], "partial")
        self.assertEqual(out["next_mission"]["type"], "targeted_practice")
        ev = evidence_for(self, att["attempt_id"])
        self.assertEqual((ev["evidence_format"], ev["strength"]), ("scenario_performance", "medium"))

    def test_awaiting_rubric_survives_restart_and_can_be_abandoned(self):
        self._install([1])
        att = self.k.start_exam(self.sid, "vec-ideas", fast_track=True)["attempt"]
        self.k.save_answers(self.sid, att["attempt_id"], {"response": self.RESPONSE})
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        restarted = CourseKernel(self.ws)
        st = restarted.state(self.sid)
        self.assertEqual((st["phase"], st["attempt_status"]), ("assessment_pending", "awaiting_rubric"))
        self.assertTrue(st["active_attempt"]["rubric_pending"])
        self.assertFalse(restarted.abandon_exam(self.sid, att["attempt_id"])["counted_as_success"])


class ReviewQueueHardeningTests(CourseCase):
    def setUp(self):
        super().setUp()
        self.clock = Clock(datetime.now(UTC) - timedelta(days=3))
        self.k = CourseKernel(self.ws, clock=self.clock)

    def test_not_due_is_refused_and_lapse_schedules_a_short_retry(self):
        self.approve()
        self.author()
        pass_exam(self)
        with self.assertRaises(CourseStateError) as ctx:
            self.k.start_review(self.sid, "vec-sim")
        self.assertEqual(ctx.exception.code, "review_not_due")
        self.clock.advance(hours=25)
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        Path(att["file"]).write_text(EXAM_NO_NORMALIZE, encoding="utf-8")
        out = self.k.submit_and_finalize(self.sid, att["attempt_id"])
        outcome = out["assessment_result"]["outcome"]
        self.assertNotEqual(outcome, "correct")
        self.assertIn("Təkrar yoxlama", out["next_mission"]["reason_az"])
        st = self.k.state(self.sid)
        q = st["review_queue"]
        self.assertEqual((q[0]["status"], q[0]["outcome"]), ("completed", outcome))
        retry = [x for x in q if x["status"] == "scheduled"]
        self.assertEqual((len(retry), retry[0]["interval_days"], retry[0]["basis"]), (1, 0.5, "retry_after_lapse"))
        node = nodes_of(st)["vec-sim"]
        self.assertEqual(node["status"], "passed")                               # history kept, flagged
        self.assertEqual(node["progress"]["retention"]["status"], "lapsed")

    def test_prerequisite_first_order_abandon_keeps_due_and_other_reviews(self):
        self.approve()
        self.author()
        pass_exam(self)
        its, les = search_content()
        self.k.install_items(self.sid, "vec-search", its)
        self.k.install_lesson(self.sid, "vec-search", les)
        pass_exam(self, node="vec-search", code=NEAREST_REF)
        self.clock.advance(hours=25)
        self.assertEqual([r["node_id"] for r in self.k.turn(self.sid)["due_reviews"]], ["vec-sim", "vec-search"])
        self.assertEqual(self.sk.next_action(self.sid)["action"], "course_due_review")
        second = copy.deepcopy(its["items"][0])
        second.update(item_id="search-two",
                      prompt_md=("Yeni tapşırıq: `most_similar(query, docs)` sənədlər siyahısında istiqamətcə ən yaxın "
                                 "olanın yerini versin."),
                      checks=[{"id": "two", "name": "İki sənəd", "category": "core",
                               "code": "assert most_similar([0,1], [[0,2],[3,0]]) == 0",
                               "fail_feedback": "Ən yaxın indeksi qaytar."},
                              {"id": "far", "name": "Uzun yanlış sənəd", "category": "transfer",
                               "code": "assert most_similar([0,1], [[0.1,0.9],[9,9]]) == 0",
                               "fail_feedback": "Uzunluq qalib gəlməməlidir."}])
        self.k.install_items(self.sid, "vec-search", {"node_id": "vec-search", "items": [second]})
        att = self.k.start_review(self.sid, "vec-search")["attempt"]
        self.k.abandon_exam(self.sid, att["attempt_id"])
        st = self.k.state(self.sid)
        q = {x["node_id"]: x for x in st["review_queue"] if x["status"] == "scheduled"}
        self.assertEqual(q["vec-search"]["retries"], 1)
        self.assertIn("vec-sim", q)
        self.assertEqual([r["node_id"] for r in st["due_reviews"]], ["vec-sim", "vec-search"])

    def test_capability_change_invalidates_pending_review(self):
        self.approve()
        self.author()
        pass_exam(self)
        changed = roadmap()
        changed["nodes"][0]["skills"] = ["dot product", "norm", "miqyas"]
        v = self.k.propose_roadmap(self.sid, changed)["roadmap_version"]
        out = self.k.approve_roadmap(self.sid, v, channel="cli")
        self.assertEqual(len(out["superseded"]["reviews"]), 1)
        self.clock.advance(days=3)
        st = self.k.state(self.sid)
        self.assertEqual(st["due_reviews"], [])
        self.assertEqual([q["status"] for q in st["review_queue"]], ["invalidated"])

    def test_review_evidence_never_claims_unobserved_spacing(self):
        self.approve()
        self.author()
        pass_exam(self)
        self.clock.advance(days=2)
        att = self.k.start_review(self.sid, "vec-sim")["attempt"]
        Path(att["file"]).write_text(NEAREST_REF, encoding="utf-8")
        self.k.submit_and_finalize(self.sid, att["attempt_id"])
        ev = evidence_for(self, att["attempt_id"])
        self.assertFalse(ev["review"]["counts_as_delayed"])
        self.assertLess(ev["review"]["observed_spacing_hours"], 1)
        self.assertNotEqual(ev["scope"], "delayed_independent_performance")

    def test_interrupted_finalization_enqueues_exactly_once(self):
        self.approve()
        self.author()
        att = self.k.start_exam(self.sid, "vec-sim", fast_track=True)["attempt"]
        Path(att["file"]).write_text(COSINE_REF, encoding="utf-8")
        self.k.submit_exam(self.sid, att["attempt_id"])
        self.k.score_exam(self.sid, att["attempt_id"])
        real = CourseKernel._commit

        def crash_on_finalize(kernel, d, led, state, event, files=None):
            if event.get("event_type") == "course_exam_finalized":
                raise OSError("crash after evidence append")
            return real(kernel, d, led, state, event, files)
        with patch.object(CourseKernel, "_commit", crash_on_finalize), self.assertRaises(OSError):
            self.k.finalize_exam(self.sid, att["attempt_id"])
        restarted = CourseKernel(self.ws, clock=self.clock)
        st = restarted.state(self.sid)                     # reconcile completes the finalization
        self.assertEqual(st["attempt_status"], "finalized")
        self.assertEqual(len(st["review_queue"]), 1)
        restarted.finalize_exam(self.sid, att["attempt_id"])
        self.assertEqual(len(restarted.state(self.sid)["review_queue"]), 1)


class BridgeRouteTests(CourseCase):
    def test_non_code_and_review_routes_go_through_the_same_guards(self):
        self.k.init(self.sid)
        self.k.record_sources(self.sid, manifest_for(self.d / "sources"),
                              [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        v = self.k.propose_roadmap(self.sid, concept_roadmap())["roadmap_version"]
        self.k.approve_roadmap(self.sid, v, channel="cli")
        payload = copy.deepcopy(CONCEPT_ITEMS)
        payload["items"] = [payload["items"][0]]
        self.k.install_items(self.sid, "vec-ideas", payload)
        self.k.install_lesson(self.sid, "vec-ideas", copy.deepcopy(CONCEPT_LESSON))
        srv = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=self.ws,
                                       course_session=self.sid))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def post(path, body):
            rev = call(port, "/api/course/state")[1]["revision"]
            return call(port, path, {"ui_schema": 2, "expected_revision": rev, **body})
        try:
            rev = call(port, "/api/course/state")[1]["revision"]
            old_build = call(port, "/api/course/mission/start", {"ui_schema": 1, "expected_revision": rev,
                                                                 "node": "vec-ideas"})
            self.assertEqual((old_build[0], old_build[1]["error"]), (409, "stale_ui"))       # v1 build refused
            self.assertEqual(post("/api/course/review/start", {"node": "vec-ideas"})[0], 409)   # not due
            self.assertEqual(post("/api/course/mission/start", {"node": "vec-ideas"})[0], 200)
            self.assertEqual(post("/api/course/practice/begin", {"node": "vec-ideas"})[0], 200)
            code, body, _ = post("/api/course/practice/answer", {"node": "vec-ideas", "answers": {"p1": "b"}})
            self.assertEqual((code, body["passed"]), (200, True))
            att = post("/api/course/exam/start", {"node": "vec-ideas"})[1]["attempt"]
            self.assertEqual(post("/api/course/help", {"node": "vec-ideas", "kind": "explain"})[0], 423)
            self.assertEqual(post("/api/course/exam/answers", {"attempt_id": att["attempt_id"],
                                                               "answers": {"q1": ["bad"]}})[0], 409)
            self.assertEqual(post("/api/course/exam/answers", {"attempt_id": att["attempt_id"],
                                                               "answers": {"q1": "c", "q2": ["a", "c"],
                                                                           "q3": ["s2", "s1", "s3"]}})[0], 200)
            code, body, _ = post("/api/course/exam/submit", {"attempt_id": att["attempt_id"]})
            self.assertEqual((code, body["assessment_result"]["outcome"]), (200, "correct"))
        finally:
            srv.shutdown()


if __name__ == "__main__":
    import unittest
    unittest.main()

"""Stage A: studio jobs - the status machine, stage keys, idempotency, drift/supersede/rebase,
needs_learner, leases and budgets."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime import studio_jobs as jobs  # noqa: E402
from runtime import studio_plan as plan_mod  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import AGENT_DIRECTIVES  # noqa: E402
from runtime.studio_store import StudioError, verify_events  # noqa: E402
from course_fixture import roadmap  # noqa: E402
from studio_fixture import FakeAuthor, StudioCase, search_lesson  # noqa: E402

AT = "2026-10-13T09:00:00+00:00"


def fresh_job(status="planning"):
    job = jobs.new_job("sess_0123456789abcdef", AT, plan_mod.resolve_policy(), {"roadmap_version": "rm-x",
                                                                               "policy_versions": jobs.policy_versions()})
    job["status"] = status
    if status == "paused":
        job["paused_from"] = "authoring"
    return job


class StatusMachineTests(unittest.TestCase):
    def test_every_pair_is_either_in_the_table_or_refused_with_a_stable_code(self):
        self.assertEqual(set(jobs.TRANSITIONS), set(jobs.STATUSES))
        self.assertEqual(len(jobs.STATUSES), 14)
        allowed = refused = 0
        for a in jobs.STATUSES:
            for b in jobs.STATUSES:
                job = fresh_job(a)
                permitted = b in jobs.TRANSITIONS[a]
                if a == "paused":
                    permitted = permitted and b in {"authoring", "cancelled", "superseded", "blocked"}
                if permitted:
                    jobs.transition(job, b, at=AT, reason="t")
                    self.assertEqual(job["status"], b)
                    self.assertEqual(job["status_history"][-1]["from"], a)
                    allowed += 1
                else:
                    with self.assertRaises(StudioError) as ctx:
                        jobs.transition(job, b, at=AT, reason="t")
                    self.assertEqual(ctx.exception.code, "studio_transition_refused", f"{a}->{b}")
                    self.assertEqual(job["status"], a)                       # a refusal changes nothing
                    self.assertEqual(len(job["status_history"]), 1)
                    refused += 1
        self.assertEqual(allowed + refused, 14 * 14)
        self.assertGreater(refused, allowed)

    def test_terminal_statuses_have_no_exit_and_self_transitions_are_refused(self):
        for status in ("promoted", "superseded", "cancelled"):
            self.assertEqual(jobs.TRANSITIONS[status], frozenset())
            self.assertTrue(jobs.is_terminal(fresh_job(status)))
        for status in jobs.STATUSES:
            with self.assertRaises(StudioError):
                jobs.transition(fresh_job(status), status, at=AT, reason="t")

    def test_table_only_names_known_statuses_and_unknown_targets_have_their_own_code(self):
        for targets in jobs.TRANSITIONS.values():
            self.assertTrue(targets <= set(jobs.STATUSES))
        with self.assertRaises(StudioError) as ctx:
            jobs.transition(fresh_job(), "done", at=AT, reason="t")
        self.assertEqual(ctx.exception.code, "studio_unknown_status")

    def test_documented_happy_path_is_reachable(self):
        job = fresh_job()
        for step in ("authoring", "validating", "checking", "remediation", "validating", "checking", "ready_for_review",
                     "accepted", "awaiting_learner_approval", "promoted"):
            jobs.transition(job, step, at=AT, reason="t")
        self.assertEqual(job["status"], "promoted")

    def test_paused_job_only_resumes_to_where_it_was(self):
        job = fresh_job("authoring")
        jobs.transition(job, "paused", at=AT, reason="t")
        self.assertEqual(job["paused_from"], "authoring")
        with self.assertRaises(StudioError):
            jobs.transition(job, "checking", at=AT, reason="t")
        jobs.transition(job, "authoring", at=AT, reason="t")
        self.assertIsNone(job["paused_from"])


class StageKeyAndIdempotencyTests(unittest.TestCase):
    def test_stage_key_is_deterministic_and_sensitive_to_every_input(self):
        versions = jobs.policy_versions()
        key = jobs.stage_key("job_a", "stage", "ih1", versions)
        self.assertEqual(key, jobs.stage_key("job_a", "stage", "ih1", versions))
        self.assertEqual(len(key), 64)
        self.assertNotEqual(key, jobs.stage_key("job_b", "stage", "ih1", versions))
        self.assertNotEqual(key, jobs.stage_key("job_a", "order", "ih1", versions))
        self.assertNotEqual(key, jobs.stage_key("job_a", "stage", "ih2", versions))
        bumped = {**versions, "validators": {**versions["validators"], "lesson": "validate-lesson-v2"}}
        self.assertNotEqual(key, jobs.stage_key("job_a", "stage", "ih1", bumped))

    def test_completing_an_already_completed_stage_key_is_a_noop(self):
        unit = {}
        key = jobs.stage_key("job_a", "stage", "ih1", jobs.policy_versions())
        self.assertTrue(jobs.complete_stage(unit, key))
        self.assertFalse(jobs.complete_stage(unit, key))
        self.assertEqual(unit["stage_keys_done"], [key])

    def test_idempotency_registry_is_body_bound(self):
        registry = {}
        self.assertIsNone(jobs.idem_replay(registry, None, "submit", {"a": 1}))
        self.assertIsNone(jobs.idem_replay(registry, "key-12345678", "submit", {"a": 1}))
        jobs.idem_remember(registry, "key-12345678", "submit", {"a": 1}, {"status": "staged"}, AT)
        self.assertEqual(jobs.idem_replay(registry, "key-12345678", "submit", {"a": 1}),
                         {"status": "staged", "idempotent_replay": True})
        for action, body in (("submit", {"a": 2}), ("plan", {"a": 1})):
            with self.assertRaises(StudioError) as ctx:
                jobs.idem_replay(registry, "key-12345678", action, body)
            self.assertEqual(ctx.exception.code, "studio_conflict")
        with self.assertRaises(StudioError) as ctx:
            jobs.idem_replay(registry, "short", "submit", {})
        self.assertEqual(ctx.exception.code, "studio_invalid_idempotency_key")


class PolicyTests(unittest.TestCase):
    def test_defaults_match_the_design(self):
        policy = plan_mod.resolve_policy()
        self.assertEqual(policy["id"], "studio-targets-v2")
        self.assertEqual(policy["budgets"], {"max_attempts_per_unit": 3, "max_remediation_rounds": 3,
                                             "max_runner_seconds": 900, "max_output_bytes": 1048576})
        self.assertEqual((policy["targets"]["min_items"], policy["targets"]["min_transfer_items"]), (3, 1))
        self.assertFalse(policy["allow_private_sources"])

    def test_policy_overrides_may_tighten_but_never_loosen(self):
        self.assertEqual(plan_mod.resolve_policy({"targets": {"min_items": 5}})["targets"]["min_items"], 5)
        for bad in ({"targets": {"min_items": 2}}, {"targets": {"min_transfer_items": 0}},
                    {"targets": {"items_must_be_fresh": False}}, {"budgets": {"max_attempts_per_unit": 0}},
                    {"budgets": {"nope": 1}}, {"allow_private_sources": "yes"}, {"surprise": 1}):
            with self.assertRaises(StudioError) as ctx:
                plan_mod.resolve_policy(bad)
            self.assertEqual(ctx.exception.code, "studio_policy_invalid")

    def test_units_follow_prerequisite_order_lesson_first(self):
        plan = roadmap()
        plan["nodes"].reverse()                                  # roadmap lists the dependent node first
        order = [u["unit_id"] for u in plan_mod.plan_units(plan)]
        self.assertEqual(order, ["lesson:vec-sim", "items:vec-sim", "lesson:vec-search", "items:vec-search"])

    def test_cycles_are_refused(self):
        plan = roadmap()
        plan["nodes"][0]["requires"] = ["vec-search"]
        with self.assertRaises(StudioError) as ctx:
            plan_mod.plan_units(plan)
        self.assertEqual(ctx.exception.code, "studio_roadmap_cycle")

    def test_items_predicate_demands_count_transfer_and_freshness(self):
        targets = plan_mod.DEFAULT_TARGETS
        mk = lambda scopes: {"items": [{"scope": s} for s in scopes]}          # noqa: E731
        ok = {"valid": True, "freshness": {"a": {"status": "fresh"}}}
        self.assertTrue(plan_mod.evaluate_unit("items", mk(["independent_reproduction", "near_transfer", "far_transfer"]), ok, targets)["complete"])
        few = plan_mod.evaluate_unit("items", mk(["near_transfer", "far_transfer"]), ok, targets)
        self.assertIn("at least 3 items", few["problems"][0])
        core_only = plan_mod.evaluate_unit("items", mk(["independent_reproduction"] * 3), ok, targets)
        self.assertIn("near_transfer or far_transfer", core_only["problems"][0])
        stale = plan_mod.evaluate_unit("items", mk(["near_transfer"] * 3),
                                       {"valid": True, "freshness": {"x": {"status": "needs_review"}}}, targets)
        self.assertTrue(any("not fresh" in p for p in stale["problems"]))
        invalid = plan_mod.evaluate_unit("items", mk([]), {"valid": False, "errors": ["boom"]}, targets)
        self.assertEqual(invalid, {"complete": False, "problems": ["boom"]})

    def test_lesson_predicate_is_extensible_to_walkthrough_and_ladder(self):
        targets = {**plan_mod.TARGETS_V1, "walkthrough_required": True, "ladder_required": True}
        verdict = plan_mod.evaluate_unit("lesson", {"objective": "x"}, {"valid": True}, targets)
        self.assertEqual(len(verdict["problems"]), 2)
        self.assertTrue(plan_mod.evaluate_unit("lesson", {"objective": "x"}, {"valid": True}, plan_mod.TARGETS_V1)["complete"])


class JobLifecycleTests(StudioCase):
    def test_init_creates_one_job_bound_to_the_proposed_roadmap(self):
        out = self.st.init(self.sid)
        self.assertEqual(out["result"], "created")
        job = jobs.load_job(self.studio_root(), out["job_id"])
        self.assertEqual(job["status"], "planning")
        self.assertRegex(job["job_id"], r"^job_[0-9a-f]{16}$")
        binding = job["binding"]
        self.assertEqual(binding["roadmap_version"], self.ck.state(self.sid)["roadmap"]["version"])
        self.assertEqual(set(binding["capabilities"]), {"vec-sim", "vec-search"})
        self.assertEqual(binding["manifest_version"], "sm-fixture")
        self.assertEqual(binding["sources"]["r1"]["content_sha256"],
                         self.ck.studio_snapshot(self.sid)["resources"][0]["content_sha256"])
        self.assertEqual(binding["policy_versions"]["policy"], "studio-targets-v2")
        self.assertIn("lesson", binding["policy_versions"]["validators"])
        self.assertEqual(job["policy"]["budgets"]["max_attempts_per_unit"], 3)
        again = self.st.init(self.sid)
        self.assertEqual((again["result"], again["job_id"]), ("already_initialized", out["job_id"]))
        self.assertEqual(len(jobs.list_jobs(self.studio_root())), 1)

    def test_plan_creates_units_in_prerequisite_order_and_is_idempotent(self):
        job_id = self.st.init(self.sid)["job_id"]
        planned = self.st.plan(self.sid, job_id)
        self.assertEqual(planned["status"], "authoring")
        self.assertEqual(list(planned["units"]), ["lesson:vec-sim", "items:vec-sim", "lesson:vec-search", "items:vec-search"])
        self.assertTrue(all(u["status"] == "pending" for u in planned["units"].values()))
        again = self.st.plan(self.sid, job_id)
        self.assertEqual((again["status"], again["revision"]), ("authoring", planned["revision"]))
        job = jobs.load_job(self.studio_root(), job_id)
        self.assertEqual(len({u["inputs_hash"] for u in job["units"].values()}), 4)

    def test_status_with_no_job(self):
        self.assertEqual(self.st.status(self.sid)["status"], "no_job")

    def test_job_ids_are_validated_before_touching_the_filesystem(self):
        self.st.init(self.sid)
        for bad in ("../x", "job_zz", "job_0123456789abcdef/../../x"):
            with self.assertRaises(StudioError):
                self.st.status(self.sid, bad)
        with self.assertRaises(StudioError) as ctx:
            self.st.status(self.sid, "job_0123456789abcdef")
        self.assertEqual(ctx.exception.code, "studio_job_not_found")

    def test_pause_resume_cancel(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.st.plan(self.sid, job_id)
        self.assertEqual(self.st.pause(self.sid, job_id)["status"], "paused")
        self.assertEqual(self.st.next_task(self.sid, job_id), {"state": "idle", "reason": "paused"})
        with self.assertRaises(StudioError):
            self.st.pause(self.sid, job_id)                              # already paused
        self.assertEqual(self.st.resume(self.sid, job_id)["status"], "authoring")
        self.assertEqual(self.st.next_task(self.sid, job_id)["state"], "order")
        cancelled = self.st.cancel(self.sid, job_id)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.st.next_task(self.sid, job_id), {"state": "blocked", "reason": "cancelled"})
        for op in (self.st.pause, self.st.resume, self.st.cancel):
            with self.assertRaises(StudioError) as ctx:
                op(self.sid, job_id)
            self.assertIn(ctx.exception.code, {"studio_transition_refused"})
        self.assertEqual(self.st.status(self.sid)["status"], "cancelled")
        self.assertTrue(verify_events(self.d, self.sk.ledger(self.sid))["ok"])


class IdempotencyThroughTheKernelTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.job_id = self.st.init(self.sid)["job_id"]
        self.st.plan(self.sid, self.job_id)
        self.task = self.st.next_task(self.sid, self.job_id)
        self.order = self.task["order"]
        self.payload = FakeAuthor().payload_for(self.order)

    def test_same_key_same_body_replays_the_stored_result(self):
        first = self.st.submit(self.sid, self.job_id, self.order["order_id"], self.payload, idempotency_key="submit-key-0001")
        self.assertEqual(first["status"], "staged")
        events = len(self.st.events(self.sid, self.job_id)["events"])
        revision = self.st.status(self.sid, self.job_id)["revision"]
        again = self.st.submit(self.sid, self.job_id, self.order["order_id"], self.payload, idempotency_key="submit-key-0001")
        self.assertTrue(again.pop("idempotent_replay"))
        self.assertEqual(again, first)
        self.assertEqual(len(self.st.events(self.sid, self.job_id)["events"]), events)
        self.assertEqual(self.st.status(self.sid, self.job_id)["revision"], revision)

    def test_same_key_different_body_is_a_conflict(self):
        self.st.submit(self.sid, self.job_id, self.order["order_id"], self.payload, idempotency_key="submit-key-0002")
        other = copy.deepcopy(self.payload)
        other["objective"] += " (changed)"
        with self.assertRaises(StudioError) as ctx:
            self.st.submit(self.sid, self.job_id, self.order["order_id"], other, idempotency_key="submit-key-0002")
        self.assertEqual(ctx.exception.code, "studio_conflict")
        with self.assertRaises(StudioError) as ctx:
            self.st.plan(self.sid, self.job_id, idempotency_key="submit-key-0002")      # same key, another action
        self.assertEqual(ctx.exception.code, "studio_conflict")

    def test_resubmitting_a_staged_order_without_a_key_is_a_stage_key_noop(self):
        first = self.st.submit(self.sid, self.job_id, self.order["order_id"], self.payload)
        again = self.st.submit(self.sid, self.job_id, self.order["order_id"], self.payload)
        self.assertEqual((again["status"], again["reason"]), ("noop", "stage_already_completed"))
        self.assertEqual(again["staged_digest"], first["staged_digest"])
        job = jobs.load_job(self.studio_root(), self.job_id)
        self.assertEqual(len(job["units"]["lesson:vec-sim"]["stage_keys_done"]), 1)
        self.assertEqual(sum(1 for e in self.st.events(self.sid, self.job_id)["events"] if e["type"] == "unit_staged"), 1)


class DriftTests(StudioCase):
    def staged_lesson_job(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.st.plan(self.sid, job_id)
        task = self.st.next_task(self.sid, job_id)
        FakeAuthor().answer(self.st, self.sid, task)
        return job_id

    def finish_with(self, author=None):
        author = author or FakeAuthor()
        return author.run(self.st, self.sid), author

    def test_roadmap_change_supersedes_archives_and_rebase_reuses_unchanged_units(self):
        old_id = self.staged_lesson_job()
        author = FakeAuthor()
        author.run(self.st, self.sid, old_id)                       # build the old job completely first
        self.assertEqual(self.st.status(self.sid, old_id)["status"], "checking")
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın sənədləri tap və uyğunluq xalını izah et."
        self.ck.propose_roadmap(self.sid, changed)
        out = self.st.status(self.sid, old_id)
        self.assertEqual(out["status"], "superseded")
        self.assertEqual(out["drift"], ["roadmap_version", "capabilities"])
        root = self.studio_root()
        self.assertFalse((root / "jobs" / f"{old_id}.json").exists())                    # archived by rename...
        archived = list((root / "archive").glob(f"*-{old_id}.json"))
        self.assertEqual(len(archived), 1)                                                # ...history kept
        self.assertTrue(list((root / "archive").glob(f"*-{old_id}")))                     # orders/inbox archived too
        self.assertTrue((root / "staging" / old_id).is_dir())                             # staged payloads stay for rebase
        events = self.st.events(self.sid, old_id)
        self.assertIn("drift_superseded", [e["type"] for e in events["events"]])
        self.assertTrue(events["verify"]["ok"])
        self.assertEqual(self.st.next_task(self.sid, old_id)["reason"], "superseded")
        with self.assertRaises(StudioError) as ctx:
            self.st.submit(self.sid, old_id, "ord_0000000000000000", {})
        self.assertEqual(ctx.exception.code, "studio_job_superseded")

        rebased = self.st.rebase(self.sid, old_id)
        self.assertEqual(rebased["result"], "rebased")
        self.assertEqual(rebased["reused_units"], ["items:vec-sim", "lesson:vec-sim"])
        self.assertEqual(rebased["units"]["lesson:vec-search"]["status"], "pending")
        self.assertEqual(rebased["units"]["lesson:vec-sim"]["reused_from"], old_id)
        self.assertEqual(rebased["binding"]["roadmap_version"], self.ck.state(self.sid)["roadmap"]["version"])
        again = self.st.rebase(self.sid, old_id)
        self.assertEqual((again["result"], again["job_id"]), ("already_rebased", rebased["job_id"]))
        self.assertEqual(self.st.status(self.sid, old_id)["superseded_by"], rebased["job_id"])
        last, author2 = self.finish_with()
        self.assertEqual(last["state"], "done")
        self.assertEqual([o["unit_id"] for o in author2.seen], ["lesson:vec-search", "items:vec-search"])
        self.assertTrue(self.st.check(self.sid)["build_complete"])
        new_events = [e["type"] for e in self.st.events(self.sid, rebased["job_id"])["events"]]
        self.assertEqual(new_events.count("unit_reused"), 2)

    def test_reused_units_are_revalidated_not_trusted(self):
        old_id = self.staged_lesson_job()
        FakeAuthor().run(self.st, self.sid, old_id)
        # Tamper with the old staged lesson: the digest no longer matches its name.
        staged = next((self.studio_root() / "staging" / old_id / "lesson--vec-sim").glob("*.json"))
        staged.write_text(staged.read_text().replace("Əl ilə nümunə", "Dəyişdirilmiş nümunə"))
        changed = roadmap()
        changed["nodes"][1]["task"] = "Sorğuya ən yaxın sənədləri tap və uyğunluq xalını izah et."
        self.ck.propose_roadmap(self.sid, changed)
        rebased = self.st.rebase(self.sid, old_id)
        self.assertNotIn("lesson:vec-sim", rebased["reused_units"])
        self.assertNotIn("items:vec-sim", rebased["reused_units"])               # its lesson was not reused either
        job = jobs.load_job(self.studio_root(), rebased["job_id"])
        self.assertIn("reuse_rejected", job["units"]["lesson:vec-sim"]["reuse_note"])

    def test_source_change_supersedes_and_nothing_is_reused(self):
        old_id = self.staged_lesson_job()
        manifest, staging = self.new_manifest_text()
        self.ck.record_sources(self.sid, manifest, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}],
                               staged_cache=staging)
        out = self.st.status(self.sid, old_id)
        self.assertEqual(out["status"], "superseded")
        self.assertEqual(out["drift"], ["manifest_version", "sources"])
        rebased = self.st.rebase(self.sid, old_id)
        self.assertEqual(rebased["reused_units"], [])
        self.assertEqual(rebased["result"], "rebased")

    def test_policy_and_validator_version_change_supersedes(self):
        old_id = self.staged_lesson_job()
        with patch.dict(jobs.VALIDATOR_VERSIONS, {"lesson": "validate-lesson-v2"}):
            out = self.st.status(self.sid, old_id)
        self.assertEqual((out["status"], out["drift"]), ("superseded", ["policy_versions"]))

    def test_capability_only_drift_and_every_binding_part_is_detected(self):
        snap = self.ck.studio_snapshot(self.sid)
        bound = jobs.make_binding(snap)
        self.assertEqual(jobs.detect_drift(bound, copy.deepcopy(bound)), [])
        mutations = {"roadmap_version": "rm-other", "generation_basis": {"x": 1}, "manifest_version": "sm-2",
                     "capabilities": {**bound["capabilities"], "vec-sim": "cap-other"},
                     "sources": {"r1": {"source_id": "s", "content_sha256": "0" * 64, "status": "validated"}},
                     "policy_versions": {**bound["policy_versions"], "policy": "studio-targets-v9"}}
        for key, value in mutations.items():
            self.assertEqual(jobs.detect_drift(bound, {**bound, key: value}), [key])

    def test_drift_before_any_work_rebinds_instead_of_superseding(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.st.plan(self.sid, job_id)
        changed = roadmap()
        changed["nodes"][0]["task"] = "İki vektorun istiqamət oxşarlığını hesabla və yoxla."
        self.ck.propose_roadmap(self.sid, changed)
        out = self.st.status(self.sid, job_id)
        self.assertEqual(out["status"], "authoring")
        self.assertEqual(out["job_id"], job_id)
        self.assertEqual(self.st.plan(self.sid, job_id)["units"]["lesson:vec-sim"]["status"], "pending")
        self.assertEqual(self.st.status(self.sid)["binding"]["roadmap_version"],
                         self.ck.state(self.sid)["roadmap"]["version"])

    def test_rebase_refuses_when_nothing_drifted(self):
        job_id = self.st.init(self.sid)["job_id"]
        with self.assertRaises(StudioError) as ctx:
            self.st.rebase(self.sid, job_id)
        self.assertEqual(ctx.exception.code, "studio_rebase_not_applicable")


class NeedsLearnerTests(StudioCase):
    propose = False

    def test_without_a_roadmap_the_job_waits_for_the_learner_with_the_real_next_step(self):
        out = self.st.init(self.sid)
        self.assertEqual(out["result"], "created")
        self.assertEqual(out["result"], "created")
        job = jobs.load_job(self.studio_root(), out["job_id"])
        self.assertEqual(job["status"], "needs_learner")
        step = job["needs_learner"]
        self.assertEqual(step["blockers"], ["roadmap_not_proposed"])
        self.assertEqual(step["phase"], "source_preparation")
        self.assertEqual(step["directive"], AGENT_DIRECTIVES["source_preparation"])
        task = self.st.next_task(self.sid, job["job_id"])
        self.assertEqual(task["state"], "needs_learner")
        self.assertEqual(task["directive"], AGENT_DIRECTIVES["source_preparation"])
        root = self.studio_root()
        self.assertEqual(list((root / "orders").iterdir()), [])
        self.assertEqual(list((root / "staging").iterdir()), [])
        self.assertEqual([r for r in self.records("evidence")], [])
        self.assertIsNone(self.ck.state(self.sid)["roadmap"]["version"])               # the studio proposed nothing
        notes = (root / "notifications.jsonl").read_text()
        self.assertIn("needs_learner", notes)

    def test_job_resumes_once_the_learner_side_is_done(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.assertEqual(self.st.plan(self.sid, job_id)["status"], "needs_learner")
        self.ck.propose_roadmap(self.sid, roadmap())
        task = self.st.next_task(self.sid, job_id)
        self.assertEqual(task["state"], "order")
        out = self.st.status(self.sid, job_id)
        self.assertEqual(out["status"], "authoring")
        self.assertIsNone(out["needs_learner"])
        path = [h["to"] for h in out["history"]]
        self.assertEqual(path, ["planning", "needs_learner", "planning", "authoring"])

    def test_intake_gate_surfaces_the_goal_first_directive(self):
        sid = self.sk.start("Intake", LearningContract(goal="Vektorlar"), defer_workspace=True)["session_id"]
        self.ck.init(sid)
        out = self.st.init(sid)
        job = jobs.load_job(self.sk.dir(sid) / "studio", out["job_id"])
        self.assertEqual(job["status"], "needs_learner")
        self.assertIn("roadmap_not_proposed", job["needs_learner"]["blockers"])
        self.assertIn("diagnostic_required", job["needs_learner"]["blockers"])
        self.assertTrue(job["needs_learner"]["directive"].startswith("Goal-first interviewer"))
        self.assertTrue(self.st.next_task(sid)["directive"].startswith("Goal-first interviewer"))

    def test_course_not_initialized_is_a_learner_step_too(self):
        sid = self.sk.start("Boş", LearningContract(goal="g"))["session_id"]
        job = jobs.load_job(self.sk.dir(sid) / "studio", self.st.init(sid)["job_id"])
        self.assertEqual(job["status"], "needs_learner")
        self.assertEqual(job["needs_learner"]["blockers"], ["course_not_initialized"])
        self.assertIn("course init", job["needs_learner"]["directive"])


class LeaseTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.job_id = self.st.init(self.sid)["job_id"]
        self.st.plan(self.sid, self.job_id)

    def test_an_open_order_is_handed_out_again_until_its_lease_expires(self):
        first = self.st.next_task(self.sid, self.job_id)
        self.assertEqual(first["state"], "order")
        self.assertEqual(first["taken_by"], "inline")
        self.assertEqual(self.st.next_task(self.sid, self.job_id)["order"]["order_id"], first["order"]["order_id"])
        self.clock.advance(899)
        self.assertEqual(self.st.next_task(self.sid, self.job_id)["order"]["order_id"], first["order"]["order_id"])
        self.clock.advance(2)                                              # past max_runner_seconds (900)
        second = self.st.next_task(self.sid, self.job_id)
        self.assertEqual(second["state"], "order")
        self.assertNotEqual(second["order"]["order_id"], first["order"]["order_id"])
        self.assertEqual(second["order"]["attempt"], 2)
        self.assertEqual(second["order"]["previous_errors"][0][:13], "lease_expired")
        job = jobs.load_job(self.studio_root(), self.job_id)
        self.assertEqual(job["orders"][first["order"]["order_id"]]["status"], "expired")
        self.assertEqual(job["units"]["lesson:vec-sim"]["attempts"], 1)
        with self.assertRaises(StudioError) as ctx:
            self.st.submit(self.sid, self.job_id, first["order"]["order_id"], FakeAuthor().payload_for(first["order"]))
        self.assertEqual(ctx.exception.code, "studio_order_expired")
        self.assertIn("lease_expired", [e["type"] for e in self.st.events(self.sid, self.job_id)["events"]])
        done = FakeAuthor().answer(self.st, self.sid, second)
        self.assertEqual(done["status"], "staged")

    def test_lease_expiry_consumes_the_attempt_budget_and_blocks(self):
        for _ in range(3):
            task = self.st.next_task(self.sid, self.job_id)
            self.assertEqual(task["state"], "order")
            self.clock.advance(901)
        blocked = self.st.next_task(self.sid, self.job_id)
        self.assertEqual((blocked["state"], blocked["reason"]), ("blocked", "blocked"))
        self.assertEqual(blocked["blocked"]["kind"], "content_defect")
        self.assertEqual(blocked["blocked"]["unit_id"], "lesson:vec-sim")

    def test_reclaim_expired_is_pure_bookkeeping(self):
        job = jobs.load_job(self.studio_root(), self.job_id)
        self.st.next_task(self.sid, self.job_id)
        job = jobs.load_job(self.studio_root(), self.job_id)
        now = self.clock()
        self.assertEqual(jobs.reclaim_expired(job, now), [])
        self.clock.advance(1000)
        self.assertEqual(len(jobs.reclaim_expired(job, self.clock())), 1)
        self.assertEqual(jobs.reclaim_expired(job, self.clock()), [])


class BudgetTests(StudioCase):
    def bad_lesson(self):
        payload = search_lesson()
        payload["node_id"] = "vec-sim"
        return payload

    def test_invalid_submissions_consume_attempts_then_block_with_a_content_defect(self):
        job_id = self.st.init(self.sid)["job_id"]
        author = FakeAuthor(defects={("lesson", "vec-sim"): [{"node_id": "vec-sim"}, {"node_id": "vec-sim", "objective": "x"},
                                                             {"node_id": "vec-sim"}]})
        results = []
        for attempt in range(1, 4):
            task = self.st.next_task(self.sid, job_id)
            self.assertEqual(task["state"], "order")
            self.assertEqual((task["order"]["attempt"], task["order"]["max_attempts"]), (attempt, 3))
            self.assertEqual(len(task["order"]["previous_errors"]), 0 if attempt == 1 else 1)
            results.append(author.answer(self.st, self.sid, task))
        self.assertEqual([r["status"] for r in results], ["rejected"] * 3)
        self.assertEqual([r["attempts"] for r in results], [1, 2, 3])
        self.assertEqual([r["job_status"] for r in results], ["remediation", "remediation", "blocked"])
        self.assertTrue(results[2]["budget_exhausted"])
        self.assertIn("objective", results[0]["errors"][0])
        out = self.st.status(self.sid, job_id)
        self.assertEqual(out["status"], "blocked")
        unit = out["units"]["lesson:vec-sim"]
        self.assertEqual((unit["status"], unit["reason"], unit["attempts"]), ("invalid", "attempt_budget_exhausted", 3))
        self.assertEqual(out["blocked"]["kind"], "content_defect")
        self.assertEqual(out["blocked"]["unit_id"], "lesson:vec-sim")
        self.assertTrue(out["blocked"]["errors"])
        task = self.st.next_task(self.sid, job_id)
        self.assertEqual((task["state"], task["reason"]), ("blocked", "blocked"))
        self.assertEqual(self.st.status(self.sid, job_id)["units"]["items:vec-sim"]["status"], "pending")     # nothing skipped ahead
        self.assertIn("blocked", (self.studio_root() / "notifications.jsonl").read_text())

    def test_a_good_retry_after_a_rejection_stages_and_returns_to_authoring(self):
        job_id = self.st.init(self.sid)["job_id"]
        author = FakeAuthor(defects={("lesson", "vec-sim"): [{"node_id": "vec-sim"}]})
        first = author.answer(self.st, self.sid, self.st.next_task(self.sid, job_id))
        self.assertEqual(first["job_status"], "remediation")
        second = author.answer(self.st, self.sid, self.st.next_task(self.sid, job_id))
        self.assertEqual((second["status"], second["job_status"]), ("staged", "authoring"))
        unit = self.st.status(self.sid, job_id)["units"]["lesson:vec-sim"]
        self.assertEqual((unit["status"], unit["attempts"], unit["last_errors"]), ("staged", 1, []))

    def test_budgets_can_be_set_at_init_within_bounds(self):
        out = self.st.init(self.sid, policy={"budgets": {"max_attempts_per_unit": 1}})
        self.assertEqual(out["policy"]["budgets"]["max_attempts_per_unit"], 1)
        task = self.st.next_task(self.sid, out["job_id"])
        result = FakeAuthor(defects={("lesson", "vec-sim"): [{"node_id": "vec-sim"}]}).answer(self.st, self.sid, task)
        self.assertEqual((result["job_status"], result["budget_exhausted"]), ("blocked", True))


class BuildDigestTests(StudioCase):
    def test_build_digest_is_a_merkle_style_root_over_units_and_bindings(self):
        job_id = self.st.init(self.sid)["job_id"]
        self.assertIsNone(jobs.compute_build_digest(jobs.load_job(self.studio_root(), job_id)))
        FakeAuthor().run(self.st, self.sid, job_id)
        job = jobs.load_job(self.studio_root(), job_id)
        digest = jobs.compute_build_digest(job)
        self.assertEqual(digest, job["build_digest"])
        self.assertEqual(digest, self.st.check(self.sid, job_id)["build_digest"])
        for mutate in (lambda j: j["units"]["lesson:vec-sim"].update(staged_digest="0" * 64),
                       lambda j: j["binding"].update(roadmap_version="rm-other"),
                       lambda j: j["binding"]["sources"]["r1"].update(content_sha256="1" * 64),
                       lambda j: j["binding"]["policy_versions"].update(policy="studio-targets-v9")):
            copy_ = copy.deepcopy(job)
            mutate(copy_)
            self.assertNotEqual(jobs.compute_build_digest(copy_), digest)
        copy_ = copy.deepcopy(job)
        copy_["unit_order"].reverse()
        self.assertEqual(jobs.compute_build_digest(copy_), digest)                   # order-independent (sorted pairs)


if __name__ == "__main__":
    unittest.main()

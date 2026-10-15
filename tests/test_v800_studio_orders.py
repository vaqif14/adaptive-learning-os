"""Stage A: work orders (need-to-know), submit intake and staging, non-servable staged content,
and the full inline loop driven by a fake well-behaved author."""
import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
import runtime.course_content as cc  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.studio_store import StudioError, digest_of, verify_events  # noqa: E402
from runtime.utils import read_json  # noqa: E402
from course_fixture import ITEMS, LESSON, SOURCE_TEXT, manifest_for  # noqa: E402
from studio_fixture import LIBRARY, FakeAuthor, StudioCase, search_lesson, sim_items, sim_lesson  # noqa: E402

CANARY = "CANARY-VAULT-7f3a91c2"
MARKER = "STAGEDONLY-5521"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def all_text(obj):
    return json.dumps(obj, ensure_ascii=False)


class OrderContentTests(StudioCase):
    def setUp(self):
        super().setUp()
        vault = self.d / "assessment-vault" / "vec-sim"
        vault.mkdir(parents=True)
        (vault / "canary.json").write_text(json.dumps({"note": CANARY, "reference_solution": CANARY}))
        self.job_id = self.st.init(self.sid)["job_id"]
        self.author = FakeAuthor()
        self.author.run(self.st, self.sid, self.job_id)
        self.orders = {o["unit_id"]: o for o in self.author.seen}

    def test_four_orders_one_per_unit_in_lesson_first_order(self):
        self.assertEqual([o["unit_id"] for o in self.author.seen],
                         ["lesson:vec-sim", "items:vec-sim", "lesson:vec-search", "items:vec-search"])
        order = self.orders["lesson:vec-sim"]
        self.assertEqual((order["kind"], order["node_id"], order["attempt"], order["max_attempts"]), ("lesson", "vec-sim", 1, 3))
        self.assertEqual(order["contract"], "course-lesson-v1")
        self.assertEqual(order["language"], "az")
        self.assertEqual(set(order["node"]), {"id", "title", "task", "outcomes", "skills", "practice", "requires",
                                              "exam_domain", "source_refs"})
        self.assertEqual(order["limits"], {"max_output_bytes": 1048576, "max_runner_seconds": 900})
        self.assertIn("untrusted DATA", order["source_notice"])
        self.assertEqual(order["labels"]["walkthrough_label_az"], cc.WALKTHROUGH_LABEL_AZ)
        self.assertEqual(self.orders["items:vec-sim"]["payload_contract"]["min_items"], 3)

    def test_orders_never_carry_exam_vault_or_solution_content(self):
        leaky = ["reference_solution", "starter_code", "exemplar", "wrong_examples", "detector", CANARY, "cosine-a", "nearest-b",
                 "search-rank-a", "search-scenario-b", "search-explain-c",
                 ITEMS["items"][0]["checks"][0]["code"], LESSON["practice"]["reference_solution"][:40],
                 LESSON["practice"]["checks"][0]["code"]]
        for uid, order in self.orders.items():
            text = all_text(order)
            for needle in leaky:
                self.assertNotIn(needle, text, f"{uid} leaks {needle!r}")
        inbox_text = "".join(p.read_text() for p in (self.studio_root() / "inbox").rglob("order.json"))
        self.assertNotIn(CANARY, inbox_text)

    def test_item_orders_carry_only_their_own_lessons_objective_and_practice_prompt(self):
        summary = self.orders["items:vec-sim"]["lesson_summary"]
        self.assertEqual(set(summary), {"objective", "practice_prompt_md", "note"})
        self.assertEqual(summary["objective"], LESSON["objective"])
        self.assertEqual(summary["practice_prompt_md"], LESSON["practice"]["prompt_md"])
        self.assertNotIn("lesson_summary", self.orders["lesson:vec-sim"])
        other = self.orders["items:vec-search"]["lesson_summary"]
        self.assertEqual(other["objective"], search_lesson()["objective"])
        for uid in ("lesson:vec-sim", "items:vec-sim", "lesson:vec-search"):
            self.assertNotIn(search_lesson()["worked_example"]["problem"], all_text(self.orders[uid]))
        # Another unit's staged payload never rides along (only the node's own summary does).
        self.assertNotIn(LESSON["worked_example"]["problem"], all_text(self.orders["items:vec-sim"]))
        self.assertNotIn(LESSON["objective"], all_text(self.orders["items:vec-search"]))

    def test_orders_are_immutable_private_files_and_inbox_has_the_expected_shape(self):
        order = self.orders["lesson:vec-sim"]
        path = self.studio_root() / "orders" / self.job_id / f"{order['order_id']}.json"
        self.assertEqual(read_json(path), order)
        self.assertEqual(mode(path), 0o600)
        inbox = self.studio_root() / "inbox" / self.job_id / order["order_id"]
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["order.json", "out", "sources"])
        self.assertEqual(mode(inbox), 0o700)
        self.assertEqual(mode(inbox / "out"), 0o700)
        self.assertEqual(read_json(inbox / "order.json"), order)
        self.assertEqual(self.st.order_show(self.sid, self.job_id, order["order_id"])["order"], order)
        with self.assertRaises(StudioError) as ctx:
            self.st.order_show(self.sid, self.job_id, "ord_0000000000000000")
        self.assertEqual(ctx.exception.code, "studio_order_not_found")

    def test_public_url_sources_are_copied_as_text(self):
        order = self.orders["lesson:vec-sim"]
        self.assertEqual([(s["key"], s["source_id"]) for s in order["sources"]], [("r1", "src-fixture-001")])
        copy_ = self.studio_root() / "inbox" / self.job_id / order["order_id"] / "sources" / "src-fixture-001.txt"
        self.assertEqual(copy_.read_text(), SOURCE_TEXT)
        self.assertEqual(order["sources"][0]["content_sha256"], hashlib.sha256(SOURCE_TEXT.encode()).hexdigest())
        self.assertEqual(order["excluded_sources"], [])


class PrivateSourceTests(StudioCase):
    def record_private(self):
        manifest = manifest_for(self.d / "sources")
        manifest["resources"][0]["expected"] = {"kind": "file", "locator": "notes/private.pdf"}
        manifest["manifest_version"] = "sm-private"
        self.ck.record_sources(self.sid, manifest, [{"key": "r1", "kind": "file", "locator": "notes/private.pdf",
                                                     "upload_authorized_by_learner": True}])
        self.assertEqual(self.ck.studio_snapshot(self.sid)["resources"][0]["kind"], "file")

    def test_private_files_are_excluded_by_default(self):
        self.record_private()
        job_id = self.st.init(self.sid)["job_id"]
        order = self.st.next_task(self.sid, job_id)["order"]
        self.assertEqual(order["sources"], [])
        self.assertEqual(order["excluded_sources"], [{"key": "r1", "reason": "private_source_not_authorized"}])
        inbox = self.studio_root() / "inbox" / job_id / order["order_id"]
        self.assertEqual(list((inbox / "sources").iterdir()), [])
        self.assertFalse(read_json(self.studio_root() / "jobs" / f"{job_id}.json")["policy"]["allow_private_sources"])

    def test_the_policy_flag_authorizes_them_and_is_recorded(self):
        self.record_private()
        out = self.st.init(self.sid, policy={"allow_private_sources": True})
        self.assertTrue(out["policy"]["allow_private_sources"])
        order = self.st.next_task(self.sid, out["job_id"])["order"]
        self.assertEqual([s["source_id"] for s in order["sources"]], ["src-fixture-001"])
        created = [e for e in self.st.events(self.sid, out["job_id"])["events"] if e["type"] == "job_created"][0]
        self.assertTrue(created["data"]["allow_private_sources"])

    def test_stale_and_unvalidated_sources_are_never_handed_out(self):
        manifest = manifest_for(self.d / "sources")
        manifest["resources"][0]["stale"] = True
        manifest["manifest_version"] = "sm-stale"
        self.ck.record_sources(self.sid, manifest, [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        order = self.st.next_task(self.sid, self.st.init(self.sid)["job_id"])["order"]
        self.assertEqual((order["sources"], order["excluded_sources"]), ([], [{"key": "r1", "reason": "stale"}]))


class SubmitTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.job_id = self.st.init(self.sid, policy={"budgets": {"max_output_bytes": 200000}})["job_id"]
        self.task = self.st.next_task(self.sid, self.job_id)
        self.order = self.task["order"]
        self.inbox = Path(self.task["inbox"])
        self.good = FakeAuthor().payload_for(self.order)

    def put(self, text, name="result.json", folder="out"):
        path = self.inbox / folder / name if folder else self.inbox / name
        path.write_text(text, encoding="utf-8")
        return path

    def submit(self, payload=None):
        return self.st.submit(self.sid, self.job_id, self.order["order_id"], payload)

    def unit(self):
        return self.st.status(self.sid, self.job_id)["units"]["lesson:vec-sim"]

    def test_valid_payload_is_staged_content_addressed_and_private(self):
        self.put(json.dumps(self.good))
        out = self.submit()
        self.assertEqual(out["status"], "staged")
        digest = digest_of(self.good)
        self.assertEqual(out["staged_digest"], digest)
        staged = self.studio_root() / "staging" / self.job_id / "lesson--vec-sim" / f"{digest}.json"
        self.assertEqual(read_json(staged), self.good)
        self.assertEqual(mode(staged), 0o600)
        self.assertEqual(mode(staged.parent), 0o700)
        unit = self.unit()
        self.assertEqual((unit["status"], unit["staged_digest"], unit["attempts"]), ("staged", digest, 0))
        self.assertEqual(out["summary"]["practice_task_id"], "dot-norm")

    def test_payload_passed_directly_equals_the_inbox_path(self):
        out = self.submit(self.good)
        self.assertEqual(out["staged_digest"], digest_of(self.good))
        self.assertEqual(self.submit(json.dumps(self.good).encode())["reason"], "stage_already_completed")

    def test_only_out_result_json_is_ever_read(self):
        self.put(json.dumps(self.good), folder=None)                  # decoy beside order.json
        self.put(json.dumps(self.good), name="other.json")           # decoy in out/
        with self.assertRaises(StudioError) as ctx:
            self.submit()
        self.assertEqual(ctx.exception.code, "studio_result_missing")
        self.assertEqual(self.unit()["attempts"], 0)                  # a premature submit costs nothing
        self.assertEqual(self.unit()["status"], "ordered")

    def test_oversized_output_is_rejected_and_counts_an_attempt(self):
        self.put(" " * 200001)
        out = self.submit()
        self.assertEqual(out["status"], "rejected")
        self.assertIn("output_too_large", out["errors"][0])
        self.assertEqual(self.unit()["attempts"], 1)
        self.assertEqual(self.unit()["staged_digest"], None)
        big = self.st.next_task(self.sid, self.job_id)
        padded = dict(self.good, padding="x" * 250000)
        again = self.st.submit(self.sid, self.job_id, big["order"]["order_id"], padded)
        self.assertIn("output_too_large", again["errors"][0])

    def test_malformed_json_variants_are_rejected_strictly(self):
        cases = {"truncated": '{"node_id": "vec-sim", ', "array": "[1, 2]", "scalar": '"text"', "nan": '{"a": NaN}',
                 "infinity": '{"a": Infinity}', "duplicate": '{"node_id": "vec-sim", "node_id": "vec-search"}',
                 "binary": None}
        for name, text in cases.items():
            task = self.st.next_task(self.sid, self.job_id) if name != "truncated" else self.task
            path = Path(task["inbox"]) / "out" / "result.json"
            if text is None:
                path.write_bytes(b"\xff\xfe{}")
            else:
                path.write_text(text)
            out = self.st.submit(self.sid, self.job_id, task["order"]["order_id"])
            self.assertEqual(out["status"], "rejected", name)
            self.assertTrue(out["errors"][0].startswith("malformed_json"), (name, out["errors"]))
            if out["job_status"] == "blocked":
                break
        self.assertEqual(self.unit()["staged_digest"], None)

    def test_symlinked_result_is_refused(self):
        target = self.ws / "secret.json"
        target.write_text(json.dumps(self.good))
        (self.inbox / "out" / "result.json").symlink_to(target)
        out = self.submit()
        self.assertEqual(out["status"], "rejected")
        self.assertTrue(out["errors"][0].startswith("result_unsafe"))

    def test_invalid_payload_is_rejected_with_validator_errors_and_never_staged(self):
        bad = copy.deepcopy(self.good)
        bad["explanation"]["refs"] = [{"source_id": "src-fixture-001", "quote": "this quote is not in the source anywhere"}]
        out = self.submit(bad)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("not grounded", out["errors"][0])
        self.assertEqual(list((self.studio_root() / "staging").iterdir()), [])
        self.assertEqual(self.unit()["last_errors"], out["errors"])
        retry = self.st.next_task(self.sid, self.job_id)
        self.assertEqual(retry["order"]["previous_errors"], out["errors"])
        self.assertNotEqual(retry["order"]["order_id"], self.order["order_id"])
        with self.assertRaises(StudioError) as ctx:
            self.submit(self.good)                                    # the first order is closed
        self.assertEqual(ctx.exception.code, "studio_order_submitted")

    def test_valid_but_target_missing_items_are_rejected_with_actionable_problems(self):
        FakeAuthor().answer(self.st, self.sid, self.task)
        task = self.st.next_task(self.sid, self.job_id)
        few = sim_items()
        few["items"] = few["items"][:2]
        out = self.st.submit(self.sid, self.job_id, task["order"]["order_id"], few)
        self.assertEqual(out["status"], "rejected")
        self.assertIn("at least 3 items", out["errors"][0])
        task = self.st.next_task(self.sid, self.job_id)
        core_only = sim_items()
        for item in core_only["items"]:
            item["scope"] = "independent_reproduction"
        out = self.st.submit(self.sid, self.job_id, task["order"]["order_id"], core_only)
        self.assertIn("near_transfer or far_transfer", out["errors"][0])

    def test_submissions_are_refused_while_an_exam_is_active(self):
        version = self.ck.state(self.sid)["roadmap"]["version"]
        self.ck.approve_roadmap(self.sid, version, channel="cli", commitments={"weekly_active_minutes": 90})
        self.ck.install_items(self.sid, "vec-sim", ITEMS)
        self.ck.install_lesson(self.sid, "vec-sim", LESSON)
        self.ck.start_mission(self.sid, "vec-sim")
        attempt = self.ck.start_exam(self.sid, "vec-sim", fast_track=True)
        with self.assertRaises(StudioError) as ctx:
            self.submit(self.good)
        self.assertEqual(ctx.exception.code, "exam_active")
        self.assertEqual(self.st.next_task(self.sid, self.job_id), {"state": "idle", "reason": "exam_active"})
        self.assertEqual(self.unit()["attempts"], 0)
        self.ck.abandon_exam(self.sid, attempt["attempt_id"])
        self.assertEqual(self.submit(self.good)["status"], "staged")


class StagedContentIsolationTests(StudioCase):
    def setUp(self):
        super().setUp()
        marked = sim_lesson()
        marked["objective"] = f"{MARKER}: İki vektorun istiqamət oxşarlığını kosinusla ölçmək."
        self.author = FakeAuthor(library={**LIBRARY, ("lesson", "vec-sim"): lambda: copy.deepcopy(marked)})
        self.job_id = self.st.init(self.sid)["job_id"]
        self.assertEqual(self.author.run(self.st, self.sid, self.job_id)["state"], "done")

    def test_staged_content_is_not_in_ledger_course_state_or_learner_views(self):
        self.assertIn(MARKER, "".join(p.read_text() for p in (self.studio_root() / "staging").rglob("*.json")))
        self.assertNotIn(MARKER, (self.d / "ledger.jsonl").read_text())
        self.assertNotIn(MARKER, (self.d / "course-state.json").read_text())
        self.assertNotIn(MARKER, all_text(self.ck.state(self.sid)))
        self.assertNotIn(MARKER, all_text(self.ck.lesson_view(self.sid, "vec-sim")) if self._approved() else "")
        self.assertNotIn(MARKER, all_text(self.ck.export(self.sid)))
        events = (self.studio_root() / "events.jsonl").read_text()
        self.assertNotIn(MARKER, events)
        self.assertNotIn("reference_solution", events)
        for path in (self.d / "workspace").rglob("*"):
            if path.is_file():
                self.assertNotIn(MARKER, path.read_text(errors="ignore"))
        self.assertFalse((self.d / "course" / "lessons").exists())               # nothing was installed
        self.assertFalse((self.d / "assessment-vault").exists())

    def _approved(self):
        version = self.ck.state(self.sid)["roadmap"]["version"]
        self.ck.approve_roadmap(self.sid, version, channel="cli", commitments={"weekly_active_minutes": 90})
        return True

    def test_the_static_server_and_course_api_never_serve_staging(self):
        ui = self.ws / "ui"
        ui.mkdir()
        (ui / "index.html").write_text("<!doctype html><title>app</title>")
        server = make_server(ServerConfig(host="127.0.0.1", port=0, allow_anon=True, workspace=self.ws, ui_dir=ui,
                                          course_session=self.sid))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        staged = next((self.studio_root() / "staging" / self.job_id / "lesson--vec-sim").glob("*.json"))
        digest = staged.stem
        paths = [f"/studio/staging/{self.job_id}/lesson--vec-sim/{digest}.json",
                 f"/../studio/staging/{self.job_id}/lesson--vec-sim/{digest}.json",
                 "/%2e%2e/studio/events.jsonl", f"/studio/jobs/{self.job_id}.json", f"/{digest}.json",
                 "/api/studio", "/api/studio/events", "/api/course/state", "/api/course/export", "/api/course/lesson?node=vec-sim"]
        for path in paths:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
                    code, body = response.status, response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read().decode("utf-8", "replace")
            self.assertNotIn(MARKER, body, path)
            self.assertNotIn(self.job_id, body, path)
            if path.startswith(("/studio", "/..", "/%2e", f"/{digest}")):
                self.assertIn(code, {401, 404}, path)


class FullInlineLoopTests(StudioCase):
    def test_fake_author_drives_a_two_node_course_to_build_complete_without_evidence(self):
        state_before = (self.d / "course-state.json").read_bytes()
        records_before = self.records()
        evidence_before = self.records("evidence") + self.records("observation")
        author = FakeAuthor()
        job_id = self.st.init(self.sid)["job_id"]
        self.assertEqual(self.st.next_task(self.sid, job_id)["state"], "order")
        last = author.run(self.st, self.sid, job_id)
        self.assertEqual(last["state"], "done")
        self.assertEqual(len(author.seen), 4)
        check = self.st.check(self.sid, job_id)
        self.assertTrue(check["build_complete"])
        self.assertEqual(check["pending"], [])
        self.assertEqual(check["drift"], [])
        self.assertTrue(all(r["complete"] and r["valid"] for r in check["units"].values()))
        self.assertEqual({k: v for k, v in check["units"]["items:vec-search"]["freshness"].items()},
                         {"search-rank-a": "fresh", "search-scenario-b": "fresh", "search-explain-c": "fresh"})
        status = self.st.status(self.sid, job_id)
        self.assertEqual((status["status"], status["progress"]), ("checking", {"units_total": 4, "units_staged": 4, "units_invalid": 0}))
        self.assertEqual(status["build_digest"], check["build_digest"])
        self.assertEqual(last["build_digest"], check["build_digest"])
        self.assertEqual([h["to"] for h in status["history"]], ["planning", "authoring", "validating", "checking"])
        # Zero new evidence; course-state untouched; only anchors (event records, source studio) were added.
        self.assertEqual(self.records("evidence") + self.records("observation"), evidence_before)
        self.assertEqual((self.d / "course-state.json").read_bytes(), state_before)
        added = self.records()[len(records_before):]
        self.assertTrue(added)
        self.assertEqual({(r["record_type"], r["provenance"]["source"], r["payload"]["event_type"]) for r in added},
                         {("event", "studio", "studio_events_anchor")})
        self.assertFalse([r for r in self.records("evidence") if r["provenance"].get("source") == "studio"])
        events = self.st.events(self.sid, job_id)
        self.assertTrue(events["verify"]["ok"])
        kinds = [e["type"] for e in events["events"]]
        self.assertEqual((kinds[0], kinds[-1], kinds.count("unit_staged"), kinds.count("order_issued")),
                         ("job_created", "build_complete", 4, 4))
        self.assertTrue(verify_events(self.d, self.sk.ledger(self.sid))["ok"])
        self.assertTrue(self.sk.ledger(self.sid).integrity()["chain_ok"])
        # Idle afterwards: nothing more to order, the build waits for the (later-stage) rules gate.
        self.assertEqual(self.st.next_task(self.sid, job_id)["state"], "done")
        self.assertEqual(len(self.st.events(self.sid, job_id)["events"]), len(kinds))

    def test_inbox_result_json_loop_gives_the_same_build(self):
        inline = StudioCaseBuild.build(self, via_inbox=False)
        # A second session driven through out/result.json reaches the identical content digests.
        self.tearDown()
        self.setUp()
        via_inbox = StudioCaseBuild.build(self, via_inbox=True)
        self.assertEqual(inline["units"].keys(), via_inbox["units"].keys())
        for uid in inline["units"]:
            self.assertEqual(inline["units"][uid]["staged_digest"], via_inbox["units"][uid]["staged_digest"])

    def test_every_studio_path_has_private_modes_after_a_build(self):
        self.st.init(self.sid)
        FakeAuthor().run(self.st, self.sid)
        root = self.studio_root()
        for path in [root, *root.rglob("*")]:
            if path.is_symlink():
                self.fail(f"unexpected symlink {path}")
            self.assertEqual(mode(path), 0o700 if path.is_dir() else 0o600, str(path))

    def test_cli_loop_end_to_end(self):
        def cli(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = alearn.main(["--workspace", str(self.ws), "studio", *argv])
                except SystemExit as e:
                    code = e.code
            self.stderr = err.getvalue()
            return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)
        code, out = cli("init", "--session", self.sid)
        self.assertEqual((code, out["result"]), (0, "created"))
        job_id = out["job_id"]
        author = FakeAuthor()
        for _ in range(8):
            code, task = cli("next-task", "--session", self.sid, "--job", job_id)
            self.assertEqual(code, 0)
            if task["state"] != "order":
                break
            path = self.ws / "payload.json"
            path.write_text(json.dumps(author.payload_for(task["order"])))
            code, shown = cli("order-show", "--session", self.sid, "--job", job_id, "--order", task["order"]["order_id"])
            self.assertEqual((code, shown["order"]["order_id"]), (0, task["order"]["order_id"]))
            code, result = cli("submit", "--session", self.sid, "--job", job_id, "--order", task["order"]["order_id"],
                               "--file", str(path), "--idempotency-key", f"cli-{task['order']['order_id']}")
            self.assertEqual((code, result["status"]), (0, "staged"))
        self.assertEqual(task["state"], "done")
        code, check = cli("check", "--session", self.sid)
        self.assertEqual((code, check["build_complete"]), (0, True))
        code, status = cli("status", "--session", self.sid)
        self.assertEqual(status["status"], "ready_for_review")      # stage C: the CLI kernel runs the rules gate at `checking`
        code, events = cli("events", "--session", self.sid)
        self.assertTrue(events["verify"]["ok"])
        # A payload inside the session store is refused by safe_input_path (agents cannot read protected files).
        code, _ = cli("submit", "--session", self.sid, "--order", "ord_0000000000000000",
                      "--file", str(self.d / "ledger.jsonl"))
        self.assertEqual(code, 1)
        code, paused = cli("pause", "--session", self.sid, "--idempotency-key", "cli-pause-0001")
        self.assertEqual((code, self.stderr), (0, ""))
        self.assertEqual(paused["status"], "paused")
        code, resumed = cli("resume", "--session", self.sid)
        self.assertEqual((code, resumed["status"]), (0, "ready_for_review"))


class StudioCaseBuild:
    @staticmethod
    def build(case, *, via_inbox):
        job_id = case.st.init(case.sid)["job_id"]
        FakeAuthor().run(case.st, case.sid, job_id, via_inbox=via_inbox)
        return case.st.status(case.sid, job_id)


if __name__ == "__main__":
    unittest.main()

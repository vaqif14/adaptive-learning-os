"""Stage E: the owner HTTP surface (alearn studio serve) and its read/control routes, on the synthetic fixture course.

The owner and the learner servers are separate processes with separate credentials. These tests start the real
handlers on loopback ephemeral ports; the rules gate never runs inside a serving thread (it refuses to), so a test
that needs the gate (a stale receipt refreshed, a promotion) stops the server first.
"""
import contextlib
import http.client
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "skills/adaptive-learn/scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import alearn  # noqa: E402
import runtime.course_content as cc  # noqa: E402
from runtime import studio_handover as handover_mod  # noqa: E402
from runtime import studio_views  # noqa: E402
from runtime import studio_worker  # noqa: E402
from runtime.server import ServerConfig, make_server  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from course_fixture import IsolatedState  # noqa: E402
from rules_fixture import RulesWorld  # noqa: E402

OWNER = "owner-token-for-the-tests-0001"
LEARNER = "learner-token-for-the-tests-01"
LOOPBACK = "127.0.0.1"
OWNER_TEXT = "Dossieri yoxladım və qəbul edirəm."
VAULT_KEYS = {"reference_solution", "answer_key", "detector", "exemplar", "rubric", "fail_feedback", "misconceptions",
              "probe", "expected_text", "answer", "answers", "tolerance"}


def quiesce(limit=5.0):
    """Wait until no serving/handler thread is left: the rules gate refuses to run next to other threads."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not [t for t in threading.enumerate() if t is not threading.current_thread() and t is not threading.main_thread()]:
            return
        time.sleep(0.02)


class Serving:
    """One real server on 127.0.0.1:<ephemeral>; context manager or explicit start/stop."""

    def __init__(self, **cfg):
        self.cfg = ServerConfig(host=LOOPBACK, port=0, **cfg)
        self.srv = self.thread = None
        self.port = None

    def start(self):
        self.srv = make_server(self.cfg)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)
        quiesce()

    __enter__ = start

    def __exit__(self, *exc):
        self.stop()

    def request(self, method, path, *, token=OWNER, headers=None, body=None, raw=None, content_type="application/json"):
        """(status, parsed JSON or None, response headers). ``body`` is a dict sent as JSON; ``raw`` bytes are sent as is."""
        conn = http.client.HTTPConnection(LOOPBACK, self.port, timeout=15)
        try:
            merged = {"Host": f"{LOOPBACK}:{self.port}", **(headers or {})}
            if token is not None:
                merged["Authorization"] = f"Bearer {token}"
            data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
            if data is not None:
                merged["Content-Type"] = content_type
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in merged.items():
                conn.putheader(key, value if isinstance(value, str) else value)
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            return resp.status, parsed, dict(resp.getheaders()), text
        finally:
            conn.close()


def owner_server(w, **extra):
    return Serving(workspace=w.ws, owner_session=w.sid, owner_token=OWNER, **extra)


def all_text(value):
    """Every string value of a parsed response, whitespace-collapsed (raw JSON text escapes newlines and would hide a leak)."""
    if isinstance(value, str):
        yield " ".join(value.split())
    elif isinstance(value, dict):
        for inner in value.values():
            yield from all_text(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_text(inner)


def walk_keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from walk_keys(inner)


class ConfigTests(unittest.TestCase):
    """ServerConfig owner mode fails closed."""

    def cfg(self, **kw):
        base = {"host": LOOPBACK, "port": 0, "workspace": ROOT, "owner_session": "ses_x", "owner_token": OWNER}
        return ServerConfig(**{**base, **kw})

    def test_a_valid_owner_config(self):
        with patch.dict(os.environ, {"ADAPTIVE_API_TOKEN": LEARNER}):
            cfg = self.cfg()
        self.assertEqual((cfg.token, cfg.allow_anon, cfg.owner_session, cfg.course_session), (OWNER, False, "ses_x", None))

    def test_the_owner_token_is_required_long_enough_and_distinct(self):
        with patch.dict(os.environ, {"ADAPTIVE_API_TOKEN": LEARNER}):
            for kw, needle in (({"owner_token": None}, "requires an owner token"), ({"owner_token": ""}, "requires an owner token"),
                               ({"owner_token": "short-token"}, "too weak"), ({"owner_token": LEARNER}, "must differ"),
                               ({"owner_token": OWNER, "token": OWNER}, "must differ")):
                with self.assertRaises(ValueError, msg=str(kw)) as ctx:
                    self.cfg(**kw)
                self.assertIn(needle, str(ctx.exception))

    def test_a_padded_owner_token_is_refused_because_it_could_never_authenticate(self):
        # _authed compares the presented Bearer value trimmed, so a secret with edge whitespace would start fine and then 401 forever.
        for token in (" " + OWNER, OWNER + "\n", " " * 30, "\t" + OWNER + " "):
            with self.assertRaises(ValueError, msg=repr(token)) as ctx:
                self.cfg(owner_token=token)
            self.assertIn("whitespace", str(ctx.exception))

    def test_anonymous_mode_a_wildcard_bind_and_mixed_modes_are_refused(self):
        for kw, needle in (({"allow_anon": True}, "never allows anonymous"), ({"host": "0.0.0.0"}, "wildcard"),
                           ({"host": "::"}, "wildcard"), ({"course_session": "ses_y"}, "separate servers")):
            with self.assertRaises(ValueError, msg=str(kw)) as ctx:
                self.cfg(**kw)
            self.assertIn(needle, str(ctx.exception))

    def test_the_owner_token_never_falls_back_to_the_learner_environment_token(self):
        with patch.dict(os.environ, {"ADAPTIVE_API_TOKEN": LEARNER}):
            with self.assertRaises(ValueError):
                self.cfg(owner_token=None)


class OwnerReadRoutesTests(unittest.TestCase):
    """One gated build shared by the read-only route tests."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld(gate=True)
        cls.job_id, last = cls.w.build()
        assert last["state"] == "done", last
        cls.job = cls.w.job(cls.job_id)
        cls.digest = cls.job["build_digest"]
        cls.owner = owner_server(cls.w).start()

    @classmethod
    def tearDownClass(cls):
        cls.owner.stop()
        cls.w.close()

    def get(self, path, **kw):
        return self.owner.request("GET", path, **kw)

    # ---------------------------------------------------------------- credentials
    def test_health_names_the_mode_and_needs_no_token(self):
        status, data, _h, _t = self.get("/health", token=None)
        self.assertEqual((status, data["ok"], data["mode"]), (200, True, "owner"))

    def test_every_studio_route_needs_the_owner_token(self):
        paths = ["/api/studio", "/api/studio/events", "/api/studio/handover", "/api/studio/node?node=vec-sim"]
        for path in paths:
            for label, token in (("none", None), ("learner token", LEARNER), ("wrong", "x" * 40), ("empty", "")):
                status, data, _h, text = self.get(path, token=token)
                self.assertEqual(status, 401, f"{path} with {label}")
                self.assertNotIn(self.digest, text)
            self.assertEqual(self.get(path)[0], 200, path)
        for action in ("start", "pause", "resume", "cancel", "accept", "reject"):
            for token in (None, LEARNER, "x" * 40):
                status, _d, _h, _t = self.owner.request("POST", f"/api/studio/{action}", token=token, body={})
                self.assertEqual(status, 401, f"{action} with {token}")

    def test_a_non_ascii_authorization_header_fails_closed(self):
        conn = http.client.HTTPConnection(LOOPBACK, self.owner.port, timeout=10)
        try:
            conn.putrequest("GET", "/api/studio", skip_host=True)
            conn.putheader("Host", f"{LOOPBACK}:{self.owner.port}")
            conn.putheader("Authorization", "Bearer " + "é" * 30)
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 401)
        finally:
            conn.close()

    def test_the_owner_server_mounts_no_learner_or_run_routes(self):
        for method, path in (("GET", "/api/course/state"), ("GET", "/api/course/lesson?node=vec-sim"), ("GET", "/api/languages"),
                             ("GET", "/api/inspect?session=x"), ("POST", "/api/course/resume"), ("POST", "/api/run"),
                             ("POST", "/api/verify-python"), ("GET", "/roadmap.html")):
            status, _d, _h, _t = self.owner.request(method, path, body={} if method == "POST" else None)
            self.assertEqual(status, 404, path)

    def test_foreign_host_and_cross_origin_are_refused_even_with_the_token(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}, {"Origin": "http://127.0.0.1:1"},
                        {"Origin": f"http://localhost:{self.owner.port + 1}"}):
            for method, path in (("GET", "/api/studio"), ("GET", "/api/studio/handover")):
                self.assertEqual(self.owner.request(method, path, headers=headers)[0], 403, (headers, path))
            self.assertEqual(self.owner.request("POST", "/api/studio/pause", headers=headers, body={})[0], 403, headers)
        same = {"Origin": f"http://{LOOPBACK}:{self.owner.port}"}
        self.assertEqual(self.get("/api/studio", headers=same)[0], 200)
        self.assertEqual(self.w.job(self.job_id)["status"], "ready_for_review")          # the refused POST changed nothing

    def test_security_headers_and_no_store_on_api_and_static(self):
        _s, _d, headers, _t = self.get("/api/studio")
        for name, value in (("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                            ("Cache-Control", "no-store")):
            self.assertEqual(headers.get(name), value, name)

    # --------------------------------------------------------------------- shapes
    def test_status_shape_progress_nodes_worker_runners_notifications(self):
        status, data, _h, text = self.get("/api/studio")
        self.assertEqual(status, 200)
        self.assertEqual((data["job_id"], data["status"], data["build_digest"]), (self.job_id, "ready_for_review", self.digest))
        self.assertEqual((data["progress"]["units_total"], data["progress"]["units_staged"]), (4, 4))
        self.assertEqual([(n["node"], n["lesson"]["status"], n["items"]["status"]) for n in data["nodes"]],
                         [("vec-sim", "staged", "staged"), ("vec-search", "staged", "staged")])
        self.assertTrue(all(n["title"] for n in data["nodes"]))
        self.assertTrue(set(data["worker"]) >= {"alive", "pid", "beat_at", "state"})
        self.assertFalse(data["worker"]["alive"])
        self.assertEqual({k: v["configured"] for k, v in data["runners"].items()}, {"claude": False, "codex": False})
        self.assertIn("handover_ready", [n["kind"] for n in data["notifications"]["notifications"]])
        for secret in (OWNER, LEARNER, os.environ.get("ALEARN_LEDGER_KEY") or OWNER, "Bearer"):
            self.assertNotIn(secret, text)

    def test_events_shape_limit_and_chain_verification(self):
        status, data, _h, _t = self.get("/api/studio/events")
        self.assertEqual(status, 200)
        self.assertEqual(data["job_id"], self.job_id)
        self.assertTrue(data["verify"]["ok"])
        self.assertEqual(data["total"], len(data["events"]))
        self.assertTrue({"seq", "at", "type", "data", "hash"} <= set(data["events"][0]))
        self.assertIn("rules_checked", [e["type"] for e in data["events"]])
        status, data2, _h, _t = self.get("/api/studio/events?limit=3")
        self.assertEqual((status, len(data2["events"]), data2["total"]), (200, 3, data["total"]))
        self.assertEqual(data2["events"], data["events"][-3:])
        self.assertEqual(self.get("/api/studio/events?limit=abc")[0], 400)
        self.assertLessEqual(len(self.get("/api/studio/events?limit=999999")[1]["events"]), studio_views.MAX_EVENT_LIMIT)

    def test_handover_shape_digest_rules_and_wording(self):
        status, data, _h, _t = self.get("/api/studio/handover")
        self.assertEqual(status, 200)
        doc = data["handover"]
        self.assertEqual((data["build_digest"], doc["build_digest"], data["receipt_fresh"], data["blocked"]),
                         (self.digest, self.digest, True, False))
        self.assertEqual(data["status"], "ready_for_review")
        self.assertEqual([r["rule"] for r in doc["rules"]], [f"R{i}" for i in range(1, 11)])
        for row in doc["rules"]:
            self.assertTrue(row["label_az"] and row["status_az"])
            self.assertNotRegex(row["probe_summary_az"], r"\d+/\d+")
            self.assertRegex(row["probe_summary_az"], r"^\d+ (keçdi|KEÇMƏDİ|yoxlama xətası|tətbiq olunmur|yoxlanılmadı)")
        self.assertIn("R1-R10 qəbz cədvəli", data["markdown"])
        self.assertNotRegex(data["markdown"], r"\|\s*\d+/\d+\s*\|")
        self.assertTrue(doc["not_verified"] and doc["absent"])
        self.assertEqual(set(doc["nodes"]), {"vec-sim", "vec-search"})
        before = len(list((self.w.root / "receipts" / self.job_id).glob("*.json")))
        self.get("/api/studio/handover")
        self.assertEqual(len(list((self.w.root / "receipts" / self.job_id).glob("*.json"))), before)      # read only

    def test_a_dossier_written_before_probe_summaries_still_names_not_applicable_probes(self):
        path = handover_mod.handover_dir(self.w.root, self.job_id) / "HANDOVER.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc["rules"]:
            row.pop("probe_summary_az", None)
        path.write_text(json.dumps(doc), encoding="utf-8")
        status, data, _h, _t = self.get("/api/studio/handover")
        self.assertEqual(status, 200)
        for row in data["handover"]["rules"]:
            self.assertTrue(row["probe_summary_az"], row)
            self.assertRegex(row["probe_summary_az"], r"^\d+ (keçdi|KEÇMƏDİ|tətbiq olunmur|yoxlanılmadı)")
        na = [r for r in data["handover"]["rules"] if r["probes_not_applicable"]]
        for row in na:
            self.assertIn("tətbiq olunmur (", row["probe_summary_az"])

    def test_node_preview_is_the_public_learner_view_plus_counts_only(self):
        for node in ("vec-sim", "vec-search"):
            status, data, _h, text = self.get(f"/api/studio/node?node={node}")
            self.assertEqual(status, 200, node)
            lesson_payload = self.w.staged(self.job_id, f"lesson:{node}")
            items_payload = self.w.staged(self.job_id, f"items:{node}")
            vault = []
            pr = lesson_payload["practice"]
            vault += [pr.get("reference_solution")] + [c["code"] for c in pr.get("checks", [])]
            vault += [m.get("detector") for m in pr.get("misconceptions", [])]
            for item in items_payload["items"]:
                vault += [item.get("reference_solution"), item.get("exemplar")] + [c["code"] for c in item.get("checks", [])]
                vault += [m.get("detector") for m in item.get("misconceptions", [])]
                vault += [r.get("criterion") for r in item.get("rubric") or []]
            vault = [v for v in vault if isinstance(v, str) and len(v) > 8]
            self.assertTrue(vault, node)
            shown = list(all_text(data))
            for secret in vault:
                needle = " ".join(secret.split())
                self.assertFalse(any(needle in s for s in shown), f"{node}: vault text reached the preview: {needle[:40]}")
                self.assertNotIn(json.dumps(secret, ensure_ascii=False)[1:-1], text)
            self.assertFalse(set(walk_keys(data)) & VAULT_KEYS, set(walk_keys(data)) & VAULT_KEYS)
            self.assertEqual(data["items"], [cc.public_item(i) for i in items_payload["items"]])
            self.assertEqual(data["lesson"]["walkthrough"], cc.public_walkthrough(lesson_payload["walkthrough"]))
            self.assertEqual(data["lesson"]["ladder"]["worked"], cc.public_ladder_rung(lesson_payload["ladder"], "worked"))
            blanks = lesson_payload["ladder"]["completion"]["blanks"]
            self.assertEqual(data["lesson"]["ladder"]["completion"]["blank_count"], len(blanks))
            for blank in blanks:
                self.assertNotIn(f'"{blank}"', text)                 # the blank answers are not shown
            hidden = data["hidden"]
            self.assertEqual(hidden["practice_checks"], len(pr.get("checks") or pr.get("questions") or []))
            self.assertEqual(hidden["total"], hidden["practice_checks"] + sum(hidden["item_checks"].values()))
        sim = self.get("/api/studio/node?node=vec-sim")[1]
        self.assertEqual(sim["lesson"]["practice"]["starter_code"], self.w.staged(self.job_id, "lesson:vec-sim")["practice"]["starter_code"])
        self.assertEqual((sim["lesson"]["practice"]["kind"], sim["hidden"]["practice_checks"]), ("python", 2))

    def test_node_preview_unknown_or_missing_node_is_404(self):
        for path in ("/api/studio/node?node=nope", "/api/studio/node", "/api/studio/node?node=..%2F..%2Fetc"):
            status, data, _h, _t = self.get(path)
            self.assertEqual((status, data["error"]), (404, "studio_unknown_node"), path)

    def test_a_preview_that_would_leak_the_vault_is_refused(self):
        real = cc.public_walkthrough
        secret = self.w.staged(self.job_id, "lesson:vec-sim")["practice"]["reference_solution"]
        with patch.object(cc, "public_walkthrough", lambda wt: {**real(wt), "title": secret}):
            status, data, _h, text = self.get("/api/studio/node?node=vec-sim")
        self.assertEqual((status, data["error"]), (500, "studio_preview_leak"))
        self.assertNotIn(json.dumps(secret, ensure_ascii=False)[1:-1], text)

    def test_unknown_studio_paths_are_404_after_authentication(self):
        for path in ("/api/studio/staging", "/api/studio/submit", "/api/studio/promote", "/api/studio/x"):
            self.assertEqual(self.get(path)[0], 404, path)
        for action in ("submit", "promote", "install-lesson", "order"):
            self.assertEqual(self.owner.request("POST", f"/api/studio/{action}", body={})[0], 404, action)

    def test_the_static_ui_is_served_with_the_course_csp(self):
        # the owner server serves the committed build; an ui_dir-less server has no static files
        with tempfile_dir() as ui:
            (ui / "index.html").write_text("<html>owner</html>", encoding="utf-8")
            with Serving(workspace=self.w.ws, owner_session=self.w.sid, owner_token=OWNER, ui_dir=ui) as srv:
                status, _d, headers, text = srv.request("GET", "/", token=None)
                self.assertEqual((status, text), (200, "<html>owner</html>"))
                self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(srv.request("GET", "/../../etc/passwd", token=None)[0], 404)


@contextlib.contextmanager
def tempfile_dir():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class LearnerServerHasNoStudioTests(unittest.TestCase):
    """The learner course server (anonymous loopback and token modes) never mounts /api/studio*."""

    @classmethod
    def setUpClass(cls):
        cls.w = RulesWorld(gate=False)
        cls.w.st.init(cls.w.sid)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_404_on_every_studio_route_before_any_credential_check(self):
        servers = (Serving(workspace=self.w.ws, allow_anon=True, course_session=self.w.sid),
                   Serving(workspace=self.w.ws, token=LEARNER, course_session=self.w.sid),
                   Serving(workspace=self.w.ws, token=LEARNER))
        for srv in servers:
            with srv:
                for token in (None, LEARNER, OWNER):
                    for path in ("/api/studio", "/api/studio/events", "/api/studio/handover", "/api/studio/node?node=vec-sim",
                                 "/api/studio/staging"):
                        status, data, _h, _t = srv.request("GET", path, token=token)
                        self.assertEqual((status, data), (404, {"error": "not_found"}), (path, token))
                    for action in ("start", "pause", "resume", "cancel", "accept", "reject"):
                        status, data, _h, _t = srv.request("POST", f"/api/studio/{action}", token=token, body={})
                        self.assertEqual((status, data), (404, {"error": "not_found"}), (action, token))
        self.assertNotIn(self.w.job(None)["status"], {"paused", "cancelled"})           # no studio control reached the kernel

    def test_learner_health_names_its_mode_and_staging_is_not_reachable(self):
        with Serving(workspace=self.w.ws, allow_anon=True, course_session=self.w.sid) as srv:
            self.assertEqual(srv.request("GET", "/health", token=None)[1]["mode"], "learner")
            for path in ("/studio/jobs", "/studio/staging", "/studio/events.jsonl"):
                self.assertNotEqual(srv.request("GET", path, token=None)[0], 200, path)


class ControlRoutesTests(unittest.TestCase):
    """start/pause/resume/cancel over HTTP on a cheap (ungated) job."""

    def setUp(self):
        self.w = RulesWorld(gate=False)
        self.job_id = self.w.st.init(self.w.sid)["job_id"]

    def tearDown(self):
        self.w.close()

    def post(self, srv, action, body=None, **kw):
        return srv.request("POST", f"/api/studio/{action}", body={} if body is None else body, **kw)

    def test_pause_resume_cancel_move_the_job_and_record_owner_events(self):
        with owner_server(self.w) as srv:
            status, data, _h, _t = self.post(srv, "pause")
            self.assertEqual((status, data["status"]), (200, "paused"))
            self.assertEqual(self.post(srv, "pause")[0], 409)                      # the status machine refuses a second pause
            status, data, _h, _t = self.post(srv, "resume")
            self.assertEqual((status, data["status"]), (200, self.w.job(self.job_id)["status"]))
            self.assertNotEqual(data["status"], "paused")
            self.assertEqual(self.post(srv, "resume")[0], 409)
            status, data, _h, _t = self.post(srv, "cancel", {"idempotency_key": "cancel-once-0001"})
            self.assertEqual((status, data["status"]), (200, "cancelled"))
            self.assertEqual(self.post(srv, "cancel", {"idempotency_key": "cancel-once-0001"})[1]["status"], "cancelled")   # replay
            types = [e["type"] for e in srv.request("GET", "/api/studio/events?limit=50")[1]["events"]]
            for expected in ("job_paused", "job_resumed", "job_cancelled"):
                self.assertIn(expected, types)
        self.assertEqual(self.w.job(self.job_id)["status"], "cancelled")
        self.assertTrue((self.w.root / "jobs" / f"{self.job_id}.json").is_file())            # cancelled, never deleted

    def test_no_route_accepts_authored_content_or_a_wrong_shape(self):
        with owner_server(self.w) as srv:
            payload = {"payload": {"objective": "x"}}
            for action in ("start", "pause", "resume", "cancel", "accept", "reject"):
                status, data, _h, _t = self.post(srv, action, payload)
                self.assertEqual((status, data["error"]), (400, "invalid_request"), action)
            for body in ({"statement": ["x"], "digest": "d"}, {"statement": {"a": 1}}, {"digest": 5}):
                self.assertEqual(self.post(srv, "accept", body)[0], 400, body)
            self.assertEqual(self.post(srv, "reject", {"node": "vec-sim", "note": 5})[0], 400)
            status, data, _h, _t = srv.request("POST", "/api/studio/pause", raw=b"{}", content_type="text/plain")
            self.assertEqual((status, data["error"]), (415, "json_content_type_required"))
            status, _d, _h, _t = srv.request("POST", "/api/studio/pause", raw=b"[1]")
            self.assertEqual(status, 400)
            status, _d, _h, _t = srv.request("POST", "/api/studio/pause", raw=b"{not json")
            self.assertEqual(status, 400)
        self.assertNotIn(self.w.job(self.job_id)["status"], {"paused", "cancelled"})            # nothing above reached the kernel

    def test_start_launches_the_detached_worker_with_a_scrubbed_environment(self):
        seen = {}

        def fake(workspace, sid, script, **kw):
            seen.update(sid=sid, script=Path(script).name, **kw)
            return {"started": True, "pid": 4242, "log": "/secret/path/worker.log", "result": None}
        with patch.dict(os.environ, {"ALEARN_OWNER_TOKEN": OWNER, "ADAPTIVE_API_TOKEN": LEARNER, "KEEP_ME": "1"}), \
                patch.object(studio_worker, "start_detached", fake), owner_server(self.w) as srv:
            status, data, _h, text = self.post(srv, "start", {"runner": "claude"})
        self.assertEqual((status, data["started"], data["pid"]), (200, True, 4242))
        self.assertNotIn("/secret/path", text)                                                  # the log path is not echoed
        self.assertEqual((seen["sid"], seen["script"], seen["runner"]), (self.w.sid, "alearn.py", "claude"))
        self.assertNotIn("ALEARN_OWNER_TOKEN", seen["env"])
        self.assertNotIn("ADAPTIVE_API_TOKEN", seen["env"])
        self.assertEqual(seen["env"].get("KEEP_ME"), "1")

    def test_start_validates_the_runner_and_needs_a_job(self):
        with owner_server(self.w) as srv:
            self.assertEqual(self.post(srv, "start", {"runner": "rm -rf /"})[0], 400)
            self.assertEqual(self.post(srv, "start", {"runner": "bash"})[0], 400)
        empty = RulesWorld(gate=False)
        try:
            with owner_server(empty) as srv:
                status, data, _h, _t = self.post(srv, "start")
                self.assertEqual((status, data["error"]), (404, "studio_no_job"))
                status, data, _h, _t = srv.request("GET", "/api/studio")
                self.assertEqual((status, data["status"]), (200, "no_job"))
                self.assertIn("alearn studio init", data["next_step"])
        finally:
            empty.close()


class AcceptOverHttpTests(unittest.TestCase):
    """Acceptance over HTTP is bound to the build digest and refused when the receipt is stale; promotion stays out of process."""

    def test_accept_scenario(self):
        w = RulesWorld(gate=True)
        try:
            job_id, last = w.build()
            self.assertEqual(last["state"], "done")
            digest = w.job(job_id)["build_digest"]
            receipts_dir = w.root / "receipts" / job_id
            with owner_server(w) as srv:
                post = lambda body, **kw: srv.request("POST", "/api/studio/accept", body=body, **kw)   # noqa: E731
                self.assertEqual(post({"digest": digest, "statement": ""})[1]["error"], "studio_statement_required")
                self.assertEqual(post({"digest": digest, "statement": "   "})[0], 400)
                status, data, _h, _t = post({"digest": "0" * 64, "statement": OWNER_TEXT})
                self.assertEqual((status, data["error"]), (409, "studio_digest_mismatch"))
                self.assertEqual(post({"digest": digest, "statement": "x" * 501})[0], 400)
                self.assertEqual(w.job(job_id)["status"], "ready_for_review")
                # learner activity after the receipt makes it stale: refused, and reading the handover does not recompute it
                w.sk.ledger(w.sid).append("observation", {"capability_id": "vec-sim", "note": "learner activity after the receipt"},
                                          {"source": "course_practice"})
                receipts_before = sorted(p.name for p in receipts_dir.glob("*.json"))
                status, data, _h, _t = post({"digest": digest, "statement": OWNER_TEXT})
                self.assertEqual((status, data["error"]), (409, "studio_receipt_stale"))
                status, data, _h, _t = srv.request("GET", "/api/studio/handover")
                self.assertEqual((status, data["receipt_fresh"]), (200, False))
                self.assertTrue(data["stale_reason"])
                self.assertEqual(sorted(p.name for p in receipts_dir.glob("*.json")), receipts_before)
                self.assertEqual(w.job(job_id)["status"], "ready_for_review")
            # the owner refreshes the receipt in its own process (the gate refuses to run in a serving thread)
            refreshed = w.st.handover(w.sid, job_id)
            self.assertTrue(refreshed["receipt_fresh"])
            with owner_server(w) as srv:
                post = lambda body, **kw: srv.request("POST", "/api/studio/accept", body=body, **kw)   # noqa: E731
                status, data, _h, _t = post({"digest": digest, "statement": OWNER_TEXT, "idempotency_key": "accept-0001-key"})
                self.assertEqual((status, data["accepted"]), (200, True))
                self.assertEqual(data["status"], "accepted")
                self.assertEqual((data["promotion"]["promoted"], data["promotion"]["state"]), (False, "awaiting_learner_approval"))
                self.assertIn("alearn studio promote", data["promotion"]["next_step"])
                replay = post({"digest": digest, "statement": OWNER_TEXT, "idempotency_key": "accept-0001-key"})
                self.assertEqual((replay[0], replay[1]["accepted"]), (200, True))
                self.assertEqual(post({"digest": digest, "statement": OWNER_TEXT})[1]["error"], "studio_not_reviewable")
            job = w.job(job_id)
            self.assertEqual((job["status"], job["acceptance"]["channel"], job["acceptance"]["statement"]),
                             ("accepted", "owner_ui", OWNER_TEXT))
            events = [r for r in w.records("event") if r["payload"].get("event_type") == "studio_owner_acceptance"]
            self.assertEqual([(e["payload"]["channel"], e["payload"]["build_digest"]) for e in events], [("owner_ui", digest)])
            self.assertEqual(json.loads((w.d / "course-state.json").read_text(encoding="utf-8")).get("studio", {}).get("promoted_builds", []), [])   # no promotion inside the server
            # promotion is the owner's separate step: roadmap approval, then `studio promote` in its own process
            w.approve()
            promoted = w.st.promote(w.sid, job_id)
            self.assertEqual((promoted["status"], promoted["promotion"]["promoted"]), ("promoted", True))
        finally:
            w.close()


class RejectOverHttpTests(unittest.TestCase):
    def test_reject_reopens_the_node_with_the_owner_note_and_the_ui_channel(self):
        w = RulesWorld(gate=True)
        try:
            job_id, _last = w.build()
            with owner_server(w) as srv:
                post = lambda body: srv.request("POST", "/api/studio/reject", body=body)   # noqa: E731
                self.assertEqual(post({"node": "vec-sim", "note": ""})[1]["error"], "studio_note_required")
                self.assertEqual(post({"node": "ghost", "note": "x"})[0], 404)
                self.assertEqual(w.job(job_id)["status"], "ready_for_review")
                status, data, _h, _t = post({"node": "vec-sim", "note": "İzahı sadələşdir."})
                self.assertEqual((status, data["rejected_node"], sorted(data["reopened"])),
                                 (200, "vec-sim", ["items:vec-sim", "lesson:vec-sim"]))
                self.assertEqual(data["status"], "remediation")
                status, data, _h, _t = srv.request("GET", "/api/studio")
                self.assertEqual([n["lesson"]["status"] for n in data["nodes"]], ["invalid", "staged"])
                status, data, _h, _t = srv.request("GET", "/api/studio/handover")                       # no build until it is re-authored
                self.assertEqual((status, data["error"]), (409, "studio_no_build"))
            events = [r for r in w.records("event") if r["payload"].get("event_type") == "studio_owner_rejection"]
            self.assertEqual([(e["payload"]["channel"], e["payload"]["node"]) for e in events], [("owner_ui", "vec-sim")])
        finally:
            w.close()


class HandoverWordingTests(unittest.TestCase):
    def test_probe_counts_are_words_with_reasons_not_a_fraction(self):
        probes = [{"status": "pass"}, {"status": "pass"}, {"status": "not_applicable", "detail": "the build has no   code\nto run"}]
        self.assertEqual(handover_mod.probe_summary_az(probes), "2 keçdi · 1 tətbiq olunmur (the build has no code to run)")
        self.assertEqual(handover_mod.probe_summary_az([{"status": "pass"}] * 3), "3 keçdi")
        self.assertEqual(handover_mod.probe_summary_az([{"status": "pass"}, {"status": "fail", "detail": "x"},
                                                        {"status": "error", "detail": "y"}, {"status": "skipped"}]),
                         "1 keçdi · 1 KEÇMƏDİ · 1 yoxlama xətası · 1 yoxlanılmadı (tez rejim)")
        self.assertEqual(handover_mod.probe_summary_az([]), "yoxlama yoxdur")
        two_reasons = handover_mod.probe_summary_az([{"status": "not_applicable", "detail": "a"}, {"status": "not_applicable", "detail": "b"}])
        self.assertEqual(two_reasons, "2 tətbiq olunmur (a; b)")
        self.assertNotRegex(handover_mod.probe_summary_az(probes), r"\d/\d")

    def test_a_cut_reason_ends_with_an_ellipsis_and_stays_within_the_limit(self):
        out = handover_mod.probe_summary_az([{"status": "not_applicable", "detail": "word " * 40}])
        reason = out[out.index("(") + 1:-1]
        self.assertTrue(reason.endswith("…") and len(reason) == 90, (len(reason), reason))
        exact = "x" * 90
        self.assertEqual(handover_mod.probe_summary_az([{"status": "not_applicable", "detail": exact}]), f"1 tətbiq olunmur ({exact})")

    def test_an_old_dossier_row_gets_a_summary_that_names_what_did_not_apply(self):
        row = {"rule": "R1", "probes": 4, "probes_passed": 2, "failing": [],
               "probes_not_applicable": [{"id": "a", "reason": "no code to run"}, {"id": "b", "reason": "no code to run"}]}
        self.assertEqual(handover_mod.legacy_probe_summary_az(row), "2 keçdi · 2 tətbiq olunmur (no code to run)")
        self.assertEqual(handover_mod.legacy_probe_summary_az({"probes": 3, "probes_passed": 1, "failing": [{"id": "x", "detail": "d"}],
                                                               "probes_not_applicable": []}), "1 keçdi · 1 KEÇMƏDİ · 1 yoxlanılmadı")
        new = {"rules": [{"rule": "R1", "probe_summary_az": "keep me"}, row]}
        filled = handover_mod.with_probe_summaries(new)
        self.assertEqual([r["probe_summary_az"] for r in filled["rules"]], ["keep me", "2 keçdi · 2 tətbiq olunmur (no code to run)"])
        self.assertNotIn("probe_summary_az", row)                                        # a copy: the input is never mutated
        self.assertIsNone(handover_mod.with_probe_summaries(None))

    def test_the_markdown_table_uses_the_summary_and_never_a_fraction(self):
        receipt = {"status": "pass", "scope": "full", "results": [
            {"rule": f"R{i}", "status": "pass", "probes": [{"id": f"r{i}.a", "status": "pass", "detail": ""},
                                                          {"id": f"r{i}.b", "status": "not_applicable", "detail": "pipe | in reason"}]}
            for i in range(1, 11)]}
        rows = handover_mod._rule_rows(receipt)
        self.assertEqual(rows[0]["probe_summary_az"], "1 keçdi · 1 tətbiq olunmur (pipe | in reason)")
        doc = {"blocked": False, "job_id": "j", "build_digest": "d" * 64, "roadmap_version": "rm", "created_at": "t",
               "receipt": {"receipt_id": "r", "scope": "full", "status": "pass"}, "receipt_statement_az": "s", "nodes": {},
               "titles": {}, "totals": {"nodes": 0, "lessons": 0, "walkthrough_steps": 0, "ladders": 0, "items": 0},
               "rules": rows, "blocking_findings": [], "absent": [], "not_verified": [],
               "owner_actions": {"accept": "a", "reject": "r", "refresh": "f"}}
        md = handover_mod.render_md(doc)
        self.assertIn("| R1 (güzəştsiz) | keçdi | 1 keçdi · 1 tətbiq olunmur (pipe / in reason) |", md)
        self.assertNotRegex(md, r"\|\s*\d+/\d+\s*\|")


class OwnerServeCommandTests(unittest.TestCase):
    """`alearn studio serve` reads the token from the environment only and refuses weak or shared tokens."""

    def setUp(self):
        self.iso = IsolatedState().__enter__()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.sid = SessionKernel(self.ws).start("T", LearningContract(goal="g"))["session_id"]

    def tearDown(self):
        self.tmp.cleanup()
        self.iso.__exit__()

    def run_cmd(self, env, *extra):
        captured = {}
        with patch.dict(os.environ, env, clear=False), patch.object(alearn, "run_server", lambda cfg: captured.update(cfg=cfg)):
            for name in ("ALEARN_OWNER_TOKEN", "ADAPTIVE_API_TOKEN"):
                if name not in env:
                    os.environ.pop(name, None)
            code = alearn.main(["--workspace", str(self.ws), "studio", "serve", "--session", self.sid, *extra])
        return code, captured

    def test_it_refuses_to_start_without_a_usable_owner_token(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cmd({})
        self.assertIn("ALEARN_OWNER_TOKEN", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            self.run_cmd({"ALEARN_OWNER_TOKEN": "short"})
        self.assertIn("too weak", str(ctx.exception))
        with self.assertRaises(SystemExit) as ctx:
            self.run_cmd({"ALEARN_OWNER_TOKEN": OWNER, "ADAPTIVE_API_TOKEN": OWNER})
        self.assertIn("must differ", str(ctx.exception))
        with self.assertRaises(SystemExit):
            self.run_cmd({"ALEARN_OWNER_TOKEN": OWNER}, "--host", "0.0.0.0")

    def test_there_is_no_token_argument_and_the_default_is_loopback(self):
        with self.assertRaises(SystemExit):
            alearn.main(["--workspace", str(self.ws), "studio", "serve", "--session", self.sid, "--token", OWNER])
        code, captured = self.run_cmd({"ALEARN_OWNER_TOKEN": OWNER, "ADAPTIVE_API_TOKEN": LEARNER})
        cfg = captured["cfg"]
        self.assertEqual((cfg.host, cfg.owner_session, cfg.token, cfg.allow_anon, cfg.course_session),
                         ("127.0.0.1", self.sid, OWNER, False, None))
        self.assertTrue((cfg.ui_dir / "index.html").is_file())

    def test_an_unknown_session_is_refused_before_serving(self):
        started = []
        with patch.dict(os.environ, {"ALEARN_OWNER_TOKEN": OWNER}), patch.object(alearn, "run_server", started.append):
            code = alearn.main(["--workspace", str(self.ws), "studio", "serve", "--session", "ses_nope"])
        self.assertNotEqual(code, 0)
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()

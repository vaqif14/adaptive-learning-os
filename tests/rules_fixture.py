"""TEST FIXTURE - shared helpers for the stage C tests (rules gate, handover, promotion).

Class-level builds keep the slow part (an authoring loop with sandbox validation) to one per test class; per-test
faults are applied to the files and always restored. Content is the synthetic fixture course of studio_fixture.py.
"""
import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from course_fixture import IsolatedState, manifest_for, roadmap  # noqa: E402
from runtime import studio_jobs as jobs  # noqa: E402
from runtime import studio_staging as staging  # noqa: E402
from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.studio import StudioKernel  # noqa: E402
from runtime.studio_store import write_atomic_json  # noqa: E402
from studio_fixture import FakeAuthor, FakeClock  # noqa: E402
import runtime.course_content as cc  # noqa: E402


def tree_hash(root, skip=("studio/scratch",)):
    """{relative path: sha256} of every file under ``root`` except the sanctioned scratch area."""
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if not any(rel == s or rel.startswith(s + "/") for s in skip):
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class RulesWorld:
    """One isolated course session (roadmap PROPOSED, sources validated) plus the kernels to drive it."""

    def __init__(self, *, gate=False, propose=True):
        self._verifier_timeout = cc._VERIFIER.timeout_sec
        cc._VERIFIER.timeout_sec = 30.0          # sandbox self-tests: do not flake on a loaded machine
        self.iso = IsolatedState().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.sk = SessionKernel(self.ws)
        self.sid = self.sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığını hesablamaq",
                                                               stated_background="Python əsasları"))["session_id"]
        self.d = self.sk.dir(self.sid).resolve()
        self.clock = FakeClock()
        self.ck = CourseKernel(self.ws, clock=self.clock)
        self.st = StudioKernel(self.ws, clock=self.clock, gate=gate)
        self.ck.init(self.sid)
        self.ck.record_sources(self.sid, manifest_for(self.d / "sources"),
                               [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        if propose:
            self.ck.propose_roadmap(self.sid, roadmap())

    def close(self):
        cc._VERIFIER.timeout_sec = self._verifier_timeout
        self.tmp.cleanup()
        self.iso.__exit__()

    @property
    def root(self):
        return self.d / "studio"

    def state_dir_hash(self):
        return tree_hash(Path(os.environ["ALEARN_STATE_DIR"]), skip=())

    def build(self, policy=None):
        job_id = self.st.init(self.sid, policy=policy)["job_id"]
        last = FakeAuthor().run(self.st, self.sid, job_id)
        return job_id, last

    def job(self, job_id=None):
        return jobs.load_job(self.root, job_id) if job_id else jobs.active_job(self.root)

    def records(self, record_type=None):
        recs = self.sk.ledger(self.sid).verified_records()
        return [r for r in recs if record_type is None or r["record_type"] == record_type]

    def approve(self):
        version = self.ck.state(self.sid)["roadmap"]["version"]
        return self.ck.approve_roadmap(self.sid, version, channel="cli", statement="Yol xəritəsini təsdiq edirəm.")

    def inject_staged(self, job_id, unit_id, payload):
        """Replace a staged payload consistently (file, unit digest, job digest): a build whose bytes are coherent but
        whose CONTENT is faulty, so only the matching rule can fail."""
        job = self.job(job_id)
        digest = staging.stage_payload(self.root, job_id, unit_id, payload)
        job["units"][unit_id]["staged_digest"] = digest
        job["build_digest"] = jobs.compute_build_digest(job)
        write_atomic_json(jobs.job_path(self.root, job_id), job, root=self.root)
        return digest

    def staged(self, job_id, unit_id):
        job = self.job(job_id)
        return staging.read_staged(self.root, job_id, unit_id, job["units"][unit_id]["staged_digest"])


@contextlib.contextmanager
def restored(path):
    """Edit a file inside the block; its original bytes and mode are put back afterwards."""
    path = Path(path)
    data, mode = path.read_bytes(), path.stat().st_mode & 0o777
    try:
        yield path
    finally:
        path.write_bytes(data)
        os.chmod(path, mode)


@contextlib.contextmanager
def restored_mode(path):
    path = Path(path)
    mode = path.stat().st_mode & 0o777
    try:
        yield path
    finally:
        os.chmod(path, mode)

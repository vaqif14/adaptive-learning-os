"""Strong evidence must come from a runtime check, never from a bare declaration.

`alearn evidence` lets the host agent assert any outcome/independence/strength.
Before this fix that assertion alone could produce "strong unassisted success"
and, with transfer scope, mastery. These tests pin the rule: self-reported
evidence is capped at medium (new records) and never counted as strong (legacy
records already in a ledger), while runtime-checked paths stay strong.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "scripts"))

import alearn
from runtime.contracts import LearningContract
from runtime.learner_model import aggregate_learner_state
from runtime.mastery import observations_from_projection_caps
from runtime.projection import counts_as_strong
from runtime.session import SessionKernel


def ev(**kw):
    base = {"evidence_id": "e", "capability_id": "cap", "outcome": "correct", "independence": "unassisted",
            "strength": "strong", "correctness_checked": True, "scope": "near_transfer", "mastery_eligible": True, "support_provenance": {}}
    base.update(kw)
    return base


def run_cli(*argv) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = alearn.main(list(argv))
    assert rc == 0, buf.getvalue()
    return json.loads(buf.getvalue())


class PredicateProvenanceTests(unittest.TestCase):
    def test_self_report_marker_is_not_strong(self):
        self.assertFalse(counts_as_strong(ev(verification="self_report")))

    def test_self_report_source_is_not_strong(self):
        self.assertFalse(counts_as_strong(ev(), provenance={"source": "cli_evidence_entry"}))

    def test_runtime_checked_source_is_strong(self):
        for source in ("python_verifier", "workspace_check", "exercise_verifier"):
            with self.subTest(source=source):
                self.assertTrue(counts_as_strong(ev(), provenance={"source": source}))


class CliSelfReportTests(unittest.TestCase):
    def test_cli_strong_claim_is_capped_and_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            sid = SessionKernel(Path(td)).start("t", LearningContract(goal="x"))["session_id"]
            out = run_cli("--workspace", td, "evidence", "--session", sid, "--capability", "cap",
                          "--outcome", "correct", "--independence", "unassisted",
                          "--strength", "strong", "--scope", "far_transfer")
            payload = out["record"]["payload"]
            self.assertEqual(payload["strength"], "medium")
            self.assertEqual(payload["verification"], "self_report")
            self.assertEqual(payload["format_semantics"]["requested_strength"], "strong")
            self.assertIn("self_reported_ceiling:medium", payload["format_semantics"]["reasons"])
            self.assertEqual(out["projection"]["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 0)


class LegacySelfReportTests(unittest.TestCase):
    """Ledgers written before the fix hold strong self-reports; readers must not trust them."""

    def _legacy_session(self, td):
        k = SessionKernel(Path(td))
        s = k.start("t", LearningContract(goal="x"), learner_id="L1")
        led = k.ledger(s["session_id"])
        led.append("evidence", ev(evidence_id="legacy", scope="far_transfer"), {"source": "cli_evidence_entry"})
        return k, s["session_id"], led

    def test_projection_ignores_legacy_self_report(self):
        with tempfile.TemporaryDirectory() as td:
            k, sid, _ = self._legacy_session(td)
            proj = k.rebuild_projection(sid)
            self.assertEqual(proj["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 0)

    def test_learner_model_ignores_legacy_self_report(self):
        with tempfile.TemporaryDirectory() as td:
            self._legacy_session(td)
            agg = aggregate_learner_state(Path(td) / ".learning" / "sessions", learner_id="L1")
            self.assertEqual(agg["capabilities"]["cap"]["strong_unassisted_successes"], 0)

    def test_mastery_does_not_qualify_legacy_self_report(self):
        with tempfile.TemporaryDirectory() as td:
            _, _, led = self._legacy_session(td)
            obs = observations_from_projection_caps(led.verified_records(), "cap")
            self.assertEqual(len(obs), 1)
            self.assertFalse(obs[0]["qualified"])


class RuntimeCheckedStaysStrongTests(unittest.TestCase):
    def test_verified_python_run_counts_as_strong(self):
        with tempfile.TemporaryDirectory() as td:
            # the python verifier is adapter-gated: only a python-domain session exposes it
            sid = SessionKernel(Path(td)).start("Python references", LearningContract(goal="x"))["session_id"]
            run_cli("--workspace", td, "verify-python", "--session", sid, "--capability", "cap",
                    "--code", "print(6 * 7)", "--expected-stdout", "42",
                    "--independence", "unassisted", "--scope", "independent_reproduction")
            proj = SessionKernel(Path(td)).rebuild_projection(sid)
            self.assertEqual(proj["demonstrated_capabilities"]["cap"]["strong_unassisted_successes"], 1)


if __name__ == "__main__":
    unittest.main()

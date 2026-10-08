import sys, unittest, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; SKILL = ROOT/"skills"/"adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.session import SessionKernel
from runtime.contracts import LearningContract
from runtime.diagnostician import analyze
from runtime.utils import new_id

def eng(outcome, cap, mis=None, scope="near_transfer"):
    e={"evidence_id":new_id("ev"),"capability_id":cap,"outcome":outcome,"independence":"unassisted",
       "strength":"strong","correctness_checked":True,"scope":scope,"mastery_eligible":True,"support_provenance":{}}
    if mis: e["misconception"]=mis
    return e

class DiagnosticianTests(unittest.TestCase):
    def _two_sessions(self, td):
        k=SessionKernel(Path(td))
        for _ in range(2):
            s=k.start("t",LearningContract(goal="x"),learner_id="L")
            led=k.ledger(s["session_id"])
            led.append("evidence", eng("incorrect","causation", mis="reverses cause and effect"))
            led.append("evidence", eng("incorrect","correlation", mis="reverses cause and effect"))
            k.rebuild_projection(s["session_id"])
        return Path(td)/".learning"/"sessions"

    def test_persistent_failure_surfaced(self):
        with tempfile.TemporaryDirectory() as td:
            r=analyze(self._two_sessions(td), learner_id="L")
            self.assertEqual(r["sessions_analyzed"],2)
            caps=[p["capability"] for p in r["persistent_failures"]]
            self.assertIn("causation",caps); self.assertIn("correlation",caps)

    def test_recurring_misconception_tally(self):
        with tempfile.TemporaryDirectory() as td:
            r=analyze(self._two_sessions(td), learner_id="L")
            self.assertTrue(r["recurring_misconceptions"])
            top=r["recurring_misconceptions"][0]
            self.assertEqual(top["label"],"reverses cause and effect")
            self.assertEqual(top["count"],4)  # 2 caps x 2 sessions

    def test_co_failure_cluster(self):
        with tempfile.TemporaryDirectory() as td:
            r=analyze(self._two_sessions(td), learner_id="L")
            self.assertTrue(r["co_failure_clusters"])
            self.assertEqual(sorted(r["co_failure_clusters"][0]["capabilities"]),["causation","correlation"])

    def test_resolved_capability_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            k=SessionKernel(Path(td))
            s=k.start("t",LearningContract(goal="x"),learner_id="L")
            led=k.ledger(s["session_id"])
            led.append("evidence", eng("incorrect","x"))
            s2=k.start("t",LearningContract(goal="x"),learner_id="L")
            led2=k.ledger(s2["session_id"])
            led2.append("evidence", eng("correct","x",scope="far_transfer"))  # resolved by strong success
            k.rebuild_projection(s["session_id"]); k.rebuild_projection(s2["session_id"])
            r=analyze(Path(td)/".learning"/"sessions", learner_id="L")
            self.assertNotIn("x",[p["capability"] for p in r["persistent_failures"]])

if __name__=="__main__": unittest.main()

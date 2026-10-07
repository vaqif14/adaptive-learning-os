import sys, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; SKILL = ROOT/"skills"/"adaptive-learn"
sys.path.insert(0, str(SKILL))
from runtime.listener import grade_explanation, _key_terms
from runtime.scheduling import schedule

SRC = ("Attention computes a weighted sum of values. Each query scores against keys "
       "with a dot product, scaled by the square root of the dimension, then softmax "
       "turns the scores into weights that sum to one.")

class ListenerTests(unittest.TestCase):
    def test_good_explanation_high_coverage(self):
        expl = ("Attention takes a weighted sum of values; the query scores against keys by dot "
                "product, scaled by square root of dimension, and softmax makes weights that sum to one.")
        g = grade_explanation(expl, SRC)
        self.assertEqual(g["outcome"], "correct")
        self.assertGreaterEqual(g["coverage"], 0.6)

    def test_poor_explanation_reports_missed(self):
        g = grade_explanation("Attention is some AI magic thing.", SRC)
        self.assertIn(g["outcome"], {"incorrect","partial"})
        self.assertTrue(g["missed"])          # tells you what you missed
        self.assertIn("softmax", g["missed"])

    def test_no_source(self):
        self.assertEqual(grade_explanation("x", "")["status"], "no_source_terms")

    def test_unsupported_terms_flagged(self):
        g = grade_explanation("Attention uses blockchain and quantum tunnelling.", SRC)
        self.assertTrue(any(t in g["unsupported"] for t in ["blockchain","quantum","tunnelling"]))

class FortyEightHourTests(unittest.TestCase):
    def _obs(self, day): return {"correct": True, "timestamp": (datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(days=day)).isoformat()}
    def test_fresh_concept_at_risk_after_48h(self):
        now = datetime(2026,1,4,tzinfo=timezone.utc)   # 3 days after the single touch
        st = schedule("c", [self._obs(0)], now=now)
        self.assertTrue(st.at_risk_48h)
        self.assertIsNotNone(st.apply_by)
    def test_not_at_risk_within_48h(self):
        now = datetime(2026,1,1,12,tzinfo=timezone.utc)  # 12h after
        st = schedule("c", [self._obs(0)], now=now)
        self.assertFalse(st.at_risk_48h)
    def test_reinforced_not_at_risk(self):
        now = datetime(2026,1,10,tzinfo=timezone.utc)
        st = schedule("c", [self._obs(0), self._obs(1), self._obs(2)], now=now)  # 3 recalls
        self.assertFalse(st.at_risk_48h)

if __name__=="__main__": unittest.main()

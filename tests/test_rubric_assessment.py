import io
import json
import sys
from pathlib import Path
from contextlib import redirect_stdout

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills/adaptive-learn"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "scripts"))
import alearn
from runtime.rubric import assess_rubric, artifact_digest
from runtime.session import SessionKernel
from runtime.contracts import LearningContract

TEXT = "The evidence supports this conclusion, with uncertainty."


def data():
    rubric = {"criteria": [
        {"id": "reasoning", "description": "Justify the conclusion", "max_score": 4},
        {"id": "uncertainty", "description": "Identify uncertainty", "max_score": 1, "critical": True}]}
    assessment = {"reviewer": "Teacher", "submission_sha256": artifact_digest(TEXT), "scores": [
        {"id": "reasoning", "score": 4, "rationale": "Conclusion tied to evidence", "evidence_excerpt": "evidence supports"},
        {"id": "uncertainty", "score": 1, "rationale": "Caveat present", "evidence_excerpt": "uncertainty"}]}
    return rubric, assessment


def test_full_rubric_is_attributed_and_never_runtime_verified():
    r, a = data()
    result = assess_rubric(TEXT, r, a)
    assert result["outcome"] == "correct"
    assert result["verification"] == "self_report"
    assert not result["correctness_checked"]


def test_critical_failure_cannot_be_hidden_by_high_total():
    r, a = data()
    a["scores"][1]["score"] = 0
    result = assess_rubric(TEXT, r, a)
    assert result["score_fraction"] == .8
    assert result["outcome"] == "partial"
    assert result["critical_failures"] == ["uncertainty"]


@pytest.mark.parametrize("fault", ["wrong_artifact", "missing_score", "invented_excerpt", "missing_reason", "nan", "duplicate", "unknown"])
def test_invalid_or_unbound_assessment_is_rejected(fault):
    r, a = data()
    if fault == "wrong_artifact":
        a["submission_sha256"] = "for another answer"
    elif fault == "missing_score":
        a["scores"].pop()
    elif fault == "invented_excerpt":
        a["scores"][0]["evidence_excerpt"] = "invented quotation"
    elif fault == "missing_reason":
        a["scores"][0]["rationale"] = ""
    elif fault == "nan":
        a["scores"][0]["score"] = float("nan")
    elif fault == "duplicate":
        a["scores"].append(a["scores"][0])
    else:
        a["scores"][0]["id"] = "not-a-criterion"
    with pytest.raises(ValueError):
        assess_rubric(TEXT, r, a)


def test_cli_rubric_records_medium_evidence_with_reviewer(tmp_path):
    k = SessionKernel(tmp_path)
    sid = k.start("History", LearningContract(goal="Explain"))["session_id"]
    r, a = data()
    (tmp_path / "submission.txt").write_text(TEXT)
    (tmp_path / "rubric.json").write_text(json.dumps(r))
    (tmp_path / "assessment.json").write_text(json.dumps(a))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = alearn.main(["--workspace", str(tmp_path), "rubric-check", "--session", sid,
            "--capability", "reasoning", "--submission-file", str(tmp_path / "submission.txt"),
            "--rubric-file", str(tmp_path / "rubric.json"), "--assessment-file", str(tmp_path / "assessment.json")])
    assert rc == 0
    last = k.ledger(sid).verified_records()[-1]
    assert last["payload"]["reviewer"] == "Teacher"
    assert last["payload"]["strength"] == "medium"
    assert k.rebuild_projection(sid)["demonstrated_capabilities"]["reasoning"]["strong_unassisted_successes"] == 0
